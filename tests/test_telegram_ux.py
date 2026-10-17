import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.interaction import Interactions
from oak.telegram import TelegramGateway, TelegramError


class TelegramUXTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.c = Controller(AsyncMock(), self.root / 'state.sqlite', self.root, None)
        self.addCleanup(self.c.close)
        self.c.threads[7] = 'thread'
        self.c.active[7] = 'turn'
        self.g = TelegramGateway(self.c, 'synthetic-token', {7}, self.root)
        self.addCleanup(self.g.db.close)
        self.g._deliver = AsyncMock(return_value={'message_id': 10})
        self.g._api = AsyncMock(return_value=True)
        self.c.emit = self.g.emit
        self.tasks = []
        self.addAsyncCleanup(self.finish)

    async def finish(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def approval(self):
        task = asyncio.create_task(self.c.interactions.ask(
            {'threadId': 'thread', 'turnId': 'turn', 'command': 'PRIVATE-CANARY'}, 'approval', 'Дозволити дію?'))
        self.tasks.append(task)
        await asyncio.sleep(0)
        identifier = next(iter(self.c.interactions.pending))
        return task, identifier

    def row(self, identifier):
        return self.g.db.execute('SELECT * FROM deliveries WHERE id=?', ('7:approval_request:' + identifier,)).fetchone()

    async def test_command_decision_settles_original_and_survives_restart(self):
        task, identifier = await self.approval()
        await self.g._render_delivery(self.row(identifier))
        await self.g._decision(7, '/approve ' + identifier)
        self.assertTrue(await task)
        self.g._deliver.return_value = True  # Telegram edits may return True.
        await self.g._settle_approvals()
        method, payload = self.g._deliver.call_args.args
        self.assertEqual(method, 'editMessageText')
        self.assertEqual(payload['message_id'], 10)
        self.assertEqual(payload['reply_markup'], {'inline_keyboard': []})
        self.assertIn('Дозволено', payload['text'])
        self.assertNotIn('/approve', payload['text'])
        self.c.interactions = Interactions(self.c.db, self.c)
        self.assertEqual(self.c.interactions.snapshot(7, identifier)['state'], 'accepted')
        self.assertNotIn('PRIVATE-CANARY', json.dumps(self.c.interactions.snapshot(7, identifier)))
        count = self.g._deliver.await_count
        await self.g._settle_approvals()
        self.assertEqual(count, self.g._deliver.await_count)

    async def test_late_initial_delivery_is_already_terminal_and_edit_failure_is_cosmetic(self):
        task, identifier = await self.approval()
        self.c.interactions.resolve(7, identifier, False, 'approval')
        self.assertFalse(await task)
        await self.g._render_delivery(self.row(identifier))
        self.assertIn('Відхилено', self.g._deliver.call_args.args[1]['text'])
        self.assertEqual(self.g._deliver.call_args.args[1]['reply_markup']['inline_keyboard'], [])
        task, second = await self.approval()
        await self.g._render_delivery(self.row(second))
        self.c.interactions.resolve(7, second, True, 'approval')
        self.assertTrue(await task)
        self.g._deliver.side_effect = TelegramError('editMessageText', 500)
        await self.g._settle_approvals()
        self.assertEqual(self.c.interactions.snapshot(7, second)['state'], 'accepted')
        self.assertEqual(json.loads(self.row(second)['receipt'])['approval_edit'], 'failed')

    async def test_callback_toast_duplicate_and_foreign_decision(self):
        task, identifier = await self.approval()
        def callback(number, user=7):
            return {'update_id': number, 'callback_query': {'id': 'cb', 'data': 'approve:' + identifier,
                'from': {'id': user}, 'message': {'chat': {'id': 7, 'type': 'private'}, 'message_id': 10}}}
        await self.g.handle_update(callback(1, 8))
        self.assertFalse(task.done())
        with self.assertRaises(ValueError):
            self.c.interactions.snapshot(8, identifier)
        await self.g.handle_update(callback(2))
        self.assertTrue(await task)
        self.assertTrue(self.g._api.call_args.args[1]['text'])
        await self.g.handle_update(callback(3))
        self.assertIn('недоступний', self.g._api.call_args.args[1]['text'])

    async def test_restart_expires_pending_card_without_replaying_uncertain_send(self):
        task, identifier = await self.approval()
        self.g.db.execute("UPDATE deliveries SET status='uncertain' WHERE id=?", (self.row(identifier)['id'],))
        self.g.db.commit()
        self.c.interactions = Interactions(self.c.db, self.c)
        self.assertEqual(self.c.interactions.snapshot(7, identifier)['state'], 'expired')
        await self.g._settle_approvals()
        self.g._deliver.assert_not_awaited()
