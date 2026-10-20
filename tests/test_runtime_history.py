import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.runtime import RuntimeClient


class HistoryTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_thread_resumes_without_echoing_history_before_dispatch(self):
        requests = []

        async def request(method, params):
            requests.append((method, params))
            if method == 'thread/resume':
                if params.get('excludeTurns') is not True:
                    raise ConnectionError('Full media history exceeds the transport frame limit')
                return {'thread': {'id': 'existing-thread', 'turns': []},
                        'model': params['model'], 'modelProvider': 'openai'}
            if method == 'turn/start':
                return {'turn': {'id': 'new-turn'}}
            raise AssertionError(method)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'state.sqlite'
            original = Controller(AsyncMock(), database, directory, AsyncMock())
            with original.db:
                original.db.execute('INSERT INTO chats(chat_id,thread_id) VALUES (?,?)', (7, 'existing-thread'))
            original.close()
            resumed = Controller(SimpleNamespace(request=request), database, directory, AsyncMock())
            try:
                self.assertTrue(await resumed.submit(7, 'A new request', 31))
                self.assertEqual([r[0] for r in requests], ['thread/resume', 'turn/start'])
                self.assertEqual(requests[-1][1]['threadId'], 'existing-thread')
                self.assertEqual(requests[-1][1]['input'], [{'type': 'text', 'text': 'A new request'}])
                self.assertEqual(resumed.db.execute('SELECT status FROM inputs').fetchone()[0], 'accepted')
                self.assertIsNone(resumed.db.execute('SELECT previous_thread_id FROM chats').fetchone()[0])
            finally:
                resumed.close()

    async def test_transport_errors_release_pending_rpc_with_private_safe_reason(self):
        for payload, reason in [(b'x' * 2048, 'frame_limit_exceeded'),
                                (b'{"private secret":invalid}\n', 'invalid_json'),
                                (b'\xff\n', 'invalid_message')]:
            with self.subTest(reason=reason):
                client = RuntimeClient()
                stream = asyncio.StreamReader(limit=1024)
                stream.feed_data(payload)
                stream.feed_eof()
                client.process = SimpleNamespace(stdout=stream)
                pending = asyncio.get_running_loop().create_future()
                client._pending[1] = pending
                with self.assertLogs('oak.runtime', level='WARNING') as logs:
                    await client._read_loop()
                with self.assertRaises(ConnectionError):
                    await pending
                event = client.events.get_nowait()
                self.assertEqual(event['params']['reason'], reason)
                self.assertNotIn('private secret', json.dumps(event) + str(logs.output))

    async def test_close_drains_pipe_after_oversized_frame_and_reaps_process(self):
        client = RuntimeClient()
        client.process = await asyncio.create_subprocess_exec(
            sys.executable, '-c',
            "import os,sys; os.write(1,b'x'*262144+b'\\n'); sys.stdin.read()",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=1024)
        process = client.process
        client._reader = asyncio.create_task(client._read_loop())
        client._stderr = asyncio.create_task(client._drain_stderr())
        try:
            with self.assertLogs('oak.runtime', level='WARNING'):
                event = await asyncio.wait_for(client.events.get(), 5)
            self.assertEqual(event['params']['reason'], 'frame_limit_exceeded')
            await asyncio.wait_for(client.close(), 5)
            self.assertEqual(process.returncode, 0)
            self.assertTrue(process.stdout.at_eof())
            self.assertTrue(process.stderr.at_eof())
            self.assertIsNone(client.process)
        finally:
            await client.close()


if __name__ == '__main__':
    unittest.main()
