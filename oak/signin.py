"""Status-only gateway bridge to the synthetic broker's private Unix control app."""

import asyncio
import hashlib
import re
import time

from aiohttp import ClientSession, ClientTimeout, UnixConnector

from .requests import RequestUnavailable
from .signin_broker import origin


class SignInBridge:
    def __init__(self, gateway, config):
        if (not isinstance(config, dict) or set(config) != {'socket', 'origin', 'synthetic_only'}
                or config['synthetic_only'] is not True or not isinstance(config['socket'], str)
                or not config['socket'].startswith('/')):
            raise ValueError('Only an explicit synthetic broker is supported.')
        self.gateway, self.c, self.db = gateway, gateway.controller, gateway.db
        self.socket = config['socket']
        self.origin = origin(config['origin'], loopback=True)
        self.tasks = set()
        self.db.executescript('''CREATE TABLE IF NOT EXISTS signin_attempts (
            request_id TEXT PRIMARY KEY, session_id TEXT, runtime_epoch TEXT NOT NULL,
            phase TEXT NOT NULL, cleanup TEXT NOT NULL, panel_hash TEXT);''')
        if 'panel_hash' not in {r[1] for r in self.db.execute('PRAGMA table_info(signin_attempts)')}:
            self.db.execute('ALTER TABLE signin_attempts ADD COLUMN panel_hash TEXT')
        with self.db:
            self.db.execute("UPDATE signin_attempts SET phase='uncertain',cleanup='uncertain' "
                            "WHERE phase IN ('attempted','waiting') OR cleanup='attempted'")
        self.c.requests.broker = self

    async def ipc(self, route, value):
        # The TCP human app has no control routes. No shared key is placed in
        # model environment, JSON state, URLs or ordinary logs.
        try:
            async with ClientSession(connector=UnixConnector(path=self.socket),
                                     timeout=ClientTimeout(total=8)) as client:
                async with client.post('http://broker' + route, json=value) as response:
                    if response.status != 200:
                        raise ValueError
                    if response.content_length is not None and response.content_length > 4096:
                        raise ValueError
                    raw = await response.content.read(4097)
                    if len(raw) > 4096:
                        raise ValueError
                    import json
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ValueError
                    return result
        except Exception:
            raise RequestUnavailable('Broker control unavailable.') from None

    def scope(self, row, attempt):
        return {'request_id': row['id'], 'session_id': attempt['session_id'],
                'runtime_epoch': row['runtime_epoch']}

    def launch_binding(self, row, request):
        authorization = request.headers.get('Authorization', '')
        panel = authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('oak_session', '')
        return {'request_id': row['id'], 'owner': row['owner'], 'chat_id': row['chat_id'],
                'thread_id': row['thread_id'], 'turn_id': row['turn_id'], 'runtime_epoch': row['runtime_epoch'],
                'panel_hash': hashlib.sha256(panel.encode()).hexdigest(), 'expires': row['expires'],
                'provider': 'oak_synthetic'}

    async def start(self, owner, chat, identifier, request):
        async with self.c._lock(chat):
            row = self.c.requests.row(owner, chat, identifier)
            import json
            if (row['kind'] != 'sign_in' or json.loads(row['form']).get('provider') != 'oak_synthetic'
                    or row['outcome'] != 'pending' or not self.c.requests._live(row)
                    or row['expires'] <= time.time()):
                raise RequestUnavailable('Sign-in request unavailable.')
            if self.db.execute('SELECT 1 FROM signin_attempts WHERE request_id=?', (identifier,)).fetchone():
                raise RequestUnavailable('Sign-in was already attempted; it is not replayed.')
            binding = self.launch_binding(row, request)
            with self.db:
                self.db.execute('INSERT INTO signin_attempts VALUES (?,NULL,?,?,?,?)',
                                (identifier, row['runtime_epoch'], 'attempted', 'waiting', binding['panel_hash']))
        # Dispatch intent is committed before IPC, and no routing lock spans IPC.
        try:
            value = await self.ipc('/create', binding)
            if (set(value) != {'session_id','request_id','provider','simulated','state','runtime_epoch','cleanup','origin','ticket','expires'}
                    or value['request_id'] != identifier or value['runtime_epoch'] != row['runtime_epoch']
                    or value['provider'] != 'oak_synthetic' or value['simulated'] is not True
                    or value['state'] != 'waiting' or value['origin'] != self.origin
                    or value['cleanup'] != 'waiting'
                    or not re.fullmatch(r'[a-f0-9]{32}', str(value['session_id']))
                    or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', str(value['ticket']))
                    or type(value['expires']) not in (int,float) or not time.time() < value['expires'] <= row['expires']):
                raise RequestUnavailable('Broker response unavailable.')
        except Exception:
            with self.db:
                self.db.execute("UPDATE signin_attempts SET phase='uncertain',cleanup='uncertain' WHERE request_id=?", (identifier,))
            raise RequestUnavailable('Broker dispatch is uncertain; it is not replayed.') from None
        async with self.c._lock(chat):
            fresh = self.c.requests.row(owner, chat, identifier)
            with self.db:
                self.db.execute("UPDATE signin_attempts SET session_id=?,phase='waiting' WHERE request_id=?",
                                (value['session_id'], identifier))
            if fresh['outcome'] != 'pending' or not self.c.requests._live(fresh) or fresh['expires'] <= time.time():
                self.revoke(identifier)
                raise RequestUnavailable('Sign-in request ended.')
        self.spawn(self.monitor(owner, chat, identifier))
        # Ticket exists only in the authenticated human response and broker RAM.
        return {'origin': self.origin, 'ticket': value['ticket'], 'request_id': identifier,
                'expires': value['expires'], 'provider': 'oak_synthetic', 'simulated': True}

    def spawn(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def monitor(self, owner, chat, identifier):
        try:
            while True:
                row = self.c.requests.row(owner, chat, identifier)
                attempt = self.db.execute('SELECT * FROM signin_attempts WHERE request_id=?', (identifier,)).fetchone()
                if row['outcome'] != 'pending' or attempt['phase'] != 'waiting':
                    return
                session = self.db.execute('SELECT owner,expires FROM web_sessions WHERE hash=?', (attempt['panel_hash'],)).fetchone()
                if not session or session['owner'] != owner or session['expires'] <= time.time():
                    async with self.c._lock(chat):
                        self.c.requests._terminal(row, 'cancelled')
                    return
                value = await self.ipc('/status', self.scope(row, attempt))
                expected = {'session_id','request_id','provider','simulated','state','runtime_epoch','cleanup'}
                if (set(value) != expected or value['session_id'] != attempt['session_id']
                        or value['request_id'] != identifier or value['runtime_epoch'] != row['runtime_epoch']
                        or value['provider'] != 'oak_synthetic' or value['simulated'] is not True
                        or value['cleanup'] not in {'waiting','confirmed','uncertain'}
                        or value['state'] not in {'waiting','active','authenticated','cancelled','expired','failed'}):
                    raise RequestUnavailable('Broker status unavailable.')
                if value['state'] in {'authenticated','cancelled','expired','failed'}:
                    await self.complete(owner, chat, identifier, value['state'], value['cleanup'])
                    return
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            raise
        except Exception:
            with self.db:
                self.db.execute("UPDATE signin_attempts SET phase='uncertain',cleanup='uncertain' WHERE request_id=? AND phase='waiting'", (identifier,))

    async def complete(self, owner, chat, identifier, outcome, cleanup='confirmed'):
        if outcome not in {'authenticated','cancelled','expired','failed'} or cleanup not in {'confirmed','uncertain'}:
            raise RequestUnavailable('Invalid broker completion.')
        if outcome == 'authenticated' and cleanup != 'confirmed':
            outcome = 'failed'
        async with self.c._lock(chat):
            row = self.c.requests.row(owner, chat, identifier)
            attempt = self.db.execute('SELECT * FROM signin_attempts WHERE request_id=?', (identifier,)).fetchone()
            if not attempt or attempt['phase'] != 'waiting' or row['outcome'] != 'pending':
                return
            session = self.db.execute('SELECT owner,expires FROM web_sessions WHERE hash=?', (attempt['panel_hash'],)).fetchone()
            if (owner not in self.gateway.allowed or not session or session['owner'] != owner
                    or session['expires'] <= time.time()):
                self.c.requests._terminal(row, 'cancelled')
                return
            if not self.c.requests._live(row) or row['expires'] <= time.time():
                self.c.requests._terminal(row, 'expired')
                return
            # Both state transitions commit before waking the original native RPC.
            import json
            response = self.c.requests._response(row, outcome)
            with self.db:
                self.db.execute('UPDATE signin_attempts SET phase=?,cleanup=? WHERE request_id=?',
                                (outcome, cleanup, identifier))
                self.db.execute("UPDATE input_requests SET outcome=?,response=?,delivery='ready' WHERE id=? AND outcome='pending'",
                                (outcome, json.dumps(response), identifier))
            future = self.c.requests.waiters.get(identifier)
            if future is not None and not future.done():
                future.set_result(response)

    def revoke(self, identifier):
        row = self.db.execute('SELECT * FROM signin_attempts WHERE request_id=?', (identifier,)).fetchone()
        if not row or row['cleanup'] in {'attempted','confirmed'}:
            return
        with self.db:
            self.db.execute("UPDATE signin_attempts SET cleanup='attempted' WHERE request_id=?", (identifier,))
        self.spawn(self.cleanup(identifier))

    async def cleanup(self, identifier):
        attempt = self.db.execute('SELECT * FROM signin_attempts WHERE request_id=?', (identifier,)).fetchone()
        request = self.db.execute('SELECT * FROM input_requests WHERE id=?', (identifier,)).fetchone()
        if not attempt or not request:
            return
        try:
            if not attempt['session_id']:
                raise RequestUnavailable('Unknown broker session.')
            value = await self.ipc('/cancel', self.scope(request, attempt))
            if value.get('state') not in {'cancelled','expired','authenticated','failed'} or value.get('session_id') != attempt['session_id']:
                raise RequestUnavailable('Cleanup unavailable.')
            state = 'confirmed' if value.get('cleanup') == 'confirmed' else 'uncertain'
        except Exception:
            state = 'uncertain'
        with self.db:
            self.db.execute('UPDATE signin_attempts SET cleanup=? WHERE request_id=?', (state, identifier))
            if state == 'confirmed':
                self.db.execute("UPDATE signin_attempts SET phase=? WHERE request_id=? AND phase IN ('attempted','waiting','uncertain')",
                                (value['state'], identifier))

    def snapshot(self, identifier):
        row = self.db.execute('SELECT phase,cleanup FROM signin_attempts WHERE request_id=?', (identifier,)).fetchone()
        return dict(row) if row else {'phase': 'available', 'cleanup': 'waiting'}

    async def close(self):
        rows = self.db.execute("SELECT request_id FROM signin_attempts WHERE cleanup!='confirmed'").fetchall()
        for row in rows:
            self.revoke(row[0])
        if self.tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tuple(self.tasks), return_exceptions=True), 10)
            except asyncio.TimeoutError:
                pass
