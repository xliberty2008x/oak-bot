"""Recovery warnings are attempted once per interrupted evidence, never replayed."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller


class RestartNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = AsyncMock()
        self.controllers = []
        self.addCleanup(self.close_controllers)

    def close_controllers(self):
        for controller in self.controllers:
            controller.close()

    def open_controller(self, emit=None):
        controller = Controller(self.client, self.root / 'state.sqlite', self.root,
                                emit if emit is not None else AsyncMock())
        self.controllers.append(controller)
        return controller

    def reopen(self, controller, emit=None):
        self.controllers.remove(controller)
        controller.close()
        return self.open_controller(emit)

    @staticmethod
    def add_input(controller, identifier, chat=7, status='uncertain'):
        with controller.db:
            controller.db.execute(
                'INSERT INTO inputs(update_id,chat_id,text,status) VALUES (?,?,?,?)',
                (identifier, chat, 'Synthetic pending work', status))

    @staticmethod
    def add_turn(controller, identifier, chat=7):
        with controller.db:
            controller.db.execute('INSERT INTO turns VALUES (?,?,?,?)',
                                  (identifier, 'synthetic-thread', chat, 'inProgress'))

    async def test_repeated_restart_preserves_evidence_without_repeating_warning_or_input(self):
        controller = self.open_controller()
        self.add_input(controller, 101, status='dispatching')
        self.add_input(controller, 102)
        self.add_turn(controller, 'interrupted-turn')
        await controller.recover()
        controller.emit.assert_awaited_once()
        notice = controller.emit.await_args.args[1]
        self.assertTrue(notice['runId'].startswith('recovery-'))
        self.assertEqual(controller.db.execute(
            'SELECT count(*) FROM recovery_notices').fetchone()[0], 3)
        inputs = [tuple(row) for row in controller.db.execute('SELECT * FROM inputs')]
        turns = [tuple(row) for row in controller.db.execute('SELECT * FROM turns')]
        self.assertEqual([row[0] for row in controller.db.execute(
            'SELECT status FROM inputs')], ['uncertain', 'uncertain'])
        self.assertEqual(turns[0][3], 'uncertain')
        controller = self.reopen(controller)
        await controller.recover()
        await controller.recover()
        controller.emit.assert_not_awaited()
        self.assertEqual([tuple(row) for row in controller.db.execute('SELECT * FROM inputs')], inputs)
        self.assertEqual([tuple(row) for row in controller.db.execute('SELECT * FROM turns')], turns)
        self.client.request.assert_not_awaited()

    async def test_new_interrupted_input_and_turn_each_get_a_new_warning(self):
        controller = self.open_controller()
        self.add_input(controller, 101)
        await controller.recover()
        first = controller.emit.await_args.args[1]['runId']
        controller = self.reopen(controller)
        self.add_input(controller, 102, status='dispatching')
        await controller.recover()
        controller.emit.assert_awaited_once()
        second = controller.emit.await_args.args[1]['runId']
        self.assertNotEqual(first, second)
        controller = self.reopen(controller)
        self.add_turn(controller, 'new-interrupted-turn')
        await controller.recover()
        controller.emit.assert_awaited_once()
        self.assertNotIn(controller.emit.await_args.args[1]['runId'], {first, second})
        self.client.request.assert_not_awaited()

    async def test_notice_receipts_stay_scoped_to_each_conversation(self):
        controller = self.open_controller()
        self.add_input(controller, 101, chat=7)
        self.add_input(controller, 102, chat=8)
        await controller.recover()
        self.assertEqual({call.args[0] for call in controller.emit.await_args_list}, {7, 8})
        controller = self.reopen(controller)
        self.add_input(controller, 103, chat=8)
        await controller.recover()
        controller.emit.assert_awaited_once()
        self.assertEqual(controller.emit.await_args.args[0], 8)
        self.client.request.assert_not_awaited()

    async def test_failed_sink_does_not_repeat_an_ambiguous_warning_on_restart(self):
        controller = self.open_controller(AsyncMock(side_effect=ConnectionError('Synthetic failure')))
        self.add_input(controller, 101, status='dispatching')
        with self.assertRaises(ConnectionError):
            await controller.recover()
        controller = self.reopen(controller)
        await controller.recover()
        controller.emit.assert_not_awaited()
        self.assertEqual(controller.db.execute(
            'SELECT status FROM inputs WHERE update_id=101').fetchone()[0], 'uncertain')
        self.client.request.assert_not_awaited()

    async def test_cancelled_sink_does_not_repeat_an_ambiguous_warning_on_restart(self):
        controller = self.open_controller(AsyncMock(side_effect=asyncio.CancelledError))
        self.add_turn(controller, 'interrupted-turn')
        with self.assertRaises(asyncio.CancelledError):
            await controller.recover()
        controller = self.reopen(controller)
        await controller.recover()
        controller.emit.assert_not_awaited()
        self.assertEqual(controller.db.execute(
            'SELECT status FROM turns WHERE turn_id=?', ('interrupted-turn',)).fetchone()[0], 'uncertain')
        self.client.request.assert_not_awaited()

    async def test_unattempted_other_scope_notice_survives_first_sink_failure(self):
        controller = self.open_controller(AsyncMock(side_effect=ConnectionError('Synthetic failure')))
        self.add_turn(controller, 'first-interrupted-turn', chat=7)
        self.add_turn(controller, 'second-interrupted-turn', chat=8)
        with self.assertRaises(ConnectionError):
            await controller.recover()
        controller.emit.assert_awaited_once()
        self.assertEqual(controller.emit.await_args.args[0], 7)
        controller = self.reopen(controller)
        await controller.recover()
        controller.emit.assert_awaited_once()
        self.assertEqual(controller.emit.await_args.args[0], 8)
        self.assertEqual([row[0] for row in controller.db.execute(
            'SELECT status FROM turns')], ['uncertain', 'uncertain'])
        controller = self.reopen(controller)
        await controller.recover()
        controller.emit.assert_not_awaited()
        self.client.request.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
