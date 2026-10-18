"""A broker-owned, fixed adapter process. No native PTY adoption or output API."""

import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
from urllib.parse import urlsplit

from .cli_contract import metadata

MAX_LINE = 16384


def authorization_url(value):
    # Only a trusted adapter produces this private URL; it is never public data.
    try:
        parsed = urlsplit(value)
        if (not isinstance(value, str) or len(value) > 8192
                or re.search(r'[\x00-\x20\x7f\\]', value)
                or parsed.scheme != 'https' or parsed.netloc != 'accounts.google.com'
                or parsed.path not in {'/o/oauth2/auth', '/o/oauth2/v2/auth'} or parsed.fragment):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Invalid private authorization destination.') from None
    return value


class PrivateProcess:
    """command comes only from immutable provisioning, never an HTTP/model field.

    The adapter emits one private challenge and one allowlisted verification line.
    Unexpected output fails closed. Stderr is discarded, never logged or returned.
    Human input is newline framed once; it is never written to argv or environment.
    """

    def __init__(self, command, cache_root, *, seconds=300):
        self.command = tuple(command)
        self.cache_root = Path(cache_root)
        self.seconds = seconds
        self.process = self.task = None
        self.directory = None
        self.challenge = None
        self.result = None
        self.state = 'waiting'
        self.submitted = False
        self.closed = False
        self.reaped = False

    async def start(self):
        if self.state != 'waiting' or self.closed:
            raise ValueError('CLI process has already been attempted.')
        self.state = 'active'  # Consumption precedes all external I/O.
        try:
            self.directory = Path(tempfile.mkdtemp(prefix='cli-', dir=self.cache_root))
            self.directory.chmod(0o700)
            env = {'HOME': str(self.directory), 'XDG_CONFIG_HOME': str(self.directory / 'config'),
                   'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                   'PYTHONNOUSERSITE': '1'}
            launch = asyncio.create_task(asyncio.create_subprocess_exec(*self.command,
                cwd=self.directory, env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_LINE, start_new_session=True, umask=0o077))
            try:
                self.process = await asyncio.shield(launch)
            except asyncio.CancelledError:
                self.process = await launch
                raise
            self.task = asyncio.create_task(self._watch())
        except BaseException:
            self.state = 'uncertain'
            await asyncio.shield(self.close())
            raise ValueError('Private CLI launch was not confirmed.') from None

    async def _watch(self):
        try:
            async with asyncio.timeout(self.seconds):
                while raw := await self.process.stdout.readline():
                    if len(raw) > MAX_LINE or not raw.endswith(b'\n'):
                        raise ValueError
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError
                    if set(value) == {'challenge'} and self.challenge is None and not self.submitted:
                        self.challenge = authorization_url(value['challenge'])
                    elif set(value) == {'verified'} and self.submitted and self.result is None:
                        self.result = metadata(value['verified'])
                    else:
                        raise ValueError
                code = await self.process.wait()
                if code != 0 or self.result is None:
                    raise ValueError
            await self._reap()
            self.challenge = None
            self.state = 'authenticated'
        except asyncio.CancelledError:
            raise
        except Exception:
            self.result = self.challenge = None
            self.state = 'uncertain' if self.submitted else 'failed'
            await self._reap()

    async def submit(self, code):
        if (self.state != 'active' or self.challenge is None or self.submitted or self.closed
                or not isinstance(code, str) or not 1 <= len(code) <= 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in code)):
            raise ValueError('Private CLI input is unavailable.')
        self.submitted = True
        self.challenge = None
        try:
            self.process.stdin.write(code.encode('utf-8') + b'\n')
            await self.process.stdin.drain()
            self.process.stdin.close()
        except Exception:
            self.state = 'uncertain'
            await self.close()
            raise ValueError('Private CLI input outcome is unknown; do not replay.') from None

    async def _reap(self):
        if self.process is None or self.reaped:
            return
        # Reap the exact group created by this process; never caller-supplied PIDs.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)
        await self.process.wait()
        self.reaped = True

    async def close(self):
        self.closed = True
        self.challenge = None
        if self.task and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self._reap()
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = None
