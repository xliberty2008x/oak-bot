"""Owner/conversation-scoped panel operations against real SQLite and HTTP."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock

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


if __name__ == '__main__':
    unittest.main()
