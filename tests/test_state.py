import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.interaction import Interactions
from oak.memory import MemoryStore
from oak.schedule import Scheduler


class StateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

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
