import json
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import test_telegram_ux as fixtures
from oak.reminders import ReminderActions
from oak.job_controls import JobControls


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.TelegramUXTests.setUp
    finish = fixtures.TelegramUXTests.finish

    async def occurrence(self, *, recurring=False):
        job = self.c.scheduler.add(7, 'Drink water', delay_seconds=0,
                                   interval_seconds=3600 if recurring else None)
        await self.c.scheduler.tick()
        token = self.c.db.execute('SELECT token FROM reminder_occurrences WHERE job_id=?', (job,)).fetchone()[0]
        return job, token

    async def test_snooze_atomic_once_and_keeps_recurring_parent(self):
        parent, token = await self.occurrence(recurring=True)
        original = tuple(self.c.db.execute('SELECT due,status FROM jobs WHERE id=?', (parent,)).fetchone())
        with patch('oak.reminders.time.time', return_value=2000000000):
            self.c.db.execute('UPDATE reminder_occurrences SET expires=2000001000')
            self.c.db.commit()
            result = self.c.scheduler.reminders.decide(7, token, '600')
            repeated = ReminderActions(self.c.scheduler).decide(7, token, '600')
        self.assertEqual(result, repeated)
        self.assertEqual(tuple(self.c.db.execute('SELECT due,status FROM jobs WHERE id=?', (parent,)).fetchone()), original)
        self.assertEqual(self.c.db.execute('SELECT due FROM jobs WHERE id=?', (result['job'],)).fetchone()[0], 2000000600)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 2)
        with self.assertRaises(ValueError):
            self.c.scheduler.reminders.decide(7, token, '3600')
        with self.assertRaises(ValueError):
            self.c.scheduler.reminders.decide(8, token, '600')

    async def test_expired_and_agent_job_occurrences_cannot_snooze(self):
        parent, token = await self.occurrence()
        self.c.db.execute('UPDATE reminder_occurrences SET expires=0')
        self.c.db.commit()
        with self.assertRaises(ValueError):
            self.c.scheduler.reminders.decide(7, token, '600')
        self.c.db.execute('UPDATE reminder_occurrences SET expires=4000000000')
        self.c.db.execute("UPDATE jobs SET mode='run' WHERE id=?", (parent,))
        self.c.db.commit()
        with self.assertRaises(ValueError):
            self.c.scheduler.reminders.decide(7, token, '600')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    async def test_reminder_buttons_settle_after_done_and_duplicate_callback(self):
        _, token = await self.occurrence()
        row = self.g.db.execute('SELECT * FROM deliveries WHERE id=?', ('7:reminder:' + token,)).fetchone()
        await self.g._render_delivery(row)
        callback = {'update_id': 20, 'callback_query': {'id': 'cb', 'data': f'remind:{token}:done',
            'from': {'id': 7}, 'message': {'chat': {'type': 'private', 'id': 7}, 'message_id': 10}}}
        await self.g.handle_update(callback)
        callback['update_id'] = 21
        await self.g.handle_update(callback)
        await self.g._settle_reminders()
        self.assertEqual(self.g._deliver.call_args.args[0], 'editMessageText')
        self.assertEqual(self.g._deliver.call_args.args[1]['reply_markup']['inline_keyboard'], [])
        self.assertIn('завершено', self.g._deliver.call_args.args[1]['text'])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    async def test_snooze_receipt_failure_rolls_back_new_job(self):
        _, token = await self.occurrence()
        self.c.db.execute("CREATE TRIGGER reject_reminder_receipt BEFORE UPDATE ON reminder_occurrences BEGIN SELECT RAISE(ABORT,'fixture'); END")
        self.c.db.commit()
        with self.assertRaises(Exception):
            self.c.scheduler.reminders.decide(7, token, '600')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        self.assertEqual(self.c.scheduler.reminders.snapshot(7, token)['state'], 'pending')

    async def test_rescheduling_recurring_job_to_old_due_creates_one_new_delivery(self):
        parent, first = await self.occurrence(recurring=True)
        due = self.c.db.execute('SELECT due FROM reminder_occurrences WHERE token=?', (first,)).fetchone()[0]
        controls = JobControls(self.c)
        revision = self.c.db.execute('SELECT revision FROM jobs WHERE id=?', (parent,)).fetchone()[0]
        controls.reschedule_owned(7, parent, 'reschedule-previous-due', revision,
                                  datetime.fromtimestamp(due, timezone.utc).isoformat())
        await self.c.scheduler.tick()
        rows = self.c.db.execute('SELECT token,due,generation FROM reminder_occurrences WHERE job_id=? ORDER BY generation',
                                 (parent,)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][0], rows[1][0])
        self.assertAlmostEqual(rows[0][1], rows[1][1], places=5)
        self.assertEqual([row[2] for row in rows], [1, 2])
        await self.c.scheduler.reminders.publish(parent, 7, 'Drink water', rows[1][1], rows[1][2])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM reminder_occurrences').fetchone()[0], 2)
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM deliveries WHERE id LIKE '7:reminder:%'").fetchone()[0], 2)
        for row in rows:
            await self.g._render_delivery(self.g.db.execute('SELECT * FROM deliveries WHERE id=?',
                                                           ('7:reminder:' + row[0],)).fetchone())
        self.assertEqual(self.g._deliver.await_count, 2)

    async def test_legacy_occurrence_migration_keeps_decisions_and_does_not_replay_uncertain(self):
        from oak.schedule import Scheduler
        parent = self.c.scheduler.add(7, 'Legacy reminder', delay_seconds=0, interval_seconds=3600)
        with self.c.db:
            self.c.db.execute('DROP TABLE reminder_occurrences')
            self.c.db.execute('''CREATE TABLE reminder_occurrences (
                token TEXT PRIMARY KEY,job_id TEXT,chat_id INTEGER,due REAL,prompt TEXT,
                expires REAL,state TEXT,action TEXT,new_job TEXT,UNIQUE(job_id,due))''')
            self.c.db.executemany('INSERT INTO reminder_occurrences VALUES (?,?,7,?,?,4000000000,?,?,?)', [
                ('a' * 16, parent, 1, 'Legacy reminder', 'snoozed', '600', 'original-child'),
                ('b' * 16, parent, 2, 'Legacy reminder', 'pending', None, None)])
            self.c.db.execute("UPDATE jobs SET status='running' WHERE id=?", (parent,))
        before = [tuple(row) for row in self.c.db.execute('SELECT * FROM reminder_occurrences ORDER BY due')]
        self.c.scheduler = Scheduler(self.c.db, self.c)
        after = [tuple(row) for row in self.c.db.execute('SELECT * FROM reminder_occurrences ORDER BY due')]
        self.assertEqual([row[:-1] for row in after], before)
        self.assertEqual([row[-1] for row in after], [1, 2])
        self.assertEqual(self.c.db.execute('SELECT occurrence FROM jobs WHERE id=?', (parent,)).fetchone()[0], 2)
        self.assertEqual(self.c.scheduler.reminders.decide(7, 'a' * 16, '600'),
                         {'state': 'snoozed', 'job': 'original-child'})
        await self.c.scheduler.recover()
        await self.c.scheduler.tick()
        self.assertEqual(self.c.db.execute('SELECT status FROM jobs WHERE id=?', (parent,)).fetchone()[0], 'uncertain')
        self.assertEqual(self.g.db.execute("SELECT count(*) FROM deliveries WHERE id LIKE '7:reminder:%'").fetchone()[0], 0)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    async def test_uncertain_publish_is_not_claimed_as_a_new_occurrence(self):
        from unittest.mock import AsyncMock
        parent = self.c.scheduler.add(7, 'Unknown send', delay_seconds=0, interval_seconds=3600)
        self.c.emit = AsyncMock(side_effect=RuntimeError('delivery unknown'))
        self.c.notice = AsyncMock()
        await self.c.scheduler.tick()
        await self.c.scheduler.tick()
        self.assertEqual(self.c.emit.await_count, 1)
        row = self.c.db.execute('SELECT status,occurrence FROM jobs WHERE id=?', (parent,)).fetchone()
        self.assertEqual(tuple(row), ('uncertain', 1))
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM reminder_occurrences').fetchone()[0], 1)

    async def test_cancel_controls_reject_foreign_jobs_and_settle_without_replay(self):
        job = self.c.scheduler.add(7, 'Own reminder', delay_seconds=60)
        self.g._task_controls(7)
        row = self.g.db.execute("SELECT * FROM deliveries WHERE id LIKE 'task-controls:%'").fetchone()
        await self.g._render_delivery(row)
        self.assertIn('недоступна', self.g._cancel_job(8, job))
        self.assertIn('скасовано', self.g._cancel_job(7, job))
        self.assertIn('вже', self.g._cancel_job(7, job))
        await self.g._settle_task_controls(7, 10)
        self.assertEqual(self.g._deliver.call_args.args[1]['reply_markup']['inline_keyboard'], [])
        self.assertEqual(self.c.scheduler.list(7), [])

    async def test_failure_notice_identifies_bounded_job_without_reexecution(self):
        from unittest.mock import AsyncMock
        job = self.c.scheduler.add(7, '[work](https://example.invalid)\n' + 'a' * 100, delay_seconds=0, mode='run')
        self.c.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.c.db.commit()
        self.c.notice = AsyncMock()
        await self.c.scheduler.recover()
        text = self.c.notice.call_args.args[1]
        self.assertIn(job, text)
        self.assertIn('\\[work\\]', text)
        self.assertIn('…', text)
        self.assertEqual(self.c.scheduler.list(7)[0]['status'], 'uncertain')
