"""Offline gateway/worker boundary tests; no model, browser or Telegram calls."""

import asyncio
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.bus import EventBus
from oak.controller import Controller
from oak.executor_worker import FileTransfers
from oak.telegram import TelegramGateway
from oak.tools import Tools
from oak.transfer import CHUNK, ExecutorFiles


class LocalWorker:
    """The worker opens files via descriptors; gateway Path.open is monitored."""

    def __init__(self, workspace, runtime):
        self.files = FileTransfers({'workspace': str(workspace), 'runtime_home': str(runtime)})
        self.calls = []
        self.writer = object()
        self.results = {}

    async def start(self):
        return self

    async def close(self):
        self.files.close_all()

    async def call(self, op, args):
        self.calls.append((op, dict(args)))
        if op.startswith('file.'):
            return getattr(self.files, op.split('.')[1])(**args)
        return self.results.get(op, {'ok': True})


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.state = self.root / 'state'
        self.runtime = self.root / 'runtime'
        for path in (self.workspace, self.state / 'inbox', self.runtime / 'generated_images'):
            path.mkdir(parents=True)
        self.worker = LocalWorker(self.workspace, self.runtime)
        self.config = {'state_dir': str(self.state), 'execution': {'profile': '/synthetic'},
                       'computer': {'enabled': True, 'display': ':92'}}
        self.controller = Controller(SimpleNamespace(home=self.runtime), self.state / 'state.sqlite',
                                     self.workspace, AsyncMock(), config=self.config)
        with patch('oak.executor.ExecutionClient', return_value=self.worker):
            self.tools = Tools(self.controller, self.config)
        self.controller.tools = self.tools
        self.bus = EventBus(self.controller)
        self.controller.bus = self.bus
        self.controller.emit = self.bus.emit
        self.gateway = TelegramGateway(self.controller, 'synthetic:test', {7}, self.state)

    async def asyncTearDown(self):
        await self.tools.close()
        self.gateway.db.close()
        self.controller.close()
        self.temp.cleanup()

    @contextlib.contextmanager
    def deny_gateway_workspace_reads(self):
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.is_relative_to(self.workspace) or path.is_relative_to(self.runtime):
                raise AssertionError('Gateway opened an untrusted model path')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            yield

    async def test_text_only_smoke_without_tools_does_not_access_worker_workspace(self):
        self.controller.tools = None
        with self.deny_gateway_workspace_reads(), \
                patch.object(Path, 'mkdir', side_effect=PermissionError('executor owns workspace')):
            result = await self.controller._input('Synthetic smoke input', [])
        self.assertEqual(result, [{'type': 'text', 'text': 'Synthetic smoke input'}])
        self.assertEqual(self.worker.calls, [])

    async def test_24mib_incoming_stream_and_immutable_outbound_snapshot(self):
        source = self.state / 'inbox' / 'large.bin'
        block = bytes(range(256)) * 256
        expected = hashlib.sha256()
        with source.open('wb') as output:
            for _ in range(384):
                output.write(block)
                expected.update(block)
        with self.deny_gateway_workspace_reads():
            target, size = await self.tools.files.write_trusted(source, self.workspace / 'inbox/large.bin')
            snapshot = await self.tools.files.snapshot(target)
        self.assertEqual(size, 24 * 1024 * 1024)
        self.assertTrue(self.tools.files.stored(snapshot))
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).digest(), expected.digest())
        chunks = [args for op, args in self.worker.calls if op == 'file.write_chunk']
        self.assertEqual(len(chunks), 384)
        self.assertTrue(all(len(base64.b64decode(args['data'])) == CHUNK for args in chunks))
        target.write_bytes(b'mutated after snapshot')
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).digest(), expected.digest())
        # The existing Telegram upload boundary accepts >20 MiB snapshots, while
        # retaining its existing 50 MiB outgoing cap independently of 2 GiB input.
        self.assertEqual(self.gateway._artifact_file(snapshot), snapshot)
        with self.assertRaises(ValueError):
            self.gateway._artifact_file(target)

    async def test_model_symlink_is_rejected_without_privileged_read(self):
        private = self.state / 'private-marker'
        private.write_bytes(b'never-read-this-canary')
        (self.workspace / 'image.png').symlink_to(private)
        with self.deny_gateway_workspace_reads(), self.assertRaises(OSError):
            await self.tools.files.snapshot(self.workspace / 'image.png')
        self.assertEqual(list(self.tools.files.directory.iterdir()), [])

    async def test_generated_images_namespace_does_not_open_runtime_auth(self):
        image = self.runtime / 'generated_images/image.png'
        image.write_bytes(b'image')
        (self.runtime / 'auth.json').write_bytes(b'private-auth-canary')
        with self.deny_gateway_workspace_reads():
            snapshot = await self.tools.files.snapshot(image, namespace='generated_images')
            with self.assertRaises(ValueError):
                await self.tools.files.snapshot(self.runtime / 'auth.json', namespace='generated_images')
        self.assertEqual(snapshot.read_bytes(), b'image')

    async def test_artifact_bus_snapshots_now_and_migrates_legacy_paths_asynchronously(self):
        output = self.workspace / 'output.txt'
        output.write_text('original')
        with self.deny_gateway_workspace_reads():
            result = await self.tools.artifact(7, output, 'Example')
        self.assertTrue(result['queued_for_delivery'])
        row = self.controller.db.execute('SELECT * FROM artifacts').fetchone()
        self.assertNotEqual(row['path'], str(output))
        output.write_text('replacement')
        self.assertEqual(Path(row['path']).read_text(), 'original')
        with self.controller.db:
            self.controller.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?)',
                ('legacy', 7, str(output), output.name, 'text/plain'))
        with self.deny_gateway_workspace_reads():
            migrated = await self.bus.artifact_path(7, 'legacy')
        self.assertTrue(self.tools.files.stored(migrated))
        self.assertEqual(migrated.read_text(), 'replacement')
        count = len(self.worker.calls)
        self.assertEqual(await self.bus.artifact_path(7, 'legacy'), migrated)
        self.assertEqual(len(self.worker.calls), count)
        with self.assertRaises(ValueError):
            await self.bus.artifact_path(8, 'legacy')

    async def test_legacy_telegram_delivery_snapshots_before_claim_without_replaying(self):
        output = self.workspace / 'legacy.txt'
        output.write_text('legacy delivery')
        payload = {'chat_id': 7, '_upload': {'path': str(output)}}
        with self.gateway.db:
            self.gateway.db.execute('INSERT INTO deliveries VALUES (?,?,?,?,?,NULL)',
                ('delivery', 7, 'sendDocument', json.dumps(payload), 'pending'))
        row = self.gateway.db.execute('SELECT * FROM deliveries WHERE id=?', ('delivery',)).fetchone()
        delivered = []
        async def send(method, current):
            delivered.append((method, current))
            self.assertTrue(self.tools.files.stored(current['_upload']['path']))
            return {'message_id': 1}
        with self.deny_gateway_workspace_reads(), patch.object(self.gateway, '_deliver', side_effect=send):
            await self.gateway._render_delivery(row)
            await self.gateway._render_delivery(row)
        self.assertEqual(len(delivered), 1)
        persisted = self.gateway.db.execute('SELECT * FROM deliveries WHERE id=?', ('delivery',)).fetchone()
        self.assertEqual(persisted['status'], 'delivered')
        self.assertTrue(self.tools.files.stored(json.loads(persisted['payload'])['_upload']['path']))

    async def test_media_research_browser_computer_and_direct_transcription_are_worker_calls(self):
        self.worker.results.update({'media.transcribe': 'transcript',
                                    'browser.run': {'text': 'page'},
                                    'computer.status': {'available': True}})
        self.assertIsNone(self.tools.media)
        self.assertIsNone(self.tools.research)
        with patch('oak.media.MediaTools', side_effect=AssertionError('privileged media')), \
                patch('oak.research.ResearchTools', side_effect=AssertionError('privileged research')):
            await self.tools.media_call('speak', text='Example', language='uk')
            self.assertEqual(await self.tools.transcribe(self.workspace / 'audio.ogg'), 'transcript')
            for action in ('search', 'fetch', 'youtube'):
                await self.tools.execute(7, 'oak_research', {'action': action, 'query': 'example'})
            await self.tools.execute(7, 'oak_browser', {'action': 'read'})
            await self.tools.computer.status()
            await self.tools.computer.run(action='screenshot')
        self.assertEqual([op for op, _ in self.worker.calls], [
            'media.speak', 'media.transcribe', 'research.search', 'research.fetch',
            'research.youtube', 'browser.run', 'computer.status', 'computer.run'])

    async def test_attachment_transcription_and_text_ingest_use_worker_transfer(self):
        source = self.state / 'inbox' / 'voice.ogg'
        source.write_bytes(b'synthetic audio')
        self.worker.results['media.transcribe'] = 'Test voice'
        with self.deny_gateway_workspace_reads():
            items = await self.controller._input('', [{'path': str(source), 'mime': 'audio/ogg'}])
        self.assertIn('Test voice', items[0]['text'])
        self.assertIn('media.transcribe', [op for op, _ in self.worker.calls])
        self.assertTrue(any(op == 'file.write_chunk' for op, _ in self.worker.calls))

    async def test_empty_execution_configuration_never_constructs_local_adapters(self):
        with patch('oak.executor.ExecutionClient', return_value=self.worker), \
                patch('oak.media.MediaTools', side_effect=AssertionError('local fallback')):
            tools = Tools(self.controller, {**self.config, 'execution': {}})
        self.assertIs(tools.executor, self.worker)

    async def test_snapshot_rejects_hardlinks_as_trusted(self):
        first = self.tools.files.directory / 'first.txt'
        second = self.tools.files.directory / 'second.txt'
        first.write_text('data')
        first.chmod(0o600)
        os.link(first, second)
        self.assertFalse(self.tools.files.stored(first))
        self.assertFalse(self.tools.files.stored(second))


if __name__ == '__main__':
    unittest.main()
