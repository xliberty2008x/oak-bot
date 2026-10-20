import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.bus import EventBus
from oak.controller import Controller
from oak.events import EventMapper
from oak.interaction import Interactions
from oak.memory import MemoryStore
from oak.runtime import RpcError
from oak.schedule import Scheduler


class StateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

    async def test_tool_upgrade_preserves_selected_image_and_known_output_references_without_file_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = str(Path(folder).resolve())
            photo = workspace + '/inbox/selected-photo.png'
            output = workspace + '/artifacts/selected-video.mp4'
            items = [
                {'type': 'userMessage', 'content': [
                    {'type': 'text', 'text': 'Animate the selected photo.'},
                    {'type': 'localImage', 'path': photo},
                    {'type': 'localImage', 'path': '/private/outside-image.png'},
                    {'type': 'localImage', 'path': workspace + '/.runtime/account.png'},
                    {'type': 'localImage', 'path': workspace + '/secrets/account.png'},
                    {'type': 'localImage', 'path': workspace + '/inbox/../../outside.png'},
                    {'type': 'image', 'url': 'https://example.test/image?private-token'}]},
                {'type': 'dynamicToolCall', 'id': 'sent-file', 'tool': 'oak_send_file', 'status': 'completed', 'success': True,
                 'arguments': json.dumps({'path': output, 'caption': 'Do not copy arbitrary arguments.'})},
                {'type': 'dynamicToolCall', 'id': 'failed-file', 'tool': 'oak_send_file', 'status': 'completed', 'success': False,
                 'arguments': {'path': workspace + '/artifacts/failed.mp4'}},
                {'type': 'dynamicToolCall', 'id': 'unrelated', 'tool': 'unrelated_tool', 'status': 'completed', 'success': True,
                 'arguments': {'path': workspace + '/artifacts/unrelated.mp4'},
                 'contentItems': [{'type': 'inputText', 'text': 'Do not copy generic results.'}]},
                {'type': 'agentMessage', 'text': 'Large previous reply: ' + 'x' * 25000},
            ]
            client = AsyncMock()

            async def request(method, params):
                if method == 'thread/turns/list':
                    self.assertEqual(params, {'threadId': 'previous-thread', 'itemsView': 'summary',
                                              'limit': 12, 'sortDirection': 'desc'})
                    return {'data': [{'id': 'previous-turn', 'items': [
                        item for item in items if item['type'] != 'dynamicToolCall']}], 'nextCursor': 'older-history'}
                if method == 'thread/start':
                    return {'model': params['model'], 'modelProvider': 'openai', 'thread': {'id': 'upgraded-thread'}}
                if method == 'turn/start':
                    return {'turn': {'id': 'new-turn'}}
                raise AssertionError(method)

            client.request.side_effect = request
            controller = Controller(client, ':memory:', folder, AsyncMock())
            try:
                controller.tools = SimpleNamespace(version='oak-v16-browser-capabilities', specs=[], computer=None,
                                                   browser_action_approval='never')
                controller.threads[7] = 'previous-thread'
                controller.db.execute('INSERT INTO chats(chat_id,thread_id,tools_version) VALUES (?,?,?)',
                                      (7, 'previous-thread', 'oak-v15-a2ui-display-cards'))
                controller.db.execute('INSERT INTO conversation_models(chat_id,model,effort) VALUES (?,?,?)',
                                      (7, 'synthetic-choice', 'ultra'))
                bus, mapper = EventBus(controller), EventMapper()
                for item in items:
                    if item['type'] == 'dynamicToolCall':
                        for event in mapper.feed({'method': 'item/completed', 'params': {
                                'threadId': 'previous-thread', 'turnId': 'previous-turn', 'item': item}}):
                            await bus.emit(7, event)
                with (patch.object(Path, 'resolve', side_effect=AssertionError('No runtime path resolution')),
                        patch.object(Path, 'read_bytes', side_effect=AssertionError('No attachment reads')),
                        patch.object(Path, 'stat', side_effect=AssertionError('No attachment stat'))):
                    await controller._thread(7)
                params = next(call.args[1] for call in client.request.await_args_list if call.args[0] == 'thread/start')
                context = params['developerInstructions']
                self.assertIn(photo, context)
                self.assertIn(output, context)
                self.assertIn('file_queued_for_delivery', context)
                for omitted in ('outside-image.png', 'account.png', '../../', 'private-token', 'failed.mp4',
                                'unrelated.mp4', 'Do not copy arbitrary arguments.', 'Do not copy generic results.'):
                    self.assertNotIn(omitted, context)
                self.assertEqual(params['model'], 'synthetic-choice')
                self.assertEqual(params['dynamicTools'], [])
                self.assertEqual([call.args[0] for call in client.request.await_args_list],
                                 ['thread/turns/list', 'thread/start'])
                self.assertEqual(controller.model_settings(7), {'model': 'synthetic-choice', 'effort': 'ultra'})
                record = controller.db.execute('SELECT tools_version,previous_thread_id FROM chats WHERE chat_id=7').fetchone()
                self.assertEqual(tuple(record), ('oak-v16-browser-capabilities', 'previous-thread'))
                await controller.submit(7, 'Continue using that selected photo.', 17)
                turn = client.request.await_args.args[1]
                self.assertEqual((turn['threadId'], turn['model'], turn['effort']),
                                 ('upgraded-thread', 'synthetic-choice', 'ultra'))
            finally:
                controller.close()

    async def test_historic_output_references_require_complete_scoped_success_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(AsyncMock(), ':memory:', folder, AsyncMock())
            self.addCleanup(controller.close)
            bus = EventBus(controller)
            workspace = str(Path(folder).resolve())

            async def record(chat=7, thread='selected-thread', turn='selected-turn', name='selected',
                             *, include_start=True, include_args=True, success=True):
                mapper = EventMapper()
                item = {'type': 'dynamicToolCall', 'id': name, 'tool': 'oak_send_file',
                        'status': 'completed', 'success': success,
                        'arguments': {'path': workspace + '/artifacts/' + name + '.mp4'}}
                for event in mapper.feed({'method': 'item/completed', 'params': {
                        'threadId': thread, 'turnId': turn, 'item': item}}):
                    if event['type'] == 'TOOL_CALL_START' and not include_start:
                        continue
                    if event['type'] == 'TOOL_CALL_ARGS' and not include_args:
                        continue
                    await bus.emit(chat, event)

            await record()
            await record(chat=8, name='other-owner')
            await record(thread='other-thread', name='other-thread')
            await record(turn='older-turn', name='older-turn')
            await record(name='failed', success=False)
            await record(name='missing-start', include_start=False)
            await record(name='missing-args', include_args=False)
            self.assertEqual(controller._history_output_files(7, 'selected-thread', ['selected-turn']), [
                {'kind': 'file_queued_for_delivery', 'path': workspace + '/artifacts/selected.mp4'}])
            self.assertEqual(controller._history_output_files(7, 'selected-thread', []), [])
            # A busy history whose receipt start fell outside the bounded
            # window cannot authorize reconstructing that tool's arguments.
            with controller.db:
                filler = json.dumps({'type': 'TOOL_CALL_ARGS', 'threadId': 'selected-thread',
                                     'runId': 'selected-turn', 'toolCallId': 'filler', 'delta': '{}'})
                controller.db.executemany(
                    'INSERT INTO ui_events(chat_id,run_id,event) VALUES (?,?,?)',
                    [(7, 'selected-turn', filler)] * 768)
            self.assertEqual(controller._history_output_files(7, 'selected-thread', ['selected-turn']), [])

    async def test_tool_upgrade_keeps_bounded_summary_turns_in_chronological_order(self):
        client = AsyncMock()

        async def request(method, params):
            if method == 'thread/turns/list':
                self.assertEqual(params['itemsView'], 'summary')
                self.assertEqual(params['limit'], 12)
                self.assertEqual(params['sortDirection'], 'desc')
                return {'data': [
                    {'id': 'newer', 'items': [{'type': 'agentMessage', 'text': 'NEWER_RESULT'}]},
                    {'id': 'older', 'items': [{'type': 'userMessage', 'content': [
                        {'type': 'text', 'text': 'OLDER_REQUEST'}]}]},
                ], 'nextCursor': 'deliberately-not-hydrated'}
            self.assertEqual(method, 'thread/start')
            return {'model': params['model'], 'modelProvider': 'openai', 'thread': {'id': 'new'}}

        client.request.side_effect = request
        controller = Controller(client, ':memory:', '.', AsyncMock())
        self.addCleanup(controller.close)
        controller.tools = SimpleNamespace(version='new-tools', specs=[], computer=None,
                                           browser_action_approval='never')
        controller.threads[7] = 'old'
        controller.db.execute('INSERT INTO chats(chat_id,thread_id,tools_version) VALUES (?,?,?)',
                              (7, 'old', 'old-tools'))
        await controller._thread(7)
        context = client.request.await_args.args[1]['developerInstructions']
        self.assertLess(context.index('OLDER_REQUEST'), context.index('NEWER_RESULT'))
        self.assertEqual([call.args[0] for call in client.request.await_args_list],
                         ['thread/turns/list', 'thread/start'])

    async def test_steer_reconciliation_starts_only_after_exact_terminal_turn_metadata(self):
        for status in ('completed', 'interrupted', 'failed', 'absent'):
            with self.subTest(status=status):
                client = AsyncMock()
                controller = Controller(client, ':memory:', '.', AsyncMock())
                self.addCleanup(controller.close)
                controller._thread = AsyncMock(return_value='selected-thread')
                controller.threads[7] = 'selected-thread'
                controller.active[7] = 'selected-turn'

                async def request(method, params):
                    if method == 'turn/steer':
                        raise RpcError(-1, 'no active turn')
                    if method == 'thread/turns/list':
                        self.assertEqual(params, {'threadId': 'selected-thread', 'itemsView': 'notLoaded',
                                                  'limit': 12, 'sortDirection': 'desc'})
                        return {'data': [{'id': 'other-turn' if status == 'absent' else 'selected-turn',
                                          'status': 'completed' if status == 'absent' else status}],
                                'nextCursor': 'older-history-not-needed'}
                    self.assertEqual(method, 'turn/start')
                    return {'turn': {'id': 'replacement-turn'}}

                client.request.side_effect = request
                with patch('oak.controller.asyncio.sleep', new_callable=AsyncMock):
                    if status == 'absent':
                        with self.assertRaises(RuntimeError):
                            await controller.submit(7, 'Synthetic follow-up', 101)
                        self.assertEqual(controller.active[7], 'selected-turn')
                        self.assertNotIn('turn/start', [call.args[0] for call in client.request.await_args_list])
                        self.assertEqual(controller.db.execute(
                            'SELECT status FROM inputs').fetchone()[0], 'uncertain')
                    else:
                        self.assertTrue(await controller.submit(7, 'Synthetic follow-up', 101))
                        self.assertEqual(controller.active[7], 'replacement-turn')
                        self.assertEqual(controller.db.execute(
                            'SELECT status FROM inputs').fetchone()[0], 'accepted')
                self.assertEqual(sum(call.args[0] == 'thread/turns/list'
                                     for call in client.request.await_args_list), 1)
                self.assertNotIn('thread/read', [call.args[0] for call in client.request.await_args_list])

    def test_history_file_references_are_lexical_workspace_paths(self):
        from oak.controller import _history_file
        with patch.object(Path, 'resolve', side_effect=AssertionError('No filesystem resolution')):
            self.assertEqual(_history_file('inbox/photo.png', '/workspace'), '/workspace/inbox/photo.png')
            for value in ('/workspace-two/photo.png', '/private/photo.png', '../photo.png',
                          '/workspace', '/workspace/browser-profile/Cookies', '.hidden/photo.png',
                          'inbox/../photo.png', 'inbox/line\nphoto.png', '', None, 'x' * 4097):
                self.assertIsNone(_history_file(value, '/workspace'), value)

    async def test_selected_model_persists_per_topic_and_keeps_existing_thread_history(self):
        client = AsyncMock()

        async def request(method, params):
            if method == 'experimentalFeature/list':
                return {'data': [{'name': 'fast_mode', 'enabled': True}], 'nextCursor': None}
            if method == 'model/list':
                if params.get('cursor'):
                    return {'data': [{'model': 'synthetic-choice', 'displayName': 'Duplicate', 'hidden': False,
                                      'defaultReasoningEffort': 'medium'}], 'nextCursor': None}
                return {'data': [
                    {'model': 'gpt-6.1-sol', 'displayName': 'Default', 'hidden': False, 'defaultReasoningEffort': 'low',
                     'additionalSpeedTiers': ['fast'],
                     'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]},
                    {'model': 'synthetic-choice', 'displayName': 'Synthetic choice', 'hidden': False, 'defaultReasoningEffort': 'medium',
                     'serviceTiers': [{'id': 'priority', 'name': 'Fast', 'description': 'Synthetic tier'}, {'id': ['bad']}],
                     'supportedReasoningEfforts': [{'reasoningEffort': value} for value in
                                                  ['low', 'medium', 'ultra', 'ultra', '../bad', 'bad effort', '\x7f', 'x' * 65, 42]]},
                    {'model': 'hidden-choice', 'displayName': 'Hidden', 'hidden': True}, {'model': 42}], 'nextCursor': 'next'}
            if method == 'thread/resume':
                return {'model': params['model'], 'modelProvider': 'openai', 'thread': {'id': params['threadId']}}
            if method == 'turn/start':
                return {'turn': {'id': 'selected-turn'}}
            if method == 'turn/steer':
                return {}
            raise AssertionError(method)

        client.request.side_effect = request
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / 'models.sqlite'
            controller = Controller(client, db_path, folder, AsyncMock())
            try:
                self.assertTrue(hasattr(controller, 'set_model'), 'An explicit model choice must be saved per conversation.')
                topic = controller.sessions.resolve(7, 12)
                controller.threads[topic] = 'history-thread'
                controller.loaded.add('history-thread')
                with controller.db:
                    controller.db.execute('INSERT INTO chats(chat_id,thread_id) VALUES (?,?)', (topic, 'history-thread'))
                catalogue = await controller.model_catalog()
                self.assertEqual([item['model'] for item in catalogue], ['gpt-6.1-sol', 'synthetic-choice'])
                self.assertEqual(catalogue[1]['efforts'], ['low', 'medium', 'ultra'])
                self.assertEqual([item['turbo_available'] for item in catalogue], [False, True])
                self.assertIsNone(controller.turbo_for(7))
                self.assertEqual(controller.effort_for(7), 'low')
                controller.config['runtime_config'] = {'model_reasoning_effort': 'high'}
                self.assertEqual(controller.effort_for(7), 'high')
                controller.config['runtime_config']['model_reasoning_effort'] = '/bad/config'
                self.assertIsNone(controller.effort_for(7))
                controller.config.clear()
                self.assertEqual(await controller.set_model(topic, 'synthetic-choice'), {'model': 'synthetic-choice', 'effort': 'medium'})
                for model, effort in [('synthetic-choice', 'unsupported'), ('gpt-6.1-sol', 'ultra')]:
                    with self.assertRaises(ValueError):
                        await controller.set_model(topic, model, effort)
                self.assertEqual(await controller.set_model(topic, 'synthetic-choice', 'ultra'), {'model': 'synthetic-choice', 'effort': 'ultra'})
                await controller.set_model(topic, 'synthetic-choice', turbo=True)
                self.assertEqual(await controller.set_model(topic, 'synthetic-choice'), {'model': 'synthetic-choice', 'effort': 'ultra'})
                self.assertTrue(controller.turbo_for(topic))
                with self.assertRaises(ValueError):
                    await controller.set_model(topic, 'gpt-6.1-sol', 'low')
                self.assertEqual(controller.model_for(7), 'gpt-6.1-sol')
                self.assertEqual(controller.model_for(controller.sessions.resolve(8, 12)), 'gpt-6.1-sol')
                await controller.submit(topic, 'continue existing history', 10)
                payload = client.request.await_args.args[1]
                self.assertEqual((payload['threadId'], payload['model'], payload['effort']), ('history-thread', 'synthetic-choice', 'ultra'))
                self.assertEqual(payload['serviceTierForTurn'], 'priority')
                self.assertNotIn('serviceTier', payload)
                flags = next(call.args[1] for call in reversed(client.request.await_args_list)
                             if call.args[0] == 'experimentalFeature/list')
                self.assertEqual(flags['threadId'], 'history-thread')
                await controller.submit(topic, 'steer existing work', 11)
                self.assertEqual(client.request.await_args.args[0], 'turn/steer')
                self.assertNotIn('model', client.request.await_args.args[1])
                self.assertNotIn('effort', client.request.await_args.args[1])
                self.assertNotIn('serviceTierForTurn', client.request.await_args.args[1])
            finally:
                controller.close()
            restarted = Controller(client, db_path, folder, AsyncMock())
            try:
                self.assertEqual(restarted.model_settings(topic), {'model': 'synthetic-choice', 'effort': 'ultra'})
                self.assertEqual(restarted.effort_for(topic), 'ultra')
                self.assertTrue(restarted.turbo_for(topic))
                await restarted.submit(topic, 'after restart', 12)
                self.assertEqual(client.request.await_args.args[1]['effort'], 'ultra')
                self.assertEqual(client.request.await_args.args[1]['serviceTierForTurn'], 'priority')
                resume = next(call.args[1] for call in client.request.await_args_list if call.args[0] == 'thread/resume')
                self.assertEqual((resume['threadId'], resume['model']), ('history-thread', 'synthetic-choice'))
                self.assertEqual(restarted.threads[topic], 'history-thread')
                restarted.active.clear()
                async def disabled_fast(method, params):
                    return {'data': [{'name': 'fast_mode', 'enabled': False}]} if method == 'experimentalFeature/list' else await request(method, params)
                client.request.side_effect = disabled_fast
                starts = sum(call.args[0] == 'turn/start' for call in client.request.await_args_list)
                with self.assertRaises(RpcError):
                    await restarted.submit(topic, 'unavailable fast must not silently run standard', 13)
                self.assertEqual(sum(call.args[0] == 'turn/start' for call in client.request.await_args_list), starts)
                self.assertEqual(restarted.db.execute('SELECT status FROM inputs WHERE update_id=13').fetchone()[0], 'failed')
                client.request.side_effect = request
                await restarted.set_model(topic, 'synthetic-choice', turbo=False)
                await restarted.submit(topic, 'explicit standard speed', 14)
                self.assertEqual(client.request.await_args.args[1]['serviceTierForTurn'], 'default')
                self.assertFalse(restarted.turbo_for(topic))
                self.assertIsNone(restarted.turbo_for(7))
            finally:
                restarted.close()

    async def test_model_changes_reject_unlisted_choices_and_accepted_work(self):
        client = AsyncMock()
        client.request.return_value = {'data': [{'model': 'synthetic-choice', 'displayName': 'Choice',
                                                'hidden': False, 'defaultReasoningEffort': 'medium'}]}
        controller = Controller(client, ':memory:', '.', AsyncMock())
        self.addCleanup(controller.close)
        self.assertTrue(hasattr(controller, 'set_model'), 'Model changes need validation before saving.')
        for slug in ('', 'not-listed'):
            with self.assertRaises(ValueError):
                await controller.set_model(7, slug)
        controller.active[7] = 'working'
        with self.assertRaises(RuntimeError):
            await controller.set_model(7, 'synthetic-choice')
        controller.active.clear()
        for status in ('pending', 'dispatching'):
            with controller.db:
                controller.db.execute('INSERT OR REPLACE INTO inputs(update_id,chat_id,text,status) VALUES (1,7,?,?)', ('accepted input', status))
            with self.assertRaises(RuntimeError):
                await controller.set_model(7, 'synthetic-choice')
        controller.db.execute('DELETE FROM inputs')
        job = controller.scheduler.add(7, 'accepted scheduled work', delay_seconds=0, mode='run')
        controller.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        with self.assertRaises(RuntimeError):
            await controller.set_model(7, 'synthetic-choice')
        controller.db.execute("UPDATE jobs SET status='done' WHERE id=?", (job,))
        intake = sqlite3.connect(':memory:')
        self.addCleanup(intake.close)
        intake.execute('CREATE TABLE intake(chat_id INTEGER,status TEXT)')
        controller.telegram = SimpleNamespace(db=intake)
        for status in ('pending', 'processing'):
            intake.execute('DELETE FROM intake')
            intake.execute('INSERT INTO intake VALUES (7,?)', (status,))
            with self.assertRaises(RuntimeError):
                await controller.set_model(7, 'synthetic-choice')
        self.assertEqual(controller.model_settings(7), {'model': 'gpt-6.1-sol', 'effort': None})
        client.request.side_effect = ConnectionError('synthetic private diagnostics')
        with self.assertRaises(RpcError) as failed:
            await controller.model_catalog()
        self.assertNotIn('private', str(failed.exception))

    async def test_telegram_topics_keep_legacy_and_owner_scoped_state_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / 'state.sqlite'
            controller = Controller(AsyncMock(), db_path, folder, AsyncMock())
            self.assertTrue(hasattr(controller, 'sessions'), 'Topic routing must persist with the controller.')
            try:
                sessions = controller.sessions
                self.assertEqual(sessions.resolve(7), 7)
                self.assertEqual(sessions.resolve(7, 1), 7)
                topic = sessions.resolve(7, 12, name='Дослідження')
                other = sessions.resolve(8, 12, name='Інша розмова')
                self.assertLess(topic, 0)
                self.assertNotEqual(topic, other)
                self.assertEqual(sessions.destination(topic), {'chat_id': 7, 'message_thread_id': 12})
                self.assertIsNone(sessions.destination(-1))
                self.assertEqual(sessions.lookup(7, 'topic:12'), topic)
                sessions.resolve(8, 13)
                self.assertIsNone(sessions.lookup(7, 'topic:13'))
                self.assertIsNone(sessions.lookup(8, 'topic:14'))
                controller.memory.remember(7, 'General only')
                controller.memory.remember(topic, 'Topic only')
                controller.scheduler.add(topic, 'Topic task', delay_seconds=60)
                sessions.resolve(7, 12, closed=True)
            finally:
                controller.close()
            restarted = Controller(AsyncMock(), db_path, folder, AsyncMock())
            try:
                self.assertEqual(restarted.sessions.resolve(7, 12), topic)
                self.assertEqual(restarted.sessions.list(7)[1]['name'], 'Дослідження')
                self.assertTrue(restarted.sessions.list(7)[1]['closed'])
                self.assertEqual([note['text'] for note in restarted.memory.search(7)], ['General only'])
                self.assertEqual([note['text'] for note in restarted.memory.search(topic)], ['Topic only'])
                self.assertEqual(restarted.scheduler.list(7), [])
                self.assertEqual(len(restarted.scheduler.list(topic)), 1)
                restarted.web = SimpleNamespace(public_url='https://oak.example/?view=status')
                await restarted.command(topic, '/web', 1)
                self.assertEqual(restarted.emit.await_args.args[0], topic)
                self.assertEqual(restarted.emit.await_args.args[1]['value']['url'],
                                 'https://oak.example/?view=status&conversation=topic%3A12')
                from oak.tools import Tools
                tools = Tools(restarted, {'computer': {'enabled': True, 'display': ':99'}})
                await tools.set_computer_enabled(7, False)
                self.assertFalse(tools.computer_status(topic)['enabled'])
                self.assertTrue(tools.computer_status(other)['enabled'])
            finally:
                restarted.close()

    async def test_thread_open_failure_is_not_replayed_on_recovery(self):
        for error, status in ((RpcError(-32000, 'Cannot resume'), 'failed'),
                              (ConnectionError('Disconnected'), 'uncertain')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                client = SimpleNamespace(request=AsyncMock(side_effect=error))
                controller = Controller(client, Path(folder) / 'state.sqlite', folder, AsyncMock())
                try:
                    with self.assertRaises(type(error)):
                        await controller.submit(7, 'one task', 1)
                    self.assertEqual(controller.db.execute('SELECT status FROM inputs').fetchone()[0], status)
                    await controller.recover()
                    client.request.assert_awaited_once()
                finally:
                    controller.close()

    async def test_desktop_remains_owned_until_the_active_turn_finishes(self):
        from oak.tools import Tools
        with tempfile.TemporaryDirectory() as folder:
            controller = SimpleNamespace(cwd=folder, db=self.db, active={7: 'first', 8: 'second'})
            tools = Tools(controller, {'computer': {'enabled': True, 'display': ':99'}})
            tools.computer.run = AsyncMock(return_value={})
            await tools.execute(7, 'oak_computer', {'action': 'screenshot'}, {'turnId': 'first'})
            with self.assertRaises(RuntimeError):
                await tools.execute(8, 'oak_computer', {'action': 'click'}, {'turnId': 'second'})
            controller.active.pop(7)
            await tools.execute(8, 'oak_computer', {'action': 'screenshot'}, {'turnId': 'second'})
            self.assertEqual(tools.computer.run.await_count, 2)

    async def test_desktop_switch_cancels_input_and_persists_owner_permission(self):
        from oak.tools import Tools
        with tempfile.TemporaryDirectory() as folder:
            controller = SimpleNamespace(cwd=folder, db=self.db, active={7: 'first'}, stop=AsyncMock())
            config = {'computer': {'enabled': True, 'display': ':99'}}
            tools = Tools(controller, config)
            running = asyncio.Event()

            async def action(**args):
                running.set()
                await asyncio.Event().wait()

            tools.computer.run = action
            task = asyncio.create_task(tools.execute(7, 'oak_computer', {'action': 'type'}, {'turnId': 'first'}))
            await running.wait()
            self.assertFalse((await tools.set_computer_enabled(7, False))['enabled'])
            self.assertTrue(task.cancelled())
            controller.stop.assert_awaited_once_with(7)
            reopened = Tools(controller, config)
            self.assertFalse(reopened.computer_status(7)['enabled'])
            self.assertTrue(reopened.computer_status(8)['enabled'])
            self.db.execute('CREATE TABLE web_conversations (chat_id INTEGER, owner INTEGER)')
            self.db.execute('INSERT INTO web_conversations VALUES (-10,7)')
            self.assertFalse(reopened.computer_status(-10)['enabled'])
            with self.assertRaises(ValueError):
                await reopened.execute(7, 'oak_computer', {'action': 'click'}, {'turnId': 'first'})
            self.assertTrue((await reopened.set_computer_enabled(7, True))['enabled'])

    async def test_desktop_rejects_invalid_coordinates_before_sending_input(self):
        from oak.computer import ComputerTools
        with self.assertRaises(ValueError):
            ComputerTools('.', 'remote.example:0')
        desktop = ComputerTools('.', ':99')
        desktop._geometry = AsyncMock(return_value=(1280, 800))
        desktop._exec = AsyncMock()
        for args in ({'action': 'click', 'x': 1280, 'y': 0},
                     {'action': 'drag', 'x': 10, 'y': 10, 'end_x': -1, 'end_y': 20},
                     {'action': 'key', 'key': 'Return; touch file'}):
            with self.assertRaises(ValueError):
                await desktop.run(**args)
        desktop._exec.assert_not_awaited()

    async def test_managed_desktop_is_explicit_and_rejects_bad_configuration(self):
        from oak.desktop import ManagedDesktop
        with tempfile.TemporaryDirectory() as folder:
            config = {'state_dir': folder, 'workspace': folder,
                      'computer': {'enabled': True, 'display': ':99'}}
            async with ManagedDesktop(config) as desktop:
                self.assertFalse(desktop.enabled)
                self.assertEqual(desktop.processes, [])
            for settings in ({'display': 'remote:0'}, {'width': 0}, {'height': True}):
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    ManagedDesktop({**config, 'computer': {'enabled': True, 'managed': True, **settings}})

    def test_selected_memory_import_is_atomic_and_chat_scoped(self):
        memory = MemoryStore(self.db)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'selected.json'
            path.write_text(json.dumps({'profile': 'Oak likes trees', 'logs': {'one': 'Oak speaks Ukrainian'}}))
            ids = memory.import_file(7, path)
            memory.remember(8, 'Oak other conversation')
            self.assertEqual(len(memory.search(7, 'Oak')), 2)
            self.assertEqual(len(memory.search(8, 'Oak')), 1)
            self.assertFalse(memory.forget(8, ids[0]))
            for invalid in ([], {'profile': 'valid', 'logs': {'large': 'x' * 32001}}, {'transcripts': []}):
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    memory.import_file(7, path)
            self.assertEqual(len(memory.search(7)), 2)

    async def test_approval_is_single_use_scoped_and_cancelled_durably(self):
        controller = SimpleNamespace(chat_for_thread=lambda thread: 7, emit=AsyncMock())
        interactions = Interactions(self.db, controller)
        metadata = {'threadId': 'thread', 'turnId': 'turn'}
        task = asyncio.create_task(interactions.ask(metadata, 'approval', 'Allow?'))
        await asyncio.sleep(0)
        request_id = controller.emit.await_args.args[1]['value']['id']
        with self.assertRaises(ValueError):
            interactions.resolve(8, request_id, True, 'approval')
        interactions.resolve(7, request_id, True, 'approval')
        with self.assertRaises(ValueError):
            interactions.resolve(7, request_id, True, 'approval')
        self.assertTrue(await task)
        task = asyncio.create_task(interactions.ask(metadata, 'input', 'Question?'))
        await asyncio.sleep(0)
        request_id = controller.emit.await_args.args[1]['value']['id']
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.db.execute('SELECT status FROM interactions WHERE id=?', (request_id,)).fetchone()[0], 'cancelled')
        interactions.ask = AsyncMock(return_value='{"name":"Oak"}')
        response = await interactions.user_input({'method': 'mcpServer/elicitation/request', 'mode': 'form',
                                                  'message': 'Name', 'requestedSchema': {'type': 'object'}})
        self.assertEqual(response, {'action': 'decline', 'content': None})
        interactions.ask.assert_not_awaited()

    async def test_schedule_claim_cancel_and_recurrence_wait_for_completion(self):
        self.db.execute('CREATE TABLE turns(turn_id TEXT PRIMARY KEY,status TEXT)')
        controller = SimpleNamespace(active={}, notice=AsyncMock())

        async def submit(chat_id, text, update_id, idle_only):
            self.assertTrue(idle_only)
            controller.active[chat_id] = 'turn'
            with self.db:
                self.db.execute("INSERT INTO turns VALUES ('turn','inProgress')")
            return True

        controller.submit = AsyncMock(side_effect=submit)
        scheduler = Scheduler(self.db, controller)
        for invalid in (float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                scheduler.add(7, 'invalid', delay_seconds=0, interval_seconds=invalid)
        cancelled = scheduler.add(7, 'cancel me', delay_seconds=0)
        self.assertFalse(scheduler.cancel(8, cancelled))
        self.assertTrue(scheduler.cancel(7, cancelled))
        job = scheduler.add(7, 'work', delay_seconds=0, interval_seconds=60, mode='run')
        await scheduler.tick()
        await scheduler.tick()
        self.assertEqual(scheduler.list(7)[0]['status'], 'running')
        controller.submit.assert_awaited_once()
        with self.db:
            self.db.execute("UPDATE turns SET status='completed'")
        await scheduler.tick()
        self.assertEqual(scheduler.list(7)[0]['status'], 'pending')
        self.assertTrue(scheduler.cancel(7, job))
        self.assertEqual(Scheduler(self.db, controller).list(7), [])

    async def test_interrupted_schedule_is_quarantined_after_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite3'
            controller = SimpleNamespace(active={}, notice=AsyncMock())
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE turns(turn_id TEXT PRIMARY KEY,status TEXT)')
                scheduler = Scheduler(db, controller)
                job = scheduler.add(7, 'one task', delay_seconds=0)
                db.execute("UPDATE jobs SET status='running'")
            db.close()
            with sqlite3.connect(path) as db:
                scheduler = Scheduler(db, controller)
                await scheduler.recover()
                self.assertEqual(scheduler.list(7)[0]['status'], 'uncertain')
                self.assertTrue(scheduler.cancel(7, job))
                self.assertEqual(scheduler.list(7), [])
            db.close()


if __name__ == '__main__':
    unittest.main()
