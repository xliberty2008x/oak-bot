"""Large worker responses survive real socket backpressure and cancellation."""

import asyncio
import json
import os
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak import executor_worker as worker


class WorkerWireTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        first.setblocking(False)
        second.setblocking(False)
        self.receiver = second
        self.output = os.fdopen(os.dup(first.fileno()), 'wb', buffering=0)
        self.addCleanup(self.output.close)

    async def receive_line(self):
        chunks = []
        loop = asyncio.get_running_loop()
        while True:
            data = await loop.sock_recv(self.receiver, 4096)
            if not data:
                raise EOFError('Wire closed before complete response')
            chunks.append(data)
            if data.endswith(b'\n'):
                return b''.join(chunks)
            # Deliberately drain incrementally; the server must wait for
            # writability repeatedly rather than assuming one successful write.
            await asyncio.sleep(0)

    async def test_megabyte_response_is_complete_then_next_request_works(self):
        payload = 'actual-image-evidence-' * 90000
        fake = SimpleNamespace(dispatch=AsyncMock(side_effect=[{'evidence': payload}, {'ready': True}]), close=AsyncMock())
        reader = asyncio.StreamReader()
        reader.feed_data(worker._encode({'id': 'large', 'op': 'quality.inspect', 'args': {}}))
        with patch.object(worker, 'Worker', return_value=fake):
            serving = asyncio.create_task(worker.serve({}, reader, self.output))
            try:
                response = await asyncio.wait_for(self.receive_line(), 5)
                self.assertGreater(len(response), 1024 * 1024)
                self.assertEqual(json.loads(response), {'id': 'large', 'ok': True, 'result': {'evidence': payload}})
                reader.feed_data(worker._encode({'id': 'next', 'op': 'hello', 'args': {}}))
                again = await asyncio.wait_for(self.receive_line(), 2)
                self.assertEqual(json.loads(again), {'id': 'next', 'ok': True, 'result': {'ready': True}})
                reader.feed_eof()
                await asyncio.wait_for(serving, 2)
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
        fake.close.assert_awaited_once()

    async def test_nonreading_peer_times_out_and_worker_closes(self):
        fake = SimpleNamespace(dispatch=AsyncMock(return_value={'evidence': 'x' * 2_000_000}), close=AsyncMock())
        reader = asyncio.StreamReader()
        reader.feed_data(worker._encode({'id': 'large', 'op': 'quality.inspect', 'args': {}}))
        with patch.object(worker, 'Worker', return_value=fake), patch.object(worker, 'WRITE_TIMEOUT', .05):
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(worker.serve({}, reader, self.output), 1)
        fake.close.assert_awaited_once()

    async def test_cancellation_removes_writer_and_connection_can_drain(self):
        task = asyncio.create_task(worker._write_all(self.output, b'x' * 2_000_000 + b'\n'))
        await asyncio.sleep(.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        loop = asyncio.get_running_loop()
        self.assertFalse(loop.remove_writer(self.output.fileno()))
        self.assertTrue(await loop.sock_recv(self.receiver, 4096))


if __name__ == '__main__':
    unittest.main()
