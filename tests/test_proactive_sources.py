import asyncio
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from oak.proactive import ProactiveAlerts, Watch
from oak.proactive_sources import FeedSource, feed_url, parse_feed, read_feed


class FeedTests(unittest.IsolatedAsyncioTestCase):
    watch = Watch('test', 'grant', 'https://example.org/news.rss', 'Accessibility project funding')
    item = ('<item><guid>grant-1</guid><title>Accessibility grant opens</title>'
            '<link>https://example.org/grants/1</link><pubDate>Thu, 19 Nov 2026 12:00:00 GMT</pubDate>'
            '<description>Applications close on Friday.</description></item>')

    def feed(self, *items):
        return ('<rss><channel>' + ''.join(items) + '</channel></rss>').encode()

    async def test_changed_primary_evidence_fails_recheck(self):
        responses = iter([self.feed(self.item), self.feed(self.item.replace('Friday', 'Thursday'))])
        async def fetch(url):
            self.assertEqual(self.watch.source, url)
            return next(responses)
        source = FeedSource(fetch)
        candidate, = await source.scan(self.watch, time.time())
        self.assertFalse(await source.verify(self.watch, candidate, time.time()))

    def test_excludes_unrelated_stories_conflicting_records_and_external_links(self):
        unrelated = self.item.replace('grant opens', 'grantmaker opens')
        external = self.item.replace('example.org/grants', 'other.example/grants')
        conflicting = self.item.replace('Friday', 'Thursday')
        for items in [(unrelated,), (external,), (self.item, conflicting),
                      (self.item, conflicting.replace('grant-1', 'other-id'))]:
            with self.subTest(items=items):
                self.assertEqual(parse_feed(self.feed(*items), self.watch), [])
        self.assertEqual(len(parse_feed(self.feed(self.item, self.item), self.watch)), 1)

    def test_invalid_source_and_xml(self):
        for url in ['http://example.org/rss', 'https://localhost/rss', 'https://example.org:8000/rss',
                    'https://example.org/rss?api_key=example', 'https://account@example.org/rss', 'https://127.0.0.1/rss']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                feed_url(url)
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE rss><rss/>', self.watch)


@unittest.skipUnless(os.environ.get('OAK_TEST_PUBLIC_FEEDS') == '1',
                     'Set OAK_TEST_PUBLIC_FEEDS=1 to read two live primary publishers.')
class LiveFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_publishers_deliver_once_across_restart(self):
        # Pick a project from the current index, then watch its stable per-project
        # release feed. The global index can rotate between scan and verification.
        source = FeedSource()
        index = 'https://pypi.org/rss/updates.xml'
        updates = await source.scan(Watch('test-owner', '*', index, 'Find a recently released package'), time.time())
        package = urlsplit(updates[0].url).path.split('/')[2]
        feeds = [(f'https://pypi.org/rss/project/{package}/releases.xml', 'Track a Python distribution release'),
                 ('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.atom',
                  'Follow the latest seismic observation')]
        # Discover a currently published headline, then opt in to that exact topic.
        # Outbound Telegram is intercepted. Both discovery and verification fetch live bytes.
        async def run(url, interest, db):
            reads = []
            messages = []
            async def fetch(url):
                reads.append(url)
                return await read_feed(url)
            async def send(owner, text):
                self.assertEqual(owner, 'test-owner')
                messages.append(text)
                return len(messages)
            source = FeedSource(fetch)
            now = time.time()
            items = await source.scan(Watch('test-owner', '*', url, interest), now)
            item = next(row for row in items if row.published_at <= now < row.expires_at and len(row.title) <= 80 and '|' not in row.title)
            engine = ProactiveAlerts(db, source, send)
            engine.command('test-owner', f'/alerts watch {item.title} | {url} | {interest}')
            await engine.tick()
            self.assertEqual(len(messages), 1, (url, engine.command('test-owner', '/alerts history')))
            self.assertIn(item.url, messages[0])
            self.assertIn(interest, messages[0])
            self.assertIn(interest, engine.command('test-owner', 'why this?', reply_to=1))
            await ProactiveAlerts(db, source, send).tick()
            self.assertEqual(len(messages), 1)
            self.assertEqual(reads, [url] * 4)
            self.assertIn('sent', engine.command('test-owner', '/alerts history'))
        with tempfile.TemporaryDirectory() as tmp:
            await asyncio.gather(*(run(url, interest, Path(tmp) / str(i)) for i, (url, interest) in enumerate(feeds)))
