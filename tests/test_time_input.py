from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from oak.controller import Controller
from oak.time_input import parse_when, reminder_input


class ReminderTimeTests(unittest.IsolatedAsyncioTestCase):
    zone = ZoneInfo('Europe/Kyiv')
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)

    def parse(self, text, now=None):
        return parse_when(text, self.zone, now=now or self.now)

    def test_seconds_units_and_local_next_occurrence(self):
        for value, seconds in [('60', 60), ('1.5', 1.5), ('10m', 600), ('2h', 7200), ('1d', 86400)]:
            self.assertEqual(self.parse(value).timestamp() - self.now.timestamp(), seconds)
        self.assertEqual(self.parse('18:30').isoformat(), '2026-10-18T18:30:00+03:00')
        self.assertEqual(self.parse('09:00').isoformat(), '2026-10-19T09:00:00+03:00')

    def test_tomorrow_is_a_calendar_day_across_dst(self):
        now = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
        actual = self.parse('завтра 12:00', now=now)
        self.assertEqual(actual.isoformat(), '2026-10-25T12:00:00+02:00')
        self.assertEqual(actual.timestamp() - now.timestamp(), 25 * 3600)

    def test_dst_gap_fold_rejected_but_explicit_offsets_are_distinct(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for value in ['2026-03-29T03:30', '2026-10-25T03:30']:
            with self.assertRaisesRegex(ValueError, 'UTC-зсувом'):
                self.parse(value, now)
        a = self.parse('2026-10-25T03:30+03:00', now)
        b = self.parse('2026-10-25T03:30+02:00', now)
        self.assertEqual(b.timestamp() - a.timestamp(), 3600)

    def test_elapsed_transition_clock_schedules_the_next_calendar_date(self):
        for reference, expected in (
            ('2026-03-29T04:00:00+03:00', '2026-03-30T03:30:00+03:00'),
            ('2026-10-25T04:00:00+02:00', '2026-10-26T03:30:00+02:00'),
        ):
            with self.subTest(reference=reference):
                self.assertEqual(self.parse('03:30', datetime.fromisoformat(reference)).isoformat(), expected)

    def test_upcoming_gap_or_second_fold_still_requires_explicit_offset(self):
        for reference in ('2026-03-29T02:00:00+02:00', '2026-10-25T02:00:00+03:00',
                          '2026-10-25T03:45:00+03:00'):
            with self.subTest(reference=reference), self.assertRaisesRegex(ValueError, 'UTC-зсувом'):
                self.parse('03:30', datetime.fromisoformat(reference))

    def test_invalid_or_unbounded_input_does_not_become_a_date(self):
        for value in ['nan', 'inf', '-1', '25:00', '12:60', '1e30', '9' * 400, 'yesterday', '2026-01-01T00:00Z']:
            with self.subTest(value=value[:20]), self.assertRaises(ValueError):
                self.parse(value)
        for value in ['', '10m', 'завтра 09:00', '10m ' + 'x' * 16001]:
            with self.assertRaises(ValueError):
                reminder_input(value, self.zone, now=self.now)

    def test_iso_space_date_and_prompt_remain_separate(self):
        moment, text = reminder_input('2026-10-19 09:00 Call the office', self.zone, now=self.now)
        self.assertEqual(moment.isoformat(), '2026-10-19T09:00:00+03:00')
        self.assertEqual(text, 'Call the office')
        moment, text = reminder_input('10m multi word reminder', self.zone, now=self.now)
        self.assertEqual(text, 'multi word reminder')

    async def test_command_stores_only_valid_scoped_reminders_and_confirms_zone(self):
        with tempfile.TemporaryDirectory() as folder:
            c = Controller(AsyncMock(), ':memory:', folder, AsyncMock())
            self.addCleanup(c.close)
            topic = c.sessions.resolve(7, 42)
            result = await c.command(topic, '/remind 10m Перевірити звіт', 1)
            self.assertIn('Europe/Kyiv', result)
            self.assertEqual(c.scheduler.list(topic)[0]['prompt'], 'Перевірити звіт')
            self.assertEqual(c.scheduler.list(7), [])
            await c.command(topic, '/remind nan broken', 2)
            self.assertEqual(len(c.scheduler.list(topic)), 1)
            c.client.request.assert_not_awaited()
