"""Owner-scoped, opt-in proactive alerts, independent of the host or chat transport."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, time as local_time
from itertools import islice
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .proactive_context import DiscoveryTopic, item_affinity, match_context, valid_draft


@dataclass(frozen=True)
class Watch:
    owner: str
    topic: str
    source: str
    interest: str
    discovery: bool = False


@dataclass(frozen=True)
class Candidate:
    """Times are Unix seconds. The source derives the key from a stable item identifier."""
    key: str
    title: str
    url: str
    published_at: float
    expires_at: float
    next_step: str = "Read the source for details."
    uncertainty: str = ""
    evidence_id: str = ""
    deadline_at: float | None = None
    summary: str = ""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_owner (
    owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, timezone TEXT NOT NULL,
    quiet_start TEXT, quiet_end TEXT, daily_cap INTEGER NOT NULL DEFAULT 2
);
CREATE TABLE IF NOT EXISTS alert_watch (
    owner TEXT NOT NULL, topic TEXT COLLATE NOCASE NOT NULL, source TEXT NOT NULL,
    interest TEXT NOT NULL, muted INTEGER NOT NULL DEFAULT 0, reduced INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(owner,topic,source)
);
CREATE TABLE IF NOT EXISTS alert_discovery (
    owner TEXT PRIMARY KEY REFERENCES alert_owner(owner), enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alert_discovery_failure (
    owner TEXT NOT NULL, source TEXT NOT NULL, occurred_at REAL NOT NULL, PRIMARY KEY(owner,source)
);
CREATE TABLE IF NOT EXISTS alert_abstention (
    owner TEXT NOT NULL, fingerprint BLOB NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(owner,fingerprint)
);
CREATE TABLE IF NOT EXISTS alert_discovery_secret (
    id INTEGER PRIMARY KEY CHECK(id=1), secret BLOB NOT NULL
);
INSERT OR IGNORE INTO alert_discovery_secret VALUES(1,randomblob(32));
CREATE TABLE IF NOT EXISTS alert_discovery_topic (
    owner TEXT NOT NULL, topic TEXT NOT NULL COLLATE NOCASE, muted INTEGER NOT NULL DEFAULT 0,
    reduced INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,topic)
);
CREATE TABLE IF NOT EXISTS alert_scan_failure (
    owner TEXT NOT NULL, topic TEXT COLLATE NOCASE NOT NULL, source TEXT NOT NULL,
    occurred_at REAL NOT NULL, PRIMARY KEY(owner,topic,source),
    FOREIGN KEY(owner,topic,source) REFERENCES alert_watch(owner,topic,source) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS alert_resolved (
    owner TEXT NOT NULL, url TEXT NOT NULL, PRIMARY KEY(owner,url)
);
CREATE TABLE IF NOT EXISTS alert_item (
    owner TEXT NOT NULL, id TEXT NOT NULL, topic TEXT COLLATE NOCASE NOT NULL, source TEXT NOT NULL,
    item_key TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, interest TEXT NOT NULL,
    published_at REAL NOT NULL, expires_at REAL NOT NULL, next_step TEXT NOT NULL,
    state TEXT NOT NULL, reason TEXT NOT NULL, updated_at REAL NOT NULL,
    attempted_at REAL, receipt TEXT, PRIMARY KEY(owner,id)
);
CREATE TABLE IF NOT EXISTS alert_conversation (
    owner TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY(owner,id),
    FOREIGN KEY(owner,id) REFERENCES alert_item(owner,id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS alert_attempts ON alert_item(owner,attempted_at);
CREATE INDEX IF NOT EXISTS alert_receipts ON alert_item(owner,receipt);
CREATE INDEX IF NOT EXISTS alert_urls ON alert_item(owner,url);
"""
_HELP = (
    "Add an alert with /alerts watch TOPIC | https://primary-source/feed | YOUR INTEREST. "
    "Use /alerts quiet 22:00-08:00 Europe/Kyiv, /alerts cap 2, /alerts history, "
    "/alerts pause, /alerts resume, /alerts mute TOPIC, /alerts unmute TOPIC, /alerts remove TOPIC or "
    "/alerts resolve ITEM_URL. Use * as the topic to watch every headline. "
    "Reply to an alert with “why this?”, “less like this”, “mute this topic” or “resolved”."
)
_REPLIES = {
    "why this": "why", "чому це": "why", "чому саме це": "why",
    "less like this": "less", "менше такого": "less",
    "mute this topic": "mute", "вимкни цю тему": "mute",
    "resolved": "resolved", "вирішено": "resolved", "stop": "pause", "стоп": "pause", "stop sending these": "pause",
    "don’t message me first": "pause", "don't message me first": "pause", "do not message me first": "pause",
    "не пиши мені першою": "pause", "не пиши мені першим": "pause", "не надсилай мені таке": "mute",
}
_GLOBAL = {
    "pause proactive alerts": "pause", "stop proactive alerts": "pause",
    "resume proactive alerts": "resume", "призупини сповіщення": "pause",
    "призупини проактивні сповіщення": "pause", "віднови сповіщення": "resume",
    "віднови проактивні сповіщення": "resume",
    "enable proactive alerts": "setup",
    "keep an eye on useful opportunities for my projects": "setup",
    "увімкни проактивні сповіщення": "setup_uk",
    "стеж за корисними можливостями для моїх проєктів": "setup_uk",
}


def _words(text):
    return " ".join(text.strip().casefold().rstrip("?!.,").split())


def is_alert_command(text, *, reply_to=None):
    if not isinstance(text, str):
        return False
    phrase = _words(text)
    return (bool(re.match(r"^/alerts(?:\s|$)", text.strip(), re.I))
            or phrase in _GLOBAL or (reply_to is not None and phrase in _REPLIES))


def _https(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and bool(parts.hostname) and parts.username is None
                and parts.password is None and not parts.fragment
                and len(url) <= 2048 and all(ch.isprintable() and not ch.isspace() for ch in url))
    except (TypeError, ValueError):
        return False


def _bounded(text, limit):
    return (isinstance(text, str) and 0 < len(text.strip()) <= limit
            and all(ch.isprintable() for ch in text))


def _valid(c):
    return (isinstance(c, Candidate) and _bounded(c.key, 2048) and _bounded(c.title, 500)
            and _https(c.url) and _bounded(c.next_step, 300)
            and isinstance(c.uncertainty, str) and isinstance(c.summary, str) and len(c.summary) <= 4096
            and (c.deadline_at is None or (isinstance(c.deadline_at, (int, float))
                 and math.isfinite(c.deadline_at) and 0 < c.deadline_at < 253402300799))
            and all(isinstance(t, (int, float)) and math.isfinite(t) and 0 < t < 253402300799
                    for t in (c.published_at, c.expires_at)))


class ProactiveAlerts:
    """A host supplies authenticated owner IDs, a read-only source and a private sender.

    sender(owner, text) must perform a single delivery attempt, returning a confirmed
    message ID. recent(owner, candidate) checks that owner's conversation before dispatch.
    Use a separate state file for each assistant deployment.
    """

    def __init__(self, db_path, source, sender, *, recent=None, clock=time.time, timezone="UTC",
                 catalogue=(), context=None, compose=None):
        self.path = os.fspath(db_path)
        self.source = source
        self.sender = sender
        self.recent = recent
        self.catalogue = tuple(catalogue)
        if len(self.catalogue) > 24 or any(not isinstance(topic, DiscoveryTopic) for topic in self.catalogue):
            raise ValueError("Supply at most 24 public discovery catalogue entries.")
        for topic in self.catalogue:
            if not _https(topic.source):
                raise ValueError("Discovery catalogue sources must be public HTTPS URLs.")
            if hasattr(source, "validate"):
                source.validate(topic.source)
        self.context = context
        self.compose = compose
        self.discovery_owners = set()
        self.clock = clock
        ZoneInfo(timezone)
        self.timezone = timezone
        # The journal inherits the database's owner-only mode, even with a permissive umask.
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        with self._db() as db:
            db.executescript(_SCHEMA)
        with self._db() as db:
            self.discovery_secret = db.execute("SELECT secret FROM alert_discovery_secret WHERE id=1").fetchone()[0]
            if "discovery" not in {r["name"] for r in db.execute("PRAGMA table_info(alert_item)")}:
                db.execute("ALTER TABLE alert_item ADD COLUMN discovery INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def configure_discovery(self, owner, *, enabled=True):
        """Authorize discovery for this process. An owner's earlier opt-out always survives restart."""
        owner = str(owner)
        if not enabled:
            self.discovery_owners.discard(owner)
            return
        if not self.catalogue or self.context is None:
            return
        with self._db() as db:
            db.execute("""INSERT OR IGNORE INTO alert_owner(owner,timezone,daily_cap,quiet_start,quiet_end)
                          VALUES(?,?,1,'22:00','08:00')""", (owner, self.timezone))
            db.execute("INSERT OR IGNORE INTO alert_discovery(owner) VALUES(?)", (owner,))
            db.executemany("INSERT OR IGNORE INTO alert_discovery_topic(owner,topic) VALUES(?,?)",
                           ((owner, topic.topic) for topic in self.catalogue))
        self.discovery_owners.add(owner)

    def _discovery_enabled(self, db, owner):
        return owner in self.discovery_owners and db.execute(
            """SELECT 1 FROM alert_discovery d JOIN alert_owner o USING(owner)
               WHERE d.owner=? AND d.enabled=1 AND o.enabled=1""", (owner,)).fetchone() is not None

    def conversation_context(self, owner, reply_to=None):
        """Return a confirmed prior assistant message for its owner's private conversation."""
        query = """SELECT c.text FROM alert_conversation c JOIN alert_item i USING(owner,id)
                   WHERE i.owner=? AND i.discovery=1 AND i.state='sent'"""
        if reply_to is not None:
            query += " AND i.receipt=?"
            key = str(reply_to)
        else:
            query += " AND i.attempted_at>=? ORDER BY i.attempted_at DESC,i.rowid DESC LIMIT 1"
            key = self.clock() - 24 * 3600
        with self._db() as db:
            row = db.execute(query, (str(owner), key)).fetchone()
            return row["text"] if row else None

    def owns_receipt(self, owner, receipt):
        """Let an adapter route contextual controls without consuming ordinary chat replies."""
        if receipt is None:
            return False
        with self._db() as db:
            row = db.execute("SELECT 1 FROM alert_item WHERE owner=? AND receipt=? LIMIT 1",
                             (str(owner), str(receipt))).fetchone()
            return row is not None

    def is_enabled(self, owner):
        """Whether an adapter should collect recent conversation references for this owner."""
        with self._db() as db:
            row = db.execute("""SELECT 1 FROM alert_watch w JOIN alert_owner o ON o.owner=w.owner
                                WHERE w.owner=? AND w.muted=0 AND o.enabled=1 LIMIT 1""",
                             (str(owner),)).fetchone()
            return row is not None or bool(self._eligible_topics(db, str(owner)))

    def command(self, owner, text, *, reply_to=None):
        if not is_alert_command(text, reply_to=reply_to):
            return None
        owner = str(owner)
        phrase = _words(text)
        with self._db() as db:
            if phrase in _REPLIES:
                item = db.execute("SELECT * FROM alert_item WHERE owner=? AND receipt=?",
                                  (owner, str(reply_to))).fetchone()
                if not item:
                    return None  # A reply to an ordinary message belongs to normal chat.
                action = _REPLIES[phrase]
                ukrainian = item["discovery"] and bool(re.search("[а-яіїєґ]", phrase))
                if action == "why" and ukrainian:
                    return (f"Новина стосується теми «{item['topic']}», яка з’явилася в нашій розмові "
                            f"або в описі твоїх проєктів. Ось перевірена публікація: {item['url']}")
                if action == "why" and item["discovery"]:
                    return (f"The publisher's update matched {item['topic']} in your recent conversation or "
                            f"assistant context. I rechecked it before sending. {item['url']}")
                if action == "why":
                    return f"You asked to watch {item['topic']} because {item['interest']}. " \
                           f"I rechecked the primary source before sending. {item['url']}"
                if action == "pause":
                    db.execute("UPDATE alert_owner SET enabled=0 WHERE owner=?", (owner,))
                    return "Добре, призупиняю проактивні сповіщення." if ukrainian else "Proactive alerts paused."
                if action == "resolved":
                    db.execute("INSERT OR IGNORE INTO alert_resolved(owner,url) VALUES(?,?)", (owner, item["url"]))
                    db.execute("UPDATE alert_item SET reason='marked resolved by owner' WHERE owner=? AND id=?",
                               (owner, item["id"]))
                    if ukrainian:
                        return f"Гаразд, позначаю цю новину з теми «{item['topic']}» як опрацьовану."
                    return "Marked this finding resolved."
                return self._topic_control(db, owner, action, item["topic"], ukrainian=ukrainian)
            if phrase in _GLOBAL:
                action, arg = _GLOBAL[phrase], ""
            else:
                tail = text.strip()[len("/alerts"):].strip().split(None, 1)
                action = tail[0].casefold() if tail else "status"
                arg = tail[1].strip() if len(tail) > 1 else ""
            if action in ("setup", "setup_uk") and owner in self.discovery_owners:
                if phrase in _GLOBAL:
                    # The owner's express request resumes scanning. Preserve each topic's
                    # mute and frequency settings, quiet hours, and interruption cap.
                    db.execute("UPDATE alert_owner SET enabled=1 WHERE owner=?", (owner,))
                    db.execute("UPDATE alert_discovery SET enabled=1 WHERE owner=?", (owner,))
                if phrase not in _GLOBAL and not self._discovery_enabled(db, owner):
                    if action == "setup_uk":
                        return "Скажи «увімкни проактивні сповіщення», і я стежитиму за новинами для твоїх проєктів."
                    return "I can find timely updates from your recent project context. Say “enable proactive alerts” to begin."
                if action == "setup_uk":
                    return ("Я перевірятиму відкриті джерела й напишу, коли знайду щось "
                            "справді корисне для твоїх поточних проєктів. Тихі години й вимкнені теми врахую.")
                return ("I’ll check public sources and message you when a verified update is useful for your "
                        "current projects. I’ll respect your quiet hours and muted topics.")
            if action == "setup":
                return ("Which topic, primary RSS/Atom feed, and interest should I watch? "
                        "Send /alerts watch TOPIC | https://publisher.example/feed | WHY IT MATTERS. "
                        "Choose quiet hours with /alerts quiet 22:00-08:00 Europe/Kyiv.")
            if action == "setup_uk":
                return ("Яку тему відстежувати, у якому офіційному RSS/Atom-джерелі й чому вона важлива? "
                        "Надішли /alerts watch ТЕМА | https://publisher.example/feed | ЧОМУ ЦЕ ВАЖЛИВО. "
                        "Обери тихі години командою /alerts quiet 22:00-08:00 Europe/Kyiv.")
            pref = db.execute("SELECT * FROM alert_owner WHERE owner=?", (owner,)).fetchone()
            if action == "watch":
                return self._watch(db, owner, arg)
            if action in ("status", "help"):
                if not pref:
                    return "Proactive alerts are off. " + _HELP
                watches = db.execute("""SELECT w.topic,w.source,w.muted,w.reduced,f.occurred_at
                                        FROM alert_watch w LEFT JOIN alert_scan_failure f USING(owner,topic,source)
                                        WHERE w.owner=? ORDER BY w.topic,w.source""", (owner,)).fetchall()
                state = "enabled" if pref["enabled"] else "paused"
                quiet = (f"{pref['quiet_start']}–{pref['quiet_end']} {pref['timezone']}"
                         if pref["quiet_start"] else "off")
                lines = [f"Alerts {state}. Daily cap {pref['daily_cap']}. Quiet hours {quiet}."]
                if owner in self.discovery_owners:
                    mode = db.execute("SELECT enabled FROM alert_discovery WHERE owner=?", (owner,)).fetchone()
                    state = "on" if mode and mode["enabled"] else "off"
                    lines.append(f"Contextual discovery is {state}. Use /alerts disable discovery or /alerts enable discovery.")
                    failed = {row[0] for row in db.execute(
                        "SELECT source FROM alert_discovery_failure WHERE owner=?", (owner,))}
                    if "" in failed:
                        lines.append("Recent private context is temporarily unavailable.")
                    sources = {topic.source for topic in self.catalogue}.intersection(failed)
                    if sources:
                        hosts = sorted({urlsplit(source).hostname for source in sources})
                        lines.append("Recent publisher scans unavailable: " + ", ".join(hosts)[:240])
                    muted = db.execute("SELECT topic FROM alert_discovery_topic WHERE owner=? AND muted=1", (owner,))
                    muted_topics = [r["topic"] for r in muted]
                    if muted_topics:
                        lines.append("Muted discovery topics: " + ", ".join(muted_topics)[:350])
                for watch in watches:
                    flag = " (muted)" if watch["muted"] else " (less often)" if watch["reduced"] else ""
                    source = watch["source"]
                    shown = source if len(source) <= 180 else source[:179] + "…"
                    if watch["occurred_at"] is not None:
                        flag += " (last scan failed; check the publisher feed URL)"
                    lines.append(f"{watch['topic']}{flag} · {shown}")
                return "\n".join(lines + [_HELP])
            if action == "history":
                rows = db.execute("SELECT title,state,reason FROM alert_item WHERE owner=? "
                                  "ORDER BY updated_at DESC,rowid DESC LIMIT 8", (owner,))
                return "\n".join(f"{r['title'][:220]} — {r['state']} ({r['reason']})" for r in rows) or "No alert decisions yet."
            if not pref:
                return "Add your first topic and permitted source to enable alerts. " + _HELP
            if action in ("enable", "disable") and arg.casefold() == "discovery":
                if owner not in self.discovery_owners:
                    return "Contextual discovery is unavailable for this assistant."
                db.execute("UPDATE alert_discovery SET enabled=? WHERE owner=?", (action == "enable", owner))
                return "Contextual discovery enabled." if action == "enable" else "Contextual discovery disabled."
            if action == "resolve":
                if not _https(arg):
                    return "Use /alerts resolve followed by the HTTPS link to the item."
                db.execute("INSERT OR IGNORE INTO alert_resolved(owner,url) VALUES(?,?)", (owner, arg))
                db.execute("UPDATE alert_item SET reason='marked resolved by owner' WHERE owner=? AND url=? "
                           "AND attempted_at IS NOT NULL", (owner, arg))
                return "Marked this finding resolved."
            if action in ("pause", "resume"):
                db.execute("UPDATE alert_owner SET enabled=? WHERE owner=?", (action == "resume", owner))
                if owner in self.discovery_owners and re.search("[а-яіїєґ]", phrase):
                    return "Проактивні сповіщення призупинено." if action == "pause" else "Проактивні сповіщення відновлено."
                return "Proactive alerts paused." if action == "pause" else "Proactive alerts resumed."
            if action in ("mute", "unmute", "less", "remove"):
                return self._topic_control(db, owner, action, arg)
            if action == "cap":
                if not re.fullmatch(r"[1-9]|1[0-9]|20", arg):
                    return "Choose a daily cap from 1 to 20."
                db.execute("UPDATE alert_owner SET daily_cap=? WHERE owner=?", (int(arg), owner))
                return f"Daily cap set to {arg}."
            if action == "quiet":
                return self._quiet(db, owner, arg, pref["timezone"])
            return _HELP

    def _watch(self, db, owner, arg):
        parts = [part.strip() for part in arg.split("|")]
        if len(parts) != 3:
            return _HELP
        topic, source, interest = parts
        if not (_bounded(topic, 80) and _https(source) and _bounded(interest, 240)):
            return "Use a topic of 1–80 characters, an HTTPS source URL, and an interest of 1–240 characters."
        validate = getattr(self.source, "validate", None)
        if validate:
            try:
                validate(source)
            except ValueError:
                return "Use a public HTTPS source URL supported by this assistant."
        present = db.execute("SELECT 1 FROM alert_watch WHERE owner=? AND topic=? AND source=?",
                             (owner, topic, source)).fetchone()
        count = db.execute("SELECT COUNT(*) FROM alert_watch WHERE owner=?", (owner,)).fetchone()[0]
        if not present and count >= 8:
            return "You can watch eight sources. Remove a topic before adding another."
        db.execute("INSERT OR IGNORE INTO alert_owner(owner,timezone) VALUES(?,?)", (owner, self.timezone))
        db.execute("""INSERT INTO alert_watch (owner,topic,source,interest) VALUES(?,?,?,?)
                      ON CONFLICT(owner,topic,source) DO UPDATE SET interest=excluded.interest""",
                   (owner, topic, source, interest))
        return f"Saved {topic}. Approved source: {source}. Interest: {interest}."

    @staticmethod
    def _topic_control(db, owner, action, topic, *, ukrainian=False):
        if action == "remove":
            result = db.execute("DELETE FROM alert_watch WHERE owner=? AND topic=?", (owner, topic))
            message = "Removed"
        elif action == "less":
            result = db.execute("UPDATE alert_watch SET reduced=1 WHERE owner=? AND topic=?", (owner, topic))
            message = "Limited to one alert every three days for"
        else:
            result = db.execute("UPDATE alert_watch SET muted=? WHERE owner=? AND topic=?",
                                (action == "mute", owner, topic))
            message = "Muted" if action == "mute" else "Unmuted"
        if action == "less":
            inferred = db.execute("UPDATE alert_discovery_topic SET reduced=1 WHERE owner=? AND topic=?", (owner, topic))
        else:
            inferred = db.execute("UPDATE alert_discovery_topic SET muted=? WHERE owner=? AND topic=?",
                                  (action != "unmute", owner, topic))
        if not result.rowcount and not inferred.rowcount:
            return "Такої теми немає." if ukrainian else "No matching alert topic."
        if ukrainian:
            if action == "less":
                return f"Писатиму про тему «{topic}» не частіше ніж раз на три дні."
            return f"Добре, більше не надсилатиму новини з теми «{topic}»."
        return f"{message} {topic}."

    @staticmethod
    def _quiet(db, owner, arg, timezone):
        if arg.casefold() == "off":
            db.execute("UPDATE alert_owner SET quiet_start=NULL,quiet_end=NULL WHERE owner=?", (owner,))
            return "Quiet hours turned off."
        match = re.fullmatch(r"(\d\d:\d\d)-(\d\d:\d\d)(?:\s+(\S+))?", arg)
        if match:
            start, end, zone = match.groups()
            zone = zone or timezone
            try:
                ZoneInfo(zone)
                if local_time.fromisoformat(start) == local_time.fromisoformat(end):
                    raise ValueError("empty window")
            except (ZoneInfoNotFoundError, ValueError):
                pass
            else:
                db.execute("UPDATE alert_owner SET quiet_start=?,quiet_end=?,timezone=? WHERE owner=?",
                           (start, end, zone, owner))
                return f"Quiet hours set to {start}–{end} {zone}."
        return "Use /alerts quiet 22:00-08:00 Europe/Kyiv or /alerts quiet off."

    async def tick(self, owners=None):
        """Scan at most eight sources per opted-in owner and eight candidates per source."""
        allowed = None if owners is None else {str(owner) for owner in owners}
        now = self.clock()
        with self._db() as db:
            db.execute("""UPDATE alert_item SET state='suppressed',reason='expired',updated_at=?
                          WHERE state='deferred' AND expires_at<=?""", (now, now))
            db.execute("DELETE FROM alert_abstention WHERE expires_at<=?", (now,))
            # Expired decisions are useful audit history for one month. Confirmed and
            # uncertain attempts keep their deduplication record across restarts.
            db.execute("DELETE FROM alert_item WHERE attempted_at IS NULL AND expires_at<?",
                       (now - 30 * 86400,))
            watches = db.execute("""SELECT w.owner,topic,source,interest FROM alert_watch w
                                    JOIN alert_owner o ON o.owner=w.owner
                                    WHERE enabled=1 AND muted=0 ORDER BY w.owner,topic,source""").fetchall()
        for row in watches:
            watch = Watch(**dict(row))
            if allowed is not None and watch.owner not in allowed:
                continue
            if not self._current(watch):
                continue
            try:
                candidates = await self.source.scan(watch, self.clock())
            except Exception:
                # A single mutable status per watch; transport details can include secrets.
                with self._db() as db:
                    db.execute("""INSERT OR REPLACE INTO alert_scan_failure
                                  SELECT owner,topic,source,? FROM alert_watch
                                  WHERE owner=? AND topic=? AND source=?""",
                               (self.clock(), watch.owner, watch.topic, watch.source))
                continue
            with self._db() as db:
                db.execute("DELETE FROM alert_scan_failure WHERE owner=? AND topic=? AND source=?",
                           (watch.owner, watch.topic, watch.source))
            for candidate in islice(candidates, 8):
                if _valid(candidate):
                    await self._consider(watch, candidate)

        for owner in sorted(self.discovery_owners.intersection(allowed) if allowed is not None else self.discovery_owners):
            await self._discover(owner)

    def _context_messages(self, owner):
        try:
            messages = self.context(owner)
            result = (messages,) if isinstance(messages, str) else tuple(islice(messages or (), 24))
        except Exception:
            self._scan_status(owner, "", failed=True)
            return ()
        self._scan_status(owner, "", failed=False)
        return result

    def _scan_status(self, owner, source, *, failed):
        with self._db() as db:
            if failed:
                db.execute("INSERT OR REPLACE INTO alert_discovery_failure VALUES(?,?,?)",
                           (owner, source, self.clock()))
            else:
                db.execute("DELETE FROM alert_discovery_failure WHERE owner=? AND source=?", (owner, source))

    def _eligible_topics(self, db, owner):
        if not self._discovery_enabled(db, owner):
            return []
        muted = {row[0].casefold() for row in db.execute(
            "SELECT topic FROM alert_discovery_topic WHERE owner=? AND muted=1", (owner,))}
        return [topic for topic in self.catalogue if topic.topic.casefold() not in muted]

    async def _discover(self, owner):
        with self._db() as db:
            topics = self._eligible_topics(db, owner)
        if not topics:
            return
        messages = self._context_messages(owner)
        findings = []
        fetched = {}
        # Rotate the starting route each quarter-hour so a four-source budget
        # still covers a larger catalogue of simultaneous interests.
        start = int(self.clock() // 900) % len(topics)
        for topic in topics[start:] + topics[:start]:
            excerpt = match_context(topic, messages)
            if not excerpt:
                continue
            watch = Watch(owner, topic.topic, topic.source, topic.reason or topic.topic, discovery=True)
            if topic.source not in fetched:
                if len(fetched) >= 4:
                    continue
                with self._db() as db:
                    if topic not in self._eligible_topics(db, owner):
                        continue
                try:
                    # Only a fixed publisher URL is fetched. Private context never leaves
                    # the assistant as a search term, path, or request parameter.
                    items = await self.source.scan(replace(watch, topic="*"), self.clock())
                    fetched[topic.source] = tuple(islice(items, 64))
                except Exception:
                    fetched[topic.source] = ()
                    self._scan_status(owner, topic.source, failed=True)
                else:
                    self._scan_status(owner, topic.source, failed=False)
            for candidate in fetched[topic.source]:
                if _valid(candidate):
                    score = item_affinity(topic, candidate)
                    if score:
                        timely = candidate.published_at <= self.clock() < candidate.expires_at
                        priority = (timely, score, candidate.published_at)
                        findings.append((priority, watch, candidate, topic, excerpt))
        # An expired high-affinity article must not crowd a timely match out of a bounded review.
        findings.sort(key=lambda item: item[0], reverse=True)
        evaluated = 0
        for _, watch, candidate, topic, excerpt in findings[:16]:
            # Hash the matched excerpt with a deployment secret. A rejected draft
            # stays rejected until context or publisher evidence actually changes.
            payload = json.dumps([topic.topic, topic.source, excerpt, candidate.key, candidate.title,
                                  candidate.summary, candidate.evidence_id, candidate.expires_at])
            fingerprint = hmac.digest(self.discovery_secret, payload.encode(), "sha256")
            with self._db() as db:
                abstained = db.execute("SELECT 1 FROM alert_abstention WHERE owner=? AND fingerprint=?",
                                       (owner, fingerprint)).fetchone()
            if abstained or not self._decision(watch, candidate):
                continue
            await self._consider(watch, candidate, topic=topic, excerpt=excerpt, fingerprint=fingerprint)
            evaluated += 1
            if evaluated == 3:
                break

    def _current(self, watch):
        with self._db() as db:
            return db.execute("""SELECT 1 FROM alert_watch w JOIN alert_owner o ON o.owner=w.owner
                                 WHERE w.owner=? AND topic=? AND source=? AND interest=? AND enabled=1 AND muted=0""",
                              (watch.owner, watch.topic, watch.source, watch.interest)).fetchone() is not None

    def _policy(self, db, watch, candidate, now):
        if candidate.expires_at <= now or (candidate.deadline_at is not None and candidate.deadline_at <= now):
            return "suppressed", "expired"
        if candidate.published_at > now:
            return "suppressed", "publication is in the future"
        if candidate.uncertainty:
            return "suppressed", "uncertain evidence"
        if watch.discovery:
            if not self._discovery_enabled(db, watch.owner):
                return "deferred", "contextual discovery disabled or paused"
            pref = db.execute("""SELECT o.*,t.muted,t.reduced FROM alert_owner o
                                 JOIN alert_discovery_topic t ON t.owner=o.owner
                                 WHERE o.owner=? AND t.topic=?""", (watch.owner, watch.topic)).fetchone()
            if not pref:
                return "suppressed", "topic no longer available"
        else:
            pref = db.execute("""SELECT o.*,w.muted,w.reduced,w.interest FROM alert_owner o
                                 JOIN alert_watch w ON w.owner=o.owner WHERE o.owner=? AND w.topic=? AND w.source=?""",
                              (watch.owner, watch.topic, watch.source)).fetchone()
            if not pref or pref["interest"] != watch.interest:
                return "suppressed", "watch changed or removed"
        if not pref["enabled"] or pref["muted"]:
            return "deferred", "paused or muted"
        local = datetime.fromtimestamp(now, ZoneInfo(pref["timezone"]))
        start, end = pref["quiet_start"], pref["quiet_end"]
        if start and end:
            hm = local.strftime("%H:%M")
            quiet = (start <= hm < end) if start < end else (hm >= start or hm < end)
            if quiet:
                return "deferred", "quiet hours"
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        count = db.execute("SELECT COUNT(*) FROM alert_item WHERE owner=? AND attempted_at>=?",
                           (watch.owner, midnight)).fetchone()[0]
        if count >= pref["daily_cap"]:
            return "deferred", "daily cap"
        if pref["reduced"] and db.execute("SELECT 1 FROM alert_item WHERE owner=? AND topic=? "
                                         "AND attempted_at>? LIMIT 1",
                                         (watch.owner, watch.topic, now - 3 * 86400)).fetchone():
            return "deferred", "owner requested fewer alerts"
        return None

    def _decision(self, watch, candidate, outcome=None, *, claim=False):
        now = self.clock()
        item_id = hashlib.sha256((watch.source + "\0" + candidate.key).encode()).hexdigest()
        with self._db() as db:
            # In-flight and ambiguous deliveries count towards both deduplication and the cap.
            delivered = db.execute("""SELECT 1 FROM alert_item WHERE owner=? AND (id=? OR url=?)
                                      AND attempted_at IS NOT NULL LIMIT 1""",
                                   (watch.owner, item_id, candidate.url)).fetchone()
            if delivered:
                return None
            decision = self._policy(db, watch, candidate, now)
            resolved = db.execute("SELECT 1 FROM alert_resolved WHERE owner=? AND url=?",
                                  (watch.owner, candidate.url)).fetchone()
            if resolved:
                decision = ("suppressed", "resolved or already covered in conversation")
            elif not decision and self.recent and self.recent(watch.owner, candidate):
                db.execute("INSERT OR IGNORE INTO alert_resolved(owner,url) VALUES(?,?)", (watch.owner, candidate.url))
                decision = ("suppressed", "already covered in recent conversation")
            decision = decision or outcome
            if decision is None and not claim:
                return item_id
            state, reason = decision or ("uncertain", "delivery claimed without a confirmed receipt")
            attempted = now if not decision else None
            db.execute("""INSERT INTO alert_item
                          (owner,id,topic,source,item_key,title,url,interest,published_at,expires_at,next_step,
                           state,reason,updated_at,attempted_at,discovery) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(owner,id) DO UPDATE SET topic=excluded.topic,title=excluded.title,
                            url=excluded.url,interest=excluded.interest,published_at=excluded.published_at,
                            expires_at=excluded.expires_at,next_step=excluded.next_step,state=excluded.state,
                            reason=excluded.reason,updated_at=excluded.updated_at,attempted_at=excluded.attempted_at,
                            discovery=excluded.discovery""",
                       (watch.owner, item_id, watch.topic, watch.source, candidate.key, candidate.title, candidate.url,
                        watch.interest, candidate.published_at, candidate.expires_at, candidate.next_step,
                        state, reason, now, attempted, int(watch.discovery)))
            return item_id if attempted is not None else None

    async def _consider(self, watch, candidate, *, topic=None, excerpt=None, fingerprint=None):
        if not watch.discovery and not self._decision(watch, candidate):
            return
        if watch.discovery:
            current = match_context(topic, self._context_messages(watch.owner))
            if current != excerpt:
                self._decision(watch, candidate, ("suppressed", "relevant context changed"))
                return
            if self.compose:
                try:
                    draft = await self.compose(watch.owner, excerpt, topic, candidate)
                except Exception:
                    self._decision(watch, candidate, ("deferred", "delivery evaluation unavailable"))
                    return
                if not valid_draft(draft):
                    with self._db() as db:
                        db.execute("INSERT OR REPLACE INTO alert_abstention VALUES(?,?,?)",
                                   (watch.owner, fingerprint, candidate.expires_at))
                    self._decision(watch, candidate, ("suppressed", "delivery evaluation abstained"))
                    return
                if not self._decision(watch, candidate):
                    return
                if match_context(topic, self._context_messages(watch.owner)) != excerpt:
                    self._decision(watch, candidate, ("suppressed", "relevant context changed"))
                    return
            else:
                draft = f"{candidate.title}\nThis connects to {watch.interest} from your recent context."
        try:
            reader_watch = replace(watch, topic="*") if watch.discovery else watch
            verified = await self.source.verify(reader_watch, candidate, self.clock())
        except Exception:
            self._decision(watch, candidate, ("deferred", "source recheck unavailable"))
            return
        if verified is not True:
            self._decision(watch, candidate, ("suppressed", "primary evidence did not confirm the finding"))
            return
        if watch.discovery and match_context(topic, self._context_messages(watch.owner)) != excerpt:
            self._decision(watch, candidate, ("suppressed", "relevant context changed"))
            return
        item_id = self._decision(watch, candidate, claim=True)
        if not item_id:
            return
        published = datetime.fromtimestamp(candidate.published_at, ZoneInfo("UTC")).strftime("%Y-%m-%d")
        deadline = ""
        if candidate.deadline_at is not None:
            ends = datetime.fromtimestamp(candidate.deadline_at, ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M UTC")
            deadline = f" Deadline: {ends}."
        text = (f"{candidate.title}\nPublished {published}.{deadline} Why this matters: {watch.interest}\n"
                f"{candidate.next_step}\n{candidate.url}")
        if watch.discovery:
            text = f"{draft.strip()}{deadline}\n{candidate.url}"
        try:
            receipt = await self.sender(watch.owner, text)
            if isinstance(receipt, bool) or not isinstance(receipt, (str, int)) or not str(receipt).strip():
                raise ValueError("missing confirmed message receipt")
        except Exception:
            # Telegram may have accepted the send before an error or a process crash.
            return
        with self._db() as db:
            db.execute("""UPDATE alert_item SET state='sent',reason='verified against approved source',
                          receipt=?,updated_at=? WHERE owner=? AND id=?""",
                       (str(receipt), self.clock(), watch.owner, item_id))
            if watch.discovery:
                db.execute("INSERT INTO alert_conversation(owner,id,text) VALUES(?,?,?)", (watch.owner, item_id, text))
