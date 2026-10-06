"""Owned, temporary HTTPS preview tunnel.

Quick Tunnel hostnames change on every start and do not support SSE. Use the
web gateway's long-poll transport here; production should use a named tunnel
or a TLS reverse proxy with a stable hostname and streaming support.
"""

import asyncio
import os
import re
import shutil


class PreviewTunnel:
    def __init__(self):
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
                match = re.search(rb'https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com(?=[\s/|]|$)', tail)
                if match:
                    self._url_ready.set_result(match[0].decode('ascii'))
            # Consume output continuously; never forward diagnostics or tokens.

    async def start(self, port):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Preview tunnel requires a valid local TCP port.')
        if self.process is not None:
            raise RuntimeError('Preview tunnel is already started.')
        binary = shutil.which('cloudflared')
        if not binary:
            raise RuntimeError('Install cloudflared to enable the HTTPS preview tunnel.')
        self._closing = False
        self._url_ready = asyncio.get_running_loop().create_future()
        self.process = await asyncio.create_subprocess_exec(
            binary, 'tunnel', '--config', '/dev/null', '--url', f'http://127.0.0.1:{port}',
            '--protocol', 'http2', '--no-autoupdate', stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={key: value for key, value in os.environ.items()
                 if not key.startswith(('TUNNEL_', 'CLOUDFLARED_'))})
        self._exit = asyncio.create_task(self.process.wait())
        self._drains = [asyncio.create_task(self._drain(self.process.stdout)),
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
        """Monitor in the service TaskGroup; unexpected exit requests restart."""
        if self._exit is None:
            raise RuntimeError('Preview tunnel has not been started.')
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
