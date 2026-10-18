import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from oak.bus import EventBus
from oak.controller import Controller
from oak.messages import MAX_EVENTS, MAX_MESSAGE_CHARS, MAX_RESPONSE_BYTES, message_events
from oak.web import WebGateway


class MessageHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.controller = Controller(AsyncMock(), ':memory:', self.root, AsyncMock(), config={'state_dir': str(self.root)})
        self.addCleanup(self.controller.close)
        self.bus = EventBus(self.controller)

    async def emit(self, kind, *, chat=7, thread='t', run='r', msgid='m', **fields):
        await self.bus.emit(chat, {'type': kind, 'threadId': thread, 'runId': run,
                           **({'messageId': msgid} if kind.startswith('TEXT_MESSAGE') else {}), **fields})

    async def test_projection_is_assistant_only_with_authoritative_final_and_fixed_failure(self):
        for role, message in [('assistant', 'm'), ('user', 'private'), ('tool', 'tool')]:
            await self.emit('TEXT_MESSAGE_START', role=role, msgid=message)
            await self.emit('TEXT_MESSAGE_CONTENT', msgid=message, delta='visible' if role == 'assistant' else 'PRIVATE_CONTENT')
        await self.emit('TEXT_MESSAGE_END', text='**corrected final**', arbitrary='PRIVATE_EXTENSION')
        await self.emit('CUSTOM', name='progress', value={'message': 'PRIVATE_PROGRESS'})
        await self.emit('TOOL_CALL_RESULT', toolCallId='call', messageId='result', content='PRIVATE_RESULT')
        await self.emit('RUN_ERROR', message='PRIVATE_PROVIDER_ERROR')
        await self.emit('TEXT_MESSAGE_CONTENT', msgid='unknown', delta='PRIVATE_UNKNOWN_ROLE')
        value = message_events(self.controller.db, 7)
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertEqual([event['type'] for event in value['events']], ['start', 'delta', 'end', 'run_end'])
        self.assertEqual(value['events'][2]['text'], '**corrected final**')
        self.assertEqual(value['events'][-1]['status'], 'failed')
        self.assertTrue(value['partial'])

    async def test_role_before_window_scope_and_same_message_ids_remain_separate(self):
        await self.emit('TEXT_MESSAGE_START', role='assistant')
        for index in range(MAX_EVENTS + 5):
            await self.emit('TEXT_MESSAGE_CONTENT', delta=str(index))
        await self.emit('TEXT_MESSAGE_START', role='user', run='other')
        await self.emit('TEXT_MESSAGE_CONTENT', delta='PRIVATE_OTHER_RUN', run='other')
        await self.emit('TEXT_MESSAGE_START', role='user', thread='other-thread')
        await self.emit('TEXT_MESSAGE_CONTENT', delta='PRIVATE_OTHER_THREAD', thread='other-thread')
        await self.emit('TEXT_MESSAGE_START', role='assistant', chat=8)
        await self.emit('TEXT_MESSAGE_CONTENT', delta='PRIVATE_OTHER_OWNER', chat=8)
        page = message_events(self.controller.db, 7)
        self.assertTrue(page['partial'])
        self.assertIsNotNone(page['before'])
        self.assertNotIn('PRIVATE', json.dumps(page))
        older = message_events(self.controller.db, 7, before=page['before'])
        self.assertEqual(older['events'][0]['type'], 'start')
        self.assertLess(older['events'][-1]['sequence'], page['events'][0]['sequence'])
        self.assertEqual(message_events(self.controller.db, 7, after=page['cursor'])['events'], [])

    async def test_byte_budget_and_source_limit_are_explicit_without_mutating_log(self):
        await self.emit('TEXT_MESSAGE_START', role='assistant')
        for _ in range(4):
            await self.emit('TEXT_MESSAGE_END', text='🙂' * (MAX_MESSAGE_CHARS + 1))
        value = message_events(self.controller.db, 7)
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False).encode()), MAX_RESPONSE_BYTES)
        self.assertTrue(value['partial'])
        self.assertEqual(len(value['events'][-1]['text']), MAX_MESSAGE_CHARS)
        self.assertIsNotNone(value['before'])
        stored = json.loads(self.controller.db.execute('SELECT event FROM ui_events ORDER BY sequence DESC LIMIT 1').fetchone()[0])
        self.assertEqual(len(stored['text']), MAX_MESSAGE_CHARS + 1)
        cursor = 0
        sequences = []
        while True:
            page = message_events(self.controller.db, 7, after=cursor)
            self.assertGreater(page['cursor'], cursor)
            sequences.extend(e['sequence'] for e in page['events'])
            cursor = page['cursor']
            if not page['has_more']:
                break
        self.assertEqual(sequences, list(range(1, 6)))

    async def test_authenticated_endpoint_rejects_foreign_deleted_scopes_and_bad_cursors(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [7, 8], {})
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        self.assertEqual((await client.get('/api/messages')).status, 401)
        await client.post('/api/session', json={'key': gateway.keys['7']})
        scope = self.controller.sessions.resolve(8, 42)
        self.assertEqual((await client.get('/api/messages?conversation=topic:42')).status, 404)
        self.assertEqual((await client.get('/api/messages?after=-1')).status, 400)
        self.assertEqual((await client.get('/api/messages?before=3&after=2')).status, 400)
        scope = self.controller.sessions.resolve(7, 42)
        await self.emit('TEXT_MESSAGE_START', chat=scope, role='assistant')
        await self.emit('TEXT_MESSAGE_CONTENT', chat=scope, delta='selected topic')
        response = await client.get('/api/messages?conversation=topic:42')
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['events'][-1]['delta'], 'selected topic')
        self.controller.sessions.delete(scope)
        self.assertEqual((await client.get('/api/messages?conversation=topic:42')).status, 404)


if __name__ == '__main__':
    unittest.main()
