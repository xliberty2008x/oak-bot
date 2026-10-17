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

    async def test_task_preview_is_bounded_and_absent_from_summary(self):
        prompt = '<script>owner text</script>\n' + '🙂' * 100
        identifier = self.c.scheduler.add(11, prompt, delay_seconds=60)
        self.c.scheduler.add(22, 'OTHER_OWNER_PROMPT', delay_seconds=60)
        value = await (await self.http.get('/api/tasks', headers=self.headers)).json()
        self.assertEqual(len(value['tasks']), 1)
        task = value['tasks'][0]
        self.assertEqual(task['id'], identifier)
        self.assertEqual(task['preview'], ' '.join(prompt.split())[:79] + '…')
        self.assertEqual(len(task['preview']), 80)
        self.assertNotIn('prompt', task)
        self.assertNotIn('OTHER_OWNER_PROMPT', json.dumps(value))
        self.assertNotIn('preview', value['summary']['next_task'])
        panel = await (await self.http.get('/api/panel', headers=self.headers)).json()
        self.assertNotIn('owner text', json.dumps(panel))

    async def test_create_task_requires_confirmation_and_deduplicates_owned_operation(self):
        self.c.sessions.resolve(11, 42)
        payload = {'operation_id': 'a' * 32, 'confirmed': True, 'prompt': 'A scheduled task',
                   'at': '2026-11-10T09:00:00+02:00', 'mode': 'run', 'interval_seconds': 3600}
        url = '/api/tasks?conversation=topic:42'
        self.assertEqual((await self.http.post(url, headers=self.headers, json=payload | {'confirmed': False})).status, 400)
        first_response = await self.http.post(url, headers=self.headers, json=payload)
        self.assertEqual(first_response.status, 200)
        first = await first_response.json()
        self.assertEqual(await (await self.http.post(url, headers=self.headers, json=payload)).json(), first)
        self.assertEqual((await self.http.post(url, headers=self.headers, json=payload | {'prompt': 'Changed'})).status, 409)
        tasks = await (await self.http.get(url, headers=self.headers)).json()
        self.assertEqual(len(tasks['tasks']), 1)
        self.assertEqual(tasks['tasks'][0]['revision'], 1)
        self.assertEqual((await (await self.http.get('/api/tasks', headers=self.headers)).json())['tasks'], [])
        self.assertEqual((await self.http.post('/api/tasks?conversation=topic:999', headers=self.headers, json=payload)).status, 404)
        self.assertEqual((await self.http.post(url, headers=self.headers, json=payload | {'operation_id': 'b' * 32, 'at': '2026-10-25T03:30'})).status, 400)

    async def test_reschedule_rechecks_scope_revision_and_active_status(self):
        identifier = self.c.scheduler.add(11, 'Move me', delay_seconds=60, interval_seconds=3600)
        other = self.c.scheduler.add(22, 'Other', delay_seconds=60)
        payload = {'confirmed': True, 'operation_id': 'a' * 32, 'expected_revision': 1, 'at': '2026-11-11T08:00+02:00'}
        path = '/api/tasks/' + identifier + '/reschedule'
        self.assertEqual((await self.http.post('/api/tasks/' + other + '/reschedule', headers=self.headers, json=payload)).status, 404)
        first = await (await self.http.post(path, headers=self.headers, json=payload)).json()
        self.assertEqual(first['revision'], 2)
        self.assertEqual(await (await self.http.post(path, headers=self.headers, json=payload)).json(), first)
        self.assertEqual((await self.http.post(path, headers=self.headers, json=payload | {'operation_id': 'b' * 32})).status, 409)
        with self.c.db:
            self.c.db.execute("UPDATE jobs SET status='running' WHERE id=?", (identifier,))
        self.assertEqual((await self.http.post(path, headers=self.headers, json=payload | {'operation_id': 'c' * 32, 'expected_revision': 2})).status, 409)
        self.assertEqual(self.c.db.execute('SELECT interval FROM jobs WHERE id=?', (identifier,)).fetchone()[0], 3600)

    async def test_pause_resume_preserves_due_and_uses_scoped_receipts(self):
        identifier = self.c.scheduler.add(11, 'Pause me', delay_seconds=60)
        due = self.c.scheduler.list(11)[0]['due']
        payload = {'confirmed': True, 'operation_id': 'a' * 32, 'expected_revision': 1, 'paused': True}
        path = '/api/tasks/' + identifier + '/pause'
        self.assertEqual((await self.http.post(path, headers=self.headers, json=payload | {'paused': 'true'})).status, 400)
        first = await (await self.http.post(path, headers=self.headers, json=payload)).json()
        self.assertEqual(first['status'], 'paused')
        self.assertEqual(await (await self.http.post(path, headers=self.headers, json=payload)).json(), first)
        listing = await (await self.http.get('/api/tasks', headers=self.headers)).json()
        self.assertEqual(listing['tasks'][0]['status'], 'paused')
        self.assertEqual(listing['summary']['active_count'], 0)
        self.assertIsNone(listing['summary']['next_task'])
        resumed = await (await self.http.post(path, headers=self.headers, json=payload | {'operation_id': 'b' * 32, 'expected_revision': 2, 'paused': False})).json()
        self.assertEqual(resumed['status'], 'pending')
        self.assertEqual(resumed['due'], due)
        self.c.client.request.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
