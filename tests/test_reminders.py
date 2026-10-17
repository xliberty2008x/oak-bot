import json
import unittest
from unittest.mock import patch

import test_telegram_ux as fixtures
from oak.reminders import ReminderActions


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
