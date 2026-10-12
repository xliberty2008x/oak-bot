"""Human-only website forms and expiring authenticated browser sessions.

Only the private Unix control API accepts gateway commands. Human field values
never cross that API, enter durable Oak state, or appear in exception responses.
The standalone launcher requires an independently provisioned execution boundary.
"""

import asyncio
import argparse
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import signal
import struct
import time
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

from .signin_broker import origin
from .signin_providers import RecoverableFormError, SubmitUncertainError


PROVIDERS = frozenset({'google', 'instagram', 'website'})
CONNECTED = frozenset({'authenticated', 'owner_confirmed'})
TERMINAL = frozenset({'cancelled', 'expired', 'failed'})
FORM_SECONDS = 300
SESSION_SECONDS = 900


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) is not None


def website_target(value):
    """Validate a non-secret destination; the adapter additionally pins public DNS."""
    try:
        parsed = urlsplit(value)
        if (not isinstance(value, str) or len(value) > 2048 or re.search(r'[\x00-\x20\x7f\\]', value)
                or parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username or parsed.password
                or not parsed.hostname or parsed.fragment or not parsed.hostname.isascii()
                or not re.fullmatch(r'[a-zA-Z0-9.-]+', parsed.hostname)
                or any(re.search(r'password|secret|token|credential|session|^code$|^otp$', key, re.I)
                       for key, _ in parse_qsl(parsed.query))):
            raise ValueError
        return 'https://' + parsed.hostname.lower()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Use a public HTTPS website URL without credentials or tokens.') from None


@dataclass
class LoginLease:
    binding: dict
    ticket_hash: str
    ticket_deadline: float
    id: str = field(default_factory=lambda: secrets.token_hex(16))
    state: str = 'waiting'
    human_hash: str = ''
    browser: object = None
    form_schema: str = ''
    connected: object = None
    idle_deadline: float = 0
    human_deadline: float = 0
    gateway_deadline: float = 0
    session_deadline: float = 0
    cleanup: str = 'waiting'
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closing: object = None
    diagnostics_snapshot: object = None
    delegated_task: object = None
    delegated_goal: str = ''


class LoginBroker:
    def __init__(self, human_origin, parent_origin, gateway_uid, *, browser_factory=None,
                 connected_factory=None, boundary_check=None, peer_check=None, composer_factory=None,
                 tiktok_static_resources=False, static_resources=None, auth_hosts=None,
                 anonymous_static_resources=False, step_planner_factory=None):
        self.origin, self.parent = origin(human_origin), origin(parent_origin)
        if self.origin == self.parent or type(gateway_uid) is not int or gateway_uid <= 0:
            raise ValueError('Separate HTTPS origins and a non-root gateway are required.')
        from .signin_providers import WebsiteBrowser
        self.browser_factory = browser_factory or WebsiteBrowser
        self.connected_factory = connected_factory
        self.composer_factory = composer_factory
        self.step_planner_factory = step_planner_factory
        if type(tiktok_static_resources) is not bool:
            raise ValueError('Static-resource activation must be explicit boolean configuration.')
        self.tiktok_static_resources = tiktok_static_resources
        if type(anonymous_static_resources) is not bool:
            raise ValueError('Anonymous resource discovery requires explicit boolean configuration.')
        self.anonymous_static_resources = anonymous_static_resources
        from .website_resources import validate_static_resources
        self.static_resources = validate_static_resources({} if static_resources is None else static_resources)
        validate_static_resources(self.static_resources, tiktok=tiktok_static_resources)
        from .website_auth_hosts import validate_auth_hosts
        self.auth_hosts = validate_auth_hosts({} if auth_hosts is None else auth_hosts)
        self.boundary_check = boundary_check
        self.peer_check = peer_check
        self.gateway_uid = gateway_uid
        self.leases = {}
        self.sweeper = None
        # 32 private fields may each contain 1024 characters. JSON escaping
        # (including surrogate pairs) must fit before per-field validation.
        self.human = web.Application(client_max_size=524288, middlewares=[self.human_guard])
        self.human.add_routes([web.get('/', self.page), web.get('/login.js', self.script),
                              web.get('/form-controls.js', self.controls_script),
                              web.get('/login.css', self.style), web.post('/api/attach', self.attach),
                              web.post('/api/form', self.form), web.post('/api/submit', self.submit),
                              web.post('/api/complete', self.complete),
                              web.post('/api/approve-origin', self.approve_origin),
                              web.post('/api/choose-action', self.choose_action),
                              web.post('/api/cancel', self.cancel_human), web.post('/api/detach', self.detach)])
        # The 32 KiB plan is wrapped with the bound lease scope at this layer.
        self.control = web.Application(client_max_size=65536, middlewares=[self.control_guard])
        self.control.add_routes([web.post('/health', self.health), web.post('/create', self.create),
                                 web.post('/status', self.status), web.post('/cancel', self.cancel),
                                 web.post('/use', self.use), web.post('/diagnostics', self.diagnostics),
                                 web.post('/agent', self.agent)])
        from .website_task_mcp import WebsiteTaskMCP
        self.task_mcp = WebsiteTaskMCP(self.task_scope, self.start_task, self.task_status, self.cancel_task)
        self.control.router.add_route('*', '/mcp', self.task_mcp.handle)

    def boundary(self):
        # No JSON flag enables the channel. The installed launcher supplies the
        # root-owned policy verifier; absent/changed evidence closes capability.
        if self.boundary_check is None or self.boundary_check() is not True:
            raise ValueError('Execution boundary is unavailable.')

    @web.middleware
    async def human_guard(self, request, handler):
        try:
            if request.method == 'POST' and request.headers.get('Origin') != self.origin:
                raise web.HTTPForbidden()
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({'error': 'Канал входу недоступний.'}, status=exc.status)
        except RecoverableFormError as exc:
            response = web.json_response({'recoverable': True, 'code': exc.code,
                'error': ('Не вдалося скласти форму. Скасуй запит і відкрий новий.'
                          if exc.code == 'composition_unavailable' else
                          'Форма оновлюється. Дані не надсилаються повторно.')}, status=409)
        except SubmitUncertainError:
            response = web.json_response({'code': 'submit_uncertain',
                'error': 'Результат дії невідомий. Її не буде повторено автоматично.'}, status=409)
        except Exception:
            response = web.json_response({'error': 'Дія не виконана. Онови форму або скасуй вхід.'}, status=409)
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; "
                                 "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors " + self.parent})
        return response

    @web.middleware
    async def control_guard(self, request, handler):
        try:
            if self.peer_check is not None:
                accepted = self.peer_check(request)
            else:
                sock = request.transport.get_extra_info('socket')
                accepted = (sock is not None and sock.family == socket.AF_UNIX and
                            struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
                            == self.gateway_uid)
            if not accepted:
                raise ValueError('Wrong control peer.')
            self.boundary()
            request['oak.control.authorized'] = True
            return await handler(request)
        except Exception:
            return web.json_response({'error': 'Website login control unavailable.'}, status=409)

    async def health(self, request):
        if await request.json() != {}:
            raise ValueError('Unexpected health fields.')
        return web.json_response({'ready': True, 'providers': sorted(PROVIDERS), 'protocol': 1,
                                  'website_task': self.step_planner_factory is not None})

    def binding(self, value):
        fields = {'request_id', 'owner', 'chat_id', 'thread_id', 'turn_id', 'runtime_epoch',
                  'panel_hash', 'expires', 'provider'}
        if isinstance(value, dict) and value.get('provider') == 'website':
            fields.add('url')
            website_target(value.get('url'))
            if value.get('agentic') is True:
                fields.add('agentic')
        if (not isinstance(value, dict) or set(value) != fields or not identifier(value['request_id'])
                or type(value['owner']) is not int or type(value['chat_id']) is not int
                or any(not isinstance(value[k], str) or not 0 < len(value[k]) <= 200
                       for k in ('thread_id', 'turn_id', 'runtime_epoch'))
                or not re.fullmatch(r'[a-f0-9]{64}', str(value['panel_hash']))
                or type(value['expires']) not in (int, float)
                or not time.time() < value['expires'] <= time.time() + FORM_SECONDS + 1
                or value['provider'] not in PROVIDERS):
            raise ValueError('Invalid website-login binding.')
        return dict(value)

    def result(self, lease):
        return {'session_id': lease.id, 'request_id': lease.binding['request_id'],
                'provider': lease.binding['provider'], 'runtime_epoch': lease.binding['runtime_epoch'],
                'state': lease.state, 'cleanup': lease.cleanup, 'simulated': False,
                'session_expires': lease.session_deadline}

    async def create(self, request):
        binding = self.binding(await request.json())
        if binding['request_id'] in self.leases or len(self.leases) >= 32:
            raise ValueError('Login already attempted or capacity reached.')
        ticket = secrets.token_urlsafe(32)
        lease = LoginLease(binding, hashed(ticket), min(time.time() + 30, binding['expires']))
        lease.gateway_deadline = time.time() + 45
        self.leases[binding['request_id']] = lease
        return web.json_response({**self.result(lease), 'origin': self.origin,
                                  'ticket': ticket, 'expires': lease.ticket_deadline})

    def expired(self, lease):
        now = time.time()
        if now >= lease.gateway_deadline:
            return True
        if lease.state in CONNECTED:
            return now >= lease.session_deadline
        return (now >= lease.binding['expires'] or (lease.state == 'active' and
                (now >= lease.idle_deadline or now >= lease.human_deadline)))

    async def bounded(self, lease, operation):
        remaining = ((lease.session_deadline if lease.state in CONNECTED else lease.binding['expires'])
                     - time.time())
        if remaining <= 0 or self.expired(lease):
            await self.finish(lease, 'expired')
            raise ValueError('Login expired.')
        try:
            result = await asyncio.wait_for(operation(), min(30, remaining))
        except RecoverableFormError:
            if self.expired(lease):
                await self.finish(lease, 'expired')
                raise ValueError('Login expired.') from None
            # No write is retried. Keep the bound lease for a fresh observation
            # and, only after that, another explicit human action.
            raise
        except BaseException:
            # Keep real credentials/context unavailable after a failed input.
            await asyncio.shield(self.finish(lease, 'failed'))
            raise
        if self.expired(lease):
            await self.finish(lease, 'expired')
            raise ValueError('Login expired.')
        return result

    async def attach(self, request):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {'request_id', 'ticket'}:
            raise ValueError('Invalid attachment.')
        lease = self.leases.get(value['request_id'])
        if lease is None or not isinstance(value['ticket'], str) or len(value['ticket']) > 100:
            raise ValueError('Unknown attachment.')
        async with lease.lock:
            self.boundary()
            if (lease.state != 'waiting' or self.expired(lease) or time.time() >= lease.ticket_deadline
                    or not hmac.compare_digest(hashed(value['ticket']), lease.ticket_hash)):
                raise ValueError('Attachment consumed or expired.')
            lease.ticket_hash = ''
            lease.state = 'active'
            lease.idle_deadline = time.time() + 120
            lease.human_deadline = time.time() + 45  # includes browser cold start
            lease.form_schema = ('website-v2' if lease.binding.get('agentic')
                                 else 'website-v1' if lease.binding['provider'] == 'website'
                                 or self.composer_factory is not None else 'provider-v1')
            if lease.binding['provider'] == 'website':
                from .website_forms import WebsiteForms
                options = ({'composer': self.composer_factory()}
                           if self.composer_factory and not lease.binding.get('agentic') else {})
                if self.tiktok_static_resources:
                    options['static_resources'] = True
                destination = website_target(lease.binding['url'])
                if destination in self.static_resources:
                    options['resource_hosts'] = self.static_resources[destination]
                if destination in self.auth_hosts:
                    options['auth_hosts'] = self.auth_hosts[destination]
                if lease.binding.get('agentic'):
                    from .website_agent import WebsiteAgentForms
                    options['origin_approval'] = True
                    options['navigation_seconds'] = max(1, min(900, lease.binding['expires'] - time.time()))
                    def navigation_guard():
                        self.boundary()
                        if lease.state not in CONNECTED | {'active'} or self.expired(lease):
                            raise ValueError('Website navigation authority is unavailable.')
                    options['navigation_guard'] = navigation_guard
                    if self.anonymous_static_resources:
                        options['anonymous_static_resources'] = True
                    lease.browser = WebsiteAgentForms(lease.binding['url'], **options)
                else:
                    lease.browser = WebsiteForms(lease.binding['url'], **options)
            elif self.composer_factory is not None:
                from .website_forms import ComposedProviderForms
                lease.browser = ComposedProviderForms(lease.binding['provider'], composer=self.composer_factory())
            else:
                lease.browser = self.browser_factory(lease.binding['provider'])
            await self.bounded(lease, lease.browser.start)
            human = secrets.token_urlsafe(32)
            lease.human_hash = hashed(human)
            lease.human_deadline = time.time() + 45
            return web.json_response({'lease': human, 'provider': lease.binding['provider'],
                                      'form_schema': lease.form_schema,
                                      'destination': website_target(lease.binding['url']) if lease.binding['provider'] == 'website'
                                      else 'https://accounts.google.com' if lease.binding['provider'] == 'google'
                                      else 'https://www.instagram.com'})

    async def human_lease(self, request, fields):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('Invalid human request.')
        lease = self.leases.get(value['request_id'])
        auth = request.headers.get('Authorization', '')
        if (lease is None or not auth.startswith('Bearer ') or len(auth) > 120
                or not lease.human_hash or not hmac.compare_digest(hashed(auth[7:]), lease.human_hash)):
            raise ValueError('Unknown human lease.')
        return lease, value

    async def form(self, request):
        lease, _ = await self.human_lease(request, {'request_id'})
        async with lease.lock:
            self.boundary()
            if self.expired(lease):
                await self.finish(lease, 'expired')
            if lease.state != 'active':
                return web.json_response({'state': lease.state, 'session_expires': lease.session_deadline})
            lease.human_deadline = time.time() + 45
            if await self.bounded(lease, lease.browser.verified):
                # Authentication is established by the provider adapter, never
                # a success flag submitted by the human or ordinary form API.
                await self.promote(lease, 'authenticated')
                return web.json_response({'state': lease.state, 'session_expires': lease.session_deadline})
            snapshot = await self.bounded(lease, lease.browser.snapshot)
            if snapshot.get('form_schema', lease.form_schema) != lease.form_schema:
                raise ValueError('Private form schema changed.')
            snapshot['form_schema'] = lease.form_schema
            # Private labels/options are already bounded in UTF-8 by the scene
            # validator. ASCII escaping can triple Unicode-heavy valid forms.
            return web.json_response({'state': lease.state, 'form': snapshot},
                dumps=lambda value: json.dumps(value, ensure_ascii=False))

    async def promote(self, lease, state):
        if lease.delegated_task is not None:
            await lease.delegated_task.close()
        lease.state = state
        lease.cleanup = 'retained'
        lease.session_deadline = time.time() + SESSION_SECONDS
        if self.connected_factory is not None:
            lease.connected = self.connected_factory(lease.browser)
            await self.bounded(lease, lease.connected.start)
        retain = getattr(lease.browser, 'retain_session', None)
        if callable(retain):
            # Retain only after the connected adapter has checked the root
            # document. This extends lifetime, never destination authority.
            await self.bounded(lease, lambda: retain(SESSION_SECONDS))

    async def complete(self, request):
        lease, value = await self.human_lease(request, {'request_id', 'revision'})
        async with lease.lock:
            self.boundary()
            if (lease.state != 'active' or lease.binding['provider'] != 'website'
                    or lease.binding.get('agentic') is not True or lease.form_schema != 'website-v2'):
                raise ValueError('Owner completion is not available.')
            if self.expired(lease):
                await self.finish(lease, 'expired')
                raise ValueError('Login expired.')
            if (type(value['revision']) is not str or not 1 <= len(value['revision']) <= 128
                    or not callable(getattr(lease.browser, 'owner_finishable', None))):
                raise ValueError('Invalid owner completion.')
            finishable = await self.bounded(lease, lambda: lease.browser.owner_finishable(value['revision']))
            self.boundary()
            if lease.state != 'active' or finishable is not True:
                raise ValueError('Owner completion requires a completed private human step.')
            # No website action, supplied value or model judgment establishes
            # this outcome. Only this revision-bound private human request does.
            await self.promote(lease, 'owner_confirmed')
            return web.json_response({'accepted': True})

    async def approve_origin(self, request):
        # Only the private human channel can approve the server-held pending
        # transition. A caller cannot supply a destination or credential value.
        lease, value = await self.human_lease(request, {'request_id', 'revision'})
        async with lease.lock:
            self.boundary()
            if (lease.state != 'active' or lease.binding['provider'] != 'website'
                    or lease.binding.get('agentic') is not True or lease.form_schema != 'website-v2'):
                raise ValueError('Origin approval is not available.')
            if self.expired(lease):
                await self.finish(lease, 'expired')
                raise ValueError('Login expired.')
            if (type(value['revision']) is not str or not 1 <= len(value['revision']) <= 128
                    or not callable(getattr(lease.browser, 'approve_origin', None))):
                raise ValueError('Invalid origin approval.')
            lease.idle_deadline = time.time() + 120
            lease.human_deadline = time.time() + 45
            await self.bounded(lease, lambda: lease.browser.approve_origin(value['revision']))
            self.boundary()
            if lease.state != 'active':
                raise ValueError('Origin approval is no longer active.')
            lease.human_deadline = time.time() + 45
            return web.json_response({'approved': True})

    async def choose_action(self, request):
        lease, value = await self.human_lease(request, {'request_id', 'revision', 'choice'})
        async with lease.lock:
            self.boundary()
            if self.expired(lease) or lease.state != 'active' or lease.form_schema != 'website-v1':
                raise ValueError('Login is not active.')
            if (type(value['revision']) is not str or not 1 <= len(value['revision']) <= 128
                    or type(value['choice']) is not str or not re.fullmatch(r'o[1-9][0-9]?', value['choice'])
                    or not callable(getattr(lease.browser, 'choose_action', None))):
                raise ValueError('Invalid clarification response.')
            # Human activity may renew idle liveness, never absolute binding
            # expiry, gateway liveness, authentication or session lifetime.
            lease.idle_deadline = time.time() + 120
            lease.human_deadline = time.time() + 45
            await self.bounded(lease, lambda: lease.browser.choose_action(value['revision'], value['choice']))
            lease.human_deadline = time.time() + 45
            return web.json_response({'accepted': True})

    async def submit(self, request):
        lease, value = await self.human_lease(request, {'request_id', 'revision', 'action', 'values'})
        async with lease.lock:
            self.boundary()
            if self.expired(lease) or lease.state != 'active':
                raise ValueError('Login is not active.')
            if (not isinstance(value['revision'], str) or len(value['revision']) > 200
                    or not isinstance(value['action'], str) or len(value['action']) > 80
                    or not isinstance(value['values'], dict)):
                raise ValueError('Invalid form response.')
            lease.idle_deadline = time.time() + 120
            lease.human_deadline = time.time() + 45
            try:
                if lease.binding['provider'] == 'website' and value['action'] == 'confirm':
                    if value['values'] or not await self.bounded(lease, lambda: lease.browser.confirmable(value['revision'])):
                        raise ValueError('The website form is not ready for owner confirmation.')
                    await self.promote(lease, 'owner_confirmed')
                else:
                    await self.bounded(lease, lambda: lease.browser.submit(
                        value['revision'], value['action'], value['values']))
            finally:
                value['values'].clear()
            lease.human_deadline = time.time() + 45
            return web.json_response({'accepted': True})

    async def lookup(self, request):
        return self.lookup_scope(await request.json())

    def lookup_scope(self, value):
        if not isinstance(value, dict) or set(value) != {'request_id', 'session_id', 'runtime_epoch'}:
            raise ValueError('Invalid control scope.')
        lease = self.leases.get(value['request_id'])
        if not lease or lease.id != value['session_id'] or lease.binding['runtime_epoch'] != value['runtime_epoch']:
            raise ValueError('Unknown control scope.')
        return lease

    def task_scope(self, value):
        lease = self.lookup_scope(value)
        if (lease.binding.get('agentic') is not True or lease.binding['provider'] != 'website'
                or lease.state != 'active' or self.expired(lease)):
            raise ValueError('Delegated website task is unavailable.')
        return lease

    async def task_operation(self, lease, operation, *arguments):
        # Inference runs outside this lock. Every actual browser operation still
        # checks liveness, the current scene, and the existing dispatch guards.
        async with lease.lock:
            self.boundary()
            self.task_scope({'request_id': lease.binding['request_id'],
                             'session_id': lease.id, 'runtime_epoch': lease.binding['runtime_epoch']})
            from .website_agent import validate_agent_observation
            method = getattr(lease.browser, 'agent_' + operation)
            return validate_agent_observation(await self.bounded(lease, lambda: method(*arguments)))

    async def start_task(self, lease, *, goal=None):
        from .website_goal import DEFAULT_GOAL, validate_task_goal
        goal = validate_task_goal(DEFAULT_GOAL if goal is None else goal)
        async with lease.lock:
            self.boundary()
            if lease.delegated_task is None:
                if self.step_planner_factory is None or lease.state != 'active' or self.expired(lease):
                    raise ValueError('Jev delegation is not configured or has ended.')
                from .website_task import WebsiteTask
                planner = self.step_planner_factory() if goal == DEFAULT_GOAL else self.step_planner_factory(goal=goal)
                lease.delegated_goal = goal
                lease.delegated_task = WebsiteTask(planner,
                    observe=lambda: self.task_operation(lease, 'observe'),
                    act=lambda revision, ref: self.task_operation(lease, 'act', revision, ref),
                    present=lambda revision, plan: self.task_operation(lease, 'present', revision, plan))
                lease.delegated_task.start()
            elif lease.delegated_goal != goal:
                raise ValueError('The existing website task objective cannot be replaced.')
            # A repeated RPC observes the same job; it never creates a new budget
            # or replays a failed/uncertain browser operation.
            return lease.delegated_task.snapshot()

    async def task_status(self, lease):
        if lease.delegated_task is None:
            raise ValueError('No delegated website task exists.')
        return lease.delegated_task.snapshot()

    async def cancel_task(self, lease):
        if lease.delegated_task is None:
            raise ValueError('No delegated website task exists.')
        await lease.delegated_task.close()
        return lease.delegated_task.snapshot()

    async def agent(self, request):
        """Task-bound, value-free agent operations on an explicitly agentic lease."""
        value = await request.json()
        if type(value) is not dict or set(value) != {'request_id', 'session_id', 'runtime_epoch', 'operation'}:
            raise ValueError('Invalid website agent request.')
        operation = value.pop('operation')
        lease = self.lookup_scope(value)
        if not lease.binding.get('agentic') or lease.binding['provider'] != 'website':
            raise ValueError('This lease does not expose agent operations.')
        allowed = {'observe': {'action'}, 'act': {'action', 'revision', 'action_ref'},
                   'present': {'action', 'revision', 'plan'}}
        if (type(operation) is not dict or type(operation.get('action')) is not str
                or operation['action'] not in allowed or set(operation) != allowed[operation['action']]):
            raise ValueError('Invalid website agent operation.')
        async with lease.lock:
            self.boundary()
            if self.expired(lease):
                await self.finish(lease, 'expired')
            if lease.state != 'active':
                return web.json_response({'state': lease.state})
            from .website_agent import validate_agent_observation
            browser = lease.browser
            try:
                if (operation['action'] != 'observe' and lease.delegated_task is not None
                        and lease.delegated_task.snapshot()['state'] in {'running', 'waiting_for_owner'}):
                    from .website_agent import AgentProposalError
                    raise AgentProposalError('human_step_pending')
                if operation['action'] == 'observe':
                    action = browser.agent_observe
                elif operation['action'] == 'act':
                    action = lambda: browser.agent_act(operation['revision'], operation['action_ref'])
                else:
                    action = lambda: browser.agent_present(operation['revision'], operation['plan'])
                observed = validate_agent_observation(await self.bounded(lease, action))
                return web.json_response({'state': lease.state, 'progress': observed['progress'],
                                          'observation': observed})
            except RecoverableFormError as error:
                if error.code in {'stale_form', 'invalid_plan'}:
                    # Keep the same lease lock and refresh only the read. The
                    # rejected act/present is never replayed, even if this fails.
                    self.boundary()
                    if lease.state != 'active':
                        return web.json_response({'state': lease.state})

                    async def refresh():
                        return validate_agent_observation(await browser.agent_observe())

                    try:
                        observed = await self.bounded(lease, refresh)
                    except RecoverableFormError:
                        # An unsettled document may still need an explicit read.
                        observed = None
                    except Exception:
                        if lease.state != 'active':
                            return web.json_response({'state': lease.state})
                        raise
                    self.boundary()
                    if lease.state != 'active':
                        return web.json_response({'state': lease.state})
                    if observed is not None:
                        return web.json_response({'state': lease.state, 'progress': observed['progress'],
                            'observation': observed, 'error': {'code': error.code}})
                return web.json_response({'state': lease.state, 'progress': browser.agent_progress(),
                                          'error': {'code': error.code}})
            except SubmitUncertainError:
                # bounded() has already closed this lease. Preserve the reason
                # without exporting active-only progress or replaying dispatch.
                return web.json_response({'state': lease.state, 'error': {'code': 'action_uncertain'}})

    async def diagnostics(self, request):
        # Trusted Unix peer + the same unguessable lease scope as status. This
        # does not renew the lease or expose observations to the normal agent.
        lease = await self.lookup(request)
        async with lease.lock:
            if self.expired(lease):
                await self.finish(lease, 'expired')
            browser = lease.browser
            details = browser.diagnostics() if hasattr(browser, 'diagnostics') else lease.diagnostics_snapshot
            if lease.delegated_task is not None:
                details = {**(details or {}), 'delegated_task': lease.delegated_task.diagnostics()}
            return web.json_response({'state': lease.state, 'diagnostics': details})

    async def status(self, request):
        lease = await self.lookup(request)
        # A verified heartbeat must not wait behind a browser cold start or
        # navigation. It cannot revive an already expired lease.
        if not self.expired(lease) and lease.state not in TERMINAL:
            lease.gateway_deadline = time.time() + 45
        async with lease.lock:
            if self.expired(lease):
                await self.finish(lease, 'expired')
            elif lease.state not in TERMINAL:
                lease.gateway_deadline = time.time() + 45
            return web.json_response(self.result(lease))

    async def use(self, request):
        value = await request.json()
        required = {'request_id', 'session_id', 'runtime_epoch', 'owner', 'chat_id', 'turn_id', 'operation'}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError('Invalid connected operation.')
        lease = self.leases.get(value['request_id'])
        if not lease or value['session_id'] != lease.id or any(
                value[k] != lease.binding[k] for k in ('runtime_epoch', 'owner', 'chat_id', 'turn_id')):
            raise ValueError('Wrong connected session.')
        async with lease.lock:
            if lease.state not in CONNECTED or lease.connected is None:
                raise ValueError('A verified website session is required.')
            result = await self.bounded(lease, lambda: lease.connected.run(value['operation']))
            return web.json_response({'result': result})

    async def cancel(self, request):
        lease = await self.lookup(request)
        async with lease.lock:
            await self.finish(lease, 'cancelled')
            return web.json_response(self.result(lease))

    async def cancel_human(self, request):
        lease, _ = await self.human_lease(request, {'request_id'})
        async with lease.lock:
            await self.finish(lease, 'cancelled')
            return web.json_response({'state': lease.state})

    async def detach(self, request):
        lease, _ = await self.human_lease(request, {'request_id'})
        async with lease.lock:
            if lease.state in CONNECTED:
                # The parent can remove the iframe as soon as control reports
                # success, before the last human response arrives. Closing that
                # form must not revoke the authenticated task's browser.
                lease.human_hash = ''
            else:
                await self.finish(lease, 'cancelled')
            return web.json_response({'state': lease.state})

    async def finish(self, lease, state):
        lease.state = state
        lease.ticket_hash = lease.human_hash = ''
        if lease.delegated_task is not None:
            await lease.delegated_task.close()
        if lease.browser is None:
            lease.cleanup = 'confirmed'
            return
        try:
            if lease.closing is not None and lease.closing.done() and (
                    lease.closing.cancelled() or lease.closing.exception() is not None):
                lease.closing = None
            if lease.closing is None:
                if hasattr(lease.browser, 'diagnostics'):
                    lease.diagnostics_snapshot = lease.browser.diagnostics()
                lease.closing = asyncio.create_task(lease.browser.close())
                lease.closing.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            await asyncio.wait_for(asyncio.shield(lease.closing), 10)
            lease.browser = lease.connected = None
            lease.cleanup = 'confirmed'
        except BaseException:
            lease.cleanup = 'uncertain'
            raise

    async def sweep(self):
        while True:
            await asyncio.sleep(1)
            try:
                self.boundary()
                boundary_ready = True
            except Exception:
                boundary_ready = False
            for key, lease in tuple(self.leases.items()):
                async with lease.lock:
                    if not boundary_ready or self.expired(lease):
                        try:
                            await self.finish(lease, 'expired')
                        except Exception:
                            pass
                    if lease.browser is None and time.time() > max(lease.binding['expires'], lease.session_deadline) + 30:
                        self.leases.pop(key, None)

    async def close(self):
        if self.sweeper is not None:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        for lease in self.leases.values():
            async with lease.lock:
                await self.finish(lease, 'cancelled')
        await self.task_mcp.close()

    async def page(self, request):
        text = (Path(__file__).parent / 'login_web/index.html').read_text()
        return web.Response(text=text.replace('PARENT_ORIGIN', self.parent), content_type='text/html')

    async def script(self, request):
        return web.FileResponse(Path(__file__).parent / 'login_web/login.js')

    async def style(self, request):
        return web.FileResponse(Path(__file__).parent / 'login_web/login.css')

    async def controls_script(self, request):
        return web.FileResponse(Path(__file__).parent / 'web/form-controls.js')


async def serve(config_path):
    """Run only as the provisioned broker UID, with immutable configuration."""
    from .connected_browser import ConnectedBrowser
    from .executor import boundary_check, immutable, load_profile
    path = Path(config_path)
    if os.geteuid() == 0 or not immutable(path) or path.stat().st_size > 16384:
        raise ValueError('A non-root broker and immutable configuration are required.')
    config = json.loads(path.read_text())
    required = {'origin', 'parent_origin', 'gateway_uid', 'broker_uid', 'socket', 'port', 'execution'}
    if (not isinstance(config, dict) or not required <= set(config)
            or set(config) - required - {'composer', 'tiktok_static_resources', 'static_resources', 'auth_hosts', 'anonymous_static_resources'}
            or type(config.get('tiktok_static_resources', False)) is not bool
            or type(config.get('anonymous_static_resources', False)) is not bool
            or config['broker_uid'] != os.geteuid() or config['gateway_uid'] == os.geteuid()
            or type(config['port']) is not int or not 1024 <= config['port'] <= 65535):
        raise ValueError('Invalid broker configuration.')
    from .website_resources import validate_static_resources
    resource_policy = validate_static_resources(config.get('static_resources', {}))
    validate_static_resources(resource_policy, tiktok=config.get('tiktok_static_resources', False))
    from .website_auth_hosts import validate_auth_hosts
    auth_policy = validate_auth_hosts(config.get('auth_hosts', {}))
    profile = load_profile(config['execution'])
    if os.geteuid() == profile['executor_uid'] or config['gateway_uid'] == profile['executor_uid']:
        raise ValueError('Separate gateway, executor and broker UIDs are required.')
    address = Path(config['socket'])
    if not address.is_absolute() or address.exists() or not address.parent.is_dir():
        raise ValueError('A fresh private control socket is required.')
    info = address.parent.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o007:
        raise ValueError('Invalid broker runtime directory.')
    composition = steps = None
    if 'composer' in config:
        from .website_composer import composer_factory, step_planner_factory
        composition = composer_factory(config['composer'], config['broker_uid'])
        steps = step_planner_factory(config['composer'], config['broker_uid'])
    broker = LoginBroker(config['origin'], config['parent_origin'], config['gateway_uid'],
                         connected_factory=ConnectedBrowser,
                         composer_factory=composition,
                         step_planner_factory=steps,
                         tiktok_static_resources=config.get('tiktok_static_resources', False),
                         anonymous_static_resources=config.get('anonymous_static_resources', False),
                         static_resources=resource_policy, auth_hosts=auth_policy,
                         boundary_check=lambda: boundary_check(config['execution']))
    human, control = web.AppRunner(broker.human, access_log=None), web.AppRunner(broker.control, access_log=None)
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)
    try:
        await human.setup()
        await control.setup()
        await web.UnixSite(control, str(address)).start()
        address.chmod(0o660)
        await web.TCPSite(human, '127.0.0.1', config['port']).start()
        broker.sweeper = asyncio.create_task(broker.sweep())
        await stop.wait()
    finally:
        await human.cleanup()
        await control.cleanup()
        try:
            await broker.close()
        finally:
            address.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Oak private website-form broker')
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.config))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        # Never include provider URLs, submitted fields, or browser diagnostics.
        raise SystemExit('Private website-form broker stopped.') from None


if __name__ == '__main__':
    main()
