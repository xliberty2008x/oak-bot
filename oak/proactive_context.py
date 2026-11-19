"""Local topic selection for automatic discovery. No conversation text becomes a public query."""
from dataclasses import dataclass
from functools import lru_cache
import re


@dataclass(frozen=True)
class DiscoveryTopic:
    topic: str
    source: str
    context_terms: tuple[str, ...]
    item_terms: tuple[str, ...]
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.topic, str) or not self.topic.strip() or not isinstance(self.reason, str):
            raise ValueError("Provide a discovery topic and optional description.")
        if len(self.topic) > 80 or len(self.reason) > 240 or not all(c.isprintable() for c in self.topic + self.reason):
            raise ValueError("Invalid discovery topic description.")
        for terms in (self.context_terms, self.item_terms):
            if not isinstance(terms, tuple) or not 1 <= len(terms) <= 32:
                raise ValueError("Set one to 32 terms for each side of the relevance match.")
            for term in terms:
                if not isinstance(term, str) or not term.strip() or len(term) > 80 or not term.isprintable():
                    raise ValueError("Discovery terms must be short phrases.")
                if "*" in term and (not term.endswith("*") or "*" in term[:-1] or len(term[:-1].strip()) < 4):
                    raise ValueError("A word stem needs at least four characters followed by *.")


@lru_cache(maxsize=512)
def _pattern(term):
    stem = term.endswith("*")
    words = re.escape((term[:-1] if stem else term).strip().casefold()).replace(r"\ ", r"\s+")
    suffix = r"[\w’'-]*" if stem else ""
    return re.compile(r"(?<!\w)" + words + suffix + r"(?!\w)")


# A newer explicit closure/negative preference beats older positive mentions.
_CLOSED = re.compile(
    r"\b(?:no longer|don['’]t (?:want|like|follow)|stop (?:following|sending)|"
    r"(?:already )?(?:finished|completed|cancelled|canceled|resolved)|"
    r"not (?:into|interested)|do not (?:want|like|follow)|stopped|більше не|"
    r"не (?:цікавить|цікавлюся|хочу)|закінчи\w*|заверши\w*|скасува\w*)\b", re.I)


def match_context(topic, messages):
    """Input is newest first. Only a bounded matching excerpt stays in RAM."""
    for message in messages:
        if not isinstance(message, str) or message.lstrip().startswith("/"):
            continue
        clauses = re.split(r"(?<=[.!?;])\s+|\n+|\s+(?:but|але)\s+", message[:1600])
        for clause in reversed(clauses):
            text = " ".join(clause.split())[:500]
            if not text:
                continue
            if any(_pattern(term).search(text.casefold()) for term in topic.context_terms):
                return None if _CLOSED.search(text) else text
    return None


def item_affinity(topic, candidate):
    """Rank explicit catalogue concepts in the independent publisher evidence."""
    title = candidate.title.casefold()
    summary = candidate.summary.casefold()
    return sum(2 if _pattern(term).search(title) else 1 if _pattern(term).search(summary) else 0
               for term in topic.item_terms)


def valid_draft(text):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 700:
        return False
    # The verified item URL is the only link the delivery gate should introduce.
    return (all(char.isprintable() or char == "\n" for char in text)
            and not re.search(r"(?:\w+://|(?:mailto|tel):|www\.|\b(?:[\w-]+\.)+[^\W\d_]{2,}\b|\[[^\]]*\]\()", text, re.I))
