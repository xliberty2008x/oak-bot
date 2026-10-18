"""Separate-origin human input and status-only Unix control for private CLI jobs."""

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
import socket
import sqlite3
import struct
import time

from aiohttp import web

from .cli_contract import PATH, PROVIDER, TERMINAL, identifier, status
from .signin_broker import origin


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Lease:
    binding: dict
    id: str = field(default_factory=lambda: secrets.token_hex(16))
    state: str = 'waiting'
    cleanup: str = 'waiting'
    ticket: str = ''
    ticket_expires: float = 0
    human: str = ''
    heartbeat: float = 0
    human_deadline: float = 0
    session_expires: float = 0
    process: object = None
    revision: str = field(default_factory=lambda: secrets.token_hex(16))
    action: str = ''
    fingerprint: bytes = b''
    key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CLIBroker:
    def __init__(self, human_origin, parent_origin, gateway_uid, database, *, process_factory=None,
                 boundary_check=None, peer_check=None):
        self.origin, self.parent = origin(human_origin), origin(parent_origin)
        if self.origin == self.parent or type(gateway_uid) is not int or gateway_uid <= 0:
            raise ValueError('Separate origins and identities are required.')
        self.gateway_uid, self.factory = gateway_uid, process_factory
        self.boundary_check, self.peer_check = boundary_check, peer_check
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS cli_receipts (
            request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, binding TEXT NOT NULL,
            state TEXT NOT NULL, cleanup TEXT NOT NULL)''')
        with self.db:
            self.db.execute("UPDATE cli_receipts SET state='uncertain',cleanup='uncertain' "
                            "WHERE state NOT IN ('cancelled','expired','failed','uncertain')")
        self.leases = {}
        self.sweeper = None
        self.human = web.Application(client_max_size=32768, middlewares=[self.human_guard])
        self.control = web.Application(client_max_size=8192, middlewares=[self.control_guard])
        self.control.add_routes([web.post('/health', self.health), web.post('/create', self.create),
            web.post('/status', self.control_status), web.post('/cancel', self.cancel), web.post('/use', self.use)])
        self.human.add_routes([web.post(PATH+'api/attach', self.attach), web.post(PATH+'api/form', self.form),
            web.post(PATH+'api/submit', self.submit), web.post(PATH+'api/cancel', self.cancel_human),
            web.post(PATH+'api/detach', self.detach)])

    def boundary(self):
        if self.boundary_check is None or self.boundary_check() is not True:
            raise ValueError('Private CLI boundary unavailable.')

    @web.middleware
    async def human_guard(self, request, handler):
        try:
            if request.method == 'POST' and request.headers.get('Origin') != self.origin:
                raise web.HTTPForbidden()
            self.boundary()
            response = await handler(request)
        except web.HTTPException as error:
            response = web.json_response({'error': 'Приватний канал CLI недоступний.'}, status=error.status)
        except Exception:
            response = web.json_response({'error': 'Дію не підтверджено. Введення не повторюється.'}, status=409)
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy':
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors " + self.parent})
        return response

    @web.middleware
    async def control_guard(self, request, handler):
        try:
            sock = request.transport.get_extra_info('socket')
            accepted = (self.peer_check(request) if self.peer_check else sock is not None
                and sock.family == socket.AF_UNIX and struct.unpack('3i', sock.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1] == self.gateway_uid)
            if not accepted:
                raise ValueError
            self.boundary()
            return await handler(request)
        except Exception:
            return web.json_response({'error': 'Private CLI control unavailable.'}, status=409)

    def binding(self, value):
        fields = {'request_id', 'owner', 'chat_id', 'thread_id', 'turn_id', 'runtime_epoch',
                  'panel_hash', 'expires', 'provider'}
        if (not isinstance(value, dict) or set(value) != fields or not identifier(value['request_id'])
                or value['provider'] != PROVIDER or type(value['owner']) is not int or type(value['chat_id']) is not int
                or any(not isinstance(value[k], str) or not 0 < len(value[k]) <= 200
                       for k in ('thread_id', 'turn_id', 'runtime_epoch'))
                or not isinstance(value['panel_hash'], str) or len(value['panel_hash']) != 64
                or any(c not in '0123456789abcdef' for c in value['panel_hash'])
                or type(value['expires']) not in (int, float) or not time.time() < value['expires'] <= time.time()+301):
            raise ValueError('Invalid CLI binding.')
        return dict(value)

    def result(self, lease):
        return status({'request_id': lease.binding['request_id'], 'session_id': lease.id,
            'runtime_epoch': lease.binding['runtime_epoch'], 'provider': PROVIDER,
            'state': lease.state, 'cleanup': lease.cleanup, 'session_expires': lease.session_expires})

    def save(self, lease):
        with self.db:
            self.db.execute('UPDATE cli_receipts SET state=?,cleanup=? WHERE request_id=?',
                            (lease.state, lease.cleanup, lease.binding['request_id']))

    def expired(self, lease):
        now = time.time()
        deadline = lease.session_expires if lease.state == 'authenticated' else lease.binding['expires']
        return now >= lease.heartbeat or now >= deadline or (
            lease.state == 'active' and now >= lease.human_deadline)

    async def refresh(self, lease):
        if lease.state in TERMINAL:
            return
        if self.expired(lease):
            await self.finish(lease, 'expired')
        elif lease.process is not None and lease.process.state in TERMINAL | {'authenticated'}:
            state = lease.process.state
            if state == 'authenticated' and lease.state != 'authenticated':
                lease.state, lease.cleanup = state, 'retained'
                lease.session_expires = time.time() + 900
                self.save(lease)
            elif state != 'authenticated':
                await self.finish(lease, state)

    async def health(self, request):
        if await request.json() != {}:
            raise ValueError
        return web.json_response({'ready': self.factory is not None, 'protocol': 1,
                                  'providers': [PROVIDER] if self.factory else [], 'path': PATH})

    async def create(self, request):
        binding = self.binding(await request.json())
        if self.factory is None or len(self.leases) >= 16:
            raise ValueError('CLI unavailable.')
        ticket = secrets.token_urlsafe(32)
        lease = Lease(binding, ticket=digest(ticket), ticket_expires=min(time.time()+30, binding['expires']),
                      heartbeat=time.time()+45)
        with self.db:
            self.db.execute('INSERT INTO cli_receipts VALUES (?,?,?,?,?)',
                (binding['request_id'], lease.id, json.dumps(binding), lease.state, lease.cleanup))
        self.leases[binding['request_id']] = lease
        return web.json_response({**self.result(lease), 'origin': self.origin, 'path': PATH,
                                  'ticket': ticket, 'expires': lease.ticket_expires})

    async def attach(self, request):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {'request_id', 'ticket'} or not isinstance(value['ticket'], str):
            raise ValueError
        lease = self.leases.get(value['request_id'])
        if lease is None:
            raise ValueError
        async with lease.lock:
            if (lease.state != 'waiting' or self.expired(lease) or time.time() >= lease.ticket_expires
                    or not hmac.compare_digest(digest(value['ticket']), lease.ticket)):
                raise ValueError
            lease.ticket = ''
            lease.state = 'active'
            lease.human_deadline = time.time()+45
            self.save(lease)  # Never relaunch after failed/unknown process creation.
            lease.process = self.factory()
            try:
                await lease.process.start()
            except BaseException:
                await asyncio.shield(self.finish(lease, 'uncertain'))
                raise
            human = secrets.token_urlsafe(32)
            lease.human = digest(human)
            return web.json_response({'lease': human, 'provider': PROVIDER})

    async def human_lease(self, request, fields):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {'request_id'} | fields:
            raise ValueError
        lease = self.leases.get(value['request_id'])
        auth = request.headers.get('Authorization', '')
        if (lease is None or not lease.human or not auth.startswith('Bearer ') or len(auth) > 120
                or not hmac.compare_digest(digest(auth[7:]), lease.human)):
            raise ValueError
        return lease, value

    async def form(self, request):
        lease, _ = await self.human_lease(request, set())
        async with lease.lock:
            await self.refresh(lease)
            if lease.state == 'active':
                lease.human_deadline = time.time()+45
            challenge = lease.process.challenge if lease.process and lease.state == 'active' else None
            return web.json_response({'state': lease.state, 'revision': lease.revision,
                                      'authorization_url': challenge, 'submitted': bool(lease.action)})

    async def submit(self, request):
        lease, value = await self.human_lease(request, {'revision', 'action_id', 'code'})
        try:
            async with lease.lock:
                await self.refresh(lease)
                if (not identifier(value['action_id']) or value['revision'] != lease.revision
                        or not isinstance(value['code'], str) or len(value['code']) > 4096):
                    raise ValueError
                fingerprint = hmac.digest(lease.key, value['code'].encode(), 'sha256')
                if lease.action:
                    if lease.action == value['action_id'] and hmac.compare_digest(fingerprint, lease.fingerprint):
                        return web.json_response({'accepted': True})
                    raise ValueError('Input already consumed.')
                if lease.state != 'active' or lease.process is None or lease.process.challenge is None:
                    raise ValueError
                lease.action, lease.fingerprint = value['action_id'], fingerprint
                try:
                    await lease.process.submit(value['code'])
                except BaseException:
                    await asyncio.shield(self.finish(lease, 'uncertain'))
                    raise
                return web.json_response({'accepted': True})
        finally:
            value.pop('code', None)

    async def lookup(self, request):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {'request_id', 'session_id', 'runtime_epoch'}:
            raise ValueError
        lease = self.leases.get(value['request_id'])
        if lease is None:
            row = self.db.execute('SELECT * FROM cli_receipts WHERE request_id=?', (value['request_id'],)).fetchone()
            if row is None:
                raise ValueError
            lease = Lease(json.loads(row['binding']), id=row['session_id'], state=row['state'], cleanup=row['cleanup'])
        if lease.id != value['session_id'] or lease.binding['runtime_epoch'] != value['runtime_epoch']:
            raise ValueError
        return lease

    async def control_status(self, request):
        lease = await self.lookup(request)
        async with lease.lock:
            await self.refresh(lease)
            if lease.state not in TERMINAL:
                lease.heartbeat = time.time()+45
            return web.json_response(self.result(lease))

    async def use(self, request):
        lease = await self.lookup(request)
        async with lease.lock:
            await self.refresh(lease)
            if lease.state != 'authenticated' or lease.process is None or lease.process.result is None:
                raise ValueError
            return web.json_response({'verification': lease.process.result})

    async def cancel(self, request):
        lease = await self.lookup(request)
        async with lease.lock:
            await self.finish(lease, 'cancelled')
            return web.json_response(self.result(lease))

    async def cancel_human(self, request):
        lease, _ = await self.human_lease(request, set())
        async with lease.lock:
            await self.finish(lease, 'cancelled')
            return web.json_response({'state': lease.state})

    async def detach(self, request):
        lease, _ = await self.human_lease(request, set())
        async with lease.lock:
            await self.refresh(lease)
            if lease.state == 'authenticated':
                lease.human = ''
            else:
                await self.finish(lease, 'cancelled')
            return web.json_response({'state': lease.state})

    async def finish(self, lease, state):
        unowned = lease.process is None and lease.cleanup == 'uncertain'
        lease.state, lease.ticket, lease.human = state, '', ''
        lease.fingerprint, lease.key = b'', b''
        lease.cleanup = 'uncertain'
        self.save(lease)
        if lease.process is not None:
            await asyncio.wait_for(lease.process.close(), 10)
            lease.process = None
        lease.cleanup = 'uncertain' if unowned else 'confirmed'
        self.save(lease)

    async def sweep(self):
        while True:
            await asyncio.sleep(1)
            for lease in tuple(self.leases.values()):
                async with lease.lock:
                    try:
                        self.boundary()
                        await self.refresh(lease)
                    except Exception:
                        await self.finish(lease, 'uncertain')
                    if lease.state in TERMINAL and lease.cleanup == 'confirmed':
                        self.leases.pop(lease.binding['request_id'], None)

    async def close(self):
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        for lease in self.leases.values():
            await self.finish(lease, 'cancelled')
        self.db.close()
