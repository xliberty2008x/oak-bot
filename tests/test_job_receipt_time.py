import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from oak.controller import Controller
from oak.job_controls import JobControls


class JobReceiptTimeTests(unittest.TestCase):
    def test_create_receipt_does_not_reinterpret_time_after_configuration_changes(self):
        c = Controller(AsyncMock(), ':memory:', '.', AsyncMock(), config={'timezone': 'UTC'})
        self.addCleanup(c.close)
        jobs = JobControls(c)
        first = jobs.create_owned(7, 'same-create-operation', 'Reminder', '2026-10-25T03:30')
        c.scheduler.timezone = ZoneInfo('Europe/Kyiv')
        self.assertEqual(jobs.create_owned(7, 'same-create-operation', 'Reminder', '2026-10-25T03:30'), first)
        self.assertEqual(c.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
