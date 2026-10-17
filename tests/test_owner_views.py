"""Owner artifact and activity projections expose only authorized bounded data."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.bus import EventBus
from oak.controller import Controller
from oak.owner_views import OwnerViews


class OwnerViewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.controller = Controller(AsyncMock(), ':memory:', self.root, AsyncMock())
        self.addCleanup(self.controller.close)
        self.db = self.controller.db
        self.bus = EventBus(self.controller)
        self.views = OwnerViews(self.controller)

    def artifact(self, identifier, scope=7, *, name='звіт.pdf', mime='application/pdf'):
        path = self.root / identifier
        path.write_bytes(b'SYNTHETIC_CONTENT')
        with self.db:
            self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?)', (identifier, scope, str(path), name, mime))
        return path

    def test_artifact_gallery_is_scoped_and_contains_no_path_or_content(self):
        self.artifact('mine')
        self.artifact('other', 8, name='OTHER_OWNER')
        result = self.views.artifacts(7, 7)
        self.assertEqual(len(result['items']), 1)
        item = result['items'][0]
        self.assertEqual((item['name'], item['mime'], item['kind']), ('звіт.pdf', 'application/pdf', 'document'))
        self.assertEqual(item['url'], '/api/artifacts/mine')
        self.assertEqual(item['size_bytes'], len(b'SYNTHETIC_CONTENT'))
        for secret in (str(self.root), 'SYNTHETIC_CONTENT', 'OTHER_OWNER'):
            self.assertNotIn(secret, json.dumps(result))
        with self.assertRaises(LookupError):
            self.views.artifacts(8, 7)
        self.controller.client.request.assert_not_called()

    def test_removed_file_has_no_download_url_and_metadata_remains_bounded(self):
        path = self.artifact('missing', name='../private/\x01' + 'x' * 160, mime='text/html; PRIVATE_HEADER')
        path.unlink()
        item = self.views.artifacts(7, 7)['items'][0]
        self.assertFalse(item['available'])
        self.assertIsNone(item['size_bytes'])
        self.assertIsNone(item['url'])
        self.assertEqual(len(item['name']), 120)
        self.assertEqual(item['mime'], 'application/octet-stream')
        self.assertNotIn('/', item['name'])
        self.assertNotIn('PRIVATE_HEADER', json.dumps(item))

    def test_gallery_pagination_and_deleted_topic(self):
        topic = self.controller.sessions.resolve(7, 42)
        for number in range(5):
            self.artifact(str(number), topic, mime='image/png')
        first = self.views.artifacts(7, topic, limit=2)
        self.assertEqual([row['id'] for row in first['items']], ['4', '3'])
        self.artifact('new', topic)
        second = self.views.artifacts(7, topic, before=first['next_cursor'], limit=2)
        self.assertEqual([row['id'] for row in second['items']], ['2', '1'])
        self.assertTrue(all(item['kind'] == 'image' for item in second['items']))
        self.controller.sessions.delete(topic)
        with self.assertRaises(LookupError):
            self.views.artifacts(7, topic)

    def turn(self, identifier, *, scope=7, status='completed', events=()):
        with self.db:
            self.db.execute('INSERT INTO turns VALUES (?,?,?,?)', (identifier, 'thread', scope, status))
            for event in events:
                self.db.execute('INSERT INTO ui_events(chat_id,run_id,event) VALUES (?,?,?)',
                                (scope, identifier, json.dumps(event)))

    def test_activity_projects_lifecycle_and_fixed_tool_labels_without_private_data(self):
        self.turn('PRIVATE_TURN', events=[
            {'type': 'RUN_STARTED', 'timestamp': 100000, 'input': 'PRIVATE_INPUT'},
            {'type': 'TOOL_CALL_START', 'toolCallId': 'one', 'toolCallName': 'webSearch'},
            {'type': 'TOOL_CALL_START', 'toolCallId': 'one', 'toolCallName': 'webSearch'},
            {'type': 'TOOL_CALL_START', 'toolCallId': 'two', 'toolCallName': 'PRIVATE_TOOL'},
            {'type': 'TOOL_CALL_ARGS', 'delta': 'PRIVATE_ARGS'},
            {'type': 'TOOL_CALL_RESULT', 'content': 'PRIVATE_RESULT'},
            {'type': 'RUN_FINISHED', 'timestamp': 101500, 'outcome': {'type': 'success'}, 'result': 'PRIVATE_RESULT'},
        ])
        item = self.views.activity(7, 7)['items'][0]
        self.assertEqual(item['duration_seconds'], 1.5)
        self.assertEqual((item['started_at'], item['finished_at']), (100, 101.5))
        self.assertEqual(item['tool_count'], 2)
        self.assertEqual(set(item['tool_labels']), {'Шукаю в інтернеті', 'Виконую дію'})
        self.assertEqual(item['task_outcome'], 'unverified')
        self.assertEqual(item['status'], 'completed')
        self.assertNotIn('PRIVATE_', json.dumps(item))

    def test_activity_status_duration_and_unknown_timing_remain_distinct(self):
        for status in ('inProgress', 'uncertain', 'failed', 'interrupted', 'PRIVATE_STATUS'):
            self.turn(status, status=status, events=[{'type': 'RUN_STARTED', 'timestamp': 100000}])
        self.turn('failed-timed', status='failed', events=[
            {'type': 'RUN_STARTED', 'timestamp': 100000},
            {'type': 'RUN_ERROR', 'timestamp': 99000, 'message': 'PRIVATE_ERROR'}])
        result = self.views.activity(7, 7)
        self.assertEqual({item['status'] for item in result['items']}, {'inProgress', 'uncertain', 'failed', 'interrupted', 'unknown'})
        self.assertTrue(all(item['duration_seconds'] is None for item in result['items']))
        self.assertNotIn('PRIVATE_', json.dumps(result))
        self.assertTrue(all(item['task_outcome'] == 'unverified' for item in result['items']))

    def test_activity_ignores_notice_events_and_scopes_pagination(self):
        topic = self.controller.sessions.resolve(7, 42)
        for number in range(4):
            self.turn(str(number), scope=topic)
        self.turn('other-owner', scope=8)
        self.db.execute('INSERT INTO ui_events(chat_id,run_id,event) VALUES (?,?,?)',
                        (topic, 'notice', json.dumps({'type': 'RUN_STARTED', 'timestamp': 1})))
        first = self.views.activity(7, topic, limit=2)
        second = self.views.activity(7, topic, before=first['next_cursor'], limit=2)
        self.assertEqual((len(first['items']), len(second['items'])), (2, 2))
        self.assertIsNone(second['next_cursor'])
        self.assertFalse({item['id'] for item in first['items']} & {item['id'] for item in second['items']})
        self.assertEqual(self.views.activity(7, 7)['items'], [])
        with self.assertRaises(LookupError):
            self.views.activity(8, topic)
        self.controller.sessions.delete(topic)
        with self.assertRaises(LookupError):
            self.views.activity(7, topic)

    def test_activity_bounded_labels_and_malformed_timestamp_never_expose_raw_values(self):
        self.turn('run', events=[{'type': 'TOOL_CALL_START', 'toolCallId': str(number),
                                  'toolCallName': 'PRIVATE_' + str(number)} for number in range(210)]
                  + [{'type': 'RUN_STARTED', 'timestamp': 'PRIVATE_TIME'}, {'type': 'RUN_ERROR', 'timestamp': 10**30}])
        item = self.views.activity(7, 7)['items'][0]
        self.assertEqual(item['tool_count'], 210)
        self.assertTrue(item['tool_labels_truncated'])
        self.assertEqual(item['tool_labels'], ['Виконую дію'])
        self.assertIsNone(item['started_at'])
        self.assertIsNone(item['finished_at'])


if __name__ == '__main__':
    unittest.main()
