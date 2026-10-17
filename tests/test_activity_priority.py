import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.telegram import TelegramGateway


class ActivityPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_cannot_starve_a_pending_streamed_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            c = Controller(AsyncMock(), ':memory:', folder, AsyncMock())
            self.addCleanup(c.close)
            g = TelegramGateway(c, 'synthetic-token', {7}, Path(folder))
            self.addCleanup(g.db.close)
            g._rich_supported = False
            g._apply(7, {'type': 'RUN_STARTED', 'runId': 'turn'})
            g._apply(7, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'turn', 'delta': 'A streamed answer'})
            sent = []

            async def deliver(method, payload):
                sent.append(payload['text'])
                g._last_send[7] = time.monotonic()
                if len(sent) == 2:
                    raise asyncio.CancelledError
                return {'message_id': 10}

            g._deliver = AsyncMock(side_effect=deliver)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(g._render(), 3)
            self.assertEqual(sent[0], 'A streamed answer')
            self.assertIn('Працюю', sent[1])
