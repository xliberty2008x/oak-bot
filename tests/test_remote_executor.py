"""Executor remote transport tests with synthetic bytes and mocked adapters."""
import asyncio
import base64
import hashlib
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import WSMsgType, web

from oak.remote import ExecutorRemote, RemoteDesktop


class ExecutorRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_websocket_frame_is_split_into_bounded_lease_bound_chunks(self):
        seen = []
        async def call(operation, args):
            seen.append((operation, args))
            return {'size': len(base64.b64decode(args['data']))}
        client = SimpleNamespace(call=AsyncMock(side_effect=call))
        bridge = ExecutorRemote(client, 'a' * 32)
        data = bytes(range(256)) * 1024
        await bridge.write(data)
        self.assertEqual(len(seen), 4)
        self.assertTrue(all(op == 'remote.write' and args['id'] == 'a' * 32 for op, args in seen))
        self.assertTrue(all(len(base64.b64decode(args['data'])) == 65536 for _, args in seen))
        self.assertEqual(b''.join(base64.b64decode(args['data']) for _, args in seen), data)

    async def test_read_waits_through_timeout_and_validates_worker_data(self):
        client = SimpleNamespace(call=AsyncMock(side_effect=[
            {'data': '', 'eof': False}, {'data': base64.b64encode(b'RFB 003.008\n').decode(), 'eof': False},
            {'data': '', 'eof': True}]))
        bridge = ExecutorRemote(client, 'a' * 32)
        self.assertEqual(await bridge.read(), b'RFB 003.008\n')
        self.assertEqual(await bridge.read(), b'')
        for invalid in ({'data': '*', 'eof': False}, {'data': '', 'eof': 'false'},
                        {'data': base64.b64encode(b'x' * 65537).decode(), 'eof': False}):
            client.call = AsyncMock(return_value=invalid)
            with self.assertRaises(OSError):
                await bridge.read()

    async def test_relay_cancellation_drains_short_rpc_without_cancelling_client(self):
        entered, release = asyncio.Event(), asyncio.Event()
        cancelled = []
        async def call(operation, args):
            if operation == 'remote.close':
                return {'closed': True}
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return {'data': '', 'eof': False}
        client = SimpleNamespace(call=AsyncMock(side_effect=call))
        bridge = ExecutorRemote(client, 'a' * 32)
        reading = asyncio.create_task(bridge.read())
        await entered.wait()
        reading.cancel()
        await asyncio.sleep(0)
        self.assertFalse(reading.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await reading
        self.assertEqual(cancelled, [])
        await bridge.close()
        client.call.assert_awaited_with('remote.close', {'id': 'a' * 32})

    async def test_close_is_lease_bound_idempotent_and_disables_future_input(self):
        client = SimpleNamespace(call=AsyncMock(return_value={'closed': True}))
        bridge = ExecutorRemote(client, 'a' * 32)
        await bridge.close()
        await bridge.close()
        client.call.assert_awaited_once_with('remote.close', {'id': 'a' * 32})
        with self.assertRaises(OSError):
            await bridge.input(text='dummy')
        with self.assertRaises(OSError):
            await bridge.write(b'x')


class ExecutorRemoteSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE web_sessions(hash TEXT,owner INTEGER,expires REAL)')
        self.db.execute('INSERT INTO web_sessions VALUES (?,?,?)',
                        (hashlib.sha256(b'dummy-owner-session').hexdigest(), 11, 4102444800))
        self.client = SimpleNamespace(call=AsyncMock(return_value={'accepted': True}), close=AsyncMock())
        self.computer = SimpleNamespace(_lock=asyncio.Lock(), _type=AsyncMock(), _key=AsyncMock(), _exec=AsyncMock())
        self.controller = SimpleNamespace(tools=SimpleNamespace(computer=self.computer, executor=self.client),
                                          manual_owner=11, pause_for_manual=AsyncMock())
        self.gateway = SimpleNamespace(controller=self.controller, db=self.db, allowed={11}, public_url='https://oak.example',
                                       owner=lambda request: 11, confirmed_owner=AsyncMock(return_value=11))
        self.remote = RemoteDesktop(self.gateway)
        self.request = SimpleNamespace(headers={'Authorization': 'Bearer dummy-owner-session'}, cookies={}, json=AsyncMock())
        response = await self.remote.start(self.request)
        attached = json.loads(response.body)
        self.lease, self.protocol = attached['lease'], attached['protocol']
        self.state = self.remote.current
        self.state['socket'] = SimpleNamespace(prepared=True, closed=False, close=AsyncMock())
        self.state['worker'] = ExecutorRemote(self.client, 'b' * 32)

    async def asyncTearDown(self):
        await self.remote.close()
        self.db.close()

    async def test_status_uses_worker_availability_without_gateway_x11vnc(self):
        with patch('oak.remote.shutil.which', return_value=None):
            self.assertTrue(self.remote.status(11)['configured'])

    async def test_type_and_key_use_worker_lease_without_host_computer(self):
        for payload, operation in (({'text': 'Привіт'}, 'remote.type'), ({'key': 'ctrl+a'}, 'remote.key')):
            self.request.json.return_value = {**payload, 'lease': self.lease}
            response = await self.remote.type(self.request)
            self.assertEqual(json.loads(response.body), {'ok': True})
            self.client.call.assert_awaited_with(operation, {'id': 'b' * 32, **payload})
        self.computer._type.assert_not_awaited()
        self.computer._key.assert_not_awaited()

    async def test_old_lease_and_other_owner_session_cannot_type(self):
        self.request.json.return_value = {'text': 'dummy', 'lease': 'old-lease'}
        with self.assertRaises(web.HTTPConflict):
            await self.remote.type(self.request)
        self.request.json.return_value = {'text': 'dummy', 'lease': self.lease}
        self.request.headers = {'Authorization': 'Bearer other-owner-session'}
        with self.assertRaises(web.HTTPForbidden):
            await self.remote.type(self.request)
        self.client.call.assert_not_awaited()

    async def test_state_bound_cleanup_closes_only_its_worker_lease(self):
        await self.remote.finish(self.state)
        self.client.call.assert_awaited_once_with('remote.close', {'id': 'b' * 32})
        self.assertIsNone(self.remote.current)
        self.assertIsNone(self.controller.manual_owner)
        self.computer._exec.assert_not_awaited()
        self.client.close.assert_not_awaited()

    async def test_worker_lease_is_required_even_with_prepared_websocket(self):
        self.state['worker'] = None
        self.request.json.return_value = {'text': 'dummy', 'lease': self.lease}
        with self.assertRaises(web.HTTPConflict):
            await self.remote.type(self.request)
        self.client.call.assert_not_awaited()

    async def test_websocket_uses_worker_without_gateway_subprocess_or_x11_transport(self):
        class FakeSocket:
            prepared = False
            closed = False
            def __init__(self):
                self.send_bytes = AsyncMock()
            async def prepare(self, request):
                self.prepared = True
            async def close(self, **kwargs):
                self.closed = True
            async def __aiter__(self):
                yield SimpleNamespace(type=WSMsgType.BINARY, data=b'RFB 003.008\n')
        reads = 0
        async def call(operation, args):
            nonlocal reads
            if operation == 'remote.start':
                return {'id': 'b' * 32}
            if operation == 'remote.read':
                reads += 1
                return {'data': base64.b64encode(b'RFB 003.008\n' if reads == 1 else b'').decode(), 'eof': reads > 1}
            if operation == 'remote.write':
                return {'size': len(base64.b64decode(args['data']))}
            if operation == 'remote.close':
                return {'closed': True}
            raise AssertionError(operation)
        self.client.call.side_effect = call
        self.request.headers.update({'Origin': 'https://oak.example', 'Sec-WebSocket-Protocol': self.protocol})
        self.request.host, self.request.secure = 'oak.example', True
        self.state['worker'] = None
        ws = FakeSocket()
        with patch('oak.remote.web.WebSocketResponse', return_value=ws), \
                patch('oak.remote.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn, \
                patch('oak.remote.socket.socketpair') as local_transport:
            result = await self.remote.socket(self.request)
        self.assertIs(result, ws)
        ws.send_bytes.assert_awaited_once_with(b'RFB 003.008\n')
        self.assertTrue(ws.closed)
        self.assertIsNone(self.controller.manual_owner)
        self.client.call.assert_any_await('remote.start', {})
        self.client.call.assert_any_await('remote.close', {'id': 'b' * 32})
        spawn.assert_not_awaited()
        local_transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
