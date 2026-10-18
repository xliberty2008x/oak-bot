"""Real broker HTTP and subprocess; fake native runtime, no provider traffic."""

import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from oak.bus import EventBus
from oak.cli_broker import CLIBroker
from oak.cli_process import PrivateProcess
from oak.controller import Controller
from oak.requests import RequestUnavailable
from oak.runtime import RuntimeClient
from oak.tools import Tools
from oak.web import WebGateway
from tests.test_cli_process import CANARY, CHALLENGE, VERIFY


class CLIIntegrationFixture:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = RuntimeClient()
        self.runtime.request = AsyncMock()
        self.runtime._send = AsyncMock()
        self.c = Controller(self.runtime, self.root/'state.db', self.root/'workspace', None,
            config={'state_dir': str(self.root), 'execution': {'profile': '/nonexistent/fixture.json'}})
        self.addCleanup(self.c.close)
        self.c.tools = Tools(self.c, {})
        self.runtime.tool_handler = self.c.tools.handle
        self.bus = EventBus(self.c)
        self.c.emit = self.bus.emit
        self.c.threads[7], self.c.active[7] = 'thread', 'turn'
        self.gateway = WebGateway(self.c, self.bus, 'fixture-token', [7, 8], {
            'public_url': 'https://panel.example.test', 'cli_login': {
                'socket': '/run/oak-cli-fixture/control.sock', 'origin': 'https://private.example.test'}})
        self.c.web = self.gateway
        self.bridge = self.gateway.cli_login
        script = self.root/'fixed_adapter.py'
        script.write_text('import json, sys\nprint(json.dumps({"challenge": '+repr(CHALLENGE)+'}), flush=True)\n'
            'code=input()\nassert code == '+repr(CANARY)+'\n'
            'print(code, file=sys.stderr)\nprint(json.dumps({"verified": '+repr(VERIFY)+'}), flush=True)\n')
        self.broker = CLIBroker(self.bridge.origin, self.gateway.public_url, 1001, self.root/'broker.db',
            process_factory=lambda: PrivateProcess((sys.executable, '-I', str(script)), self.root),
            boundary_check=lambda: True, peer_check=lambda _: True)
        self.human = TestClient(TestServer(self.broker.human))
        self.control = TestClient(TestServer(self.broker.control))
        await self.human.start_server()
        await self.control.start_server()
        self.calls = []
        async def ipc(route, data):
            self.calls.append((route, dict(data)))
            response = await self.control.post(route, json=data)
            if response.status != 200:
                raise RequestUnavailable('Fixture control unavailable.')
            return await response.json()
        self.bridge.ipc = AsyncMock(side_effect=ipc)
        self.panel = 'fixture-owner-panel'
        self.panel_hash = hashlib.sha256(self.panel.encode()).hexdigest()
        self.request = SimpleNamespace(headers={'Authorization': 'Bearer '+self.panel}, cookies={})
        with self.c.db:
            self.c.db.execute('INSERT INTO web_sessions VALUES (?,?,?,?)', (self.panel_hash, 7, time.time()+600, 'telegram'))
        self.tasks = []
        self.addAsyncCleanup(self.finish)

    async def finish(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.bridge.close()
        await self.human.close()
        await self.control.close()
        await self.broker.close()

    async def until(self, predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(.005)

    async def begin(self):
        task = asyncio.create_task(self.runtime._handle_server_request({'id': 42, 'method': 'item/tool/call',
            'params': {'threadId': 'thread', 'turnId': 'turn', 'tool': 'oak_cli_auth', 'arguments': {'provider': 'colab'}}}))
        self.tasks.append(task)
        await self.until(lambda: self.c.db.execute('SELECT count(*) FROM input_requests').fetchone()[0] or task.done())
        self.assertFalse(task.done())
        row = self.c.db.execute('SELECT * FROM input_requests').fetchone()
        return task, row['id']

    async def launch(self, identifier):
        return await self.bridge.start(7, 7, identifier, self.request)

    async def human_complete(self, launch):
        identifier = launch['request_id']
        headers = {'Origin': self.broker.origin}
        response = await self.human.post('/cli/api/attach', headers=headers,
            json={'request_id': identifier, 'ticket': launch['ticket']})
        self.assertEqual(response.status, 200)
        headers['Authorization'] = 'Bearer '+(await response.json())['lease']
        lease = self.broker.leases[identifier]
        await self.until(lambda: lease.process.challenge is not None)
        response = await self.human.post('/cli/api/submit', headers=headers,
            json={'request_id': identifier, 'revision': lease.revision, 'action_id': 'd'*32, 'code': CANARY})
        self.assertEqual(response.status, 200)

    def public_records(self):
        return ('\n'.join(self.c.db.iterdump()) + json.dumps(self.bus.replay(7))
            + json.dumps([call.args for call in self.runtime._send.await_args_list])
            + json.dumps(self.gateway.controls.summary(7))
            + json.dumps(self.calls))


class CLILoginTests(CLIIntegrationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_real_private_completion_resumes_exact_native_request_only_once(self):
        task, identifier = await self.begin()
        launch = await self.launch(identifier)
        await self.human_complete(launch)
        await asyncio.wait_for(asyncio.shield(task), 5)
        self.runtime._send.assert_awaited_once()
        result = self.runtime._send.await_args.args[0]
        self.assertEqual(result['id'], 42)
        value = json.loads(result['result']['contentItems'][0]['text'])
        self.assertEqual(value['verification'], VERIFY)
        self.assertTrue(value['authenticated'])
        self.assertEqual(self.c.requests.row(7,7,identifier)['delivery'], 'sent')
        for forbidden in (CANARY, CHALLENGE, launch['ticket'], self.panel):
            self.assertNotIn(forbidden, self.public_records())
        self.c.requests.invalidate_turn('thread', 'turn')
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')
        self.assertIsNone(self.broker.leases[identifier].process)

    async def test_ordinary_request_endpoint_cannot_receive_a_cli_code(self):
        _, identifier = await self.begin()
        with self.assertRaises(ValueError):
            await self.c.requests.decide(7,7,identifier, {'decision':'submit', 'revision':1,
                'requestId':'wrong-channel', 'values':{'code':CANARY}})
        self.assertNotIn(CANARY, self.public_records())
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM input_request_receipts').fetchone()[0], 0)

    async def test_other_owner_scope_panel_or_turn_cannot_launch(self):
        _, identifier = await self.begin()
        for owner, chat in ((8,7),(7,8)):
            with self.assertRaises(RequestUnavailable):
                await self.bridge.start(owner,chat,identifier,self.request)
        with self.assertRaises(RequestUnavailable):
            await self.bridge.start(7,7,identifier,SimpleNamespace(headers={},cookies={}))
        self.c.active[7] = 'new-turn'
        with self.assertRaises(RequestUnavailable):
            await self.launch(identifier)
        self.assertFalse(self.broker.leases)

    async def test_unknown_create_is_not_replayed(self):
        _, identifier = await self.begin()
        self.bridge.ipc = AsyncMock(side_effect=RequestUnavailable('Unknown dispatch.'))
        for _ in range(2):
            with self.assertRaises(RequestUnavailable):
                await self.launch(identifier)
        self.bridge.ipc.assert_awaited_once()
        self.assertEqual(self.bridge.attempt(identifier)['phase'], 'uncertain')

    async def test_unconfigured_capability_returns_unsupported_without_input_request(self):
        self.c.requests.cli_login = None
        value = await self.c.requests.cli_auth({})
        self.assertEqual(value['outcome'], 'unsupported')
        self.assertFalse(value['native_session_attach'])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM input_requests').fetchone()[0], 0)

    async def test_panel_logout_cancels_broker_process(self):
        _, identifier = await self.begin()
        await self.launch(identifier)
        with self.c.db:
            self.c.db.execute('DELETE FROM web_sessions')
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')
        self.assertEqual(self.broker.leases[identifier].state, 'cancelled')

    async def test_authority_checks_original_owner_scope_thread_turn_and_both_epochs(self):
        _, identifier = await self.begin()
        await self.launch(identifier)
        row, attempt = dict(self.c.requests.row(7,7,identifier)), dict(self.bridge.attempt(identifier))
        self.assertTrue(self.bridge.live(row, attempt))
        for key, value in (('owner',8),('chat_id',8),('thread_id','new-thread'),
                           ('turn_id','new-turn'),('runtime_epoch','old-epoch')):
            with self.subTest(field=key):
                self.assertFalse(self.bridge.live({**row,key:value}, attempt))
        self.runtime.runtime_epoch = 'replacement-runtime'
        self.assertFalse(self.bridge.live(row, attempt))
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')

    async def test_turn_ending_during_status_read_never_queries_private_verification(self):
        task, identifier = await self.begin()
        launch = await self.launch(identifier)
        original = self.bridge.ipc.side_effect
        async def changed(route, data):
            value = await original(route, data)
            if route == '/status' and value.get('state') == 'authenticated':
                self.c.active.pop(7, None)
            return value
        self.bridge.ipc.side_effect = changed
        await self.human_complete(launch)
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')
        self.assertFalse(any(route == '/use' for route, _ in self.calls))
        self.assertFalse(task.done())
        self.c.requests.invalidate_turn('thread', 'turn')
