"""Authenticated, ephemeral RFB bridge for manual control of the Oak desktop."""

import asyncio
import contextlib
import hashlib
import os
import secrets
import shutil
import socket
import time
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web


_KEYS = set(('Return Tab Escape BackSpace Delete Insert Left Right Up Down Home End '
             'Page_Up Page_Down space minus equal bracketleft bracketright backslash '
             'semicolon apostrophe comma period slash grave').split()) | set('abcdefghijklmnopqrstuvwxyz0123456789') | {f'F{i}' for i in range(1, 13)}


def valid_key(value):
    if not isinstance(value, str) or len(value) > 64:
        return False
    *modifiers, key = value.split('+')
    return (key in _KEYS and len(modifiers) <= 3 and len(set(modifiers)) == len(modifiers)
            and all(modifier in {'ctrl', 'alt', 'shift'} for modifier in modifiers))


class RemoteDesktop:
    TICKET_SECONDS = 30
    SESSION_SECONDS = 1800

    def __init__(self, gateway):
        self.gateway = gateway
        self.controller = gateway.controller
        self.current = None
        self.lock = asyncio.Lock()

    def status(self, owner):
        state = self.current
        computer = getattr(self.controller.tools, 'computer', None)
        return {'configured': computer is not None and shutil.which('x11vnc') is not None,
                'connected': bool(state and state.get('socket') is not None and state['socket'].prepared),
                'manual': self.controller.manual_owner is not None,
                'can_stop': bool(state and state['owner'] == owner)}

    def session_hash(self, request):
        authorization = request.headers.get('Authorization', '')
        value = authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('oak_session', '')
        if not value or len(value) > 256:
            raise web.HTTPUnauthorized(text='Увійди в панель Oak.')
        return hashlib.sha256(value.encode()).hexdigest()

    def valid(self, state):
        row = self.gateway.db.execute('SELECT owner,expires FROM web_sessions WHERE hash=?',
                                      (state['session'],)).fetchone()
        return bool(not state.get('finishing') and row and row[0] == state['owner'] and row[1] > time.time()
                    and row[0] in self.gateway.allowed and state['deadline'] > time.monotonic())

    async def start(self, request):
        owner = await self.gateway.confirmed_owner(request)
        session = self.session_hash(request)
        async with self.lock:
            if self.current is not None:
                raise web.HTTPConflict(text='Ручне керування вже відкрите. Заверши попереднє підключення.')
            if not self.status(owner)['configured']:
                raise web.HTTPServiceUnavailable(text='Віддалений робочий стіл не налаштовано.')
            try:
                await self.controller.pause_for_manual(owner)
            except (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError):
                raise web.HTTPConflict(text='Не вдалося підтвердити зупинку задач. Зупини активні задачі та спробуй ще раз.') from None
            ticket = secrets.token_urlsafe(32)
            now = time.monotonic()
            state = {'owner': owner, 'session': session, 'lease': secrets.token_urlsafe(16),
                     'ticket': hashlib.sha256(ticket.encode()).digest(),
                     'expires': now + self.TICKET_SECONDS, 'deadline': now + self.SESSION_SECONDS,
                     'socket': None, 'process': None, 'launch': None, 'transport': None, 'watch': None}
            self.current = state
            state['watch'] = asyncio.create_task(self.watch(state))
            return web.json_response({'ticket': ticket, 'protocol': 'oak-remote.' + ticket, 'lease': state['lease'],
                                      'expires_in': self.TICKET_SECONDS})

    async def stop(self, request):
        owner = await self.gateway.confirmed_owner(request)
        self.session_hash(request)
        async with self.lock:
            if self.current is not None and self.current['owner'] != owner:
                raise web.HTTPForbidden(text='Робочим столом керує інший власник.')
            if self.current is not None:
                await self.finish(self.current)
        return web.json_response(self.status(owner))

    async def type(self, request):
        owner = self.gateway.owner(request)
        session = self.session_hash(request)
        data = await request.json()
        value = data.get('text') if isinstance(data, dict) else None
        key = data.get('key') if isinstance(data, dict) else None
        is_text = isinstance(data, dict) and set(data) == {'text', 'lease'} and isinstance(value, str) and 1 <= len(value) <= 1000 and '\x00' not in value
        is_key = isinstance(data, dict) and set(data) == {'key', 'lease'} and valid_key(key)
        if not is_text and not is_key:
            raise web.HTTPBadRequest(text='Вкажи текст від 1 до 1000 символів або підтриману клавішу.')
        state = self.current
        if state is None or state['socket'] is None or not state['socket'].prepared or state['socket'].closed or not self.valid(state):
            raise web.HTTPConflict(text='Спочатку підключися до робочого столу.')
        if state['owner'] != owner or state['session'] != session:
            raise web.HTTPForbidden(text='Цим підключенням керує інший сеанс панелі.')
        if not isinstance(data['lease'], str) or not secrets.compare_digest(data['lease'], state['lease']):
            raise web.HTTPConflict(text='Це підключення вже завершено. Введення не повторено.')
        task = asyncio.current_task()
        pending = state.setdefault('typing', set())
        pending.add(task)
        try:
            computer = self.controller.tools.computer
            async with asyncio.timeout(12), computer._lock:
                if self.current is not state or not self.valid(state) or state['socket'].closed:
                    raise web.HTTPConflict(text='Ручне керування вже завершено.')
                # Human input only: reuse the Unicode mapping/cache handling,
                # without the screenshot/artifact/model path of run().
                if is_text:
                    await computer._type(value)
                else:
                    await computer._key(key)
                    await asyncio.sleep(0.05)
            return web.json_response({'ok': True})
        except (RuntimeError, OSError, asyncio.TimeoutError):
            raise web.HTTPServiceUnavailable(text='Не вдалося підтвердити введення. Перевір поле перед повтором.') from None
        finally:
            pending.discard(task)

    async def watch(self, state):
        try:
            while self.current is state:
                await asyncio.sleep(1)
                if not self.valid(state) or (state['ticket'] is not None and time.monotonic() >= state['expires']):
                    await self.finish(state)
                    return
        except asyncio.CancelledError:
            pass

    async def finish(self, state):
        if state.get('cleanup') is None:
            state['finishing'] = True
            state['cleanup'] = asyncio.create_task(self._finish(state))
        # HTTP stop cancellation must not abandon an X11 process or release
        # the dispatch gate while it is still accepting input.
        await asyncio.shield(state['cleanup'])

    async def _finish(self, state):
        # Invalidate immediately; leave the manual gate in place until X11
        # input and the bridge process have both stopped.
        try:
            state['ticket'] = None
            watch = state['watch']
            if watch is not None and watch is not asyncio.current_task():
                watch.cancel()
            transport = state['transport']
            if transport is not None:
                transport.close()
            typing = tuple(state.get('typing', ()))
            for task in typing:
                task.cancel()
            if typing:
                await asyncio.gather(*typing, return_exceptions=True)
            launch = state['launch']
            if launch is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(launch)
            process = state['process']
            if process is not None:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
                # x11vnc -clear_keys releases keyboard keys, but a lost RFB
                # connection can leave a mouse button pressed in X11.
                # Release only pointer buttons, on this configured display.
                with contextlib.suppress(RuntimeError, OSError):
                    await self.controller.tools.computer._exec('xdotool',
                        *(part for button in range(1, 10) for part in ('mouseup', str(button))))
            ws = state['socket']
            if ws is not None and ws.prepared and not ws.closed:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(code=1000, message=b'Remote session ended'), 2)
        finally:
            if self.current is state:
                self.current = None
                self.controller.manual_owner = None

    async def socket(self, request):
        # A WS cannot set Authorization in browsers. The one-use subprotocol
        # ticket binds it to the authenticated owner/session that requested it.
        origin = request.headers.get('Origin', '')
        expected = {('https', urlsplit(self.gateway.public_url).netloc),
                    ('https' if request.secure else 'http', request.host)}
        parsed = urlsplit(origin)
        if not origin or (parsed.scheme, parsed.netloc) not in expected:
            raise web.HTTPForbidden(text='Untrusted origin.')
        protocols = [part.strip() for part in request.headers.get('Sec-WebSocket-Protocol', '').split(',')]
        if len(protocols) != 1 or not protocols[0].startswith('oak-remote.') or len(protocols[0]) != 54:
            raise web.HTTPUnauthorized(text='Підключення потребує нового підтвердження.')
        ticket = protocols[0][11:]
        async with self.lock:
            state = self.current
            if (state is None or state['ticket'] is None or not self.valid(state)
                    or time.monotonic() >= state['expires']
                    or not secrets.compare_digest(state['ticket'], hashlib.sha256(ticket.encode()).digest())):
                raise web.HTTPUnauthorized(text='Підключення прострочене. Відкрий його ще раз.')
            state['ticket'] = None  # Consume before any I/O or protocol upgrade.
        ws = web.WebSocketResponse(protocols=protocols, heartbeat=15, max_msg_size=256 * 1024, compress=False)
        state['socket'] = ws
        tasks = []
        child = None
        try:
            transport, child = socket.socketpair()
            transport.setblocking(False)
            state['transport'] = transport
            computer = self.controller.tools.computer
            async def launch():
                state['process'] = await asyncio.create_subprocess_exec(
                    'x11vnc', '-norc', '-quiet', '-inetd', '-nopw', '-display', computer.display,
                    '-nosel', '-noremote', '-clear_keys', '-timeout', '10', '-wait', '30',
                    stdin=child, stdout=child, stderr=asyncio.subprocess.DEVNULL,
                    env={**os.environ, 'DISPLAY': computer.display})
            state['launch'] = asyncio.create_task(launch())
            await asyncio.shield(state['launch'])
            child.close()
            child = None
            loop = asyncio.get_running_loop()
            greeting = b''
            async with asyncio.timeout(8):
                while len(greeting) < 12:
                    chunk = await loop.sock_recv(transport, 12 - len(greeting))
                    if not chunk:
                        raise OSError('Desktop bridge unavailable.')
                    greeting += chunk
            if not greeting.startswith(b'RFB ') or not self.valid(state) or state.get('finishing'):
                raise OSError('Desktop bridge unavailable.')
            await ws.prepare(request)
            await ws.send_bytes(greeting)

            async def to_desktop():
                async for message in ws:
                    if message.type != WSMsgType.BINARY:
                        break
                    if not self.valid(state):
                        break
                    await loop.sock_sendall(transport, message.data)

            async def to_owner():
                while True:
                    data = await loop.sock_recv(transport, 64 * 1024)
                    if not data or not self.valid(state):
                        return
                    await ws.send_bytes(data)

            tasks = [asyncio.create_task(to_desktop()), asyncio.create_task(to_owner())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            return ws
        except (OSError, ConnectionError, asyncio.TimeoutError):
            if ws.prepared:
                return ws
            raise web.HTTPServiceUnavailable(text='Не вдалося підключитися до робочого столу.') from None
        finally:
            if state['launch'] is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(state['launch'])
            if child is not None:
                child.close()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.shield(self.finish(state))

    async def close(self):
        if self.current is not None:
            await self.finish(self.current)
