import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.bus import EventBus
from oak.controller import Controller
from oak.rendering import utf16_length
from oak.telegram import TelegramGateway


class ReminderBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_maximum_unicode_prompt_keeps_every_character_and_stays_deliverable(self):
        with tempfile.TemporaryDirectory() as folder:
            c = Controller(AsyncMock(), ':memory:', folder, None)
            self.addCleanup(c.close)
            g = TelegramGateway(c, 'synthetic-token', {7}, Path(folder))
            self.addCleanup(g.db.close)
            bus = EventBus(c)
            bus.add_sink(g.emit)
            c.emit = bus.emit
            prompt = '🙂' * 16000
            job = c.scheduler.add(7, prompt, delay_seconds=0)
            await c.scheduler.tick()
            self.assertEqual(c.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'done')
            deliveries = [json.loads(row['payload']) for row in g._deliveries()]
            self.assertEqual(''.join(row['text'] for row in deliveries), 'Нагадування: ' + prompt)
            self.assertTrue(all(utf16_length(row['text']) <= 3800 for row in deliveries))
            self.assertIn('reply_markup', deliveries[0])
            self.assertTrue(all('reply_markup' not in row for row in deliveries[1:]))
