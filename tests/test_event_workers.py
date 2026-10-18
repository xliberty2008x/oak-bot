import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.bus import EventBus
from oak.controller import Controller


class EventWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.client = SimpleNamespace(events=asyncio.Queue())
        self.controller = Controller(self.client, ':memory:', Path(self.folder.name), AsyncMock())
        self.addCleanup(self.controller.close)
        self.controller.threads.update({11: 'a', 22: 'b'})
        self.controller.bus = EventBus(self.controller)
        self.controller.emit = self.controller.bus.emit

    def put(self, thread, delta, run='run', message='same-id'):
        self.client.events.put_nowait({'method': 'item/agentMessage/delta', 'params': {
            'threadId': thread, 'turnId': run, 'itemId': message, 'delta': delta}})

    async def start(self):
        task = asyncio.create_task(self.controller.run_events())
        async def stop():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(stop)
        return task

    async def test_blocked_conversation_does_not_hold_other_deltas_and_order_is_preserved(self):
        c = self.controller
        await c._lock(11).acquire()
        self.addAsyncCleanup(self.release, c._lock(11))
        await self.start()
        async with c.bus.subscribe(22) as other, c.bus.subscribe(11) as first:
            self.put('a', 'one ')
            self.put('b', 'other ')
            self.put('a', 'two')
            self.put('b', 'topic')
            for _ in range(4):
                await asyncio.wait_for(other.get(), 1)
            self.assertEqual(c.bus.replay(11), [])
            self.assertEqual(''.join(r['event'].get('delta', '') for r in c.bus.replay(22)), 'other topic')
            c._lock(11).release()
            for _ in range(4):
                await asyncio.wait_for(first.get(), 1)
            self.assertEqual(''.join(r['event'].get('delta', '') for r in c.bus.replay(11)), 'one two')

    async def release(self, lock):
        if lock.locked():
            lock.release()

    async def test_disconnect_drains_received_terminal_event_and_resets_partial_assembly(self):
        c = self.controller
        c.active.update({11: 'run', 22: 'remaining'})
        self.put('a', 'finished')
        self.put('b', 'partial', run='remaining')
        self.client.events.put_nowait({'method': 'turn/completed', 'params': {
            'threadId': 'a', 'turn': {'id': 'run', 'status': 'completed', 'items': []}}})
        self.client.events.put_nowait({'method': 'client/disconnected'})
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            await asyncio.wait_for(c.run_events(), 1)
        self.assertEqual(c.bus.replay(11)[-1]['event']['type'], 'RUN_FINISHED')
        terminal = c.bus.replay(22)[-1]['event']
        self.assertEqual((terminal['type'], terminal['threadId'], terminal['runId']), ('RUN_ERROR', 'b', 'remaining'))
        self.assertEqual(c.mapper._runs, {})
        c.active.clear()
        self.put('a', 'fresh')
        self.client.events.put_nowait({'method': 'client/disconnected'})
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            await c.run_events()
        self.assertEqual(c.bus.replay(11)[-3]['event']['type'], 'RUN_STARTED')
        self.assertEqual(c.bus.replay(11)[-1]['event']['delta'], 'fresh')

    async def test_worker_failure_cancels_and_awaits_other_worker(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def emit(chat, event):
            if chat == 11:
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cleaned.set()
            raise ValueError('synthetic failure')
        self.controller.emit = emit
        pump = await self.start()
        self.put('a', 'waiting')
        await asyncio.wait_for(entered.wait(), 1)
        self.put('b', 'fails')
        with self.assertRaises(ExceptionGroup) as error:
            await asyncio.wait_for(pump, 1)
        self.assertIsInstance(error.exception.exceptions[0], ValueError)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(self.controller.mapper._runs, {})

    async def test_cancel_awaits_worker_and_old_thread_is_rechecked_after_lock(self):
        c = self.controller
        await c._lock(11).acquire()
        pump = await self.start()
        self.put('a', 'old')
        self.put('b', 'alive')
        async with c.bus.subscribe(22) as queue:
            await asyncio.wait_for(queue.get(), 1)
        c.threads[11] = 'replacement'
        c._lock(11).release()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(c.bus.replay(11), [])
        pump.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pump
        self.assertEqual(c.mapper._runs, {})

    async def test_backlog_fails_closed_instead_of_dropping_events(self):
        c = self.controller
        await c._lock(11).acquire()
        self.addAsyncCleanup(self.release, c._lock(11))
        pump = await self.start()
        for _ in range(514):
            self.put('a', 'blocked')
        with self.assertRaises(ExceptionGroup) as error:
            await asyncio.wait_for(pump, 1)
        self.assertIn('backlog exceeded', str(error.exception.exceptions[0]))
        self.assertEqual(c.bus.replay(11), [])
        self.assertEqual(c.mapper._runs, {})


if __name__ == '__main__':
    unittest.main()
