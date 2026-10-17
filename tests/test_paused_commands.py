import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.job_controls import JobControls


class PausedCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_jobs_remain_listed_and_can_be_cancelled_in_their_scope(self):
        c = Controller(AsyncMock(), ':memory:', '.', AsyncMock())
        self.addCleanup(c.close)
        topic = c.sessions.resolve(7, 42)
        jobs = JobControls(c)
        job = jobs.create_owned(topic, 'create-paused-test', 'Read the report', '2027-01-01T09:00+02:00')
        jobs.pause_owned(topic, job['id'], 'pause-paused-test', job['revision'], True)
        self.assertEqual(c.scheduler.list(topic)[0]['status'], 'paused')
        self.assertIn('Призупинено', await c.command(topic, '/tasks', 1))
        self.assertFalse(c.scheduler.cancel(7, job['id']))
        self.assertTrue(c.scheduler.cancel(topic, job['id']))
        self.assertEqual(c.scheduler.list(topic), [])
