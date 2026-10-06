import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.interaction import Interactions
from oak.memory import MemoryStore
from oak.runtime import RpcError
from oak.schedule import Scheduler


class StateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

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
        self.assertEqual(response, {'action': 'accept', 'content': {'name': 'Oak'}})

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
