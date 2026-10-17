"""Owner/conversation-scoped panel operations against real SQLite and HTTP."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from oak.bus import EventBus
from oak.controller import Controller
from oak.web import WebGateway


class PanelAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.c = Controller(AsyncMock(), ':memory:', Path(self.folder.name), AsyncMock(), config={'state_dir': self.folder.name})
        self.addCleanup(self.c.close)
        self.gateway = WebGateway(self.c, EventBus(self.c), 'synthetic-token', [11, 22], {})
        self.http = TestClient(TestServer(self.gateway.app))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)
        with self.c.db:
            self.c.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                              (hashlib.sha256(b'synthetic').hexdigest(), 11, time.time() + 300))
        self.headers = {'Authorization': 'Bearer synthetic'}

    def approval(self, identifier='a' * 10, scope=11, expires=None, kind='approval'):
        with self.c.db:
            self.c.db.execute('INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?)',
                (identifier, scope, 'thread', 'turn', kind, '{"command":"PRIVATE_COMMAND"}',
                 expires if expires is not None else time.time() + 300, 'pending'))
        future = asyncio.get_running_loop().create_future()
        self.c.interactions.pending[identifier] = future
        self.addCleanup(future.cancel)
        return future

    async def test_approval_projection_redacts_metadata_and_scopes_topics(self):
        topic = self.c.sessions.resolve(11, 42)
        self.approval()
        self.approval('b' * 10, topic)
        self.approval('c' * 10, 22)
        response = await self.http.get('/api/panel?conversation=topic:42', headers=self.headers)
        data = await response.json()
        rows = data['session']['legacy_approvals']
        self.assertEqual([row['id'] for row in rows], ['b' * 10])
        self.assertEqual(set(rows[0]), {'id', 'state', 'expires', 'summary'})
        self.assertNotIn('PRIVATE_COMMAND', json.dumps(data))
        self.assertEqual((await self.http.get('/api/panel')).status, 401)

    async def test_decision_is_scoped_boolean_and_consumed_once(self):
        future = self.approval()
        payload = {'kind': 'approval', 'id': 'a' * 10, 'accepted': True}
        topic = self.c.sessions.resolve(11, 42)
        self.assertEqual((await self.http.post('/api/decision?conversation=topic:42', headers=self.headers, json=payload)).status, 404)
        self.assertFalse(future.done())
        self.assertEqual((await self.http.post('/api/decision', headers=self.headers, json=payload | {'accepted': 1})).status, 400)
        self.assertEqual((await self.http.post('/api/decision', headers=self.headers, json=payload)).status, 200)
        self.assertTrue(future.result())
        self.assertEqual((await self.http.post('/api/decision', headers=self.headers, json=payload | {'accepted': False})).status, 409)
        self.assertEqual(self.c.interactions.snapshot(11, 'a' * 10)['state'], 'accepted')

    async def test_expired_or_wrong_kind_cannot_grant_permission(self):
        future = self.approval(expires=time.time() - 1)
        payload = {'kind': 'approval', 'id': 'a' * 10, 'accepted': True}
        self.assertEqual((await self.http.post('/api/decision', headers=self.headers, json=payload)).status, 409)
        self.assertFalse(future.done())
        self.assertEqual((await self.http.post('/api/decision', headers=self.headers, json=payload | {'kind': 'anything'})).status, 400)
        self.approval('b' * 10, kind='input')
        self.assertEqual((await self.http.post('/api/decision', headers=self.headers, json=payload | {'id': 'b' * 10})).status, 404)

    async def test_memory_pages_search_and_scope_are_bounded(self):
        for number in range(25):
            self.c.memory.remember(11, 'Shared word ' + str(number))
        self.c.memory.remember(22, 'PRIVATE_OTHER')
        topic = self.c.sessions.resolve(11, 42)
        self.c.memory.remember(topic, 'TOPIC_ONLY')
        first = await (await self.http.get('/api/memory', headers=self.headers)).json()
        second = await (await self.http.get('/api/memory?offset=20', headers=self.headers)).json()
        self.assertEqual([len(first['items']), len(second['items'])], [20, 5])
        self.assertEqual(first['next_offset'], 20)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len({item['id'] for item in first['items'] + second['items']}), 25)
        self.assertNotIn('PRIVATE_OTHER', json.dumps(first))
        rows = await (await self.http.get('/api/memory?conversation=topic:42', headers=self.headers)).json()
        self.assertEqual([item['text'] for item in rows['items']], ['TOPIC_ONLY'])
        self.assertEqual((await self.http.get('/api/memory?offset=10001', headers=self.headers)).status, 400)
        self.assertEqual((await self.http.get('/api/memory?q=%22%20OR%20*', headers=self.headers)).status, 200)

    async def test_memory_operations_are_durable_idempotent_and_foreign_delete_fails(self):
        payload = {'confirmed': True, 'operation_id': 'a' * 32, 'text': 'Remember this'}
        first = await (await self.http.post('/api/memory', headers=self.headers, json=payload)).json()
        repeated = await (await self.http.post('/api/memory', headers=self.headers, json=payload)).json()
        self.assertEqual(first, repeated)
        self.assertEqual(len(self.c.memory.search(11)), 1)
        self.assertEqual((await self.http.post('/api/memory', headers=self.headers, json=payload | {'text': 'Changed'})).status, 400)
        self.c.sessions.resolve(11, 42)
        delete = {'confirmed': True, 'operation_id': 'b' * 32}
        url = '/api/memory/' + first['id'] + '/delete'
        self.assertEqual((await self.http.post(url + '?conversation=topic:42', headers=self.headers, json=delete)).status, 404)
        self.assertEqual((await self.http.post(url, headers=self.headers, json=delete)).status, 200)
        self.assertEqual((await self.http.post(url, headers=self.headers, json=delete)).status, 200)
        self.assertEqual(self.c.memory.search(11, 'Remember'), [])
        # A retry after deletion cannot resurrect a successfully added note.
        self.assertEqual(await (await self.http.post('/api/memory', headers=self.headers, json=payload)).json(), first)
        self.assertEqual(self.c.memory.search(11), [])

    async def test_memory_write_and_receipt_rollback_together(self):
        insert = self.c.memory._remember
        def fail(*args):
            insert(*args)
            raise RuntimeError('synthetic interruption before receipt')
        with patch.object(self.c.memory, '_remember', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.c.memory.change(11, 'a' * 32, 'add', 'Never committed')
        self.assertEqual(self.c.memory.search(11), [])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM web_memory_operations').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
