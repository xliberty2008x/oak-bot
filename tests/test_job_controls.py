"""Schedule mutations bind ownership, optimistic revisions and action receipts."""

from datetime import datetime
import json
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from oak.controller import Controller
from oak.job_controls import JobControls


class JobControlTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(AsyncMock(), ':memory:', '.', AsyncMock())
        self.addCleanup(self.controller.close)
        self.db = self.controller.db
        self.controls = JobControls(self.controller)

    def create(self, scope=7, operation='create-operation-1', **changes):
        return self.controls.create_owned(scope, operation, **{
            'prompt': 'Нагадати про звіт', 'at': '2026-10-18T09:00:00+03:00', **changes})

    def test_create_and_receipt_are_atomic_idempotent_and_survive_reopen(self):
        first = self.create()
        self.controls = JobControls(self.controller)
        second = self.create()
        self.assertEqual(first, second)
        self.assertEqual((first['status'], first['revision']), ('pending', 1))
        self.assertEqual(first['due'], datetime.fromisoformat('2026-10-18T09:00:00+03:00').timestamp())
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        with self.assertRaises(RuntimeError):
            self.create(prompt='Інша задача')
        with patch.object(self.controls, '_record', side_effect=RuntimeError('synthetic receipt failure')):
            with self.assertRaises(RuntimeError):
                self.create(operation='create-operation-2')
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        self.assertNotIn('Нагадати про звіт', json.dumps([tuple(row) for row in self.db.execute('SELECT * FROM owner_job_operations')]))

    def test_create_keeps_scope_mode_and_recurrence_and_rejects_deleted_topics(self):
        topic = self.controller.sessions.resolve(7, 42)
        first = self.create(topic, mode='run', interval_seconds=3600)
        other = self.create(8)
        self.assertNotEqual(first['id'], other['id'])
        row = self.db.execute('SELECT chat_id,mode,interval FROM jobs WHERE id=?', (first['id'],)).fetchone()
        self.assertEqual(tuple(row), (topic, 'run', 3600))
        self.assertEqual(self.controller.scheduler.list(7), [])
        self.controller.sessions.delete(topic)
        with self.assertRaises(LookupError):
            self.create(topic, mode='run', interval_seconds=3600)
        self.controller.client.request.assert_not_called()

    def test_create_uses_configured_timezone_and_rejects_ambiguous_or_missing_wall_time(self):
        for at in ('2026-03-29T03:30', '2026-10-25T03:30'):
            with self.subTest(at=at), self.assertRaises(ValueError):
                self.create(at=at)
        explicit = self.create(at='2026-10-25T03:30:00+02:00')
        self.assertEqual(explicit['due'], datetime.fromisoformat('2026-10-25T01:30:00+00:00').timestamp())
        self.controller.scheduler.timezone = ZoneInfo('Asia/Kathmandu')
        local = self.create(operation='create-operation-2', at='2026-10-18T05:45')
        self.assertEqual(local['due'], datetime.fromisoformat('2026-10-18T00:00:00+00:00').timestamp())

    def test_invalid_create_payloads_do_not_write_jobs_or_receipts(self):
        invalid = ({'prompt': ''}, {'prompt': 'x' * 16001}, {'prompt': None}, {'mode': 'script'}, {'mode': []},
                   {'interval_seconds': float('nan')}, {'interval_seconds': float('inf')},
                   {'interval_seconds': 10**1000}, {'interval_seconds': 59}, {'interval_seconds': True},
                   {'at': 'tomorrow'}, {'at': None})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(**changes)
        with self.assertRaises(ValueError):
            self.create(operation='short')
        self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM owner_job_operations').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
