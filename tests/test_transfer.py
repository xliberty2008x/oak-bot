"""Gateway snapshot limits apply before streaming and on immutable snapshot reuse."""

import base64
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.transfer import ExecutorFiles, LIMIT


class SnapshotLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.client = SimpleNamespace(call=AsyncMock())
        self.files = ExecutorFiles(self.client, self.root / 'worker', self.root / 'state')

    def stream(self, data, size=None):
        async def rpc(method, args):
            if method == 'file.open_read':
                return {'handle': 'fixture', 'size': len(data) if size is None else size}
            if method == 'file.read_chunk':
                return {'data': base64.b64encode(data).decode(), 'eof': True}
            if method == 'file.close':
                return {}
            raise AssertionError(method)
        self.client.call.side_effect = rpc

    async def test_default_snapshot_and_reuse_preserve_bytes_and_private_mode(self):
        self.stream(b'synthetic')
        path = await self.files.snapshot('worker.png')
        self.assertEqual(path.read_bytes(), b'synthetic')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.client.call.reset_mock()
        self.assertEqual(await self.files.snapshot(path), path)
        self.client.call.assert_not_awaited()

    async def test_requested_limit_rejects_before_read_and_removes_partial_file(self):
        self.stream(b'x', size=11)
        with self.assertRaises(ValueError):
            await self.files.snapshot('image.png', limit=10)
        self.assertEqual([c.args[0] for c in self.client.call.await_args_list], ['file.open_read', 'file.close'])
        self.assertEqual(list(self.files.directory.iterdir()), [])

    async def test_stream_exceeding_declared_size_cleans_up_and_closes(self):
        self.stream(b'01234567890', size=10)
        with self.assertRaises(ValueError):
            await self.files.snapshot('image.png', limit=10)
        self.assertEqual(self.client.call.await_args.args[0], 'file.close')
        self.assertEqual(list(self.files.directory.iterdir()), [])

    async def test_stored_snapshot_cannot_bypass_smaller_limit(self):
        self.stream(b'01234567890')
        path = await self.files.snapshot('image.png')
        self.client.call.reset_mock()
        with self.assertRaises(ValueError):
            await self.files.snapshot(path, limit=10)
        self.client.call.assert_not_awaited()
        self.assertEqual(path.read_bytes(), b'01234567890')

    async def test_invalid_limit_never_calls_worker(self):
        for value in (True, 0, -1, 1.5, LIMIT + 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.files.snapshot('image.png', limit=value)
        self.client.call.assert_not_awaited()
