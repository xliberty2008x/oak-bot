import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from oak.telegram import TelegramError, TelegramGateway, split_text
from oak.sessions import SessionStore
from oak.controller import Controller
from oak.interaction import Interactions


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = AsyncMock()
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.controller.sessions = SessionStore(db)
        self.gateway = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name))
        self.addCleanup(self.gateway.db.close)

    def update(self, number, text='hello', user=7, edited=False):
        return {'update_id': number, 'edited_message' if edited else 'message': {
            'chat': {'type': 'private', 'id': user}, 'from': {'id': user}, 'text': text}}

    async def test_dedup_edits_and_allowlist(self):
        await self.gateway.handle_update(self.update(1, user=8))
        await self.gateway.handle_update(self.update(2))
        await self.gateway.handle_update(self.update(2))
        await self.gateway.handle_update(self.update(3, 'changed', edited=True))
        self.assertEqual(self.controller.submit.await_count, 2)
        self.controller.submit.assert_awaited_with(7, 'changed', 3)
        self.assertEqual(json.loads(self.gateway.offset_path.read_text()), {'offset': 4})

    async def test_topics_route_inputs_commands_callbacks_and_metadata_separately(self):
        gateway = self.gateway
        topic = self.controller.sessions.resolve(7, 12, name='Початкова')
        update = self.update(1, 'Topic message')
        update['message']['message_thread_id'] = 12
        await gateway.handle_update(update)
        self.controller.submit.assert_awaited_once_with(topic, 'Topic message', 1)
        update = self.update(2, '/stop')
        update['message']['message_thread_id'] = 12
        await gateway.handle_update(update)
        self.controller.stop.assert_awaited_once_with(topic)
        gateway._api = AsyncMock(return_value=True)
        await gateway.handle_update({'update_id': 3, 'callback_query': {'id': 'cb', 'data': 'approve:req-1',
            'from': {'id': 7}, 'message': {'chat': {'id': 7, 'type': 'private'}, 'message_thread_id': 12}}})
        self.controller.approve.assert_awaited_once_with(topic, 'req-1', True)
        for number, service in ((4, {'forum_topic_edited': {'name': 'Дослідження\n'}}),
                                (5, {'forum_topic_edited': {'name': '', 'icon_custom_emoji_id': ''}}),
                                (6, {'forum_topic_closed': {}}), (7, {'forum_topic_reopened': {}})):
            update = self.update(number, 'Must not reach the model')
            update['message'].update(message_thread_id=12, **service)
            await gateway.handle_update(update)
        self.assertEqual(self.controller.submit.await_count, 1)
        self.assertEqual(self.controller.sessions.list(7)[1]['name'], 'Дослідження')
        self.assertFalse(self.controller.sessions.list(7)[1]['closed'])
        interactions = Interactions(self.controller.sessions.db, self.controller)
        future = asyncio.get_running_loop().create_future()
        interactions.pending['scoped-request'] = future
        with interactions.db:
            interactions.db.execute('INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?)',
                ('scoped-request', topic, 'thread', 'turn', 'approval', '{}', 4102444800, 'pending'))
        self.controller.interactions = interactions
        self.controller.approve = Controller.approve.__get__(self.controller)
        callback = {'id': 'scoped-callback', 'data': 'approve:scoped-request', 'from': {'id': 7},
                    'message': {'chat': {'id': 7, 'type': 'private'}, 'message_thread_id': 13}}
        await gateway.handle_update({'update_id': 8, 'callback_query': callback})
        self.assertFalse(future.done())
        callback['message']['message_thread_id'] = 12
        await gateway.handle_update({'update_id': 9, 'callback_query': callback})
        self.assertTrue(future.result())

    async def test_topic_outbox_survives_restart_and_sends_native_topic_addresses(self):
        gateway = self.gateway
        scope = self.controller.sessions.resolve(7, 12)
        await gateway.emit(scope, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'topic-run', 'delta': 'Topic reply'})
        await gateway.emit(scope, {'type': 'RUN_FINISHED', 'runId': 'topic-run'})
        await gateway.emit(scope, {'type': 'CUSTOM', 'name': 'approval_request',
                                  'value': {'id': 'topic-request', 'summary': 'Confirm?'}})
        restored = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name))
        self.addCleanup(restored.db.close)
        self.assertEqual(len(restored._pending()), 1)
        self.assertEqual(restored._pending()[0][0], scope)
        restored._api = AsyncMock(return_value={'message_id': 22})
        await restored._render_one(*restored._pending()[0])
        self.assertEqual(restored._api.await_args.args[1]['chat_id'], 7)
        self.assertEqual(restored._api.await_args.args[1]['message_thread_id'], 12)
        restored._last_send.clear()
        restored._apply(scope, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'topic-run', 'delta': ' edited'})
        await restored._render_one(*restored._pending()[0])
        self.assertEqual(restored._api.await_args.args[0], 'editMessageText')
        self.assertNotIn('message_thread_id', restored._api.await_args.args[1])
        await restored._render_delivery(restored._deliveries()[0])
        self.assertEqual(restored._api.await_args.args[1]['chat_id'], 7)
        self.assertEqual(restored._api.await_args.args[1]['message_thread_id'], 12)
        url = 'https://oak.example.test/?conversation=topic%3A12&surface=lesson-plan'
        await restored.emit(scope, {'type': 'CUSTOM', 'name': 'web_app_link', 'value': {
            'url': url, 'label': 'Обери тему або напиши тут.', 'button_label': 'Відкрити форму'}})
        await restored._render_delivery(restored._deliveries()[0])
        payload = restored._api.await_args.args[1]
        self.assertEqual((payload['chat_id'], payload['message_thread_id']), (7, 12))
        self.assertEqual(payload['text'], 'Обери тему або напиши тут.')
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0],
                         {'text': 'Відкрити форму', 'web_app': {'url': url}})
        await restored.emit(-1, {'type': 'RUN_STARTED', 'runId': 'browser-only'})
        self.assertNotIn(-1, restored._replies)

    async def test_topic_intake_restores_workers_and_stop_leaves_other_topics_running(self):
        entered, other_entered = asyncio.Event(), asyncio.Event()
        self.controller.db = Mock()
        self.controller.db.execute.return_value.fetchone.return_value = None
        first = self.controller.sessions.resolve(7, 12)
        second = self.controller.sessions.resolve(7, 13)

        async def submit(scope, *args, **kwargs):
            if scope == first:
                entered.set()
                await asyncio.Event().wait()
            other_entered.set()

        self.controller.submit.side_effect = submit
        update = self.update(10)
        update['message']['message_thread_id'] = 12
        await self.gateway._ingest(update)
        historical = self.update(8, 'Historical input stays General')
        historical['message'].update(message_thread_id=14, forum_topic_created={'name': 'Архів'})
        with self.gateway.db:
            self.gateway.db.execute('UPDATE intake SET chat_id=7 WHERE update_id=10')
            self.gateway.db.execute("INSERT INTO intake VALUES (8,7,?,'done')", (json.dumps(historical),))
        restored = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name))
        self.addCleanup(restored.db.close)
        self.assertEqual(restored.db.execute('SELECT chat_id FROM intake WHERE update_id=10').fetchone()[0], first)
        self.assertIsNotNone(self.controller.sessions.lookup(7, 'topic:14'))
        self.assertEqual(restored.db.execute('SELECT chat_id,status FROM intake WHERE update_id=8').fetchone()[0], 7)
        restored._poll = AsyncMock(side_effect=asyncio.Event().wait)
        restored._notice = AsyncMock()
        restored._api = AsyncMock(return_value=True)
        worker = asyncio.create_task(restored.run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            update = self.update(11)
            update['message']['message_thread_id'] = 13
            await restored._ingest(update)
            await asyncio.wait_for(other_entered.wait(), 1)
            update = self.update(12)
            update['message']['message_thread_id'] = 12
            await restored._ingest(update)
            update = self.update(13, '/stop')
            update['message']['message_thread_id'] = 12
            await restored._ingest(update)
            rows = restored.db.execute('SELECT chat_id,status FROM intake WHERE update_id>=10 ORDER BY update_id').fetchall()
            self.assertEqual([tuple(row) for row in rows], [(first, 'cancelled'), (second, 'done'), (first, 'cancelled')])
            self.controller.stop.assert_awaited_once_with(first)
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

    async def test_uncertain_submit_does_not_advance_offset(self):
        self.controller.submit.side_effect = RuntimeError('uncertain')
        self.gateway._api = AsyncMock(return_value={'message_id': 12})
        with self.assertRaises(RuntimeError):
            await self.gateway.handle_update(self.update(9))
        self.assertEqual(self.gateway.offset, 0)
        self.assertFalse(self.gateway.offset_path.exists())

    async def test_missing_topic_cancels_delivery_and_ignores_replayed_input(self):
        gateway = self.gateway
        scope = self.controller.sessions.resolve(7, 12)

        async def retire(chat_id):
            self.controller.sessions.delete(chat_id)
            await gateway.discard_topic(chat_id)

        self.controller.retire_topic.side_effect = retire
        gateway._request_sync = Mock(return_value={
            'ok': False, 'error_code': 400, 'description': 'Bad Request: message thread not found'})
        await gateway.emit(scope, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'deleted', 'delta': 'Reply'})
        await gateway._render_one(*gateway._pending()[0])
        self.controller.retire_topic.assert_awaited_once_with(scope)
        self.assertEqual(gateway.db.execute("SELECT status FROM replies WHERE run_id='deleted'").fetchone()[0], 'cancelled')
        self.assertEqual(gateway._pending(), [])
        update = self.update(10)
        update['message']['message_thread_id'] = 12
        await gateway._ingest(update)
        await gateway.emit(scope, {'type': 'RUN_STARTED', 'runId': 'late'})
        self.assertEqual(gateway.offset, 11)
        self.controller.submit.assert_not_awaited()
        self.assertEqual(gateway._pending(), [])
        for code, description in ((400, 'TOPIC_CLOSED'), (400, 'chat not found'),
                                  (400, 'message to edit not found'), (403, 'topic not found'),
                                  (503, 'topic not found')):
            gateway._request_sync.return_value = {'ok': False, 'error_code': code, 'description': description}
            with self.assertRaises(TelegramError) as error:
                await gateway._api('sendMessage', {'chat_id': 7})
            self.assertNotEqual(error.exception.reason, 'topic_missing')

    async def test_stop_is_dispatched_without_model_submission(self):
        await self.gateway.handle_update(self.update(3, '/stop'))
        self.controller.stop.assert_awaited_once_with(7)
        self.controller.submit.assert_not_awaited()
        self.assertFalse(self.gateway.events.empty())

    async def test_durable_intake_keeps_stop_responsive_and_cancels_preprocessing(self):
        entered = asyncio.Event()
        self.controller.db = Mock()
        self.controller.db.execute.return_value.fetchone.return_value = None

        async def submit(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        self.controller.submit.side_effect = submit
        await self.gateway._ingest(self.update(10, 'slow attachment'))
        self.assertEqual(self.gateway.offset, 11)
        self.assertEqual(self.gateway._activity[7], {'input:10'})
        self.controller.submit.assert_not_awaited()
        worker = asyncio.create_task(self.gateway._intake_worker(7))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await self.gateway._ingest(self.update(11, 'next message'))
            await asyncio.wait_for(self.gateway._ingest(self.update(12, '/stop')), 1)
            self.controller.stop.assert_awaited_once_with(7)
            statuses = [row[0] for row in self.gateway.db.execute('SELECT status FROM intake ORDER BY update_id')]
            self.assertEqual(statuses, ['cancelled', 'cancelled'])
            self.assertEqual(self.gateway.offset, 13)
            self.assertEqual(self.gateway._activity, {})
            self.gateway.db.execute("INSERT INTO intake VALUES (13,7,'{}','processing')")
            self.controller.db.execute.return_value.fetchone.return_value = (1,)
            self.controller.stop.return_value = 'idle'
            self.gateway._notice = AsyncMock()
            await self.gateway.handle_update(self.update(14, '/stop'))
            self.assertEqual(self.gateway.db.execute('SELECT status FROM intake WHERE update_id=13').fetchone()[0], 'uncertain')
            self.assertIn('Не вдалося підтвердити', self.gateway._notice.call_args.args[1])
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

    async def test_intake_restart_preserves_pending_and_quarantines_processing(self):
        await self.gateway._ingest(self.update(10))
        await self.gateway._ingest(self.update(11))
        with self.gateway.db:
            self.gateway.db.execute("UPDATE intake SET status='processing' WHERE update_id=10")
        restarted = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name))
        self.addCleanup(restarted.db.close)
        self.assertEqual([row[0] for row in restarted.db.execute('SELECT status FROM intake ORDER BY update_id')],
                         ['uncertain', 'pending'])

    async def test_coalesced_output_uses_one_message_then_edits(self):
        gateway = self.gateway
        gateway._api = AsyncMock(return_value={'message_id': 11})
        for event in (
            {'type': 'RUN_STARTED', 'runId': 'run'},
            {'type': 'TEXT_MESSAGE_START', 'runId': 'run', 'messageId': 'm'},
            {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'run', 'messageId': 'm', 'delta': 'Hello'},
        ):
            gateway._apply(7, event)
        await gateway._render_one(*gateway._pending()[0])
        gateway._apply(7, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'run', 'messageId': 'm', 'delta': ' world'})
        gateway._apply(7, {'type': 'TEXT_MESSAGE_END', 'runId': 'run', 'messageId': 'm', 'text': 'Hello world!'})
        gateway._apply(7, {'type': 'RUN_FINISHED', 'runId': 'run'})
        await gateway._render_one(*gateway._pending()[0])
        self.assertEqual([call.args[0] for call in gateway._api.await_args_list], ['sendRichMessage', 'editMessageText'])
        payload = gateway._api.await_args_list[-1].args[1]
        self.assertEqual(payload['message_id'], 11)
        self.assertIn('Hello world!', payload['rich_message']['html'])
        self.assertTrue(payload['rich_message']['skip_entity_detection'])
        self.assertNotIn('parse_mode', payload)
        self.assertEqual(gateway._pending(), [])
        gateway._apply(7, {'type': 'RUN_ERROR', 'message': 'Disconnected'})
        self.assertEqual([chunk['plain'] for chunk in gateway._pending()[0][2]], ['Disconnected\n'])

    async def test_rich_unknown_method_falls_back_and_caches_capability(self):
        gateway = self.gateway
        gateway._request_sync = Mock(side_effect=[
            {'ok': False, 'error_code': 404, 'description': 'Not Found'},
            {'ok': True, 'result': {'message_id': 12}},
            {'ok': True, 'result': {'message_id': 13}}])
        for run_id in ('one', 'two'):
            gateway._apply(7, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': run_id,
                               'messageId': run_id, 'delta': '**Hello** [Oak](https://example.com)'})
            gateway._apply(7, {'type': 'RUN_FINISHED', 'runId': run_id})
            gateway._last_send.clear()
            await gateway._render_one(*gateway._pending()[0])
            gateway._pending()
        self.assertEqual([call.args[0] for call in gateway._request_sync.call_args_list],
                         ['sendRichMessage', 'sendMessage', 'sendMessage'])
        classic = gateway._request_sync.call_args_list[1].args[1]
        self.assertEqual(classic['parse_mode'], 'HTML')
        self.assertEqual(classic['link_preview_options'], {'is_disabled': True})
        self.assertIn('<b>Hello</b>', classic['text'])
        saved = json.loads(gateway.db.execute("SELECT value FROM replies WHERE run_id='one'").fetchone()[0])
        self.assertEqual(saved['parts']['one'], '**Hello** [Oak](https://example.com)')

    async def test_ambiguous_rich_send_never_falls_back(self):
        gateway = self.gateway
        for number, response in enumerate((TelegramError('sendRichMessage'),
                {'ok': False, 'error_code': 503, 'description': 'Unavailable'},
                {'ok': True, 'result': {}})):
            gateway._replies.clear()
            gateway._active.clear()
            gateway._last_send.clear()
            gateway._request_sync = Mock(side_effect=response if isinstance(response, Exception) else None,
                                         return_value=response)
            run_id = 'uncertain-' + str(number)
            gateway._apply(7, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': run_id, 'delta': '**Content**'})
            with self.assertRaises(TelegramError):
                await gateway._render_one(*gateway._pending()[0])
            gateway._request_sync.assert_called_once()
            self.assertEqual(gateway.db.execute('SELECT status FROM replies WHERE run_id=?', (run_id,)).fetchone()[0], 'uncertain')

    async def test_explicit_format_rejection_downgrades_only_one_reply(self):
        gateway = self.gateway
        gateway._request_sync = Mock(side_effect=[
            {'ok': False, 'error_code': 400, 'description': "Bad Request: can't parse rich message"},
            {'ok': True, 'result': {'message_id': 12}}])
        gateway._apply(7, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'format', 'delta': '**Content**'})
        await gateway._render_one(*gateway._pending()[0])
        self.assertTrue(gateway._rich_supported)
        self.assertTrue(gateway._active[7].classic)
        self.assertEqual([call.args[0] for call in gateway._request_sync.call_args_list], ['sendRichMessage', 'sendMessage'])

    async def test_rate_limit_retries_are_bounded(self):
        self.gateway._request_sync = Mock(return_value={'ok': False, 'error_code': 429,
                                                        'parameters': {'retry_after': 1}})
        with patch('oak.telegram.asyncio.sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(TelegramError) as error:
                await self.gateway._api('sendRichMessage', {'chat_id': 7})
        self.assertEqual(error.exception.code, 429)
        self.assertEqual(self.gateway._request_sync.call_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_typing_is_best_effort_and_independent_per_run(self):
        gateway = self.gateway
        entered = asyncio.Event()

        async def fail(*args, **kwargs):
            entered.set()
            raise TelegramError('sendChatAction')

        gateway._api = AsyncMock(side_effect=fail)
        gateway._running = True
        try:
            await gateway.emit(7, {'type': 'RUN_STARTED', 'runId': 'one'})
            await gateway.emit(7, {'type': 'RUN_STARTED', 'runId': 'two'})
            await asyncio.wait_for(entered.wait(), 1)
            worker = gateway._typing_tasks[7]
            gateway._api.assert_awaited_with('sendChatAction', {'chat_id': 7, 'action': 'typing'},
                                             timeout=2, rate_limit_attempts=1)
            await gateway.emit(7, {'type': 'RUN_FINISHED', 'runId': 'one'})
            self.assertFalse(worker.done())
            self.assertEqual(gateway._activity[7], {'run:two'})
            await gateway.emit(7, {'type': 'RUN_ERROR', 'runId': 'two', 'message': 'stopped'})
            self.assertTrue(worker.done())
            self.assertEqual(gateway._typing_tasks, {})
        finally:
            await gateway._activity_remove(7)
            gateway._running = False

    async def test_typing_refresh_uses_deadline_and_gateway_cleanup_awaits_worker(self):
        gateway = self.gateway
        gateway._api = AsyncMock(return_value=True)
        gateway._activity_add(7, 'run:one')
        with patch('oak.telegram.time', SimpleNamespace(monotonic=Mock(side_effect=[10, 12, 12]))):
            with patch('oak.telegram.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await gateway._typing(7)
                sleep.assert_awaited_once_with(2)
        gateway._poll = AsyncMock(side_effect=RuntimeError('disconnected'))
        with self.assertRaises(ExceptionGroup):
            await gateway.run()
        self.assertEqual(gateway._typing_tasks, {})
        self.assertEqual(gateway._activity, {})
        self.assertFalse(gateway._running)

    def test_unicode_chunking(self):
        text = '🙂' * 2050 + ' Україна'
        chunks = split_text(text)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(part.encode('utf-16-le')) // 2 <= 4096 for part in chunks))

    async def test_owner_attachment_is_downloaded_before_submit_without_size_cap(self):
        gateway = self.gateway
        gateway._api = AsyncMock(return_value={'file_path': 'documents/file.pdf', 'file_size': 4})
        gateway._download_sync = Mock()
        update = self.update(10)
        message = update['message']
        message.pop('text')
        message['caption'] = 'Read this'
        message['document'] = {'file_id': 'file', 'file_name': '../../report.pdf',
                               'mime_type': 'application/pdf', 'file_size': 4}
        await gateway.handle_update(update)
        args, kwargs = self.controller.submit.await_args
        self.assertEqual(args, (7, 'Read this', 10))
        self.assertEqual(kwargs['attachments'][0]['name'], 'report.pdf')
        self.assertTrue(Path(kwargs['attachments'][0]['path']).is_relative_to(Path(self.temp.name) / 'inbox'))
        gateway._download_sync.assert_called_once()
        update['update_id'] = 11
        message['document']['file_size'] = 21 * 1024 * 1024
        gateway._api.return_value['file_size'] = message['document']['file_size']
        message['message_thread_id'] = 12
        await gateway.handle_update(update)
        self.assertEqual(self.controller.submit.await_count, 2)
        self.assertEqual(gateway._download_sync.call_count, 2)
        self.assertEqual(self.controller.submit.await_args.args[0], self.controller.sessions.resolve(7, 12))
        self.assertEqual(gateway.offset, 12)

    async def test_local_api_copies_large_attachment_and_rejects_unsafe_paths(self):
        root = Path(self.temp.name) / 'local-api'
        root.mkdir()
        source = root / 'large.mp4'
        with source.open('wb') as file:
            file.write(b'video')
            file.truncate(21 * 1024 * 1024)
        gateway = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name) / 'local-state',
                                  api_url='http://127.0.0.1:8081', local_directory=root)
        self.addCleanup(gateway.db.close)
        gateway._api = AsyncMock(return_value={'file_path': str(source), 'file_size': source.stat().st_size})
        result = await gateway._attachments({'video': {'file_id': 'large', 'file_size': source.stat().st_size}}, 20)
        destination = Path(result[0]['path'])
        self.assertEqual(destination.stat().st_size, source.stat().st_size)
        with destination.open('rb') as file:
            self.assertEqual(file.read(5), b'video')
        gateway._api.assert_awaited_once_with('getFile', {'file_id': 'large'}, timeout=3600)
        (root / 'link').symlink_to(source)
        (root / 'linked-directory').symlink_to(root, target_is_directory=True)
        for path in (root / 'link', root / 'linked-directory' / source.name, root,
                     root / '..' / 'large.mp4', root.parent / 'outside.mp4'):
            with self.subTest(path=path.name), self.assertRaises((ValueError, TelegramError)):
                gateway._download_sync(str(path), destination)
        self.assertEqual(list(destination.parent.glob('.download-*')), [])

    def test_api_endpoint_rejects_remote_or_credential_bearing_overrides(self):
        for url in ('http://api.telegram.org', 'https://example.org', 'http://127.0.0.1:8081/path',
                    'http://user:pass@127.0.0.1:8081', 'http://127.0.0.1:8081?key=value',
                    'http://127.0.0.1:8081#fragment', 'http://127.0.0.1:8081'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name), api_url=url)

    async def test_approval_callback_checks_sender_and_forwards_decision(self):
        gateway = self.gateway
        gateway._api = AsyncMock(return_value=True)
        await gateway.emit(7, {'type': 'CUSTOM', 'name': 'approval_request',
                               'value': {'id': 'req-1', 'summary': 'Write a file?'}})
        payload = json.loads(gateway._deliveries()[0]['payload'])
        self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'approve:req-1')
        update = {'update_id': 5, 'callback_query': {'id': 'cb', 'data': 'approve:req-1',
                  'from': {'id': 8}, 'message': {'chat': {'id': 7, 'type': 'private'}}}}
        await gateway.handle_update(update)
        self.controller.approve.assert_not_awaited()
        update['update_id'] = 6
        update['callback_query']['from']['id'] = 7
        await gateway.handle_update(update)
        self.controller.approve.assert_awaited_once_with(7, 'req-1', True)

    async def test_outbox_restores_final_and_quarantines_ambiguous_send(self):
        gateway = self.gateway
        for event in ({'type': 'RUN_STARTED', 'runId': 'r'},
                      {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'r', 'messageId': 'm', 'delta': 'Done'},
                      {'type': 'RUN_FINISHED', 'runId': 'r'}):
            await gateway.emit(7, event)
        restored = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name))
        self.addCleanup(restored.db.close)
        self.assertEqual([chunk['plain'] for chunk in restored._pending()[0][2]], ['Done\n'])
        restored._api = AsyncMock(side_effect=TelegramError('sendMessage'))
        with self.assertRaises(TelegramError):
            await restored._render_one(*restored._pending()[0])
        restarted = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temp.name))
        self.addCleanup(restarted.db.close)
        self.assertEqual(restarted._pending(), [])
        self.assertEqual(restarted.db.execute('SELECT status FROM replies').fetchone()[0], 'uncertain')

    async def test_artifact_root_validation_and_durable_receipt(self):
        gateway = self.gateway
        root = Path(self.temp.name) / 'artifacts'
        root.mkdir()
        artifact = root / 'report.txt'
        artifact.write_text('generated report')
        await gateway.emit(7, {'type': 'CUSTOM', 'name': 'artifact',
                               'value': {'path': str(artifact), 'caption': 'Report'}})
        gateway._api = AsyncMock(return_value={'message_id': 22, 'document': {'file_id': 'uploaded'}})
        await gateway._render_delivery(gateway._deliveries()[0])
        row = gateway.db.execute('SELECT status,receipt FROM deliveries').fetchone()
        self.assertEqual(row['status'], 'delivered')
        self.assertEqual(json.loads(row['receipt']), {'message_id': 22, 'file_id': 'uploaded'})
        await gateway.emit(7, {'type': 'CUSTOM', 'name': 'artifact',
                               'value': {'path': str(artifact), 'mime': 'image/png'}})
        self.assertEqual(gateway._deliveries()[0]['method'], 'sendPhoto')
        secret = root / 'bot.token'
        secret.write_text('not-a-real-secret')
        with self.assertRaises(ValueError):
            await gateway.emit(7, {'type': 'CUSTOM', 'name': 'artifact', 'value': {'path': str(secret)}})
        link = root / 'linked.txt'
        link.symlink_to(artifact)
        with self.assertRaises(ValueError):
            gateway._artifact_file(link)
        cookies = root / 'browser-profile' / 'Cookies'
        cookies.parent.mkdir()
        cookies.write_bytes(b'private browser state')
        with self.assertRaises(ValueError):
            gateway._artifact_file(cookies)


if __name__ == '__main__':
    unittest.main()
