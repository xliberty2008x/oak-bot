"""Readable schedule commands with real storage and the shared Markdown renderer."""

from html.parser import HTMLParser
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from oak.controller import Controller
from oak.rendering import CLASSIC_LIMIT, render_markdown, utf16_length


class TaskCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.controller = Controller(self.client, ':memory:', '.', AsyncMock())
        self.addCleanup(self.controller.close)

    def add(self, prompt, *, chat=7, at='2026-10-18T09:00:00+03:00', **kwargs):
        return self.controller.scheduler.add(chat, prompt, at=at, **kwargs)

    async def tasks(self, chat=7):
        result = await self.controller.command(chat, '/tasks', 123)
        self.client.request.assert_not_awaited()
        return result

    async def test_empty_schedule_has_the_exact_empty_message(self):
        self.assertEqual(await self.tasks(), 'Розклад порожній.')

    async def test_tasks_follow_due_order_and_show_full_cancel_commands(self):
        later = self.add('Пізніше', at='2026-10-19T09:00:00+03:00')
        earlier = self.add('Спочатку')
        result = await self.tasks()
        self.assertLess(result.index('Спочатку'), result.index('Пізніше'))
        self.assertIn('1. Спочатку', result)
        self.assertIn('2. Пізніше', result)
        for job in (earlier, later):
            self.assertIn('Скасувати: `/cancel ' + job + '`', result)
        self.assertIn('18.10.2026 09:00 EEST (+0300)', result)
        self.assertIn('Europe/Kyiv', result)
        self.assertNotIn('"due"', result)

    async def test_dst_repeated_hour_keeps_distinct_offsets(self):
        self.add('До переведення годинника', at='2026-10-25T00:30:00+00:00')
        self.add('Після переведення годинника', at='2026-10-25T01:30:00+00:00')
        result = await self.tasks()
        self.assertIn('25.10.2026 03:30 EEST (+0300)', result)
        self.assertIn('25.10.2026 03:30 EET (+0200)', result)
        self.assertIn('Europe/Kyiv', result)

    async def test_configured_timezone_can_have_a_fractional_hour_offset(self):
        self.controller.scheduler.timezone = ZoneInfo('Asia/Kathmandu')
        self.add('Місцевий час', at='2026-10-18T00:00:00+00:00')
        result = await self.tasks()
        self.assertIn('Asia/Kathmandu', result)
        self.assertIn('18.10.2026 05:45', result)
        self.assertIn('(+0545)', result)
        self.assertNotIn('Europe/Kyiv', result)

    async def test_out_of_range_time_keeps_every_job_visible_and_cancellable(self):
        normal = self.add('Звичайний час')
        distant = self.controller.scheduler.add(7, 'Далеке майбутнє', delay_seconds=1e25)
        snapshot = list(self.controller.db.iterdump())
        result = await self.tasks()
        self.assertIn('Звичайний час', result)
        self.assertIn('18.10.2026 09:00 EEST (+0300)', result)
        self.assertIn('Далеке майбутнє', result)
        self.assertIn('Час поза підтримуваним діапазоном', result)
        for job in (normal, distant):
            self.assertIn('/cancel ' + job, result)
        self.assertNotIn('1e+25', result)
        self.assertEqual(list(self.controller.db.iterdump()), snapshot)

    async def test_modes_states_and_recurrence_are_human_readable(self):
        self.add('Нагадати')
        running = self.add('Працювати', mode='run', interval_seconds=90061.5)
        uncertain = self.add('Перевірити', mode='run', interval_seconds=60)
        cancelled = self.add('Скасоване')
        finished = self.add('Завершене')
        with self.controller.db:
            for job, status in ((running, 'running'), (uncertain, 'uncertain'), (finished, 'done')):
                self.controller.db.execute('UPDATE jobs SET status=? WHERE id=?', (status, job))
        self.controller.scheduler.cancel(7, cancelled)
        result = await self.tasks()
        self.assertIn('Нагадування · Очікує', result)
        self.assertIn('Задача · Виконується', result)
        self.assertIn('Задача · Потребує перевірки', result)
        self.assertIn('Повтор: 1 дн 1 год 1 хв 1.5 с', result)
        self.assertIn('Повтор: 1 хв', result)
        self.assertEqual(result.count('Повтор:'), 2)
        self.assertNotIn('Скасоване', result)
        self.assertNotIn('Завершене', result)

    async def test_preview_is_one_bounded_literal_line_in_both_renderers(self):
        prompt = '<script>x</script> [link](https://example.com) `code` &copy;\n\t' + '🙂' * 100
        self.add(prompt)
        result = await self.tasks()
        expected = ' '.join(prompt.split())[:79] + '…'
        for rich in (False, True):
            with self.subTest(rich=rich):
                chunks = render_markdown(result, rich=rich)
                plain = '\n'.join(chunk.plain for chunk in chunks)
                self.assertIn(expected, plain)
                self.assertNotIn('🙂' * 100, plain)
                tags = []
                parser = HTMLParser()
                parser.handle_starttag = lambda tag, attrs: tags.append(tag)
                for chunk in chunks:
                    parser.feed(chunk.html)
                self.assertNotIn('script', tags)
                self.assertNotIn('a', tags)
                self.assertNotIn('img', tags)

    async def test_large_lists_use_existing_renderer_without_losing_ids(self):
        ids = [self.add('🙂' * 100) for _ in range(30)]
        chunks = render_markdown(await self.tasks())
        self.assertGreater(len(chunks), 1)
        plain = '\n'.join(chunk.plain for chunk in chunks)
        for job in ids:
            self.assertIn('/cancel ' + job, plain)
        for chunk in chunks:
            self.assertLessEqual(utf16_length(chunk.plain), CLASSIC_LIMIT)

    async def test_selected_conversation_is_isolated_and_listing_changes_no_state(self):
        topic = self.controller.sessions.resolve(7, 42)
        own = self.add('Вибрана тема', chat=topic)
        self.add('Загальна розмова', chat=7)
        self.add('Інший власник', chat=8)
        snapshot = list(self.controller.db.iterdump())
        changes = self.controller.db.total_changes
        result = await self.tasks(topic)
        self.assertIn('Вибрана тема', result)
        self.assertIn(own, result)
        self.assertNotIn('Загальна розмова', result)
        self.assertNotIn('Інший власник', result)
        self.assertEqual(self.controller.db.total_changes, changes)
        self.assertEqual(list(self.controller.db.iterdump()), snapshot)


if __name__ == '__main__':
    unittest.main()
