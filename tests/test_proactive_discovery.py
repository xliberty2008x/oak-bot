"""Automatic discovery through real publisher documents and a restartable owner database."""
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

from oak.proactive import ProactiveAlerts
from oak.proactive_context import DiscoveryTopic, valid_draft
from oak.proactive_sources import FeedSource


class DiscoveryTests(IsolatedAsyncioTestCase):
    source = 'https://museum.example/rss'
    catalogue = (
        DiscoveryTopic('Printmaking', source, ('engraving', 'гравюр*'), ('etching', 'woodcut')),
        DiscoveryTopic('Astronomy', 'https://science.example/rss', ('telescope',), ('meteor',)),
    )

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.now = datetime(2026, 11, 20, 13, tzinfo=timezone.utc).timestamp()
        self.context = {'1': ['Хочу спробувати зробити гравюру.'],
                        '2': ['I finished the engraving project.'], '3': ['I work with telescopes.']}
        self.reads = []
        self.messages = []
        self.publish()

    def publish(self, count=1, *, detail='Try the etching activity at the museum.'):
        stamp = format_datetime(datetime.fromtimestamp(self.now - 1800, timezone.utc))
        entries = ''.join(f'<item><title>Studio session {i}</title><guid>{i}</guid>'
                          f'<link>https://museum.example/studio/{i}</link><pubDate>{stamp}</pubDate>'
                          f'<description>{detail}</description></item>' for i in range(count))
        (self.directory / 'publisher.xml').write_text(f'<rss><channel>{entries}</channel></rss>')

    async def read_feed(self, url):
        self.reads.append(url)
        self.assertEqual(url, self.source)
        return (self.directory / 'publisher.xml').read_bytes()

    async def send(self, owner, text):
        self.messages.append((owner, text))
        return len(self.messages)

    def host(self, *, compose=None, owners=('1', '2')):
        alerts = ProactiveAlerts(self.directory / 'db', FeedSource(self.read_feed), self.send,
                                 context=self.context.get, catalogue=self.catalogue, compose=compose,
                                 clock=lambda: self.now)
        for owner in owners:
            alerts.configure_discovery(owner)
        return alerts

    async def test_unsolicited_cross_language_context_and_summary(self):
        alerts = self.host()
        await alerts.tick(owners={'2', '3'})
        self.assertEqual(self.reads, [])
        await alerts.tick(owners={'1'})
        self.assertEqual(self.reads, [self.source, self.source])
        self.assertEqual(len(self.messages), 1)
        owner, text = self.messages[0]
        self.assertEqual(owner, '1')
        self.assertIn('Studio session', text)
        self.assertIn('https://museum.example/studio/0', text)
        self.assertNotIn('/alerts', text)
        self.assertNotIn('гравюру', text)
        why = alerts.command('1', 'чому це?', reply_to=1)
        self.assertIn('Printmaking', why)
        self.assertIn('перевірена публікація', why)
        self.assertIn('https://museum.example/studio/0', why)
        self.assertIsNone(alerts.command('2', 'стоп', reply_to=1))
        self.assertNotIn('гравюру'.encode(), (self.directory / 'db').read_bytes())

        await self.host().tick()
        self.assertEqual(len(self.messages), 1)
        self.publish(2)
        await self.host().tick()
        self.assertEqual(len(self.messages), 1)
        self.assertIn('daily cap', alerts.command('1', '/alerts history'))

    async def test_context_for_conversational_followup_survives_restart(self):
        await self.host().tick()
        _, sent = self.messages[0]
        restarted = self.host()
        self.assertEqual(sent, restarted.conversation_context('1'))
        self.assertEqual(sent, restarted.conversation_context(1, reply_to=1))
        self.assertIsNone(restarted.conversation_context('1', reply_to=999))
        self.assertIsNone(restarted.conversation_context('2', reply_to=1))
        self.now += 86401
        self.assertIsNone(restarted.conversation_context('1'))
        self.assertEqual(sent, restarted.conversation_context('1', reply_to=1))

    async def test_project_request_uses_discovery_without_watch_setup(self):
        alerts = self.host()
        reply = alerts.command("1", "Keep an eye on useful opportunities for my projects.")
        self.assertIn("current projects", reply)
        self.assertNotIn("/alerts watch", reply)
        self.assertNotIn("RSS", reply)
        alerts.command("1", "/alerts mute Printmaking")
        alerts.command("1", "/alerts cap 3")
        alerts.command("1", "/alerts quiet 21:00-07:00 UTC")
        alerts.command("1", "/alerts disable discovery")
        alerts.command("1", "/alerts pause")
        reply = self.host().command("1", "Увімкни проактивні сповіщення")
        self.assertIn("проєктів", reply)
        status = alerts.command("1", "/alerts")
        self.assertIn("Alerts enabled", status)
        self.assertIn("Contextual discovery is on", status)
        self.assertIn("Daily cap 3", status)
        self.assertIn("21:00–07:00", status)
        self.assertIn("Muted discovery topics: Printmaking", status)
        await alerts.tick()
        self.assertFalse(self.reads)
        alerts.command("1", "/alerts unmute Printmaking")
        await alerts.tick()
        self.assertEqual(len(self.messages), 1)

    async def test_owner_opt_out_pause_topic_controls_and_host_revocation(self):
        alerts = self.host()
        alerts.command('1', '/alerts mute Printmaking')  # before any finding
        await self.host().tick()
        self.assertFalse(self.reads)
        alerts.command('1', '/alerts unmute Printmaking')
        await alerts.tick()
        self.assertEqual(len(self.messages), 1)
        self.assertIsNone(alerts.command('2', 'stop sending these', reply_to=1))
        self.assertIn('раз на три дні', alerts.command('1', 'менше такого', reply_to=1))
        self.assertIn('«Printmaking»', alerts.command('1', 'вимкни цю тему', reply_to=1))
        self.assertIn('опрацьовану', alerts.command('1', 'вирішено', reply_to=1))
        self.assertIn('призупиняю', alerts.command('1', 'не пиши мені першою', reply_to=1))
        before = len(self.reads)
        await self.host().tick()
        self.assertEqual(len(self.reads), before)
        alerts.command('1', 'resume proactive alerts')
        alerts.command('1', '/alerts disable discovery')
        await self.host().tick()
        self.assertEqual(len(self.reads), before)
        alerts.command('1', '/alerts enable discovery')
        alerts.configure_discovery('1', enabled=False)
        await alerts.tick()
        await self.host(owners=()).tick()
        self.assertEqual(len(self.reads), before)

    async def test_relevance_change_or_conflicting_source_after_scan(self):
        async def change_publication(url):
            if self.reads:
                self.publish(detail='The etching activity is cancelled.')
            return await self.read_feed(url)

        alerts = self.host()
        alerts.source = FeedSource(change_publication)
        await alerts.tick()
        self.assertFalse(self.messages)
        self.assertIn('did not confirm', alerts.command('1', '/alerts history'))

        async def change_interest(url):
            before = len(self.reads)
            data = await self.read_feed(url)
            if before == 3:
                self.context['1'] = ['Мене більше не цікавить гравюра.']
            return data

        alerts.source = FeedSource(change_interest)
        await alerts.tick()
        self.assertFalse(self.messages)
        self.assertIn('context changed', alerts.command('1', '/alerts history'))

    async def test_abstention_budget_and_persistence(self):
        self.publish(5)
        calls = []

        async def abstain(owner, matched_context, topic, candidate):
            calls.append(candidate.key)
            self.assertEqual(matched_context, self.context[owner][0])
            self.assertEqual(topic.topic, 'Printmaking')
            return None

        await self.host(compose=abstain).tick()
        self.assertEqual(len(calls), 3)
        await self.host(compose=abstain).tick()
        self.assertEqual(len(calls), 5)
        await self.host(compose=abstain).tick()
        self.assertEqual(len(set(calls)), len(calls))
        self.assertEqual(len(calls), 5)
        self.context['1'] = ['I just started engraving lessons.']
        await self.host(compose=abstain).tick()
        self.assertEqual(len(calls), 8)
        self.assertFalse(self.messages)

    async def test_quiet_hours_and_gated_draft(self):
        self.now += 10 * 3600
        alerts = self.host()
        await alerts.tick()
        self.assertFalse(self.messages)
        self.assertIn('quiet hours', alerts.command('1', '/alerts history'))
        self.assertFalse(valid_draft('Try https://other.example/lookup'))
        self.assertFalse(valid_draft('Try other.example/lookup'))
