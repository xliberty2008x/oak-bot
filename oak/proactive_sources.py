"""Read one owner-approved, first-party RSS or Atom feed and recheck its evidence."""

import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
from html.parser import HTMLParser
from http.client import HTTPSConnection
import re
import socket
import ssl
import time
from urllib.parse import parse_qsl, urljoin, urlsplit
from xml.etree import ElementTree as ET

from .proactive import Candidate
from .website_navigation_transport import canonical_host, resolve_public_pin


MAX_BYTES = 512 * 1024
FRESHNESS = 48 * 3600
ATOM = '{http://www.w3.org/2005/Atom}'
DC = '{http://purl.org/dc/elements/1.1/}'
CAP = '{urn:oasis:names:tc:emergency:cap:1.2}'
MEDIA = '{http://search.yahoo.com/mrss/}'


def feed_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url) or '\\' in url:
        raise ValueError('Provide a public HTTPS feed URL.')
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.port not in (None, 443) or parts.fragment):
        raise ValueError('Provide a public HTTPS feed URL without credentials or a fragment.')
    canonical_host(parts.hostname)
    if any(re.search(r'(?:token|password|secret|credential|signature|api[_-]?key|^key$|^auth$)', key, re.I)
           for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        raise ValueError('Use a public feed without access keys.')
    return parts


def _read_xml(parts, pin):
    # Connect to the checked address while authenticating TLS for the original host.
    connection = HTTPSConnection(parts.hostname, timeout=5)
    deadline = time.monotonic() + 12
    try:
        with socket.create_connection((pin, 443), timeout=5) as sock:
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            connection.request('GET', path, headers={'Accept': 'application/atom+xml, application/rss+xml, application/xml, text/xml',
                                                     'Cache-Control': 'no-cache', 'User-Agent': 'Oak/1 (proactive feed reader)'})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('Publisher feed unavailable at the approved URL.')
            data = bytearray()
            while len(data) <= MAX_BYTES and time.monotonic() < deadline:
                chunk = response.read1(65536)
                if not chunk:
                    return bytes(data)
                data.extend(chunk)
            raise ValueError('Publisher feed exceeded its read limit.')
    finally:
        connection.close()


async def read_feed(url):
    parts = feed_url(url)
    pin = await resolve_public_pin(parts.hostname)
    return await asyncio.wait_for(asyncio.to_thread(_read_xml, parts, pin), timeout=15)


def _timestamp(value):
    if not value:
        raise ValueError('Missing publication time')
    try:
        date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        date = parsedate_to_datetime(value.strip())
    if date.tzinfo is None:
        raise ValueError('Ambiguous publication time')
    return date.timestamp()


class _SummaryText(HTMLParser):
    """Strip publisher markup, including script and style contents."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def _summary(entry, atom):
    tags = (ATOM + 'summary', ATOM + 'content') if atom else ('description',)
    tags += (MEDIA + 'description', MEDIA + 'group/' + MEDIA + 'description')
    for tag in tags:
        node = entry.find(tag)
        if node is None:
            continue
        parser = _SummaryText()
        parser.feed(' '.join(node.itertext())[:8192])
        parser.close()
        value = ' '.join(' '.join(parser.parts).split())
        if value:
            return value[:700]
    return ''


def _candidate(entry, atom, watch):
    if atom:
        title = entry.findtext(ATOM + 'title')
        link = next((node.get('href') for node in entry.findall(ATOM + 'link')
                     if node.get('rel', 'alternate') == 'alternate'), None)
        date = entry.findtext(ATOM + 'published') or entry.findtext(ATOM + 'updated')
        ident = entry.findtext(ATOM + 'id')
    else:
        title, link = entry.findtext('title'), entry.findtext('link')
        date = entry.findtext('pubDate') or entry.findtext(DC + 'date')
        ident = entry.findtext('guid')
    title = ' '.join((title or '').split())
    if not title or len(title) > 300 or not link:
        return None
    if watch.topic != '*' and not re.search(r'(?<!\w)' + re.escape(watch.topic.casefold()) + r'(?!\w)', title.casefold()):
        return None
    link = urljoin(watch.source, link.strip())
    if feed_url(link).hostname != feed_url(watch.source).hostname:
        return None
    published = _timestamp(date)
    expires = published + FRESHNESS
    valid_until = entry.findtext(CAP + 'expires') or entry.findtext('expirationDate')
    deadline = _timestamp(valid_until) if valid_until else None
    if deadline is not None:
        expires = min(expires, deadline)
    # A publisher ID survives a revised permalink. Include the normalized item
    # evidence separately so changed availability/text cannot pass the recheck.
    key = hashlib.sha256((ident or link).encode()).hexdigest()
    summary = _summary(entry, atom)
    # Views, rating counters and markup can change between two reads. Bind the
    # facts actually passed to the writer and shown to the owner.
    facts = json.dumps([key, title, link, published, expires, deadline, summary],
                       ensure_ascii=False, separators=(',', ':'))
    evidence = hashlib.sha256(facts.encode()).hexdigest()
    return Candidate(key, title, link, published, expires, evidence_id=evidence, deadline_at=deadline,
                     summary=summary)


def parse_feed(raw, watch):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or b'\x00' in raw:
        raise ValueError('Unsupported publisher feed.')
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', raw, re.I):
        raise ValueError('Feed declarations are unsupported.')
    root = ET.fromstring(raw.decode('utf-8-sig'))
    atom = root.tag == ATOM + 'feed'
    if not atom and root.tag != 'rss':
        raise ValueError('Expected an RSS 2.0 or Atom feed.')
    items = root.findall(ATOM + 'entry' if atom else './channel/item')
    candidates = {}
    conflicts = set()
    urls = {}
    for item in items[:100]:
        try:
            candidate = _candidate(item, atom, watch)
        except (ValueError, TypeError, OverflowError):
            continue
        if candidate is None:
            continue
        previous = candidates.get(candidate.key)
        if previous is not None and candidate != previous:
            conflicts.add(candidate.key)
        previous_key = urls.setdefault(candidate.url, candidate.key)
        if previous_key != candidate.key:
            conflicts.update((previous_key, candidate.key))
        candidates[candidate.key] = candidate
    return sorted((item for key, item in candidates.items() if key not in conflicts),
                  key=lambda item: item.published_at, reverse=True)


class FeedSource:
    validate = staticmethod(feed_url)

    def __init__(self, fetch=read_feed):
        self.fetch = fetch

    async def scan(self, watch, now):
        return parse_feed(await self.fetch(watch.source), watch)

    async def verify(self, watch, candidate, now):
        # Replay a fresh GET even when scan and verify run within the same tick.
        return candidate in await self.scan(watch, now)
