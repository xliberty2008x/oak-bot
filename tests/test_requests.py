import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from oak.bus import EventBus
from oak.controller import Controller
from oak.requests import InputRequests, RequestUnavailable
from oak.runtime import RuntimeClient
from oak.telegram import TelegramGateway
from oak.tools import Tools
from oak.web import WebGateway


class InputRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.runtime = RuntimeClient()
        self.runtime.request = AsyncMock()
        self.runtime._send = AsyncMock()
        self.c = Controller(self.runtime, self.root / 'state.sqlite', self.root, None,
                            config={'state_dir': str(self.root)})
        self.addCleanup(self.c.close)
        self.c.tools = Tools(self.c, {})
        self.runtime.tool_handler = self.c.tools.handle
        self.runtime.request_input_handler = self.c.interactions.user_input
        self.bus = EventBus(self.c)
        self.c.emit = self.bus.emit
        self.c.threads[11] = 'original-thread'
        self.c.active[11] = 'original-turn'
        self.native = {'threadId': 'original-thread', 'turnId': 'original-turn', 'requestId': 42,
                       'runtimeEpoch': self.runtime.runtime_epoch}
        self.tasks = []
        self.addAsyncCleanup(self.finish_tasks)

    async def finish_tasks(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def begin(self, template='plan_details', metadata=None):
        task = asyncio.create_task(self.c.requests.template(metadata or self.native, template))
        self.tasks.append(task)
        await asyncio.sleep(0)
        self.assertFalse(task.done(), str(task.exception()) if task.done() else '')
        identifier = self.c.db.execute('SELECT id FROM input_requests ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        return task, identifier

    def payload(self, action='one', decision='submit', values=None):
        return {'requestId': action, 'revision': 1, 'decision': decision,
                **({'values': values or {'topic': 'Мій тиждень', 'pace': 'Спокійно'}} if decision == 'submit' else {})}

    async def test_original_native_tool_waits_and_receives_one_response_without_new_turn(self):
        metadata = {'id': 42, 'method': 'item/tool/call', 'params': {
            'threadId': 'original-thread', 'turnId': 'original-turn', 'tool': 'oak_request_input',
            'callId': 'call', 'arguments': {'template': 'plan_details'}}}
        task = asyncio.create_task(self.runtime._handle_server_request(metadata))
        self.tasks.append(task)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        identifier = next(iter(self.c.requests.waiters))
        self.runtime._send.assert_not_awaited()
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['outcome'], 'pending')
        result = await self.c.requests.decide(11, 11, identifier, self.payload())
        self.assertEqual(result['outcome'], 'submitted')
        await task
        self.runtime._send.assert_awaited_once()
        sent = self.runtime._send.await_args.args[0]
        self.assertEqual(sent['id'], 42)
        response = json.loads(sent['result']['contentItems'][0]['text'])
        self.assertEqual(response['values']['topic'], 'Мій тиждень')
        self.assertEqual(self.c.active[11], 'original-turn')
        self.runtime.request.assert_not_awaited()
        repeated = await self.c.requests.decide(11, 11, identifier, self.payload())
        self.assertEqual(repeated, {'outcome': 'submitted', 'delivery': 'sent'})
        self.runtime._send.assert_awaited_once()
        with self.assertRaises(RequestUnavailable):
            await self.c.requests.decide(11, 11, identifier, self.payload(values={'topic': 'Інше', 'pace': 'Спокійно'}))
        event = self.bus.replay(11)[0]['event']
        self.assertEqual(set(event['value']), {'id', 'kind', 'summary'})
        self.assertNotIn('Мій тиждень', json.dumps(event, ensure_ascii=False))

    async def test_native_questions_and_mcp_only_accept_validated_choices(self):
        metadata = {**self.native, 'method': 'item/tool/requestUserInput', 'questions': [
            {'id': 'pace', 'question': 'Який темп?', 'options': [{'label': 'Спокійно'}, {'label': 'Швидко'}]}]}
        task = asyncio.create_task(self.c.interactions.user_input(metadata)); self.tasks.append(task)
        await asyncio.sleep(0)
        identifier = next(iter(self.c.requests.waiters))
        await self.c.requests.decide(11, 11, identifier, self.payload(values={'pace': 'Спокійно'}))
        self.assertEqual(await task, {'answers': {'pace': {'answers': ['Спокійно']}}})
        metadata = {**self.native, 'requestId': 43, 'method': 'mcpServer/elicitation/request', 'mode': 'form',
                    'requestedSchema': {'type': 'object', 'additionalProperties': False,
                                        'properties': {'pace': {'type': 'string', 'enum': ['slow', 'fast']}}, 'required': ['pace']}}
        task = asyncio.create_task(self.c.interactions.user_input(metadata)); self.tasks.append(task)
        await asyncio.sleep(0)
        identifier = next(iter(self.c.requests.waiters))
        with self.assertRaises(ValueError):
            await self.c.requests.decide(11, 11, identifier, self.payload(values={'pace': 'not offered'}))
        await self.c.requests.decide(11, 11, identifier, self.payload(values={'pace': 'slow'}))
        self.assertEqual(await task, {'action': 'accept', 'content': {'pace': 'slow'}})

    async def test_cancel_empty_fields_and_submit_cancel_race_have_one_winner(self):
        task, identifier = await self.begin()
        results = await asyncio.gather(self.c.requests.decide(11, 11, identifier, self.payload('cancel', 'cancel')),
                                       self.c.requests.decide(11, 11, identifier, self.payload('submit')), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertEqual((await task)['outcome'], 'cancelled')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM input_request_receipts').fetchone()[0], 1)

    async def test_expiry_is_absolute_and_late_submit_does_not_resume_again(self):
        task, identifier = await self.begin()
        expiry = self.c.requests.snapshot(11, 11, identifier)['expires']
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['expires'], expiry)
        with self.c.db:
            self.c.db.execute('UPDATE input_requests SET expires=? WHERE id=?', (time.time() - 1, identifier))
        with self.assertRaises(RequestUnavailable):
            await self.c.requests.decide(11, 11, identifier, self.payload())
        self.assertEqual((await task)['outcome'], 'expired')
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['outcome'], 'expired')
        self.runtime.request.assert_not_awaited()

    async def test_scope_epoch_turn_and_original_native_id_are_bound(self):
        task, identifier = await self.begin()
        for owner, chat in [(22, 11), (11, 22)]:
            with self.assertRaises(RequestUnavailable):
                await self.c.requests.decide(owner, chat, identifier, self.payload())
        with self.assertRaises(RequestUnavailable):
            await self.c.requests.template({**self.native, 'runtimeEpoch': 'previous-runtime'}, 'task_details')
        with self.assertRaises(RequestUnavailable):
            await self.c.requests.template(self.native, 'task_details')
        self.c.active[11] = 'new-turn'
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['outcome'], 'cancelled')
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_receipt_and_consumption_rollback_together(self):
        task, identifier = await self.begin()
        self.c.db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON input_request_receipts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            await self.c.requests.decide(11, 11, identifier, self.payload())
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['outcome'], 'pending')
        self.assertFalse(task.done())
        self.c.db.execute('DROP TRIGGER fail_receipt')
        await self.c.requests.decide(11, 11, identifier, self.payload())
        self.assertEqual((await task)['outcome'], 'submitted')

    async def test_failed_runtime_send_is_uncertain_and_restart_does_not_replay(self):
        self.runtime._send.side_effect = ConnectionError('synthetic disconnected transport')
        metadata = {'id': 42, 'method': 'item/tool/call', 'params': {
            'threadId': 'original-thread', 'turnId': 'original-turn', 'tool': 'oak_request_input',
            'arguments': {'template': 'task_details'}}}
        task = asyncio.create_task(self.runtime._handle_server_request(metadata)); self.tasks.append(task)
        await asyncio.sleep(0); await asyncio.sleep(0)
        identifier = next(iter(self.c.requests.waiters))
        await self.c.requests.decide(11, 11, identifier, self.payload(values={'details': 'Звичайне уточнення'}))
        with self.assertRaises(ConnectionError):
            await task
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['delivery'], 'uncertain')
        self.c.requests = InputRequests(self.c)
        replay = await self.c.requests.decide(11, 11, identifier, self.payload(values={'details': 'Звичайне уточнення'}))
        self.assertEqual(replay['delivery'], 'uncertain')
        self.runtime._send.assert_awaited_once()
        self.runtime.request.assert_not_awaited()
        gateway = WebGateway(self.c, self.bus, 'synthetic-token', [11, 22], {})
        attention = gateway.controls.summary(11)['session']['input_request_attention']
        self.assertEqual(attention, [{'id': identifier, 'outcome': 'submitted', 'delivery': 'uncertain'}])
        self.assertEqual(gateway.controls.summary(11)['session']['uncertain_inputs'], 1)
        self.assertEqual(gateway.controls.summary(22)['session']['input_request_attention'], [])
        self.c.threads[11] = 'new-thread'
        self.assertEqual(gateway.controls.summary(11)['session']['input_request_attention'], attention)

    async def test_native_transport_replay_after_uncertain_send_is_not_dispatched_again(self):
        reader = asyncio.StreamReader()
        self.runtime.process = SimpleNamespace(stdout=reader)
        self.runtime.request_input_handler = AsyncMock(return_value={'answers': {}})
        self.runtime._send.side_effect = ConnectionError('synthetic uncertain send')
        loop = asyncio.create_task(self.runtime._read_loop()); self.tasks.append(loop)
        encoded = json.dumps({'id': 42, 'method': 'item/tool/requestUserInput', 'params': {}}).encode() + b'\n'
        reader.feed_data(encoded)
        for _ in range(30):
            await asyncio.sleep(0)
        self.assertFalse(self.runtime._server_requests)
        reader.feed_data(encoded)
        for _ in range(30):
            await asyncio.sleep(0)
        self.runtime.request_input_handler.assert_awaited_once()
        self.runtime._send.assert_awaited_once()
        reader.feed_eof(); await loop
        self.runtime.process = None

    async def test_buffered_native_request_waits_for_turn_start_registration(self):
        self.c.active.pop(11)
        async with self.c._lock(11):
            task = asyncio.create_task(self.c.requests.template(self.native, 'task_details'))
            self.tasks.append(task)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertFalse(self.c.requests.waiters)
            self.c.active[11] = 'original-turn'
        await asyncio.sleep(0)
        identifier = next(iter(self.c.requests.waiters))
        await self.c.requests.decide(11, 11, identifier, self.payload('cancel', 'cancel'))
        self.assertEqual((await task)['outcome'], 'cancelled')

    async def test_restart_expires_pending_and_marks_ready_sent_uncertain(self):
        task, identifier = await self.begin()
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
        # Represent abrupt process exit before cleanup ran, using only synthetic state.
        with self.c.db:
            self.c.db.execute("UPDATE input_requests SET outcome='pending',delivery='waiting' WHERE id=?", (identifier,))
        self.c.requests = InputRequests(self.c)
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['outcome'], 'expired')
        for delivery in ('ready', 'sent'):
            with self.c.db:
                self.c.db.execute("UPDATE input_requests SET outcome='submitted',delivery=? WHERE id=?", (delivery, identifier))
            self.c.requests = InputRequests(self.c)
            self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['delivery'], 'uncertain')
        with self.assertRaises(RequestUnavailable):
            await self.c.requests.decide(11, 11, identifier, self.payload())

    async def test_restart_does_not_reopen_transport_attention_for_completed_turn(self):
        task, identifier = await self.begin()
        await self.c.requests.decide(11, 11, identifier, self.payload())
        await task
        self.c.requests.delivered(self.runtime.runtime_epoch, 42, 'sent')
        with self.c.db:
            self.c.db.execute('INSERT INTO turns(chat_id,thread_id,turn_id,status) VALUES (?,?,?,?)',
                              (11, 'original-thread', 'original-turn', 'completed'))
        self.c.requests = InputRequests(self.c)
        self.assertEqual(self.c.requests.snapshot(11, 11, identifier)['delivery'], 'sent')
        gateway = WebGateway(self.c, self.bus, 'synthetic-token', [11], {})
        self.assertEqual(gateway.controls.summary(11)['session']['input_request_attention'], [])

    async def test_instagram_never_accepts_credentials_or_user_claimed_success(self):
        task, identifier = await self.begin('instagram_sign_in')
        snapshot = self.c.requests.snapshot(11, 11, identifier)
        self.assertFalse(snapshot['form']['capability'])
        self.assertNotIn('fields', snapshot['form'])
        canary = 'synthetic-credential-canary'
        for payload in [self.payload(values={'password': canary}),
                        {**self.payload('cancel', 'cancel'), 'authenticated': True},
                        {**self.payload('cancel', 'cancel'), 'cookies': canary}]:
            with self.assertRaises(ValueError):
                await self.c.requests.decide(11, 11, identifier, payload)
        await self.c.requests.decide(11, 11, identifier, self.payload('cancel', 'cancel'))
        response = await task
        self.assertEqual(response['reason'], 'trusted_channel_unavailable')
        self.assertFalse(response['authenticated'])
        self.assertNotIn(canary, '\n'.join(self.c.db.iterdump()))
        self.assertNotIn(canary, json.dumps(self.bus.replay(11)))
        self.runtime.request.assert_not_awaited()

    async def test_auth_elicitation_urls_and_arbitrary_free_text_fail_closed_before_storage(self):
        canary = 'synthetic-secret-canary'
        candidates = [
            {**self.native, 'method': 'mcpServer/elicitation/request', 'mode': 'url', 'url': 'https://example.test/?token=' + canary},
            {**self.native, 'method': 'mcpServer/elicitation/request', 'mode': 'form', 'requestedSchema': {'type': 'object', 'properties': {'password': {'type': 'string'}}}},
            {**self.native, 'method': 'item/tool/requestUserInput', 'questions': [{'id': 'q', 'question': 'Password ' + canary, 'isSecret': True}]},
            {**self.native, 'method': 'item/tool/requestUserInput', 'questions': [{'id': 'q', 'question': 'Enter a password', 'options': [{'label': 'Yes'}]}]},
            {**self.native, 'method': 'item/tool/requestUserInput', 'questions': [{'id': 'q', 'question': 'Free text without an approved template'}]},
        ]
        for metadata in candidates:
            result = await self.c.interactions.user_input(metadata)
            self.assertIn(result, [{'answers': {}}, {'action': 'decline', 'content': None}])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM input_requests').fetchone()[0], 0)
        self.assertNotIn(canary, '\n'.join(self.c.db.iterdump()))
        self.assertEqual(self.bus.replay(11), [])

    async def test_http_owner_origin_conversation_and_exact_telegram_launch(self):
        gateway = WebGateway(self.c, self.bus, 'synthetic-token', [11, 22], {'public_url': 'https://oak.example.test'})
        self.c.web = gateway
        telegram = TelegramGateway(self.c, 'synthetic-token', {11, 22}, self.root / 'telegram')
        self.addCleanup(telegram.db.close)
        self.bus.add_sink(telegram.emit)
        client = TestClient(TestServer(gateway.app)); self.addAsyncCleanup(client.close); await client.start_server()

        async def login(owner):
            response = await client.post('/api/session', json={'key': gateway.keys[str(owner)]})
            token = (await response.json())['access_token']; client.session.cookie_jar.clear()
            return {'Authorization': 'Bearer ' + token}

        one, two = await login(11), await login(22)
        task, identifier = await self.begin()
        delivery = json.loads(telegram.db.execute("SELECT payload FROM deliveries WHERE id=?", (f'11:input_request:{identifier}',)).fetchone()[0])
        url = delivery['reply_markup']['inline_keyboard'][0][0]['web_app']['url']
        self.assertEqual(url, 'https://oak.example.test?request=' + identifier + '&conversation=telegram')
        response = await client.get('/api/requests/' + identifier, headers=one)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['conversation'], 'telegram')
        self.assertEqual((await client.get('/api/requests/' + identifier, headers=two)).status, 404)
        self.assertEqual((await client.get('/api/requests/' + identifier)).status, 401)
        self.assertEqual((await client.post('/api/requests/' + identifier, headers={**one, 'Origin': 'https://attacker.test'}, json=self.payload())).status, 403)
        topic = self.c.sessions.resolve(11, 12)
        self.assertNotEqual(topic, 11)
        self.assertEqual((await client.post('/api/requests/' + identifier + '?conversation=topic:12', headers=one, json=self.payload())).status, 404)
        response = await client.post('/api/requests/' + identifier + '?conversation=telegram', headers=one, json=self.payload('cancel', 'cancel'))
        self.assertEqual(response.status, 200)
        self.assertEqual((await task)['outcome'], 'cancelled')
