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

    async def test_configured_deadline_reminder_and_expiry_notice(self):
        self.c.config['approval_timeout_seconds'] = 120
        self.c.interactions = Interactions(self.c.db, self.c)
        self.c.notice = AsyncMock()
        self.c.interactions.clock = lambda: 1000
        reminder_released = asyncio.Event()
        async def sleep(seconds):
            self.assertEqual(seconds, 60)
            await reminder_released.wait()
        self.c.interactions.sleep = sleep
        timeout_released = asyncio.Event()
        async def wait_for(future, seconds):
            self.assertEqual(seconds, 120)
            await timeout_released.wait()
            raise asyncio.TimeoutError
        self.c.interactions.wait_for = wait_for
        task, identifier = await self.approval()
        self.assertEqual(self.c.interactions.snapshot(7, identifier)['expires'], 1120)
        self.assertIn('Відповісти до', json.loads(self.row(identifier)['payload'])['text'])
        reminder_released.set()
        await asyncio.sleep(0)
        self.assertEqual(self.c.notice.await_count, 1)
        timeout_released.set()
        self.assertFalse(await task)
        self.assertEqual(self.c.notice.await_count, 2)
        self.assertIn('Час підтвердження минув', self.c.notice.call_args.args[1])
        self.assertEqual(self.c.interactions.snapshot(7, identifier)['state'], 'expired')
        self.assertFalse(self.c.interactions.pending)

    async def test_cancel_and_answer_cancel_reminders_and_timeout_validation(self):
        for invalid in (59, 3601, True, 90.0, '120'):
            self.c.config['approval_timeout_seconds'] = invalid
            with self.assertRaises(ValueError):
                Interactions(self.c.db, self.c)
        self.c.config['approval_timeout_seconds'] = 300
        self.c.interactions = Interactions(self.c.db, self.c)
        self.c.notice = AsyncMock()
        task, identifier = await self.approval()
        self.c.interactions.cancel_turn('thread', 'turn')
        self.assertFalse(await task)
        self.assertEqual(self.c.interactions.snapshot(7, identifier)['state'], 'cancelled')
        self.c.notice.assert_not_awaited()

    async def test_activity_tracks_execution_and_never_displays_raw_values(self):
        def event(kind, **fields):
            self.g._apply(7, {'type': kind, 'runId': 'activity-run', **fields})
        event('RUN_STARTED')
        event('TOOL_CALL_START', toolCallId='a', toolCallName='exec_command')
        event('TOOL_CALL_START', toolCallId='b', toolCallName='PRIVATE-CANARY')
        event('TOOL_CALL_ARGS', toolCallId='a', delta='SECRET')
        event('TOOL_CALL_END', toolCallId='a')
        await self.g._render_activity()
        payload = self.g._deliver.call_args.args[1]
        self.assertIn('Виконую команду', payload['text'])
        self.assertIn('Виконую дію', payload['text'])
        self.assertNotIn('SECRET', json.dumps(payload))
        self.assertNotIn('PRIVATE-CANARY', json.dumps(payload))
        event('STEP_FINISHED', toolCallId='a')
        event('TOOL_CALL_START', toolCallId='a', toolCallName='exec_command')
        await self.g._render_activity()
        self.assertNotIn('команду', self.g._deliver.call_args.args[1]['text'])
        event('TEXT_MESSAGE_CONTENT', messageId='answer', delta='My answer')
        event('RUN_FINISHED')
        await self.g._render_activity()
        self.assertIn('Обробку завершено', self.g._deliver.call_args.args[1]['text'])
        self.assertEqual(self.g._active[7].text(), 'My answer')

    async def test_activity_uncertain_send_and_restart_never_resend(self):
        self.g._apply(7, {'type': 'RUN_STARTED', 'runId': 'activity-run'})
        self.g._deliver.side_effect = TelegramError('sendMessage')
        await self.g._render_activity()
        await self.g._render_activity()
        self.assertEqual(self.g._deliver.await_count, 1)
        restored = TelegramGateway(self.c, 'synthetic-token', {7}, self.root)
        self.addCleanup(restored.db.close)
        restored._deliver = AsyncMock()
        await restored._render_activity()
        restored._deliver.assert_not_awaited()

    async def test_stop_callback_is_bound_to_run_and_is_not_replayed(self):
        self.g._apply(7, {'type': 'RUN_STARTED', 'runId': 'turn'})
        row = self.g.db.execute('SELECT * FROM activity_cards').fetchone()
        self.assertTrue(self.g._activity_payload(row)['reply_markup']['inline_keyboard'])
        self.c.client.request = AsyncMock(return_value={})
        self.assertIn('недоступний', await self.g._stop_callback(8, row['token']))
        self.assertIn('прийнято', await self.g._stop_callback(7, row['token']))
        self.assertIn('прийнято', await self.g._stop_callback(7, row['token']))
        self.c.client.request.assert_awaited_once_with('turn/interrupt', {'threadId': 'thread', 'turnId': 'turn'})
        self.assertEqual(self.g._activity_payload(row)['reply_markup']['inline_keyboard'], [])

    async def test_stale_stop_and_uncertain_stop_never_interrupt_new_run(self):
        self.g._apply(7, {'type': 'RUN_STARTED', 'runId': 'old'})
        row = self.g.db.execute('SELECT * FROM activity_cards').fetchone()
        self.c.client.request = AsyncMock()
        self.assertIn('завершилася', await self.g._stop_callback(7, row['token']))
        self.c.client.request.assert_not_awaited()
        self.g._apply(7, {'type': 'RUN_STARTED', 'runId': 'turn'})
        row = self.g.db.execute("SELECT * FROM activity_cards WHERE run_id='turn'").fetchone()
        self.c.client.request.side_effect = OSError('network')
        self.assertIn('невідомий', await self.g._stop_callback(7, row['token']))
        await self.g._stop_callback(7, row['token'])
        self.assertEqual(self.c.client.request.await_count, 1)

    async def test_durable_receipt_reaction_and_exact_success_binding(self):
        message = {'update_id': 1, 'message': {'message_id': 33, 'chat': {'type': 'private', 'id': 7},
                                              'from': {'id': 7}, 'text': 'hello'}}
        await self.g._ingest(message)
        await self.g._ingest(message)
        self.assertEqual(self.g.db.execute('SELECT count(*) FROM input_reactions').fetchone()[0], 1)
        await self.g._render_reactions()
        self.assertEqual(self.g._api.call_args.args[1]['reaction'][0]['emoji'], '👀')
        self.c.db.execute("INSERT INTO inputs(update_id,chat_id,text,status) VALUES (1,7,'hello','accepted')")
        self.c.db.execute("INSERT INTO turns VALUES ('bound','thread',7,'inProgress')")
        self.c._accepted(1, 7, 'bound')
        self.g._bind_reaction(7, 1)
        self.g._reaction_terminal(7, {'type': 'RUN_FINISHED', 'runId': 'other', 'status': 'completed'})
        await self.g._render_reactions()
        self.assertEqual(self.g._api.await_count, 1)
        self.g._reaction_terminal(7, {'type': 'RUN_FINISHED', 'runId': 'bound', 'status': 'completed'})
        await self.g._render_reactions()
        self.assertEqual(self.g._api.call_args.args[1]['reaction'][0]['emoji'], '👍')

    async def test_failed_run_and_failed_reaction_never_show_success_or_replay_input(self):
        self.g.db.execute("INSERT INTO input_reactions VALUES (1,7,33,'bound','pending','waiting')")
        self.g.db.commit()
        self.g._api.side_effect = TelegramError('setMessageReaction')
        await self.g._render_reactions()
        self.g._reaction_terminal(7, {'type': 'RUN_ERROR', 'runId': 'bound'})
        await self.g._render_reactions()
        self.assertEqual(self.g._api.await_count, 1)
        self.assertEqual(self.g.db.execute('SELECT completed FROM input_reactions').fetchone()[0], 'failed')

    async def test_edited_commands_and_media_are_consumed_once_without_dispatch(self):
        self.c.submit = AsyncMock()
        self.c.stop = AsyncMock()
        self.g._attachments = AsyncMock()
        update = {'update_id': 5, 'edited_message': {'message_id': 33, 'chat': {'type': 'private', 'id': 7},
            'from': {'id': 7}, 'text': '/stop', 'document': {'file_id': 'file'}}}
        await self.g._ingest(update)
        await self.g._ingest(update)
        self.c.submit.assert_not_awaited()
        self.c.stop.assert_not_awaited()
        self.g._attachments.assert_not_awaited()
        self.assertEqual(self.g.db.execute('SELECT count(*) FROM intake').fetchone()[0], 0)
        self.assertEqual(len(self.g._deliveries()), 1)
        restored = TelegramGateway(self.c, 'synthetic-token', {7}, self.root)
        self.addCleanup(restored.db.close)
        await restored.handle_update(update)
        self.assertEqual(len(restored._deliveries()), 1)

    async def test_foreign_edit_has_no_notice_or_reaction(self):
        await self.g._ingest({'update_id': 6, 'edited_message': {'message_id': 33,
            'chat': {'type': 'private', 'id': 7}, 'from': {'id': 8}, 'caption': 'changed'}})
        self.assertEqual(self.g._deliveries(), [])
        self.assertEqual(self.g.db.execute('SELECT count(*) FROM input_reactions').fetchone()[0], 0)

    async def test_album_is_one_input_with_all_original_attachment_ids_and_captions(self):
        self.c.submit = AsyncMock(return_value=True)
        self.g._attachments = AsyncMock(side_effect=lambda message, update: [{'path': str(update), 'mime': 'image/jpeg', 'name': 'photo.jpg'}])
        for number, caption in ((1, 'first'), (2, 'second')):
            await self.g._ingest({'update_id': number, 'message': {'message_id': number, 'media_group_id': 'album',
                'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7}, 'caption': caption, 'photo': [{'file_id': str(number)}]}})
        self.g.db.execute('UPDATE telegram_albums SET deadline=0')
        self.g.db.commit()
        worker = asyncio.create_task(self.g._intake_worker(7))
        self.tasks.append(worker)
        for _ in range(30):
            if self.c.submit.await_count:
                break
            await asyncio.sleep(0)
        self.c.submit.assert_awaited_once()
        args, kwargs = self.c.submit.call_args
        self.assertEqual(args, (7, 'first\n\nsecond', 1))
        self.assertEqual([item['path'] for item in kwargs['attachments']], ['1', '2'])
        late = {'update_id': 3, 'message': {'media_group_id': 'album', 'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7}, 'caption': 'late'}}
        await self.g._ingest(late)
        self.assertEqual(self.c.submit.await_count, 1)
        self.assertTrue(self.g.db.execute("SELECT 1 FROM deliveries WHERE id='late-album:3'").fetchone())

    async def test_album_download_failure_submits_nothing_and_restart_preserves_members(self):
        for number in (1, 2):
            await self.g._ingest({'update_id': number, 'message': {'message_id': number, 'media_group_id': 'album',
                'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7}, 'photo': [{'file_id': str(number)}]}})
        restored = TelegramGateway(self.c, 'synthetic-token', {7}, self.root)
        self.addCleanup(restored.db.close)
        self.c.submit = AsyncMock()
        restored._attachments = AsyncMock(side_effect=[[{'path': 'one', 'mime': 'image/jpeg', 'name': 'one.jpg'}], ValueError('bad file')])
        restored.db.execute('UPDATE telegram_albums SET deadline=0')
        restored.db.commit()
        worker = asyncio.create_task(restored._intake_worker(7)); self.tasks.append(worker)
        for _ in range(30):
            if restored._attachments.await_count == 2:
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.c.submit.assert_not_awaited()
        self.assertEqual(restored.db.execute('SELECT count(*) FROM telegram_album_members').fetchone()[0], 2)
