import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
import unittest

from aiohttp.test_utils import TestClient, TestServer

from oak.cli_broker import CLIBroker
from oak.cli_process import PrivateProcess
from tests.test_cli_process import CANARY, CHALLENGE, VERIFY


class CLIBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        script = self.root/'fixed_adapter.py'
        script.write_text('import json, sys\nprint(json.dumps({"challenge": '+repr(CHALLENGE)+'}), flush=True)\n'
            'code=input()\nassert code == '+repr(CANARY)+'\n'
            'print(code, file=sys.stderr)\nprint(json.dumps({"verified": '+repr(VERIFY)+'}), flush=True)\n')
        self.boundary = self.peer = True
        self.created_processes = []
        def factory():
            p = PrivateProcess((sys.executable, '-I', str(script)), self.root)
            self.created_processes.append(p)
            return p
        self.broker = CLIBroker('https://private.example.test', 'https://panel.example.test', 1001,
            self.root/'receipts.db', process_factory=factory,
            boundary_check=lambda: self.boundary, peer_check=lambda request: self.peer)
        self.human = TestClient(TestServer(self.broker.human))
        self.control = TestClient(TestServer(self.broker.control))
        await self.human.start_server()
        await self.control.start_server()
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.broker.close)
        self.binding = {'request_id': 'a'*32, 'owner': 7, 'chat_id': 42, 'thread_id': 'thread',
            'turn_id': 'turn', 'runtime_epoch': 'epoch', 'panel_hash': 'b'*64,
            'expires': time.time()+240, 'provider': 'colab'}
        self.headers = {'Origin': self.broker.origin}

    async def create(self):
        response = await self.control.post('/create', json=self.binding)
        self.assertEqual(response.status, 200)
        self.created = await response.json()
        self.scope = {key: self.created[key] for key in ('request_id', 'session_id', 'runtime_epoch')}
        self.lease = self.broker.leases[self.binding['request_id']]
        return self.created

    async def attach(self):
        if not hasattr(self, 'created'):
            await self.create()
        response = await self.human.post('/cli/api/attach', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'ticket': self.created['ticket']})
        self.assertEqual(response.status, 200)
        self.headers['Authorization'] = 'Bearer ' + (await response.json())['lease']
        async with asyncio.timeout(3):
            while self.lease.process.challenge is None:
                await asyncio.sleep(.005)

    async def submit(self, **changes):
        data = {'request_id': self.binding['request_id'], 'revision': self.lease.revision,
                'action_id': 'c'*32, 'code': CANARY, **changes}
        return await self.human.post('/cli/api/submit', headers=self.headers, json=data)

    async def test_real_process_private_input_and_status_only_control_and_receipts(self):
        records = []
        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        handler = Capture()
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        await self.attach()
        form = await self.human.post('/cli/api/form', headers=self.headers, json={'request_id': self.binding['request_id']})
        self.assertEqual((await form.json())['authorization_url'], CHALLENGE)
        self.assertEqual((await self.submit()).status, 200)
        await self.lease.process.task
        response = await self.control.post('/status', json=self.scope)
        state = await response.json()
        self.assertEqual(state['state'], 'authenticated')
        self.assertEqual(set(state), {'request_id','session_id','runtime_epoch','provider','state','cleanup','session_expires'})
        verified = await self.control.post('/use', json=self.scope)
        self.assertEqual(await verified.json(), {'verification': VERIFY})
        recorded = json.dumps(state) + '\n'.join(self.broker.db.iterdump()) + '\n'.join(records)
        for secret in (CANARY, CHALLENGE, self.created['ticket'], self.headers['Authorization']):
            self.assertNotIn(secret, recorded)

    async def test_ticket_and_submit_consumption_never_relaunch_or_rewrite(self):
        await self.attach()
        self.assertEqual((await self.control.post('/create', json=self.binding)).status, 409)
        self.assertEqual((await self.human.post('/cli/api/attach', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'ticket': self.created['ticket']})).status, 409)
        self.assertEqual((await self.submit()).status, 200)
        self.assertEqual((await self.submit()).status, 200)
        self.assertEqual((await self.submit(action_id='d'*32)).status, 409)
        self.assertEqual((await self.submit(code='different')).status, 409)
        self.assertEqual(len(self.created_processes), 1)

    async def test_scope_and_origin_guards(self):
        await self.attach()
        for key, wrong in (('request_id', 'd'*32), ('session_id', 'e'*32), ('runtime_epoch', 'old')):
            self.assertEqual((await self.control.post('/status', json={**self.scope, key: wrong})).status, 409)
        for headers in ({}, {'Origin': 'https://other.example.test'},
                        {'Origin': self.broker.origin, 'Authorization': 'Bearer wrong'}):
            self.assertIn((await self.human.post('/cli/api/form', headers=headers,
                json={'request_id': self.binding['request_id']})).status, (403, 409))

    async def test_only_fixed_binding_is_accepted(self):
        for change in ({'argv': ['sh']}, {'pid': 123}, {'session_id': 'native'}, {'provider': 'arbitrary'},
                       {'owner': True}, {'expires': float('inf')}, {'panel_hash': 'wrong'}):
            self.assertEqual((await self.control.post('/create', json={**self.binding, **change})).status, 409)
        self.assertEqual(self.created_processes, [])

    async def test_missing_boundary_or_peer_never_starts_a_process(self):
        for attribute in ('peer', 'boundary'):
            setattr(self, attribute, False)
            self.assertEqual((await self.control.post('/create', json=self.binding)).status, 409)
            setattr(self, attribute, True)
        self.assertEqual(self.created_processes, [])

    async def test_expired_session_cannot_be_revived_or_queried(self):
        await self.attach()
        self.lease.heartbeat = time.time()-1
        result = await self.control.post('/status', json=self.scope)
        self.assertEqual((await result.json())['state'], 'expired')
        self.assertEqual((await self.submit()).status, 409)
        self.assertIsNone(self.lease.process)

    async def test_auth_session_deadline_is_not_extended_by_polling(self):
        await self.attach()
        await self.submit()
        await self.lease.process.task
        await self.control.post('/status', json=self.scope)
        deadline = self.lease.session_expires
        await self.control.post('/status', json=self.scope)
        self.assertEqual(self.lease.session_expires, deadline)

    async def test_restart_receipt_prevents_replay_and_cannot_claim_success(self):
        await self.create()
        second = CLIBroker(self.broker.origin, self.broker.parent, 1001, self.root/'receipts.db',
            process_factory=self.broker.factory, boundary_check=lambda: True, peer_check=lambda _: True)
        self.addAsyncCleanup(second.close)
        async with TestClient(TestServer(second.control)) as client:
            self.assertEqual((await client.post('/create', json=self.binding)).status, 409)
            response = await client.post('/status', json=self.scope)
            self.assertEqual((await response.json())['state'], 'uncertain')
            self.assertEqual((await client.post('/use', json=self.scope)).status, 409)

    async def test_human_detach_cancels_running_process_without_replay(self):
        await self.attach()
        process = self.lease.process
        response = await self.human.post('/cli/api/detach', headers=self.headers,
                                        json={'request_id': self.binding['request_id']})
        self.assertEqual((await response.json())['state'], 'cancelled')
        self.assertIsNotNone(process.process.returncode)
        self.assertIsNone(process.directory)
