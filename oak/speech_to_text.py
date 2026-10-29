"""Trusted gateway client for a separately provisioned speech broker.

The broker owns provider credentials and primary/fallback selection. This module
only sends bounded normalized audio over a private Unix socket. It never reads
API keys, changes the model runtime, or runs inside the execution worker.
"""

import asyncio
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import wave

from aiohttp import ClientError, ClientSession, ClientTimeout, FormData, UnixConnector

from .media import ToolUnavailable


MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 65536
MAX_TEXT_CHARS = 12000


def configuration(config):
    value = config.get('speech_to_text')
    if value is None:
        return None
    allowed = {'enabled', 'broker_socket_path', 'max_duration_seconds', 'request_timeout_seconds'}
    if (not isinstance(value, dict) or set(value) - allowed
            or type(value.get('enabled', False)) is not bool):
        raise ValueError('Invalid speech broker configuration.')
    if not value.get('enabled', False):
        return None
    socket = value.get('broker_socket_path')
    if (not isinstance(socket, str) or not socket or len(os.fsencode(socket)) > 100
            or '\x00' in socket or not Path(socket).is_absolute() or '..' in Path(socket).parts):
        raise ValueError('Speech broker requires an explicit absolute Unix socket.')
    seconds = value.get('max_duration_seconds', 60)
    timeout = value.get('request_timeout_seconds', 240)
    if type(seconds) is not int or not 1 <= seconds <= 60:
        raise ValueError('Speech recognition accepts at most 60 seconds.')
    if type(timeout) is not int or not 1 <= timeout <= 240:
        raise ValueError('Invalid speech broker timeout.')
    return {'broker_socket_path': socket, 'max_duration_seconds': seconds,
            'request_timeout_seconds': timeout}


class SpeechToText:
    def __init__(self, settings, protected_roots):
        self.socket = Path(settings['broker_socket_path'])
        if any(self.socket.is_relative_to(Path(root).resolve()) for root in protected_roots if root):
            raise ValueError('Speech broker socket must stay outside conversation and runtime directories.')
        self.seconds = settings['max_duration_seconds']
        self.timeout = settings['request_timeout_seconds']
        self._lock = asyncio.Lock()
        self._tasks = set()
        self._closed = False

    def _check_socket(self):
        try:
            info = self.socket.lstat()
            if (self.socket.resolve(strict=True) != self.socket or not stat.S_ISSOCK(info.st_mode)
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError
            for directory in self.socket.parents:
                info = directory.lstat()
                if (not stat.S_ISDIR(info.st_mode)
                        or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
                        or info.st_uid not in {0, os.geteuid()}):
                    raise ValueError
        except (OSError, ValueError, RuntimeError):
            raise ToolUnavailable('Приватний розпізнавач мовлення недоступний.') from None

    @staticmethod
    def _source(path):
        source = Path(path)
        try:
            info = source.lstat()
            if (not source.is_absolute() or source.resolve(strict=True) != source
                    or not stat.S_ISREG(info.st_mode) or info.st_size <= 0
                    or info.st_size > MAX_INPUT_BYTES):
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            raise ValueError('Аудіофайл відсутній або перевищує ліміт 20 МіБ.') from None
        return source

    async def _decode(self, source, destination):
        max_wav = (self.seconds + 1) * 16000 * 2 + 8192
        args = ['/usr/bin/ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-xerror',
            '-protocol_whitelist', 'file', '-format_whitelist', 'ogg,wav,mp3,flac,aac,mov,matroska',
            '-i', str(source), '-t', str(self.seconds + 1), '-map', '0:a:0', '-vn', '-sn', '-dn',
            '-map_metadata', '-1', '-threads', '1', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
            '-fs', str(max_wav), '-f', 'wav', str(destination)]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                    'HOME': str(destination.parent), 'TMPDIR': str(destination.parent),
                    'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'})
            await asyncio.wait_for(process.wait(), 30)
            if process.returncode:
                raise ToolUnavailable('Не вдалося декодувати аудіо локально.')
        except (OSError, asyncio.TimeoutError):
            raise ToolUnavailable('Локальне декодування аудіо недоступне або перевищило час.') from None
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()

    def _wav_bytes(self, destination):
        try:
            if destination.stat().st_size > (self.seconds + 1) * 16000 * 2 + 8192:
                raise ValueError
            with wave.open(str(destination), 'rb') as audio:
                frames = audio.getnframes()
                if ((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype())
                        != (1, 2, 16000, 'NONE') or not 0 < frames <= self.seconds * 16000
                        or len(audio.readframes(frames + 1)) != frames * 2):
                    raise ValueError
            return destination.read_bytes()
        except (OSError, wave.Error, EOFError, ValueError):
            raise ValueError(f'Аудіо має містити коректний запис до {self.seconds} с.') from None

    @staticmethod
    async def _response(response, limit):
        if response.status != 200:
            raise ToolUnavailable('Розпізнавач мовлення тимчасово недоступний.')
        if response.content_length is not None and response.content_length > limit:
            raise ToolUnavailable('Розпізнавач повернув завелику відповідь.')
        raw = bytearray()
        async for chunk in response.content.iter_chunked(4096):
            raw.extend(chunk)
            if len(raw) > limit:
                raise ToolUnavailable('Розпізнавач повернув завелику відповідь.')
        return bytes(raw)

    async def transcribe(self, path):
        if self._closed:
            raise ToolUnavailable('Розпізнавач мовлення зупинено.')
        if self._lock.locked():
            raise ToolUnavailable('Розпізнавач уже обробляє інший запис.')
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            async with self._lock:
                source = self._source(path)
                self._check_socket()
                async with ClientSession(connector=UnixConnector(path=str(self.socket)),
                        timeout=ClientTimeout(total=self.timeout, connect=3), trust_env=False,
                        auto_decompress=False) as client:
                    async with client.get('http://oak-stt/ready', allow_redirects=False,
                            timeout=ClientTimeout(total=3, connect=3)) as response:
                        await self._response(response, 4096)
                    with tempfile.TemporaryDirectory(prefix='oak-stt-', dir='/dev/shm') as folder:
                        destination = Path(folder) / 'input.wav'
                        await self._decode(source, destination)
                        audio = await asyncio.to_thread(self._wav_bytes, destination)
                        form = FormData()
                        form.add_field('language', 'uk-UA')
                        form.add_field('response_format', 'json')
                        form.add_field('file', audio, filename='voice.wav', content_type='audio/wav')
                        async with client.post('http://oak-stt/v1/audio/transcriptions', data=form,
                                               allow_redirects=False) as response:
                            raw = await self._response(response, MAX_RESPONSE_BYTES)
                        try:
                            document = json.loads(raw)
                            text = document['text']
                            if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS
                                    or any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127 for c in text)):
                                raise ValueError
                            text.encode('utf-8', errors='strict')
                        except (ValueError, TypeError, KeyError, UnicodeError):
                            raise ToolUnavailable('Розпізнавач не повернув коректний текст.') from None
                        return text.strip()
        except (ClientError, asyncio.TimeoutError, OSError):
            raise ToolUnavailable('Не вдалося зв’язатися з приватним розпізнавачем мовлення.') from None
        finally:
            self._tasks.discard(task)

    async def close(self):
        self._closed = True
        tasks = tuple(task for task in self._tasks if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
