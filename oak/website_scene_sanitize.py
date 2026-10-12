"""Broker-side masking of known values in bounded scene text.

The page cannot override these Python operations. This is a second boundary for
broker-known human values, not a detector for arbitrary private page content.
Only labels, text entries, option labels and prior human-action labels change.
Unknown properties remain for the strict scene validator to reject.
"""

import base64
from copy import deepcopy
import html
import json
import re
from urllib.parse import quote, quote_plus, unquote

from .website_ui import _size


MASK = '•••'
_ENTITY = re.compile(r'&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);')
_URL_ESCAPE = re.compile(r'(?:%[0-9a-fA-F]{2})+')
_JSON_ESCAPE = re.compile(r'(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))+')


def _normalize(value):
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f\x7f]', ' ', value)).strip()


def _variants(redactions, credential_values):
    values = set()
    for collection in (redactions, credential_values):
        if type(collection) not in {list, tuple} or len(collection) > 128:
            raise ValueError('Invalid private scene redactions.')
        for value in collection:
            if type(value) is not str or len(value) > 4096:
                raise ValueError('Invalid private scene redactions.')
            if value:
                values.update((value, _normalize(value)))
    result = set()
    for value in values - {''}:
        result.add(value)
        for safe in ('', "!'()*-._~"):
            encoded = quote(value, safe=safe, errors='surrogatepass')
            result.add(encoded)
            result.add(re.sub(r'%[0-9A-F]{2}', lambda match: match[0].lower(), encoded))
        result.add(quote_plus(value, safe='', errors='surrogatepass'))
        result.add(html.escape(value, quote=True))
        result.add(html.escape(value, quote=False))
        for ascii_only in (True, False):
            result.add(json.dumps(value, ensure_ascii=ascii_only)[1:-1])
        raw = value.encode('utf-8', errors='surrogatepass')
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            encoded = encode(raw).decode('ascii')
            result.update((encoded, encoded.rstrip('=')))
    return sorted(result - {''}, key=len, reverse=True)


def _ranges(text, variants):
    ranges = []
    for value in variants:
        offset = 0
        while (index := text.find(value, offset)) >= 0:
            ranges.append((index, index + len(value)))
            offset = index + len(value)
        # DOM captions may already have been truncated before reaching Python.
        # Mask a known-value prefix at the end, but do not guess tiny prefixes.
        if len(value) > 4:
            start = text.find(value[:4])
            while start >= 0:
                if len(text) - start < len(value) and value.startswith(text[start:]):
                    ranges.append((start, len(text)))
                    break
                start = text.find(value[:4], start + 1)
    return ranges


def _replace_ranges(text, ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + MASK + text[end:]
    return text


def _mask_decoded(text, variants, pattern, decode):
    """Find secrets through escapes, replacing original spans only.

Unrelated entity/escape text is preserved rather than globally decoded into
new display text. A decoded run may be masked in full when it contains a match.
"""
    parts, spans, offset = [], [], 0
    for match in pattern.finditer(text):
        parts.append(text[offset:match.start()])
        spans.extend((index, index + 1) for index in range(offset, match.start()))
        try:
            decoded = decode(match[0])
        except (ValueError, UnicodeError):
            decoded = match[0]
        parts.append(decoded)
        spans.extend((match.start(), match.end()) for _ in decoded)
        offset = match.end()
    parts.append(text[offset:])
    spans.extend((index, index + 1) for index in range(offset, len(text)))
    decoded = ''.join(parts)
    return _replace_ranges(text, [(spans[start][0], spans[end - 1][1])
                                 for start, end in _ranges(decoded, variants)])


def _clean(value, limit, variants):
    if type(value) is not str:
        return value  # Preserve invalid types for structural rejection.
    text = _normalize(value)
    text = _replace_ranges(text, _ranges(text, variants))
    for pattern, decode in (
            (_ENTITY, html.unescape),
            (_URL_ESCAPE, lambda item: unquote(item, errors='strict')),
            (_JSON_ESCAPE, lambda item: json.loads('"' + item + '"'))):
        text = _mask_decoded(text, variants, pattern, decode)
    text = re.sub(r'[^\s@]+@[^\s@]+\.[^\s@]+', MASK, text)
    text = re.sub(r'(?:https?://|www\.)[^\s<>]+', MASK, text, flags=re.I)
    text = re.sub(r'\b[A-Za-z0-9_-]{24,}\b', MASK, text)
    # Truncate in the widget's UTF-16 units without producing a lone surrogate.
    return text.encode('utf-16-le', errors='surrogatepass')[:limit * 2].decode('utf-16-le', errors='ignore')


def sanitize_scene(scene, redactions, credential_values, *, option_redactions=None):
    """Mask text; private options may use a separate free-text redaction set.

    Omitting the optional set retains strict legacy behavior. Credentials and
    generic email/URL/token filters always apply. Option metadata is excluded
    separately by model_context, irrespective of its human-facing captions.
    """
    _size(scene, 65536)
    variants = _variants(redactions, credential_values)
    option_variants = variants if option_redactions is None else _variants(option_redactions, credential_values)
    result = deepcopy(scene)
    if type(result) is not dict:
        return result
    for key, label, limit in (('controls', 'label', 160), ('actions', 'label', 160),
                               ('texts', 'text', 240), ('history', 'action', 160)):
        items = result.get(key)
        if type(items) is not list:
            continue
        for item in items:
            if type(item) is not dict:
                continue
            if label in item:
                item[label] = _clean(item[label], limit, variants)
            if key == 'controls' and type(item.get('options')) is list:
                for option in item['options']:
                    if type(option) is dict and 'label' in option:
                        option['label'] = _clean(option['label'], 120, option_variants)
    return result
