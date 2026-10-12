"""Control-socket MCP 2025-11-25 endpoint for one already-authorized lease.

Implements JSON responses over Streamable HTTP, initialization, ping and tools.
No SSE, batches, server requests, resumption, OAuth, or MCP experimental tasks.
The broker must authenticate the Unix peer and execution boundary before setting
``request['oak.control.authorized'] = True``. This endpoint never grants a lease.
The scope lives only in gateway initialization metadata, never tool arguments.

Contract: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
Lifecycle: https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
Tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
"""

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field

from aiohttp import web

from .website_task import validate_task_snapshot


PROTOCOL_VERSION = '2025-11-25'
MAX_BODY = 16384
MAX_REQUESTS = 1024
SESSION_SECONDS = 300
_COMMON_HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
_TOOLS = (
    ('website_sign_in', 'Start once with an optional public task goal; omitted goal means existing-account sign-in. The task stays in this private workspace.'),
    ('website_task_status', 'Read the delegated task state without input values or page content.'),
    ('website_task_cancel', 'Stop delegated automation. An interrupted browser operation may also close the private workspace.'),
)


def _response(value=None, *, status=200, headers=None):
    headers = {**_COMMON_HEADERS, **(headers or {})}
    if value is None:
        return web.Response(status=status, headers=headers)
    return web.json_response(value, status=status, headers=headers)


def _error(code, message, request_id=None, *, status=200):
    value = {'jsonrpc': '2.0', 'error': {'code': code, 'message': message}}
    if request_id is not None:
        value['id'] = request_id
    return _response(value, status=status)


def _identifier(value):
    return ((type(value) is int and -(2**53) < value < 2**53)
            or (type(value) is str and 0 < len(value) <= 128
                and all(32 <= ord(c) <= 126 for c in value)))


def _scope(value):
    return (type(value) is dict and set(value) == {'request_id', 'session_id', 'runtime_epoch'}
            and all(type(value.get(k)) is str and re.fullmatch('[a-f0-9]{32}', value[k])
                    for k in ('request_id', 'session_id'))
            and type(value.get('runtime_epoch')) is str and 0 < len(value['runtime_epoch']) <= 200)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result


def _constant(_):
    raise ValueError('Non-finite JSON value.')


def _accepts(request, required):
    accepted = set()
    for header in request.headers.getall('Accept', []):
        for item in header.split(','):
            parts = item.strip().lower().split(';')
            media = parts[0].strip()
            quality = '1'
            seen_quality = False
            for part in parts[1:]:
                name, sep, value = part.strip().partition('=')
                if not sep:
                    return False
                if name == 'q':
                    if seen_quality or not re.fullmatch(r'(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)', value):
                        return False
                    seen_quality, quality = True, value
            if float(quality) > 0:
                accepted.add(media)
    return required <= accepted


@dataclass
class _Session:
    scope: dict
    lease: object
    deadline: float
    initialized: bool = False
    seen: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WebsiteTaskMCP:
    """Register ``handle`` only on the broker's authenticated control app.

    ``lookup_scope(scope)`` is synchronous and must reject inactive/expired or
    otherwise revoked leases. The three job callbacks are async, receive that
    lease, and return a validated task snapshot. Start must be idempotent per
    lease across MCP sessions. Cancellation stops automation; a broker browser
    operation interrupted in flight may conservatively close its workspace.
    """

    def __init__(self, lookup_scope, start_job, job_status, cancel_job, *,
                 allowed_origins=(), max_sessions=32):
        if (any(not callable(value) for value in (lookup_scope, start_job, job_status, cancel_job))
                or type(max_sessions) is not int or not 1 <= max_sessions <= 32
                or not isinstance(allowed_origins, (tuple, list, set, frozenset))
                or any(type(value) is not str or not re.fullmatch(r'https?://[^/?#\s]+', value)
                       for value in allowed_origins)):
            raise ValueError('Invalid MCP endpoint configuration.')
        self.lookup_scope = lookup_scope
        self.start_job, self.job_status, self.cancel_job = start_job, job_status, cancel_job
        self.allowed_origins = frozenset(allowed_origins)
        self.max_sessions = max_sessions
        self.sessions = {}
        self.closed = False

    def _lease(self, session):
        if self.closed or time.monotonic() >= session.deadline:
            raise ValueError('Expired MCP session.')
        lease = self.lookup_scope(dict(session.scope))
        if lease is not session.lease:
            raise ValueError('Replaced lease.')
        return lease

    def _prune(self):
        for token, session in tuple(self.sessions.items()):
            try:
                self._lease(session)
            except Exception:
                self.sessions.pop(token, None)

    async def _read(self, request):
        raw = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                raise ValueError('MCP message too large.')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)

    async def handle(self, request):
        # The marker is a server-side request property, never an HTTP header.
        if request.get('oak.control.authorized') is not True:
            return _error(-32000, 'Authenticated control peer required.', status=403)
        for header in ('Origin', 'MCP-Protocol-Version', 'MCP-Session-Id', 'Content-Type'):
            if len(request.headers.getall(header, [])) > 1:
                return _error(-32600, 'Duplicate transport header.', status=400)
        origin = request.headers.get('Origin')
        if origin is not None and origin not in self.allowed_origins:
            return _error(-32000, 'Origin is not allowed.', status=403)
        if self.closed:
            return _error(-32000, 'MCP endpoint is closed.', status=503)
        version = request.headers.get('MCP-Protocol-Version')
        if version is not None and version != PROTOCOL_VERSION:
            return _error(-32600, 'Unsupported protocol version.', status=400)
        token = request.headers.get('MCP-Session-Id')
        session = None
        if token is not None:
            session = self.sessions.get(token)
            try:
                if session is None:
                    raise ValueError('Unknown session.')
                self._lease(session)
            except Exception:
                self.sessions.pop(token, None)
                return _error(-32000, 'Unknown MCP session.', status=404)
        if session is not None and version != PROTOCOL_VERSION:
            return _error(-32600, 'Protocol header required.', status=400)
        if request.method == 'GET':
            if not _accepts(request, {'text/event-stream'}):
                return _error(-32600, 'Accept must include text/event-stream.', status=406)
            return _response(status=405, headers={'Allow': 'POST, DELETE'})
        if request.method not in {'POST', 'DELETE'}:
            return _response(status=405, headers={'Allow': 'POST, DELETE'})
        if request.method == 'DELETE':
            if session is None or version != PROTOCOL_VERSION:
                return _error(-32600, 'Session and protocol headers required.', status=400)
            async with session.lock:
                try:
                    if self.sessions.get(token) is not session:
                        raise ValueError('Closed session.')
                    lease = self._lease(session)
                except Exception:
                    self.sessions.pop(token, None)
                    return _error(-32000, 'Unknown MCP session.', status=404)
                self.sessions.pop(token, None)
                try:
                    await self.cancel_job(lease)
                except Exception:
                    return _error(-32603, 'Delegation cleanup failed.', status=500)
            return _response(status=204)
        if not _accepts(request, {'application/json', 'text/event-stream'}):
            return _error(-32600, 'Accept must include application/json and text/event-stream.', status=406)
        if request.content_type != 'application/json' or request.charset not in {None, 'utf-8', 'UTF-8'}:
            return _error(-32600, 'UTF-8 application/json is required.', status=415)
        try:
            message = await asyncio.wait_for(self._read(request), 5)
        except Exception:
            return _error(-32700, 'Invalid JSON message.', status=400)
        if (type(message) is not dict or message.get('jsonrpc') != '2.0'
                or set(message) - {'jsonrpc', 'id', 'method', 'params'}
                or type(message.get('method')) is not str or not 1 <= len(message['method']) <= 128
                or ('id' in message and not _identifier(message['id']))
                or ('params' in message and type(message['params']) is not dict)):
            return _error(-32600, 'Invalid JSON-RPC message.', status=400)
        request_id = message.get('id')
        params, method = message.get('params', {}), message['method']
        if method == 'initialize':
            if session is not None or request_id is None:
                return _error(-32600, 'Initialization requires a new session request.', request_id, status=400)
            return self._initialize(request_id, params)
        if session is None or version != PROTOCOL_VERSION:
            return _error(-32600, 'Session and protocol headers required.', request_id, status=400)
        async with session.lock:
            try:
                lease = self._lease(session)
                if self.sessions.get(token) is not session:
                    raise ValueError('Closed session.')
            except Exception:
                self.sessions.pop(token, None)
                return _error(-32000, 'Unknown MCP session.', request_id, status=404)
            if request_id is not None:
                key = (type(request_id), request_id)
                if key in session.seen:
                    return _error(-32600, 'Request ID already used.', request_id)
                if len(session.seen) >= MAX_REQUESTS:
                    return _error(-32000, 'Session request limit reached.', request_id)
                session.seen.add(key)
            meta = params.get('_meta', {})
            if type(meta) is not dict or 'oak/lease' in meta:
                return _error(-32602, 'Invalid request metadata.', request_id, status=400 if request_id is None else 200)
            values = {key: value for key, value in params.items() if key != '_meta'}
            if request_id is None:
                if method != 'notifications/initialized' or values:
                    return _error(-32600, 'Unsupported notification.', status=400)
                session.initialized = True
                return _response(status=202)
            if method == 'ping' and not values:
                return _response({'jsonrpc': '2.0', 'id': request_id, 'result': {}})
            if not session.initialized:
                return _error(-32000, 'Send notifications/initialized first.', request_id)
            if method == 'tools/list':
                if values:
                    return _error(-32602, 'No cursor or arguments are supported.', request_id)
                tools = [{'name': name, 'description': description,
                          'inputSchema': {'type': 'object', 'properties': {
                              'goal': {'type': 'string', 'minLength': 1, 'maxLength': 400}}
                              if name == 'website_sign_in' else {}, 'additionalProperties': False}}
                         for name, description in _TOOLS]
                return _response({'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': tools}})
            if method != 'tools/call':
                return _error(-32601, 'Method not found.', request_id)
            if (set(values) - {'name', 'arguments'} or type(values.get('name')) is not str
                    or values['name'] not in dict(_TOOLS)
                    or type(values.get('arguments', {})) is not dict):
                return _error(-32602, 'Choose a listed tool with its declared arguments.', request_id)
            arguments = values.get('arguments', {})
            if set(arguments) - ({'goal'} if values['name'] == 'website_sign_in' else set()):
                return _error(-32602, 'Invalid task arguments.', request_id)
            if 'goal' in arguments:
                from .website_goal import validate_task_goal
                try:
                    goal = validate_task_goal(arguments['goal'])
                except ValueError:
                    return _error(-32602, 'Use a public task objective without private values.', request_id)
            callback = {'website_sign_in': self.start_job, 'website_task_status': self.job_status,
                        'website_task_cancel': self.cancel_job}[values['name']]
            try:
                result = await callback(lease, goal=goal) if 'goal' in arguments else await callback(lease)
                snapshot = validate_task_snapshot(result)
                # Do not return a result for a revoked/replaced scope.
                try:
                    self._lease(session)
                except Exception:
                    self.sessions.pop(token, None)
                    return _error(-32000, 'Unknown MCP session.', request_id, status=404)
                result = {'structuredContent': snapshot,
                          'content': [{'type': 'text', 'text': json.dumps(snapshot, separators=(',', ':'))}],
                          'isError': False}
            except Exception:
                error = {'state': 'requires_agent', 'error': 'callback_failed'}
                result = {'structuredContent': error,
                          'content': [{'type': 'text', 'text': json.dumps(error, separators=(',', ':'))}],
                          'isError': True}
            return _response({'jsonrpc': '2.0', 'id': request_id, 'result': result})

    def _initialize(self, request_id, params):
        client = params.get('clientInfo')
        meta = params.get('_meta')
        if (set(params) != {'protocolVersion', 'capabilities', 'clientInfo', '_meta'}
                or type(params.get('protocolVersion')) is not str
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', params['protocolVersion'])
                or type(params.get('capabilities')) is not dict
                or type(client) is not dict
                or any(type(client.get(key)) is not str or not 1 <= len(client[key]) <= 200
                       for key in ('name', 'version'))
                or type(meta) is not dict or set(meta) != {'oak/lease'} or not _scope(meta['oak/lease'])):
            return _error(-32602, 'Invalid initialization parameters.', request_id)
        self._prune()
        if len(self.sessions) >= self.max_sessions:
            return _error(-32000, 'MCP session capacity reached.', request_id, status=503)
        try:
            scope = dict(meta['oak/lease'])
            lease = self.lookup_scope(dict(scope))
            if lease is None:
                raise ValueError('Unknown lease.')
        except Exception:
            return _error(-32000, 'Unknown website scope.', request_id, status=404)
        token = secrets.token_urlsafe(32)
        session = _Session(scope, lease, time.monotonic() + SESSION_SECONDS)
        session.seen.add((type(request_id), request_id))
        self.sessions[token] = session
        return _response({'jsonrpc': '2.0', 'id': request_id, 'result': {
            'protocolVersion': PROTOCOL_VERSION, 'serverInfo': {'name': 'oak-website-tasks', 'version': '1.0.0'},
            'capabilities': {'tools': {'listChanged': False}}}}, headers={'MCP-Session-Id': token})

    async def close(self):
        self.closed = True
        sessions, self.sessions = tuple(self.sessions.values()), {}
        seen = set()
        for session in sessions:
            if id(session.lease) in seen:
                continue
            seen.add(id(session.lease))
            try:
                async with session.lock:
                    await self.cancel_job(session.lease)
            except Exception:
                pass
