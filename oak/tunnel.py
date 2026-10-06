"""Owned, temporary HTTPS preview tunnel.

Preview hostnames are temporary. Use the web gateway's long-poll transport
here; production should use a stable hostname and streaming support.
"""

import asyncio
import os
from pathlib import Path
import re
import shutil

from aiohttp import ClientError, ClientSession, ClientTimeout


class PreviewTunnel:
    def __init__(self, provider='quick', state_dir=None):
        if provider not in {'quick', 'localhost'}:
            raise ValueError('Unknown HTTPS preview tunnel provider.')
        if provider == 'localhost' and state_dir is None:
            raise ValueError('The SSH preview tunnel requires a private state directory.')
        self.provider = provider
        self.state_dir = Path(state_dir).expanduser().resolve() if state_dir is not None else None
        suffix = rb'trycloudflare\.com' if provider == 'quick' else rb'lhr\.life'
        self._url_pattern = re.compile(rb'https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.' + suffix + rb'(?=[\s/|]|$)')
        self.process = None
        self.url = None
        self._url_ready = None
        self._exit = None
        self._drains = []
        self._closing = False

    async def _drain(self, stream, discover=False):
        tail = b''
        while data := await stream.read(4096):
            if discover and not self._url_ready.done():
                tail = (tail + data)[-8192:]
                match = self._url_pattern.search(tail)
                if match:
                    self._url_ready.set_result(match[0].decode('ascii'))
            # Consume output continuously; never forward diagnostics or tokens.

    async def start(self, port):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Preview tunnel requires a valid local TCP port.')
        if self.process is not None:
            raise RuntimeError('Preview tunnel is already started.')
        binary_name = 'cloudflared' if self.provider == 'quick' else 'ssh'
        binary = shutil.which(binary_name)
        if not binary:
            raise RuntimeError(f'Install {binary_name} to enable the HTTPS preview tunnel.')
        if self.provider == 'quick':
            arguments = [binary, 'tunnel', '--config', '/dev/null', '--url', f'http://127.0.0.1:{port}',
                         '--protocol', 'http2', '--no-autoupdate']
        else:
            arguments = [binary, '-F', '/dev/null', '-T',
                         '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                         '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30',
                         '-o', 'ServerAliveCountMax=3', '-o', 'IdentityAgent=none',
                         '-o', 'IdentityFile=none', '-o', 'PubkeyAuthentication=no',
                         '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
                         '-o', 'StrictHostKeyChecking=accept-new',
                         '-o', 'UserKnownHostsFile=' + str(self.state_dir / 'localhost-run-known-hosts'),
                         '-R', f'80:127.0.0.1:{port}', 'nokey@localhost.run']
        self._closing = False
        self._url_ready = asyncio.get_running_loop().create_future()
        self.process = await asyncio.create_subprocess_exec(
            *arguments, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={key: value for key, value in os.environ.items()
                 if not key.startswith(('TUNNEL_', 'CLOUDFLARED_'))
                 and (self.provider == 'quick' or key not in {'SSH_AUTH_SOCK', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE'})})
        self._exit = asyncio.create_task(self.process.wait())
        self._drains = [asyncio.create_task(self._drain(self.process.stdout, discover=self.provider == 'localhost')),
                        asyncio.create_task(self._drain(self.process.stderr, discover=True))]
        try:
            done, _ = await asyncio.wait((self._url_ready, self._exit), timeout=60,
                                         return_when=asyncio.FIRST_COMPLETED)
            if self._exit in done:
                raise RuntimeError('HTTPS preview tunnel exited before becoming ready.')
            if self._url_ready not in done:
                raise TimeoutError('HTTPS preview tunnel did not become ready within 60 seconds.')
            self.url = self._url_ready.result()
            return self.url
        except BaseException:
            await self.close()
            raise

    async def wait(self):
        """Monitor process exit and lost SSH mappings in the service TaskGroup."""
        if self._exit is None:
            raise RuntimeError('Preview tunnel has not been started.')
        if self.provider == 'localhost':
            missing = 0
            async with ClientSession(timeout=ClientTimeout(total=10)) as client:
                while not self._exit.done():
                    done, _ = await asyncio.wait((self._exit,), timeout=30)
                    if done:
                        break
                    try:
                        async with client.get(self.url, allow_redirects=False) as response:
                            lost = False
                            if response.status == 502:
                                body = b''
                                while len(body) < 32:
                                    chunk = await response.content.read(32 - len(body))
                                    if not chunk:
                                        break
                                    body += chunk
                                lost = body.strip() == b'no tunnel'
                    except (ClientError, asyncio.TimeoutError):
                        missing = 0
                        continue
                    missing = missing + 1 if lost else 0
                    if missing >= 2 and not self._closing:
                        raise RuntimeError('HTTPS preview tunnel lost its public mapping.')
        code = await asyncio.shield(self._exit)
        if not self._closing:
            raise RuntimeError(f'HTTPS preview tunnel exited (code {code}).')
        return code

    async def close(self):
        self._closing = True
        process = self.process
        if process is not None and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(self._exit), timeout=5)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await self._exit
        if self._exit is not None:
            await asyncio.shield(self._exit)
        for task in self._drains:
            task.cancel()
        await asyncio.gather(*self._drains, return_exceptions=True)
        self._drains.clear()
        if self._url_ready and not self._url_ready.done():
            self._url_ready.cancel()
        self.process = None
        self.url = None
