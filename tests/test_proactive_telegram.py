"""Private Telegram intake, feed recheck, durable decisions and intercepted Bot API sends."""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

from oak.sessions import SessionStore
from oak.telegram import TelegramGateway


class PublisherArchive:
    """Serve each approved publisher's RSS bytes across the executor client boundary."""

    def __init__(self, root):
        self.path = root / 'publisher.xml'
        self.calls = []

    async def call(self, operation, args):
        if operation != 'research.feed' or args != {'url': 'https://publisher.test/feed'}:
            raise AssertionError('Unexpected broker call')
        self.calls.append(operation)
        return {'xml': self.path.read_text()}

    def publish(self, key, title):
        date = datetime.now(timezone.utc).isoformat()
        self.path.write_text(
            f'<rss version="2.0"><channel><item><guid>{key}</guid><title>{title}</title>'
            f'<link>https://publisher.test/{key}</link><pubDate>{date}</pubDate></item></channel></rss>')


class ProactiveTelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.broker = PublisherArchive(self.root)
        db = sqlite3.connect(self.root / 'sessions.sqlite')
        self.addCleanup(db.close)
        # There is deliberately no agent submit() or text-generation implementation.
        self.controller = SimpleNamespace(sessions=SessionStore(db), config={'timezone': 'UTC'},
                                          tools=SimpleNamespace(executor=self.broker))
        self.requests = []
        self.update_id = 0
        self.start_gateway()
        self.open_request = urllib.request.OpenerDirector.open
        intercepted = patch('urllib.request.OpenerDirector.open', autospec=True, side_effect=self.intercept)
        intercepted.start()
        self.addCleanup(intercepted.stop)

    def start_gateway(self):
        self.gateway = TelegramGateway(self.controller, 'fixture-token', {17}, self.root)
        self.addCleanup(self.gateway.db.close)
        self.gateway.configure_proactive_alerts()

    def intercept(self, opener, request, *, timeout):
        if urllib.parse.urlsplit(request.full_url).hostname != 'api.telegram.org':
            return self.open_request(opener, request, timeout=timeout)
        self.assertEqual(request.full_url, 'https://api.telegram.org/botfixture-token/sendMessage')
        self.assertEqual(request.method, 'POST')
        self.requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({'ok': True, 'result': {'message_id': len(self.requests)}}).encode())

    async def update(self, text, *, sender=17, chat_type='private', reply_to=None, forwarded=False, topic=None):
        self.update_id += 1
        message = {'chat': {'type': chat_type, 'id': 17}, 'from': {'id': sender, 'is_bot': False},
                   'message_id': 10000 + self.update_id, 'text': text}
        if reply_to:
            message['reply_to_message'] = {'message_id': reply_to}
        if forwarded:
            message['forward_origin'] = {'type': 'user'}
        if topic:
            message['message_thread_id'] = topic
        await self.gateway._ingest({'message': message, 'update_id': self.update_id})
        for row in self.gateway._deliveries():
            # Rate limiting is incidental to these tests. Deliver using the real renderer.
            self.gateway._last_send.clear()
            await self.gateway._render_delivery(row)

    async def test_private_opt_in_tick_reply_and_restart(self):
        watch = '/alerts watch funding | https://publisher.test/feed | my accessibility project'
        self.broker.publish('grant-17', 'Funding is open')
        await self.update(watch, chat_type='group')
        await self.update(watch, sender=99)
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(self.broker.calls, [])
        self.assertEqual(self.requests, [])

        await self.update(watch, forwarded=True)
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(self.broker.calls, [])
        await self.update(watch, topic=4)
        self.assertEqual(self.requests[-1]['message_thread_id'], 4)
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(self.broker.calls, [])
        await self.update(watch)

        # Trigger the same enclosing periodic task that gateway.run() starts.
        task = asyncio.create_task(self.gateway._proactive_loop())
        try:
            async with asyncio.timeout(4):
                while not self.gateway.proactive_alerts.command('17', 'why this?', reply_to=len(self.requests)):
                    await asyncio.sleep(.01)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        alert = self.requests[-1]
        self.assertEqual(alert['chat_id'], 17)
        self.assertNotIn('message_thread_id', alert)
        self.assertIn('my accessibility project', alert['text'])
        self.assertIn('Funding is open', alert['text'])
        self.assertEqual(len(self.broker.calls), 2)
        message_id = len(self.requests)

        self.gateway.db.close()
        self.start_gateway()
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(len(self.requests), message_id)
        await self.update('why this?', reply_to=message_id)
        self.assertIn('my accessibility project', self.requests[-1]['text'])
        await self.update('mute this topic', reply_to=message_id)
        self.broker.publish('grant-18', 'Funding renewed')
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertFalse(any('https://publisher.test/grant-18' in p['text'] for p in self.requests))
        await self.update('/alerts unmute funding')
        await self.update('pause proactive alerts')
        before = len(self.broker.calls)
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(len(self.broker.calls), before)
        await self.update('resume proactive alerts')
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertIn('https://publisher.test/grant-18', self.requests[-1]['text'])

    async def test_recent_conversation_and_source_scope(self):
        # Feedback on an ordinary message still follows the normal durable intake.
        await self.update('why this?', reply_to=99, topic=4)
        await self.update('why this?', reply_to=99)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.gateway.db.execute("SELECT count(*) FROM intake WHERE status='pending'").fetchone()[0], 2)
        self.broker.publish('auction', 'School auction opened')
        await self.update('/alerts watch auction | https://publisher.test/feed | school project')
        # A real pending Telegram update is already in intake while the model is busy.
        await self.gateway._ingest({'update_id': 12, 'message': {'chat': {'type': 'private', 'id': 17},
            'from': {'id': 17}, 'text': 'We finished https://publisher.test/auction yesterday.'}})
        before = len(self.requests)
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(len(self.requests), before)
        self.update_id = 12
        await self.update('/alerts history')
        self.assertIn('recent conversation', self.requests[-1]['text'])

    @unittest.skipUnless(os.getenv('OAK_TEST_PUBLIC_FEEDS') == '1', 'requires outbound public HTTPS')
    async def test_live_usgs_feed_to_intercepted_private_telegram(self):
        from oak.executor_worker import Worker

        feed = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.atom'
        worker = Worker({'workspace': str(self.root), 'runtime_home': str(self.root)})
        self.addAsyncCleanup(worker.close)
        reads = []

        async def dispatch(operation, args):
            self.assertEqual((operation, args), ('research.feed', {'url': feed}))
            response = await worker.dispatch(operation, args)
            reads.append(response['xml'])
            return response

        self.controller.tools.executor = SimpleNamespace(call=dispatch)
        self.gateway.configure_proactive_alerts()
        interest = 'following current seismic activity for my geology project'
        await self.update(f'/alerts watch * | {feed} | {interest}')
        await self.update('/alerts cap 1')
        before = len(self.requests)
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertGreaterEqual(len(reads), 2, 'The publisher must return evidence on scan and recheck')
        self.assertEqual(len(self.requests), before + 1)
        alert, receipt = self.requests[-1], len(self.requests)
        self.assertEqual(alert['chat_id'], 17)
        self.assertIn(interest, alert['text'])
        url = next(word for word in alert['text'].split() if word.startswith('https://earthquake.usgs.gov/'))
        self.assertTrue(any(url in xml for xml in reads[1:]))
        self.assertIn(url, reads[0])
        self.gateway.db.close()
        self.start_gateway()
        await self.gateway.proactive_alerts.tick({'17'})
        self.assertEqual(len(self.requests), receipt)
        await self.update('why this?', reply_to=receipt)
        self.assertIn(url, self.requests[-1]['text'])
        self.assertIn(interest, self.requests[-1]['text'])
