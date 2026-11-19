"""Policy/recovery tests use publisher documents and real restartable SQLite stores."""
import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from oak.proactive import ProactiveAlerts
from oak.proactive_sources import FeedSource


class ProactivePolicyTest(unittest.IsolatedAsyncioTestCase):
    url = 'https://publisher.example/research/rss'
    item = 'https://publisher.example/research/grant'
    initial = datetime(2026, 11, 2, 20, 30, tzinfo=timezone.utc).timestamp()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = self.initial
        self.sent = []
        self.reads = 0
        self.publish()
        self.engine = self.start()

    def publish(self, entries=(('grant', 'Open grant', 0),)):
        items = []
        for slug, title, offset in entries:
            date = datetime.fromtimestamp(self.initial + offset, timezone.utc)
            items.append(f'<item><title>{title}</title><guid>{slug}</guid>'
                         f'<link>https://publisher.example/research/{slug}</link>'
                         f'<pubDate>{format_datetime(date)}</pubDate></item>')
        (self.root / 'rss').write_text('<rss><channel>' + ''.join(items) + '</channel></rss>')

    async def read(self, url):
        self.assertEqual(url, self.url)
        self.reads += 1
        return (self.root / 'rss').read_bytes()

    async def deliver(self, owner, text):
        self.sent.append((owner, text))
        return len(self.sent)

    def start(self, *, read=None, deliver=None, recent=None):
        return ProactiveAlerts(self.root / 'alert.sqlite', FeedSource(read or self.read),
                               deliver or self.deliver, clock=lambda: self.now, recent=recent)

    def watch(self, owner='A', topic='grant'):
        return self.engine.command(owner, f'/alerts watch {topic} | {self.url} | I fund open research')

    async def test_setup_asks_for_scope_before_any_source_is_read(self):
        response = self.engine.command('A', 'Keep an eye on useful opportunities for my projects.')
        self.assertIn('/alerts watch', response)
        self.assertIn('/alerts quiet', response)
        self.assertIn('RSS/Atom', self.engine.command('B', 'Увімкни проактивні сповіщення'))
        await self.engine.tick()
        self.assertEqual(self.reads, 0)
        self.assertIn('are off', self.engine.command('A', '/alerts'))
        self.assertFalse(self.engine.is_enabled('A'))
        self.watch()
        self.assertTrue(self.engine.is_enabled('A'))
        self.engine.command('A', '/alerts mute grant')
        self.assertFalse(self.engine.is_enabled('A'))
        self.engine.command('A', '/alerts unmute grant')
        self.engine.command('A', '/alerts pause')
        self.assertFalse(self.engine.is_enabled('A'))

    async def test_opt_in_owner_filtering_and_replay(self):
        await self.engine.tick()
        self.assertEqual(self.reads, 0)
        self.watch()
        self.watch('B')
        await self.engine.tick(owners=['B'])
        self.assertEqual(self.reads, 2)
        self.assertEqual(len(self.sent), 1)
        owner, text = self.sent[0]
        self.assertEqual(owner, 'B')
        self.assertIn(self.item, text)
        self.assertIn('I fund open research', text)
        self.assertNotIn('Deadline', text)
        self.assertIn(self.item, self.engine.command('B', 'чому це?', reply_to=1))
        self.assertIsNone(self.engine.command('A', 'why this?', reply_to=1))
        self.assertTrue(self.engine.owns_receipt('B', 1))
        self.assertFalse(self.engine.owns_receipt('A', 1))
        self.assertFalse(self.engine.owns_receipt('B', None))
        await self.start().tick(owners=['B'])
        self.assertEqual(self.reads, 3)
        self.assertEqual(len(self.sent), 1)

    async def test_mutated_primary_document_and_recent_resolution(self):
        self.watch()

        async def change_during_verify(url):
            if self.reads == 1:
                self.publish((('grant', 'Grant cancelled', 0),))
            return await self.read(url)

        await self.start(read=change_during_verify).tick()
        self.assertFalse(self.sent)
        self.assertIn('did not confirm', self.engine.command('A', '/alerts history'))
        await self.start(recent=lambda owner, candidate: candidate.url == self.item).tick()
        await self.start().tick()
        self.assertFalse(self.sent)
        self.assertIn('already covered', self.engine.command('A', '/alerts history'))

    async def test_resolved_before_scan_and_expiry_while_quiet(self):
        self.watch()
        self.engine.command('A', '/alerts resolve ' + self.item)
        self.engine.command('A', '/alerts quiet 22:00-08:00 Europe/Kyiv')
        self.publish((('grant', 'Grant open', 0), ('deadline', 'Grant deadline', -47 * 3600)))
        await self.engine.tick()
        history = self.engine.command('A', '/alerts history')
        self.assertIn('resolved', history)
        self.assertIn('quiet hours', history)
        self.now += 12 * 3600
        self.publish((('grant', 'Grant open', 0),))
        await self.start().tick()
        self.assertIn('expired', self.engine.command('A', '/alerts history'))
        self.assertFalse(self.sent)

    async def test_scan_failure_is_private_and_clears_after_recovery(self):
        self.watch()
        self.watch('B')
        (self.root / 'rss').write_bytes(b'Not a feed')
        await self.engine.tick(owners=['A'])
        self.assertIn('last scan failed', self.engine.command('A', '/alerts'))
        self.assertNotIn('last scan failed', self.engine.command('B', '/alerts'))
        self.assertFalse(self.sent)
        self.publish()
        await self.start().tick(owners=['A'])
        self.assertNotIn('last scan failed', self.engine.command('A', '/alerts'))
        self.assertEqual(len(self.sent), 1)

    async def test_pause_during_recheck_and_mute_after_receipt(self):
        self.watch()

        async def pause_during_read(url):
            if self.reads == 1:
                self.engine.command('A', 'pause proactive alerts')
            return await self.read(url)

        await self.start(read=pause_during_read).tick()
        self.assertFalse(self.sent)
        self.assertIn('paused', self.engine.command('A', '/alerts history'))
        await self.engine.tick()
        self.assertEqual(self.reads, 2)
        self.engine.command('A', 'віднови сповіщення')
        await self.engine.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Muted', self.engine.command('A', 'вимкни цю тему', reply_to=1))
        self.publish((('second', 'Another grant', 0),))
        await self.start().tick()
        self.assertEqual(self.reads, 4)
        self.engine.command('A', '/alerts unmute grant')
        await self.start().tick()
        self.assertEqual(len(self.sent), 2)

    async def test_cap_and_reduced_frequency(self):
        self.watch()
        self.engine.command('A', '/alerts cap 1')
        self.publish((('grant', 'First grant', 0), ('next', 'Second grant', -10)))
        await self.engine.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn('daily cap', self.engine.command('A', '/alerts history'))
        self.engine.command('A', 'less like this', reply_to=1)
        self.now += 86400
        await self.start().tick()
        self.assertIn('requested fewer', self.engine.command('A', '/alerts history'))
        self.assertEqual(len(self.sent), 1)

    async def test_concurrent_ticks_and_transport_losing_confirmation(self):
        self.watch()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def lose_confirmation(owner, text):
            await self.deliver(owner, text)
            entered.set()
            await release.wait()
            raise ConnectionError('receipt lost')

        worker = asyncio.create_task(self.start(deliver=lose_confirmation).tick())
        await asyncio.wait_for(entered.wait(), 2)
        try:
            await self.start().tick()
            self.assertEqual(len(self.sent), 1)
        finally:
            release.set()
        await worker
        await self.start().tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn('uncertain', self.engine.command('A', '/alerts history'))
        with sqlite3.connect(self.root / 'alert.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM alert_item').fetchone()[0], 1)
