"""Generated audio and a local mock broker only; no provider credentials/calls."""

import asyncio
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave

from aiohttp import web

from oak.controller import Controller
from oak.media import ToolUnavailable
from oak.speech_to_text import MAX_INPUT_BYTES, SpeechToText, configuration
from oak.tools import Tools
from oak.transfer import ExecutorFiles
from oak.telegram import TelegramGateway


def wav_bytes(seconds=0.1, rate=16000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(bytes(int(seconds * rate) * 2))
    return output.getvalue()


class SpeechConfigurationTests(unittest.TestCase):
    def test_disabled_default_and_explicit_safe_configuration(self):
        self.assertIsNone(configuration({}))
        self.assertIsNone(configuration({'speech_to_text': {'enabled': False}}))
        good = {'enabled': True, 'broker_socket_path': '/run/oak-stt/asr.sock'}
        configured = configuration({'speech_to_text': good})
        self.assertEqual((configured['max_duration_seconds'], configured['request_timeout_seconds']), (60, 240))
        for update in ({'enabled': 1}, {'broker_socket_path': '/tmp/../secret.sock'},
                       {'broker_socket_path': 'http://remote.invalid/asr'}, {'broker_socket_path': None},
                       {'max_duration_seconds': 61}, {'max_duration_seconds': True},
                       {'request_timeout_seconds': 241}, {'api_key': 'PRIVATE'}):
            with self.subTest(update=update), self.assertRaises(ValueError) as raised:
                configuration({'speech_to_text': good | update})
            self.assertNotIn('PRIVATE', str(raised.exception))


class SpeechBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.socket = self.root / 'asr.sock'
        self.source = self.root / 'voice.wav'
        self.source.write_bytes(wav_bytes())
        self.mode = 'openai'
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.normalized = []
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.add_routes([web.get('/ready', self.ready), web.post('/v1/audio/transcriptions', self.transcribe)])
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.UnixSite(self.runner, str(self.socket)).start()
        self.socket.chmod(0o600)
        self.addAsyncCleanup(self.runner.cleanup)
        self.client = SpeechToText({'broker_socket_path': str(self.socket),
            'max_duration_seconds': 60, 'request_timeout_seconds': 2}, [])
        self.addAsyncCleanup(self.client.close)

    async def ready(self, request):
        self.calls.append('ready')
        if self.mode == 'unready':
            return web.json_response({'error': 'PRIVATE'}, status=503)
        return web.json_response({'ready': True})

    async def transcribe(self, request):
        self.calls.append('transcribe')
        reader = await request.multipart()
        part = await reader.next()
        self.assertEqual((part.name, await part.text()), ('language', 'uk-UA'))
        part = await reader.next()
        self.assertEqual((part.name, await part.text()), ('response_format', 'json'))
        part = await reader.next()
        self.assertEqual((part.name, part.filename), ('file', 'voice.wav'))
        audio = await part.read()
        with wave.open(io.BytesIO(audio), 'rb') as normalized:
            self.assertEqual((normalized.getnchannels(), normalized.getsampwidth(), normalized.getframerate()),
                             (1, 2, 16000))
        self.assertIsNone(await reader.next())
        self.started.set()
        if self.mode == 'blocked':
            await self.release.wait()
        if self.mode == 'error':
            return web.json_response({'error': 'PRIVATE'}, status=500)
        if self.mode == 'redirect':
            return web.Response(status=302, headers={'Location': 'https://remote.invalid/'})
        if self.mode == 'oversized':
            return web.Response(body=b'x' * 70000)
        if self.mode == 'invalid':
            return web.Response(text='PRIVATE')
        if self.mode == 'empty':
            return web.json_response({'text': ''})
        if self.mode == 'invalid_unicode':
            return web.json_response({'text': '\ud800'})
        return web.json_response({'text': 'Синтетичний тест.', 'engine': self.mode,
                                  'fallback_reason': 'primary_unavailable' if self.mode == 'nemotron' else None})

    async def fake_decode(self, source, destination):
        self.normalized.append(destination.parent)
        destination.write_bytes(wav_bytes())

    async def run_mocked_decoder(self):
        with patch.object(self.client, '_decode', side_effect=self.fake_decode):
            return await self.client.transcribe(self.source)

    def assert_clean(self):
        self.assertTrue(self.normalized)
        self.assertTrue(all(not directory.exists() for directory in self.normalized))
        self.assertTrue(self.source.exists())

    async def test_primary_and_fallback_results_use_the_same_private_protocol(self):
        for engine in ('openai', 'nemotron'):
            self.mode = engine
            self.assertEqual(await self.run_mocked_decoder(), 'Синтетичний тест.')
        self.assertEqual(self.calls, ['ready', 'transcribe', 'ready', 'transcribe'])
        self.assert_clean()

    async def test_unready_and_unsafe_socket_fail_before_decoding(self):
        self.mode = 'unready'
        with patch.object(self.client, '_decode', new_callable=AsyncMock) as decode:
            with self.assertRaises(ToolUnavailable) as raised:
                await self.client.transcribe(self.source)
            decode.assert_not_awaited()
        self.assertNotIn('PRIVATE', str(raised.exception))
        self.socket.chmod(0o666)
        with self.assertRaises(ToolUnavailable):
            await self.client.transcribe(self.source)

    async def test_input_size_and_socket_symlinks_are_rejected_before_http(self):
        with self.source.open('wb') as audio:
            audio.truncate(MAX_INPUT_BYTES + 1)
        with self.assertRaises(ValueError):
            await self.client.transcribe(self.source)
        self.assertEqual(self.calls, [])
        self.source.write_bytes(wav_bytes())
        link = self.root / 'linked.sock'
        link.symlink_to(self.socket)
        self.client.socket = link
        with self.assertRaises(ToolUnavailable):
            await self.client.transcribe(self.source)
        self.assertEqual(self.calls, [])

    async def test_provider_error_redirect_invalid_and_oversized_are_safe_and_clean(self):
        for mode in ('error', 'redirect', 'invalid', 'empty', 'oversized', 'invalid_unicode'):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(ToolUnavailable) as raised:
                await self.run_mocked_decoder()
            self.assertNotIn('PRIVATE', str(raised.exception))
            self.assert_clean()
        self.assertEqual(self.calls.count('transcribe'), 6)  # No redirects or retries.

    async def test_cancel_and_busy_do_not_block_the_event_loop_or_leave_audio(self):
        self.mode = 'blocked'
        task = asyncio.create_task(self.run_mocked_decoder())
        try:
            await asyncio.wait_for(self.started.wait(), 1)
            with self.assertRaises(ToolUnavailable):
                await self.client.transcribe(self.source)
            await asyncio.wait_for(self.client.close(), 0.5)
            self.assertTrue(task.cancelled())
            self.assert_clean()
        finally:
            self.release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_total_timeout_cleans_up_and_allows_a_future_request(self):
        self.mode = 'blocked'
        self.client.timeout = 0.1
        try:
            with self.assertRaises(ToolUnavailable):
                await self.run_mocked_decoder()
            self.assert_clean()
        finally:
            self.release.set()
        self.mode = 'openai'
        self.assertEqual(await self.run_mocked_decoder(), 'Синтетичний тест.')

    async def test_real_ffmpeg_normalization_and_duration_limit(self):
        self.source.write_bytes(wav_bytes(0.1, 48000))
        self.assertEqual(await self.client.transcribe(self.source), 'Синтетичний тест.')
        self.client.seconds = 1
        self.source.write_bytes(wav_bytes(1.2))
        with self.assertRaises(ValueError):
            await self.client.transcribe(self.source)
        self.assertEqual(self.calls.count('transcribe'), 1)

    async def test_cancel_stops_the_actual_decoder_process(self):
        real_create = asyncio.create_subprocess_exec
        started = asyncio.Event()
        processes = []
        async def sleeping_decoder(*args, **kwargs):
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            process = await real_create('/usr/bin/python3', '-c', 'import time;time.sleep(30)', **kwargs)
            processes.append(process)
            started.set()
            return process
        with patch('oak.speech_to_text.asyncio.create_subprocess_exec', side_effect=sleeping_decoder):
            task = asyncio.create_task(self.client.transcribe(self.source))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(self.source.exists())


class GatewaySpeechTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.controller = SimpleNamespace(cwd=str(self.root / 'workspace'), db=self.db)

    async def test_disabled_preserves_existing_media_api(self):
        tools = Tools(self.controller, {})
        self.addAsyncCleanup(tools.close)
        tools.media_call = AsyncMock(return_value='Local legacy transcript')
        self.assertIsNone(tools.speech_to_text)
        self.assertEqual(await tools.transcribe('voice.ogg'), 'Local legacy transcript')
        tools.media_call.assert_awaited_once_with('transcribe', path='voice.ogg')

    async def test_executor_snapshot_is_bounded_and_removed_without_changing_input(self):
        tools = Tools(self.controller, {})
        self.addAsyncCleanup(tools.browser.close)
        tools.speech_to_text = SimpleNamespace(transcribe=AsyncMock(return_value='Broker transcript'))
        tools.executor = object()
        rpc = SimpleNamespace(call=AsyncMock())
        tools.files = ExecutorFiles(rpc, self.controller.cwd, self.root / 'state')
        source = self.root / 'state' / 'artifact-snapshots' / 'temporary.wav'
        async def snapshot(path, **kwargs):
            self.assertEqual(kwargs, {'limit': MAX_INPUT_BYTES})
            source.write_bytes(wav_bytes())
            return source
        with patch.object(tools.files, 'snapshot', side_effect=snapshot):
            self.assertEqual(await tools.transcribe('voice.ogg'), 'Broker transcript')
        tools.speech_to_text.transcribe.assert_awaited_once_with(source)
        self.assertFalse(source.exists())
        rpc.call.assert_not_awaited()  # No ASR dispatch into the execution worker.

    async def test_reused_snapshot_is_preserved_and_new_snapshot_cleans_on_failure(self):
        tools = Tools(self.controller, {})
        self.addAsyncCleanup(tools.browser.close)
        tools.speech_to_text = SimpleNamespace(transcribe=AsyncMock(side_effect=ToolUnavailable('safe error')))
        tools.executor = object()
        tools.files = ExecutorFiles(SimpleNamespace(call=AsyncMock()), self.controller.cwd, self.root / 'state')
        stored = tools.files.directory / 'already-stored.wav'
        stored.write_bytes(wav_bytes())
        stored.chmod(0o600)
        with self.assertRaises(ToolUnavailable):
            await tools.transcribe(stored)
        self.assertTrue(stored.exists())
        temporary = tools.files.directory / 'new-temporary.wav'
        async def snapshot(*args, **kwargs):
            temporary.write_bytes(wav_bytes())
            return temporary
        with patch.object(tools.files, 'snapshot', side_effect=snapshot):
            with self.assertRaises(ToolUnavailable):
                await tools.transcribe('new-voice.ogg')
        self.assertFalse(temporary.exists())
        self.assertTrue(stored.exists())

    async def test_automatic_legacy_owner_input_uses_gateway_hook_and_keeps_durable_attachment(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        source = workspace / 'voice.ogg'
        source.write_bytes(b'synthetic existing attachment')
        controller = Controller(AsyncMock(), ':memory:', workspace, AsyncMock())
        self.addCleanup(controller.close)
        controller.tools = SimpleNamespace(transcribe=AsyncMock(return_value='Test transcript'))
        items = await controller._input('', [{'path': str(source), 'mime': 'audio/ogg'}])
        controller.tools.transcribe.assert_awaited_once()
        self.assertIn('Test transcript', items[0]['text'])
        self.assertTrue(source.exists())
        stored = ' '.join(controller.db.iterdump())
        self.assertNotIn('Test transcript', stored)

    async def test_only_owner_voice_reaches_automatic_gateway_transcription(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        state = self.root / 'state'
        state.mkdir()
        source = state / 'inbox' / 'synthetic.ogg'
        source.parent.mkdir()
        source.write_bytes(b'synthetic voice fixture')
        controller = Controller(AsyncMock(), ':memory:', workspace, AsyncMock(),
                                config={'state_dir': str(state)})
        self.addCleanup(controller.close)
        transcribe = AsyncMock(return_value='Synthetic owner transcript')
        controller.tools = SimpleNamespace(transcribe=transcribe)
        prepared = []
        async def submit(chat, text, update_id, attachments=None):
            prepared.append(await controller._input(text, attachments))
        controller.submit = submit
        gateway = TelegramGateway(controller, 'synthetic-token', {7}, state)
        self.addCleanup(gateway.db.close)
        gateway._attachments = AsyncMock(return_value=[{'path': str(source), 'mime': 'audio/ogg'}])
        gateway._api = AsyncMock(return_value=True)
        def update(number, owner):
            return {'update_id': number, 'message': {'message_id': number,
                'chat': {'id': owner, 'type': 'private'}, 'from': {'id': owner},
                'voice': {'file_id': 'synthetic', 'duration': 1}}}
        await gateway.handle_update(update(1, 8))
        gateway._attachments.assert_not_awaited()
        transcribe.assert_not_awaited()
        await gateway.handle_update(update(2, 7))
        self.assertEqual(transcribe.await_count, 1)
        self.assertIn('Synthetic owner transcript', prepared[0][0]['text'])
        self.assertTrue(source.exists())
        await gateway.handle_update(update(2, 7))
        self.assertEqual(transcribe.await_count, 1)

    async def test_close_cancels_pending_snapshot_before_closing_executor(self):
        tools = Tools(self.controller, {})
        self.addAsyncCleanup(tools.browser.close)
        tools.speech_to_text = SimpleNamespace(transcribe=AsyncMock(), close=AsyncMock())
        tools.executor = SimpleNamespace(close=AsyncMock())
        started = asyncio.Event()
        calls = []
        async def rpc(method, args):
            calls.append(method)
            if method == 'file.open_read':
                return {'handle': 'synthetic', 'size': 100}
            if method == 'file.read_chunk':
                started.set()
                await asyncio.Event().wait()
            return {}
        tools.files = ExecutorFiles(SimpleNamespace(call=rpc), self.controller.cwd, self.root / 'state')
        task = asyncio.create_task(tools.transcribe('voice.ogg'))
        await asyncio.wait_for(started.wait(), 1)
        with self.assertRaises(ToolUnavailable):
            await tools.transcribe('another.ogg')
        await asyncio.wait_for(tools.close(), 0.5)
        self.assertTrue(task.cancelled())
        self.assertEqual(calls[-1], 'file.close')
        self.assertEqual(list(tools.files.directory.iterdir()), [])
        tools.speech_to_text.transcribe.assert_not_awaited()
        tools.speech_to_text.close.assert_awaited_once()
        tools.executor.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
