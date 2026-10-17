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

    def test_reschedule_is_revision_bound_idempotent_and_preserves_other_fields(self):
        created = self.create(mode='run', interval_seconds=3600)
        identifier = created['id']
        before = dict(self.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
        first = self.controls.reschedule_owned(7, identifier, 'reschedule-operation-1', 1, '2026-10-19T09:00:00+03:00')
        self.assertEqual((first['revision'], first['status']), (2, 'pending'))
        second = JobControls(self.controller).reschedule_owned(7, identifier, 'reschedule-operation-1', 1, '2026-10-19T09:00:00+03:00')
        self.assertEqual(first, second)
        after = dict(self.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
        self.assertEqual({k: v for k, v in before.items() if k not in {'revision', 'due'}},
                         {k: v for k, v in after.items() if k not in {'revision', 'due'}})
        with self.assertRaises(RuntimeError):
            self.controls.reschedule_owned(7, identifier, 'reschedule-operation-2', 1, '2026-10-20T09:00:00+03:00')
        with self.assertRaises(RuntimeError):
            self.controls.reschedule_owned(7, identifier, 'reschedule-operation-1', 1, '2026-10-20T09:00:00+03:00')

    def test_reschedule_rejects_foreign_stale_and_started_jobs_without_receipts(self):
        identifier = self.create()['id']
        with self.assertRaises(LookupError):
            self.controls.reschedule_owned(8, identifier, 'reschedule-operation-1', 1, '2026-10-20')
        for status in ('running', 'uncertain', 'done', 'cancelled'):
            with self.db:
                self.db.execute('UPDATE jobs SET status=? WHERE id=?', (status, identifier))
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.controls.reschedule_owned(7, identifier, 'reschedule-operation-1', 1, '2026-10-20')
            self.assertEqual(self.db.execute('SELECT status,revision FROM jobs WHERE id=?', (identifier,)).fetchone()[:], (status, 1))
        self.assertEqual(self.db.execute('SELECT count(*) FROM owner_job_operations').fetchone()[0], 1)

    def test_rescheduling_paused_job_does_not_resume_it_and_failed_receipt_rolls_back(self):
        identifier = self.create()['id']
        with self.db:
            self.db.execute("UPDATE jobs SET status='paused' WHERE id=?", (identifier,))
        first = self.controls.reschedule_owned(7, identifier, 'reschedule-operation-1', 1, '2026-10-19T09:00:00+03:00')
        self.assertEqual(first['status'], 'paused')
        with patch.object(self.controls, '_record', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.controls.reschedule_owned(7, identifier, 'reschedule-operation-2', 2, '2026-10-20')
        self.assertEqual(dict(self.db.execute('SELECT id,revision,status,due FROM jobs WHERE id=?', (identifier,)).fetchone()), first)

    def test_original_receipt_wins_after_timezone_configuration_changes(self):
        self.controller.scheduler.timezone = ZoneInfo('UTC')
        created = self.create(at='2026-10-25T03:30')
        self.controller.scheduler.timezone = ZoneInfo('Europe/Kyiv')
        self.assertEqual(self.create(at='2026-10-25T03:30'), created)


if __name__ == '__main__':
    unittest.main()
