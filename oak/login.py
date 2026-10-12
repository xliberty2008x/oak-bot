"""Status-only gateway bridge for real human website authentication."""

import asyncio
import hashlib
import json
import math
from pathlib import Path
import re
import time

from aiohttp import ClientSession, ClientTimeout, UnixConnector

from .requests import RequestUnavailable
from .signin_broker import origin

AGENT_WAIT_SECONDS = 20
AGENT_HUMAN_WAIT_SECONDS = 300
AGENT_POLL_SECONDS = 0.5
AGENT_READINESS_SECONDS = 10


class WebsiteLogin:
    def __init__(self, gateway, config):
        if (not isinstance(config, dict) or set(config) != {'socket', 'origin'}
                or not isinstance(config['socket'], str) or not config['socket'].startswith('/')):
            raise ValueError('Website login requires a private control socket and separate HTTPS origin.')
        self.gateway, self.c, self.db = gateway, gateway.controller, gateway.db
        self.origin = origin(config['origin'])
        if self.origin == origin(gateway.public_url):
            raise ValueError('Website login must have a separate HTTPS origin.')
        self.socket = str(Path(config['socket']).resolve())
        roots = [Path(self.c.cwd).resolve(), Path(self.c.config['state_dir']).resolve()]
        if self.c.config.get('telegram_api_directory'):
            roots.append(Path(self.c.config['telegram_api_directory']).resolve())
        if any(Path(self.socket).is_relative_to(root) for root in roots):
            raise ValueError('Login control must be outside ordinary Oak data.')
        self.execution = self.c.config.get('execution')
        if not self.execution:
            raise ValueError('The provisioned executor boundary is required for real website login.')
        self.db.execute('''CREATE TABLE IF NOT EXISTS website_login_attempts (
            request_id TEXT PRIMARY KEY, session_id TEXT, runtime_epoch TEXT NOT NULL,
            phase TEXT NOT NULL, cleanup TEXT NOT NULL, panel_hash TEXT NOT NULL,
            session_expires REAL NOT NULL DEFAULT 0)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS website_agent_actions (
            runtime_epoch TEXT NOT NULL, native_id TEXT NOT NULL, request_id TEXT NOT NULL,
            action TEXT NOT NULL, fingerprint TEXT NOT NULL, phase TEXT NOT NULL,
            PRIMARY KEY(runtime_epoch,native_id))''')
        with self.db:
            self.db.execute("UPDATE website_login_attempts SET phase='uncertain' WHERE phase IN ('attempted','waiting','active','authenticated','owner_confirmed')")
            self.db.execute("UPDATE website_agent_actions SET phase='uncertain' WHERE phase='attempted'")
        self.tasks = set()
        self.c.requests.website_login = self

    def boundary(self):
        from .executor import boundary_check
        return boundary_check(self.execution)

    async def ipc(self, route, data):
        if not self.boundary():
            raise RequestUnavailable('Website login execution boundary is unavailable.')
        try:
            async with ClientSession(connector=UnixConnector(path=self.socket),
                                     timeout=ClientTimeout(total=35)) as client:
                async with client.post('http://login' + route, json=data, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ValueError
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ValueError
                    return result
        except Exception:
            raise RequestUnavailable('Website login is unavailable; no automatic retry was made.') from None

    async def available(self, provider):
        if provider not in {'google', 'instagram', 'website'}:
            return False
        try:
            value = await self.ipc('/health', {})
            return (value.get('ready') is True and value.get('protocol') == 1
                    and isinstance(value.get('providers'), list) and provider in value['providers'])
        except RequestUnavailable:
            return False

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return task

    def scope(self, row, attempt):
        return {'request_id': row['id'], 'session_id': attempt['session_id'], 'runtime_epoch': row['runtime_epoch']}

    def live(self, row, attempt):
        session = self.db.execute('SELECT owner,expires FROM web_sessions WHERE hash=?', (attempt['panel_hash'],)).fetchone()
        return bool(row['owner'] in self.gateway.allowed and session and session['owner'] == row['owner']
                    and session['expires'] > time.time() and row['runtime_epoch'] == self.c.requests.epoch
                    and self.c.active.get(row['chat_id']) == row['turn_id']
                    and self.c.threads.get(row['chat_id']) == row['thread_id']
                    and not self.c.sessions.deleted(row['chat_id']))

    def validate(self, value, row, attempt=None):
        expected = {'session_id', 'request_id', 'provider', 'runtime_epoch', 'state', 'cleanup', 'simulated', 'session_expires'}
        if (not expected <= set(value) or value['request_id'] != row['id']
                or value['runtime_epoch'] != row['runtime_epoch']
                or value['provider'] != json.loads(row['form'])['provider'] or value['simulated'] is not False
                or not re.fullmatch(r'[a-f0-9]{32}', str(value['session_id']))
                or (attempt is not None and value['session_id'] != attempt['session_id'])
                or value['state'] not in {'waiting', 'active', 'authenticated', 'owner_confirmed', 'cancelled', 'expired', 'failed'}
                or (value['state'] == 'owner_confirmed' and value['provider'] != 'website')
                or value['cleanup'] not in {'waiting', 'retained', 'confirmed', 'uncertain'}
                or type(value['session_expires']) not in (int, float)
                or not math.isfinite(value['session_expires'])):
            raise RequestUnavailable('Invalid website login status.')

    async def start(self, owner, chat, identifier, request):
        async with self.c._lock(chat):
            row = self.c.requests.row(owner, chat, identifier)
            provider = json.loads(row['form']).get('provider')
            if (provider not in {'google', 'instagram', 'website'} or row['kind'] != 'sign_in' or row['outcome'] != 'pending'
                    or not self.c.requests._live(row) or row['expires'] <= time.time()):
                raise RequestUnavailable('The website login request ended.')
            if self.db.execute('SELECT 1 FROM website_login_attempts WHERE request_id=?', (identifier,)).fetchone():
                raise RequestUnavailable('Login was already attempted; it is not replayed.')
            header = request.headers.get('Authorization', '')
            panel = header[7:] if header.startswith('Bearer ') else request.cookies.get('oak_session', '')
            panel_hash = hashlib.sha256(panel.encode()).hexdigest()
            binding = {k: row[k] for k in ('owner', 'chat_id', 'thread_id', 'turn_id', 'runtime_epoch', 'expires')}
            binding.update(request_id=identifier, panel_hash=panel_hash, provider=provider)
            if provider == 'website':
                binding['url'] = json.loads(row['form'])['url']
                if identifier in self.c.requests.website_workspaces:
                    binding['agentic'] = True
            with self.db:
                self.db.execute('INSERT INTO website_login_attempts VALUES (?,NULL,?,?,?,?,0)',
                                (identifier, row['runtime_epoch'], 'attempted', 'waiting', panel_hash))
        try:
            value = await self.ipc('/create', binding)
            self.validate(value, row)
            if (value.get('origin') != self.origin or value['state'] != 'waiting'
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', str(value.get('ticket', '')))
                    or type(value.get('expires')) not in (int, float)
                    or not time.time() < value['expires'] <= row['expires']):
                raise RequestUnavailable('Invalid website login launch.')
        except Exception:
            with self.db:
                self.db.execute("UPDATE website_login_attempts SET phase='uncertain',cleanup='uncertain' WHERE request_id=?", (identifier,))
            raise RequestUnavailable('Website login dispatch is uncertain; it will not be replayed.') from None
        async with self.c._lock(chat):
            with self.db:
                self.db.execute("UPDATE website_login_attempts SET session_id=?,phase='waiting' WHERE request_id=?", (value['session_id'], identifier))
            fresh = self.c.requests.row(owner, chat, identifier)
            attempt = self.attempt(identifier)
            if fresh['outcome'] != 'pending' or not self.live(fresh, attempt) or fresh['expires'] <= time.time():
                self.revoke(identifier, force=True)
                raise RequestUnavailable('Website login request ended.')
        self.spawn(self.monitor(owner, chat, identifier))
        return {'request_id': identifier, 'provider': provider, 'origin': self.origin,
                'ticket': value['ticket'], 'expires': value['expires'], 'simulated': False}

    def attempt(self, identifier):
        return self.db.execute('SELECT * FROM website_login_attempts WHERE request_id=?', (identifier,)).fetchone()

    async def monitor(self, owner, chat, identifier):
        try:
            while True:
                row, attempt = self.c.requests.row(owner, chat, identifier), self.attempt(identifier)
                if not self.live(row, attempt) or row['outcome'] in {'cancelled', 'expired', 'failed'}:
                    await self.cleanup(identifier)
                    async with self.c._lock(chat):
                        fresh = self.db.execute('SELECT * FROM input_requests WHERE id=?', (identifier,)).fetchone()
                        if fresh is not None and fresh['outcome'] == 'pending':
                            self.c.requests._terminal(fresh, 'cancelled')
                    return
                value = await self.ipc('/status', self.scope(row, attempt))
                self.validate(value, row, attempt)
                with self.db:
                    self.db.execute('UPDATE website_login_attempts SET phase=?,cleanup=?,session_expires=? WHERE request_id=?',
                                    (value['state'], value['cleanup'], value['session_expires'], identifier))
                if row['outcome'] == 'pending' and value['state'] in {'authenticated', 'owner_confirmed', 'cancelled', 'expired', 'failed'}:
                    async with self.c._lock(chat):
                        fresh = self.c.requests.row(owner, chat, identifier)
                        if fresh['outcome'] == 'pending' and self.c.requests._live(fresh):
                            outcome = value['state'] if self.live(fresh, attempt) else 'cancelled'
                            if outcome in {'authenticated', 'owner_confirmed'} and (value['cleanup'] != 'retained'
                                    or not time.time() < value['session_expires'] <= time.time() + 901):
                                outcome = 'failed'
                            response = self.c.requests._response(fresh, outcome)
                            delivery = 'not_required' if identifier in self.c.requests.website_workspaces else 'ready'
                            with self.db:
                                self.db.execute("UPDATE input_requests SET outcome=?,response=?,delivery=? WHERE id=? AND outcome='pending'",
                                                (outcome, json.dumps(response), delivery, identifier))
                            future = self.c.requests.waiters.get(identifier)
                            if future is not None and not future.done():
                                future.set_result(response)
                if value['state'] in {'cancelled', 'expired', 'failed'}:
                    return
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            with self.db:
                self.db.execute("UPDATE website_login_attempts SET phase='uncertain' WHERE request_id=?", (identifier,))
            # Without control heartbeats the broker expires its own browser.

    def snapshot(self, identifier):
        row = self.attempt(identifier)
        return ({k: row[k] for k in ('phase', 'cleanup', 'session_expires')} if row else
                {'phase': 'available', 'cleanup': 'waiting', 'session_expires': 0})

    def revoke(self, identifier, force=False):
        attempt = self.attempt(identifier)
        if not attempt or attempt['cleanup'] == 'confirmed':
            return
        row = self.db.execute('SELECT outcome FROM input_requests WHERE id=?', (identifier,)).fetchone()
        if not force and row and row['outcome'] in {'authenticated', 'owner_confirmed'}:
            return  # Keep only until the original turn ends; monitor enforces it.
        self.spawn(self.cleanup(identifier))

    async def cleanup(self, identifier):
        attempt = self.attempt(identifier)
        row = self.db.execute('SELECT * FROM input_requests WHERE id=?', (identifier,)).fetchone()
        if not attempt or not row or attempt['cleanup'] == 'confirmed':
            return
        try:
            value = await self.ipc('/cancel', self.scope(row, attempt))
            self.validate(value, row, attempt)
            confirmed = value['cleanup'] == 'confirmed' and value['state'] in {'cancelled', 'expired', 'failed'}
        except Exception:
            confirmed = False
        with self.db:
            self.db.execute('UPDATE website_login_attempts SET phase=?,cleanup=? WHERE request_id=?',
                            ('cancelled' if confirmed else 'uncertain', 'confirmed' if confirmed else 'uncertain', identifier))

    def end_turn(self, thread, turn):
        for row in self.db.execute('SELECT id FROM input_requests WHERE thread_id=? AND turn_id=? AND kind=?', (thread, turn, 'sign_in')):
            self.revoke(row['id'], force=True)

    async def use(self, chat, metadata, provider, operation, request_id=None):
        if provider not in {'google', 'instagram', 'website'} or not isinstance(metadata, dict):
            raise RequestUnavailable('Select a supported website session.')
        if provider == 'website' and not re.fullmatch(r'[a-f0-9]{32}', str(request_id)):
            raise RequestUnavailable('Select the request_id returned by the website form.')
        for row in self.db.execute("SELECT * FROM input_requests WHERE chat_id=? AND turn_id=? AND outcome IN ('authenticated','owner_confirmed') ORDER BY expires DESC", (chat, metadata.get('turnId'))):
            if (metadata.get('threadId') != row['thread_id']
                    or metadata.get('runtimeEpoch', self.c.requests.epoch) != row['runtime_epoch']):
                raise RequestUnavailable('The website session belongs to another runtime request.')
            if request_id is not None and row['id'] != request_id:
                continue
            if json.loads(row['form']).get('provider') != provider:
                continue
            attempt = self.attempt(row['id'])
            if not attempt or attempt['phase'] not in {'authenticated', 'owner_confirmed'} or not self.live(row, attempt):
                continue
            value = await self.ipc('/use', {**self.scope(row, attempt), 'owner': row['owner'],
                                          'chat_id': chat, 'turn_id': row['turn_id'], 'operation': operation})
            if not isinstance(value.get('result'), dict):
                raise RequestUnavailable('Invalid connected-browser result.')
            return value['result']
        raise RequestUnavailable('Спочатку запроси вхід через відповідну форму для поточної задачі.')

    def agent_row(self, metadata, identifier):
        owner, chat, thread, turn, _ = self.c.requests._binding(metadata)
        row = self.c.requests.row(owner, chat, identifier)
        if (identifier not in self.c.requests.website_workspaces or row['thread_id'] != thread
                or row['turn_id'] != turn or not self.c.requests._live(row)):
            raise RequestUnavailable('The website workspace belongs to another task or has ended.')
        if row['expires'] <= time.time() and row['outcome'] == 'pending':
            self.c.requests._terminal(row, 'expired')
            row = self.c.requests.row(owner, chat, identifier)
        return row

    def agent_terminal(self, row):
        if row['outcome'] == 'pending':
            return None
        return {'request_id': row['id'], 'state': row['outcome'],
                **self.c.requests._response(row, row['outcome'])}

    @staticmethod
    def agent_response(value):
        """Only the reviewed sanitized observation can enter the native model."""
        from .website_agent import validate_agent_observation
        if type(value) is not dict or set(value) - {'state', 'progress', 'observation', 'error'}:
            raise RequestUnavailable('Invalid private website observation.')
        state = value.get('state')
        if type(state) is not str or state not in {'waiting', 'active', 'authenticated', 'owner_confirmed', 'cancelled', 'expired', 'failed'}:
            raise RequestUnavailable('Invalid private website state.')
        if state != 'active':
            if state == 'failed' and set(value) == {'state', 'error'} and value['error'] == {'code': 'action_uncertain'}:
                return {'state': 'failed', 'error': {'code': 'action_uncertain'}}
            if set(value) != {'state'}:
                raise RequestUnavailable('Invalid private website state.')
            return {'state': state}
        try:
            progress = value['progress']
            if (type(progress) is not dict or set(progress) != {'event_seq', 'outcome'}
                    or type(progress['event_seq']) is not int or not 0 <= progress['event_seq'] <= 1000000
                    or progress['outcome'] not in {'idle', 'submitted', 'stale', 'uncertain'}):
                raise ValueError
            result = {'state': state, 'progress': dict(progress)}
            if 'observation' in value:
                observation = validate_agent_observation(value['observation'])
                if progress != observation['progress']:
                    raise ValueError
                result['observation'] = observation
            elif 'error' not in value:
                raise ValueError
            if 'error' in value:
                error = value['error']
                if (type(error) is not dict or set(error) != {'code'} or error['code'] not in
                        {'stale_form', 'composition_unavailable', 'navigation_pending', 'action_uncertain',
                         'invalid_plan', 'human_step_pending', 'navigation_requires_owner'}):
                    raise ValueError
                result['error'] = {'code': error['code']}
            return result
        except (ValueError, KeyError, TypeError):
            raise RequestUnavailable('Invalid private website observation.') from None

    async def agent_ready(self, metadata, identifier, deadline):
        while True:
            row = self.agent_row(metadata, identifier)
            ended = self.agent_terminal(row)
            if ended is not None:
                return row, None, ended
            attempt = self.attempt(identifier)
            if attempt is not None:
                if not self.live(row, attempt) or attempt['phase'] == 'uncertain':
                    raise RequestUnavailable('Website attachment is unavailable or uncertain; it will not be replayed.')
                if attempt['phase'] == 'active':
                    return row, attempt, None
            if asyncio.get_running_loop().time() >= deadline:
                return row, None, {'request_id': identifier, 'state': 'waiting_for_owner'}
            await asyncio.sleep(AGENT_POLL_SECONDS)

    async def agent_observe(self, metadata, identifier, deadline):
        row, attempt, pending = await self.agent_ready(metadata, identifier, deadline)
        if pending is not None:
            return pending
        value = self.agent_response(await self.ipc('/agent', {**self.scope(row, attempt),
                                                            'operation': {'action': 'observe'}}))
        # A result cannot cross into another turn while an IPC read was pending.
        self.agent_row(metadata, identifier)
        return {'request_id': identifier, **value}

    async def agent_wait(self, metadata, identifier, sequence, deadline):
        readiness_deadline = None
        while True:
            value = await self.agent_observe(metadata, identifier, deadline)
            if value['state'] != 'active' or value.get('error') or value['progress']['event_seq'] > sequence:
                return value
            if value['progress']['event_seq'] < sequence:
                raise RequestUnavailable('The private website event sequence is stale.')
            observation = value.get('observation', {})
            if (observation.get('stage') in {'agent_working', 'review'}
                    and observation.get('scene', {}).get('stage') == 'form'
                    and observation['scene']['actions']):
                # No human plan is pending: attachment or page hydration can
                # reveal the next choice without incrementing a human event.
                # Return it to the agent instead of waiting for impossible input.
                return value
            if observation.get('stage') in {'agent_working', 'review', 'unsupported_layout'}:
                # Empty/transitional pages are unresolved, not human completion.
                # Reobserve briefly without requiring another human event.
                if readiness_deadline is None:
                    readiness_deadline = asyncio.get_running_loop().time() + AGENT_READINESS_SECONDS
                elif asyncio.get_running_loop().time() >= readiness_deadline:
                    return value
            else:
                readiness_deadline = None
            if asyncio.get_running_loop().time() >= deadline:
                ended = self.agent_terminal(self.agent_row(metadata, identifier))
                return ended if ended is not None else {**value, 'state': 'waiting_for_owner'}
            await asyncio.sleep(AGENT_POLL_SECONDS)

    def agent_human_deadline(self, row):
        # Keep one native tool request pending for the human step, within the
        # workspace's absolute TTL and below the runtime's 600-second handler.
        remaining = max(0, min(AGENT_HUMAN_WAIT_SECONDS, row['expires'] - time.time()))
        return asyncio.get_running_loop().time() + remaining

    async def agent(self, metadata, arguments):
        """One GPT tool step; no model recursion and no credential-bearing IPC."""
        if type(arguments) is not dict:
            raise RequestUnavailable('Invalid website operation.')
        action = arguments.get('action')
        fields = {'begin': {'action', 'url'}, 'observe': {'action', 'request_id'},
                  'act': {'action', 'request_id', 'revision', 'action_ref'},
                  'present': {'action', 'request_id', 'revision', 'plan_json'},
                  'wait': {'action', 'request_id', 'event_seq'}}
        if type(action) is not str or action not in fields:
            raise RequestUnavailable('Choose a website action: begin, observe, act, present, wait.')
        if set(arguments) != fields[action]:
            raise RequestUnavailable('Expected exactly these fields for ' + action + ': '
                                     + ', '.join(sorted(fields[action])) + '.')
        if action == 'begin':
            return await self.c.requests.website_workspace(metadata, arguments['url'])
        identifier = arguments['request_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise RequestUnavailable('Select the current website workspace.')
        current = self.agent_row(metadata, identifier)
        deadline = asyncio.get_running_loop().time() + AGENT_WAIT_SECONDS
        if action == 'observe':
            return await self.agent_observe(metadata, identifier, deadline)
        if action == 'wait':
            sequence = arguments['event_seq']
            if type(sequence) is not int or not 0 <= sequence <= 1000000:
                raise RequestUnavailable('Invalid website event sequence.')
            return await self.agent_wait(metadata, identifier, sequence, self.agent_human_deadline(current))
        revision = arguments['revision']
        if type(revision) is not str or not 1 <= len(revision) <= 128:
            raise RequestUnavailable('Use the current website revision.')
        operation = {'action': action, 'revision': revision}
        if action == 'act':
            ref = arguments['action_ref']
            if type(ref) is not str or not re.fullmatch(r'a[0-9]{1,12}', ref):
                raise RequestUnavailable('Use a current observed action reference.')
            operation['action_ref'] = ref
        else:
            encoded = arguments['plan_json']
            try:
                if type(encoded) is not str or len(encoded.encode()) > 32768:
                    raise ValueError
                plan = json.loads(encoded, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if type(plan) is not dict:
                    raise ValueError
            except (ValueError, TypeError):
                raise RequestUnavailable('Use a bounded catalogue plan.') from None
            operation['plan'] = plan
        row, attempt, pending = await self.agent_ready(metadata, identifier, deadline)
        if pending is not None:
            return pending
        native = json.dumps(metadata['requestId'], ensure_ascii=False, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(json.dumps(operation, sort_keys=True, allow_nan=False).encode()).hexdigest()
        # Reserve before awaiting transport; neither successful duplicates nor
        # uncertain dispatches may execute another website action.
        async with self.c._lock(row['chat_id']):
            self.agent_row(metadata, identifier)
            with self.db:
                if self.db.execute('SELECT 1 FROM website_agent_actions WHERE runtime_epoch=? AND native_id=?',
                                   (row['runtime_epoch'], native)).fetchone():
                    raise RequestUnavailable('This native website action was already attempted; it will not be replayed.')
                self.db.execute('INSERT INTO website_agent_actions VALUES (?,?,?,?,?,?)',
                                (row['runtime_epoch'], native, identifier, action, fingerprint, 'attempted'))
        try:
            result = self.agent_response(await self.ipc('/agent', {**self.scope(row, attempt), 'operation': operation}))
            self.agent_row(metadata, identifier)
        except BaseException:
            with self.db:
                self.db.execute("UPDATE website_agent_actions SET phase='uncertain' WHERE runtime_epoch=? AND native_id=?",
                                (row['runtime_epoch'], native))
            raise
        with self.db:
            self.db.execute("UPDATE website_agent_actions SET phase=? WHERE runtime_epoch=? AND native_id=?",
                            ('uncertain' if result.get('error', {}).get('code') == 'action_uncertain' else 'completed',
                             row['runtime_epoch'], native))
        if action == 'present' and result['state'] == 'active' and not result.get('error'):
            return await self.agent_wait(metadata, identifier, result['progress']['event_seq'], self.agent_human_deadline(row))
        return {'request_id': identifier, **result}

    async def delegated(self, metadata, url, goal=None):
        """One native call delegates the entire private loop through real MCP."""
        from .website_task_client import WebsiteTaskClient
        from .website_goal import validate_task_goal
        if goal is not None:
            goal = validate_task_goal(goal)
        if (await self.ipc('/health', {})).get('website_task') is not True:
            raise RequestUnavailable('Delegated website login is not configured.')
        created = await self.c.requests.website_workspace(metadata, url)
        identifier = created['request_id']
        current = self.agent_row(metadata, identifier)
        deadline = self.agent_human_deadline(current)
        row, attempt, pending = await self.agent_ready(metadata, identifier, deadline)
        if pending is not None:
            return pending
        scope = self.scope(row, attempt)
        async with ClientSession(connector=UnixConnector(path=self.socket),
                                 timeout=ClientTimeout(total=35), trust_env=False) as transport:
            client = WebsiteTaskClient(transport, scope)
            try:
                if not self.boundary():
                    raise RequestUnavailable('Website execution boundary is unavailable.')
                await client.initialize()
                self.agent_row(metadata, identifier)
                status = await client.call('website_sign_in', goal=goal) if goal is not None else await client.call('website_sign_in')
                while True:
                    current = self.agent_row(metadata, identifier)
                    ended = self.agent_terminal(current)
                    if ended is not None:
                        return ended
                    # The broker, not a model judgment or MCP job status,
                    # remains authoritative for authentication and termination.
                    remote = await self.ipc('/status', scope)
                    self.validate(remote, current, attempt)
                    if remote['state'] != 'active':
                        return {'request_id': identifier, 'state': remote['state']}
                    if status['state'] in {'requires_agent', 'cancelled'}:
                        observed = await self.agent_observe(metadata, identifier, deadline)
                        return {**observed, 'delegation': status['state'],
                                'delegation_error': status['error'],
                                'delegation_steps': status['steps'],
                                'delegation_calls': status['calls']}
                    if asyncio.get_running_loop().time() >= deadline:
                        return {'request_id': identifier, 'state': 'waiting_for_owner'}
                    await asyncio.sleep(AGENT_POLL_SECONDS)
                    self.agent_row(metadata, identifier)
                    try:
                        status = await client.call('website_task_status')
                    except Exception:
                        # A private human completion can revoke the MCP session
                        # between the status check and this read. Read only;
                        # never repeat a failed/uncertain task-start operation.
                        remote = await self.ipc('/status', scope)
                        self.validate(remote, self.agent_row(metadata, identifier), attempt)
                        if remote['state'] != 'active':
                            return {'request_id': identifier, 'state': remote['state']}
                        raise
            except asyncio.CancelledError:
                raise
            except Exception:
                raise RequestUnavailable('Delegated website task is unavailable; it was not restarted.') from None
            finally:
                try:
                    await asyncio.shield(client.close())
                except asyncio.CancelledError:
                    self.revoke(identifier, force=True)
                    raise
                except Exception:
                    # If stopping the executor is uncertain, close its exact
                    # private lease instead of leaving an unattended task.
                    self.revoke(identifier, force=True)

    async def close(self):
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for row in self.db.execute("SELECT request_id FROM website_login_attempts WHERE cleanup!='confirmed'").fetchall():
            await self.cleanup(row['request_id'])
