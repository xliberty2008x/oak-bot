import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from oak.bus import EventBus
from oak.controller import Controller
from oak.tools import Tools
from oak.web import WebGateway, telegram_owner


class WebTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.client = AsyncMock()
        self.controller = Controller(self.client, ':memory:', self.root, AsyncMock(),
                                     config={'state_dir': str(self.root)})
        self.addCleanup(self.controller.db.close)
        self.controller.tools = Tools(self.controller, {})
        self.bus = EventBus(self.controller)

    def test_telegram_auth_signature_freshness_duplicates_and_owner(self):
        token, now = '123:synthetic-test-token', 1800000000

        def signed(owner=11, date=now):
            fields = {'auth_date': str(date), 'user': json.dumps({'id': owner}),
                      'query_id': 'synthetic-query'}
            secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
            payload = '\n'.join(k + '=' + v for k, v in sorted(fields.items()))
            fields['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
            return urlencode(fields)

        valid = signed()
        self.assertEqual(telegram_owner(valid, token, {11}, now), 11)
        for value, key in ((signed(22), token), (signed(date=now - 3601), token),
                           (signed(date=now + 61), token), (valid + '&auth_date=1', token),
                           (valid, 'wrong-token'), (signed(owner=True), token)):
            with self.subTest(value=value[-20:], key=key):
                with self.assertRaises(ValueError):
                    telegram_owner(value, key, {11}, now)

    async def test_bus_scopes_live_and_durable_events_without_replacing_sinks(self):
        one, two = AsyncMock(), AsyncMock()
        self.bus.add_sink(one)
        self.bus.add_sink(two)
        async with self.bus.subscribe(11, 'a') as a, self.bus.subscribe(11, 'b') as b, \
                self.bus.subscribe(22, 'a') as other, self.bus.subscribe(11) as all_runs:
            for chat, run in ((11, 'a'), (22, 'a'), (11, 'b')):
                await self.bus.emit(chat, {'type': 'RUN_STARTED', 'runId': run})
            self.assertEqual((a.qsize(), b.qsize(), other.qsize(), all_runs.qsize()), (1, 1, 1, 2))
            first = a.get_nowait()
            self.assertEqual(first['event']['runId'], 'a')
            self.assertEqual([r['event']['runId'] for r in self.bus.replay(11)], ['a', 'b'])
            self.assertEqual([r['event']['runId'] for r in self.bus.replay(11, first['sequence'])], ['b'])
            self.assertEqual(len(self.bus.replay(22, run_id='a')), 1)
            self.assertEqual(self.bus.replay(22, run_id='b'), [])
            self.assertEqual(one.await_args_list, two.await_args_list)
            self.assertEqual(one.await_count, 3)
        self.assertFalse(self.bus.subscribers)
        self.client.request.assert_not_awaited()

    async def test_web_sessions_conversations_events_and_artifacts_are_owner_scoped(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)

        async def login(owner):
            response = await client.post('/api/session', json={'key': gateway.keys[str(owner)]})
            self.assertEqual(response.status, 200)
            cookie = response.cookies['oak_session'].value
            client.session.cookie_jar.clear()
            return {'Cookie': 'oak_session=' + cookie}

        first, second = await login(11), await login(22)
        response = await client.get('/api/bootstrap')
        self.assertEqual(response.status, 401)
        bearer = {'Authorization': 'Bearer ' + first['Cookie'].split('=', 1)[1]}
        response = await client.get('/api/bootstrap', headers=bearer)
        self.assertEqual(response.status, 200)
        response = await client.post('/api/conversations', headers=first, json={'title': 'First owner'})
        conversation = await response.json()
        chat = self.controller.db.execute('SELECT chat_id FROM web_conversations WHERE id=?',
                                          (conversation['id'],)).fetchone()[0]
        await self.bus.emit(chat, {'type': 'RUN_STARTED', 'runId': 'web-run'})
        await self.bus.emit(11, {'type': 'RUN_STARTED', 'runId': 'telegram-first'})
        await self.bus.emit(22, {'type': 'RUN_STARTED', 'runId': 'telegram-second'})
        response = await client.get('/api/events?conversation=' + conversation['id'], headers=first)
        records = await response.json()
        self.assertEqual([r['event']['runId'] for r in records], ['web-run'])
        response = await client.get('/api/events?conversation=' + conversation['id'], headers=second)
        self.assertEqual(response.status, 404)
        response = await client.get('/api/events', headers=second)
        self.assertEqual([r['event']['runId'] for r in await response.json()], ['telegram-second'])
        response = await client.post('/api/input', headers=second,
                                     json={'conversation': conversation['id'], 'text': 'denied', 'requestId': 'one'})
        self.assertEqual(response.status, 404)

        artifact = self.root / 'report.txt'
        artifact.write_text('Synthetic report')
        await self.bus.emit(chat, {'type': 'CUSTOM', 'name': 'artifact', 'value': {'path': str(artifact)}})
        value = self.bus.replay(chat)[-1]['event']['value']
        self.assertNotIn('path', value)
        response = await client.get(value['url'], headers=second)
        self.assertEqual(response.status, 404)
        response = await client.get(value['url'], headers=first)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.text(), 'Synthetic report')
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
        response = await client.get(value['url'], headers=bearer)
        self.assertEqual(response.status, 200)
        self.client.request.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
