"""Status-only CLI bridge; private input/output never enter the gateway."""

import asyncio
import hashlib
import json
from pathlib import Path
import re
import time

from .cli_contract import PATH, PROVIDER, TERMINAL, metadata, status
from .login import WebsiteLogin
from .requests import RequestUnavailable
from .signin_broker import origin


class CLILogin:
    # The same Unix boundary, original-turn and panel-session authority checks.
    boundary, ipc = WebsiteLogin.boundary, WebsiteLogin.ipc
    scope, spawn = WebsiteLogin.scope, WebsiteLogin.spawn

    def live(self, row, attempt):
        return (row['runtime_epoch'] == getattr(self.c.client, 'runtime_epoch', None)
                and WebsiteLogin.live(self, row, attempt))

    def __init__(self, gateway, config):
        if (not isinstance(config, dict) or set(config) != {'socket', 'origin'}
                or not isinstance(config['socket'], str) or not config['socket'].startswith('/')):
            raise ValueError('Private CLI requires a Unix socket and separate origin.')
        self.gateway, self.c, self.db = gateway, gateway.controller, gateway.db
        self.origin = origin(config['origin'])
        if self.origin == origin(gateway.public_url):
            raise ValueError('Private CLI requires a separate origin.')
        self.socket = str(Path(config['socket']).resolve())
        roots = [Path(self.c.cwd).resolve(), Path(self.c.config['state_dir']).resolve()]
        if self.c.config.get('telegram_api_directory'):
            roots.append(Path(self.c.config['telegram_api_directory']).resolve())
        if any(Path(self.socket).is_relative_to(root) for root in roots):
            raise ValueError('Private CLI control must stay outside ordinary data.')
        self.execution = self.c.config.get('execution')
        if not self.execution:
            raise ValueError('A provisioned execution boundary is required.')
        self.db.execute('''CREATE TABLE IF NOT EXISTS cli_login_attempts (
            request_id TEXT PRIMARY KEY, session_id TEXT, runtime_epoch TEXT NOT NULL,
            phase TEXT NOT NULL, cleanup TEXT NOT NULL, panel_hash TEXT NOT NULL,
            session_expires REAL NOT NULL DEFAULT 0, verification TEXT)''')
        with self.db:
            self.db.execute("UPDATE cli_login_attempts SET phase='uncertain',cleanup='uncertain',verification=NULL "
                            "WHERE phase NOT IN ('cancelled','expired','failed','uncertain')")
        self.tasks = set()
        self.c.requests.cli_login = self

    async def available(self):
        try:
            return await self.ipc('/health', {}) == {
                'ready': True, 'protocol': 1, 'providers': [PROVIDER], 'path': PATH}
        except RequestUnavailable:
            return False

    def attempt(self, identifier):
        return self.db.execute('SELECT * FROM cli_login_attempts WHERE request_id=?', (identifier,)).fetchone()

    def validate(self, value, row, attempt=None):
        value = status(value)
        if (value['request_id'] != row['id'] or value['runtime_epoch'] != row['runtime_epoch']
                or attempt is not None and value['session_id'] != attempt['session_id']):
            raise RequestUnavailable('Wrong CLI session.')
        return value

    async def start(self, owner, chat, identifier, request):
        async with self.c._lock(chat):
            row = self.c.requests.row(owner, chat, identifier)
            if (row['kind'] != 'cli_auth' or row['outcome'] != 'pending' or not self.c.requests._live(row)
                    or row['expires'] <= time.time() or self.attempt(identifier) is not None):
                raise RequestUnavailable('Private CLI request ended or was already attempted.')
            header = request.headers.get('Authorization', '')
            panel = header[7:] if header.startswith('Bearer ') else request.cookies.get('oak_session', '')
            panel_hash = hashlib.sha256(panel.encode()).hexdigest()
            if not self.live(row, {'panel_hash': panel_hash}):
                raise RequestUnavailable('Private CLI panel session ended.')
            binding = {key: row[key] for key in ('owner','chat_id','thread_id','turn_id','runtime_epoch','expires')}
            binding.update(request_id=identifier, panel_hash=panel_hash, provider=PROVIDER)
            with self.db:
                self.db.execute('INSERT INTO cli_login_attempts VALUES (?,NULL,?,?,?,?,0,NULL)',
                                (identifier, row['runtime_epoch'], 'attempted', 'waiting', panel_hash))
        try:
            value = await self.ipc('/create', binding)
            extra = {key: value.pop(key) for key in ('origin', 'path', 'ticket', 'expires')}
            self.validate(value, row)
            if (value['state'] != 'waiting' or extra['origin'] != self.origin or extra['path'] != PATH
                    or not isinstance(extra['ticket'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', extra['ticket'])
                    or type(extra['expires']) not in (int, float) or not time.time() < extra['expires'] <= row['expires']):
                raise ValueError
        except Exception:
            with self.db:
                self.db.execute("UPDATE cli_login_attempts SET phase='uncertain',cleanup='uncertain' WHERE request_id=?", (identifier,))
            raise RequestUnavailable('Private CLI dispatch is uncertain; it will not be replayed.') from None
        with self.db:
            self.db.execute("UPDATE cli_login_attempts SET session_id=?,phase='waiting' WHERE request_id=?", (value['session_id'], identifier))
        fresh = self.c.requests.row(owner, chat, identifier)
        if fresh['outcome'] != 'pending' or not self.live(fresh, self.attempt(identifier)) or fresh['expires'] <= time.time():
            self.revoke(identifier, force=True)
            raise RequestUnavailable('Private CLI request ended.')
        self.spawn(self.monitor(owner, chat, identifier))
        return {'request_id': identifier, 'provider': PROVIDER, 'simulated': False, **extra}

    async def monitor(self, owner, chat, identifier):
        try:
            while True:
                row, attempt = self.c.requests.row(owner, chat, identifier), self.attempt(identifier)
                if not self.live(row, attempt) or row['outcome'] in TERMINAL:
                    await self.cleanup(identifier)
                    if row['outcome'] == 'pending':
                        self.c.requests._terminal(row, 'cancelled')
                    return
                value = self.validate(await self.ipc('/status', self.scope(row, attempt)), row, attempt)
                if not self.live(row, attempt):
                    await self.cleanup(identifier)
                    return
                verification = None
                if value['state'] == 'authenticated':
                    if value['cleanup'] != 'retained' or not time.time() < value['session_expires'] <= time.time()+901:
                        raise ValueError
                    result = await self.ipc('/use', self.scope(row, attempt))
                    if set(result) != {'verification'}:
                        raise ValueError
                    verification = metadata(result['verification'])
                    if not self.live(row, attempt):
                        await self.cleanup(identifier)
                        return
                with self.db:
                    self.db.execute('UPDATE cli_login_attempts SET phase=?,cleanup=?,session_expires=?,verification=? WHERE request_id=?',
                        (value['state'], value['cleanup'], value['session_expires'], json.dumps(verification) if verification else None, identifier))
                if row['outcome'] == 'pending' and value['state'] in TERMINAL | {'authenticated'}:
                    async with self.c._lock(chat):
                        fresh = self.c.requests.row(owner, chat, identifier)
                        if fresh['outcome'] == 'pending':
                            outcome = value['state'] if self.live(fresh, self.attempt(identifier)) else 'cancelled'
                            self.c.requests._terminal(fresh, outcome)
                if value['state'] in TERMINAL:
                    return
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            with self.db:
                self.db.execute("UPDATE cli_login_attempts SET phase='uncertain',verification=NULL WHERE request_id=?", (identifier,))
            # No retry of dispatch/input. The broker expires without a heartbeat.

    def snapshot(self, identifier):
        row = self.attempt(identifier)
        return ({key: row[key] for key in ('phase','cleanup','session_expires')} if row else
                {'phase': 'available', 'cleanup': 'waiting', 'session_expires': 0})

    def response(self, identifier, outcome):
        attempt = self.attempt(identifier)
        verified = metadata(json.loads(attempt['verification'])) if outcome == 'authenticated' and attempt and attempt['verification'] else None
        return {'provider': PROVIDER, 'request_id': identifier, 'outcome': outcome,
                'authenticated': verified is not None, 'session_available': verified is not None,
                'reason': 'private_cli_verified' if verified else 'private_cli_'+outcome,
                **({'verification': verified} if verified else {})}

    def revoke(self, identifier, force=False):
        attempt = self.attempt(identifier)
        if attempt and attempt['cleanup'] != 'confirmed' and (force or attempt['phase'] != 'authenticated'):
            self.spawn(self.cleanup(identifier))

    async def cleanup(self, identifier):
        attempt = self.attempt(identifier)
        row = self.db.execute('SELECT * FROM input_requests WHERE id=?', (identifier,)).fetchone()
        if not attempt or not row or attempt['cleanup'] == 'confirmed':
            return
        try:
            value = self.validate(await self.ipc('/cancel', self.scope(row, attempt)), row, attempt)
            confirmed = value['state'] == 'cancelled' and value['cleanup'] == 'confirmed'
        except Exception:
            confirmed = False
        with self.db:
            self.db.execute('UPDATE cli_login_attempts SET phase=?,cleanup=?,verification=NULL WHERE request_id=?',
                            ('cancelled' if confirmed else 'uncertain', 'confirmed' if confirmed else 'uncertain', identifier))

    def end_turn(self, thread, turn):
        for row in self.db.execute("SELECT id FROM input_requests WHERE thread_id=? AND turn_id=? AND kind='cli_auth'", (thread, turn)):
            self.revoke(row['id'], force=True)

    async def close(self):
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        for row in self.db.execute("SELECT request_id FROM cli_login_attempts WHERE cleanup!='confirmed'").fetchall():
            await self.cleanup(row['request_id'])
