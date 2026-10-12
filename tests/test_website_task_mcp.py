"""MCP lifecycle and scope boundaries, without a model or website browser."""

import asyncio
import copy
import json
import time
import unittest
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from oak.website_task_mcp import MAX_BODY, MAX_REQUESTS, PROTOCOL_VERSION, WebsiteTaskMCP


def snapshot(state='running'):
    return {'state': state, 'phase': 'observing' if state == 'running' else 'stopped',
            'steps': 1, 'calls': 1, 'progress': {'event_seq': 0, 'outcome': 'idle'}, 'error': None}


class WebsiteTaskMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.authorized = True
        self.lease, self.other = object(), object()
        self.scope = {'request_id': 'a' * 32, 'session_id': 'b' * 32, 'runtime_epoch': 'epoch'}
        self.scopes = {tuple(sorted(self.scope.items())): self.lease}
        self.start = AsyncMock(return_value=snapshot())
        self.status = AsyncMock(return_value=snapshot('waiting_for_owner'))
        self.cancel = AsyncMock(return_value=snapshot('cancelled'))
        self.endpoint = WebsiteTaskMCP(self.lookup, self.start, self.status, self.cancel)

        @web.middleware
        async def authorized(request, handler):
            if self.authorized:
                request['oak.control.authorized'] = True
            return await handler(request)

        app = web.Application(middlewares=[authorized])
        app.router.add_route('*', '/mcp', self.endpoint.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.endpoint.close)
        self.sequence = 1
        self.token = None

    def lookup(self, scope):
        return self.scopes[tuple(sorted(scope.items()))]

    def headers(self, **changes):
        headers = {'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': PROTOCOL_VERSION}
        if self.token:
            headers['MCP-Session-Id'] = self.token
        headers.update(changes)
        return headers

    async def rpc(self, method, params=None, *, request_id=None, headers=None):
        self.sequence += 1
        value = {'jsonrpc': '2.0', 'id': self.sequence if request_id is None else request_id,
                 'method': method}
        if params is not None:
            value['params'] = params
        return await self.client.post('/mcp', json=value, headers=self.headers() if headers is None else headers)

    async def initialize(self, *, scope=None, version=PROTOCOL_VERSION):
        params = {'protocolVersion': version, 'capabilities': {},
                  'clientInfo': {'name': 'test-gateway', 'version': '1'},
                  '_meta': {'oak/lease': scope or self.scope}}
        response = await self.rpc('initialize', params,
                                  headers={'Accept': 'application/json, text/event-stream'})
        if response.status == 200 and 'MCP-Session-Id' in response.headers:
            self.token = response.headers['MCP-Session-Id']
        return response

    async def ready(self):
        self.assertEqual((await self.initialize()).status, 200)
        response = await self.client.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                          headers=self.headers())
        self.assertEqual(response.status, 202)
        self.assertEqual(await response.read(), b'')

    async def test_lifecycle_discovery_and_structured_tool_results(self):
        response = await self.initialize(version='2024-11-05')
        result = (await response.json())['result']
        self.assertEqual(result['protocolVersion'], PROTOCOL_VERSION)
        self.assertEqual(result['capabilities'], {'tools': {'listChanged': False}})
        self.assertRegex(self.token, r'^[A-Za-z0-9_-]{43}$')
        self.assertNotIn('oak/lease', await response.text())
        self.assertEqual((await (await self.rpc('ping')).json())['result'], {})
        response = await self.rpc('tools/list')
        self.assertEqual((await response.json())['error']['code'], -32000)
        await self.client.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}, headers=self.headers())
        response = await self.rpc('tools/list')
        tools = (await response.json())['result']['tools']
        self.assertEqual([t['name'] for t in tools], ['website_sign_in', 'website_task_status', 'website_task_cancel'])
        for tool in tools:
            self.assertFalse(tool['inputSchema']['additionalProperties'])
            self.assertEqual(set(tool['inputSchema']['properties']),
                             {'goal'} if tool['name'] == 'website_sign_in' else set())
        for tool, callback in [('website_sign_in', self.start), ('website_task_status', self.status),
                               ('website_task_cancel', self.cancel)]:
            response = await self.rpc('tools/call', {'name': tool, 'arguments': {}})
            result = (await response.json())['result']
            self.assertFalse(result['isError'])
            self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
            callback.assert_awaited_once_with(self.lease)

    async def test_public_objective_reaches_only_start_without_entering_receipts(self):
        await self.ready()
        goal = 'Compare the available account recovery methods.'
        response = await self.rpc('tools/call', {'name': 'website_sign_in', 'arguments': {'goal': goal}})
        self.assertFalse((await response.json())['result']['isError'])
        self.start.assert_awaited_once_with(self.lease, goal=goal)
        self.assertNotIn(goal, await response.text())
        for tool in ('website_task_status', 'website_task_cancel'):
            value = await (await self.rpc('tools/call', {'name': tool, 'arguments': {'goal': goal}})).json()
            self.assertEqual(value['error']['code'], -32602)
        for invalid in (None, '', 'x' * 401, 'Sign in as private@example.test', 'Use https://private.example.test',
                        'opaque_' + 'a' * 30, 'line\nbreak'):
            value = await (await self.rpc('tools/call', {'name': 'website_sign_in', 'arguments': {'goal': invalid}})).json()
            self.assertEqual(value['error']['code'], -32602)
        self.assertEqual(self.start.await_count, 1)

    async def test_requires_server_control_marker_not_spoofed_header(self):
        self.authorized = False
        response = await self.client.post('/mcp', headers={'oak.control.authorized': 'true'}, json={})
        self.assertEqual(response.status, 403)
        self.assertFalse(self.endpoint.sessions)
        self.start.assert_not_awaited()

    async def test_origin_is_rejected_on_every_method(self):
        await self.ready()
        for method in ('POST', 'GET', 'DELETE', 'OPTIONS'):
            response = await self.client.request(method, '/mcp', headers=self.headers(Origin='https://evil.test'), json={})
            self.assertEqual(response.status, 403)
        self.endpoint.allowed_origins = frozenset({'https://gateway.test'})
        response = await self.rpc('ping', headers=self.headers(Origin='https://gateway.test'))
        self.assertEqual(response.status, 200)
        self.cancel.assert_not_awaited()

    async def test_accept_content_type_and_version_headers(self):
        await self.ready()
        for accept in ('*/*', 'application/json', 'text/event-stream',
                       'application/json, text/event-stream;q=0', 'application/json;q=wat, text/event-stream'):
            response = await self.rpc('ping', headers=self.headers(Accept=accept))
            self.assertEqual(response.status, 406)
        response = await self.rpc('ping', headers=self.headers(Accept='application/json;q=0.8, text/event-stream;q=1.0'))
        self.assertEqual(response.status, 200)
        for version in ('2025-03-26', '', 'invalid'):
            response = await self.rpc('ping', headers=self.headers(**{'MCP-Protocol-Version': version}))
            self.assertEqual(response.status, 400)
        headers = self.headers()
        headers.pop('MCP-Protocol-Version')
        self.assertEqual((await self.rpc('ping', headers=headers)).status, 400)
        self.assertEqual((await self.client.get('/mcp', headers=headers)).status, 400)
        self.assertEqual((await self.client.delete('/mcp', headers=headers)).status, 400)
        for content_type in ('text/plain', 'application/json; charset=latin-1'):
            response = await self.client.post('/mcp', data='{}', headers=self.headers(**{'Content-Type': content_type}))
            self.assertEqual(response.status, 415)

    async def test_duplicate_transport_headers_rejected(self):
        await self.ready()
        for key, value in [('Origin', 'https://safe.test'), ('MCP-Session-Id', self.token),
                           ('MCP-Protocol-Version', PROTOCOL_VERSION), ('Content-Type', 'application/json')]:
            headers = [(k, v) for k, v in self.headers().items() if k.lower() != key.lower()]
            headers += [(key, value), (key, value)]
            response = await self.client.post('/mcp', headers=headers, data='{}')
            self.assertEqual(response.status, 400)

    async def test_get_has_no_sse_and_delete_calls_only_injected_cancel(self):
        await self.ready()
        response = await self.client.get('/mcp', headers=self.headers(Accept='text/event-stream'))
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers['Allow'], 'POST, DELETE')
        response = await self.client.delete('/mcp', headers=self.headers())
        self.assertEqual(response.status, 204)
        self.assertEqual(await response.read(), b'')
        self.cancel.assert_awaited_once_with(self.lease)
        self.assertIs(self.lookup(self.scope), self.lease)  # This fake callback has no browser operation.
        self.assertEqual((await self.rpc('ping')).status, 404)

    async def test_scope_binding_exists_only_in_initialize_metadata(self):
        invalid = {**self.scope, 'runtime_epoch': 'other'}
        self.assertEqual((await self.initialize(scope=invalid)).status, 404)
        await self.ready()
        for params in ({'name': 'website_sign_in', 'arguments': self.scope},
                       {'name': 'website_sign_in', 'arguments': {}, '_meta': {'oak/lease': self.scope}},
                       {'name': 'website_sign_in', 'arguments': {}, 'session_id': self.scope['session_id']}):
            response = await self.rpc('tools/call', params)
            self.assertEqual((await response.json())['error']['code'], -32602)
        self.start.assert_not_awaited()
        response = await self.rpc('initialize', {})
        self.assertEqual(response.status, 400)

    async def test_concurrent_delete_cancels_delegation_only_once(self):
        await self.ready()
        entered, release = asyncio.Event(), asyncio.Event()
        async def cancel(_):
            entered.set()
            await release.wait()
            return snapshot('cancelled')
        self.cancel.side_effect = cancel
        one = asyncio.create_task(self.client.delete('/mcp', headers=self.headers()))
        await entered.wait()
        two = asyncio.create_task(self.client.delete('/mcp', headers=self.headers()))
        release.set()
        a, b = await asyncio.gather(one, two)
        self.assertEqual((a.status, b.status), (204, 404))
        self.cancel.assert_awaited_once_with(self.lease)

    async def test_replayed_id_cannot_repeat_callback_even_concurrently(self):
        await self.ready()
        entered, release = asyncio.Event(), asyncio.Event()
        async def start(_):
            entered.set()
            await release.wait()
            return snapshot()
        self.start.side_effect = start
        one = asyncio.create_task(self.rpc('tools/call', {'name': 'website_sign_in'}, request_id='same'))
        await entered.wait()
        two = asyncio.create_task(self.rpc('tools/call', {'name': 'website_sign_in'}, request_id='same'))
        release.set()
        a, b = await asyncio.gather(one, two)
        self.assertIn('result', await a.json())
        self.assertEqual((await b.json())['error']['code'], -32600)
        self.start.assert_awaited_once()

    async def test_revoked_replaced_expired_and_unknown_sessions(self):
        await self.ready()
        token = self.token
        self.scopes[tuple(sorted(self.scope.items()))] = self.other
        self.assertEqual((await self.rpc('tools/list')).status, 404)
        self.assertNotIn(token, self.endpoint.sessions)
        self.scopes[tuple(sorted(self.scope.items()))] = self.lease
        await self.ready()
        self.endpoint.sessions[self.token].deadline = time.monotonic() - 1
        self.assertEqual((await self.rpc('tools/list')).status, 404)
        await self.ready()
        self.scopes.clear()
        self.assertEqual((await self.rpc('tools/list')).status, 404)
        self.assertEqual((await self.rpc('ping', headers=self.headers(**{'MCP-Session-Id': 'unknown'}))).status, 404)
        self.start.assert_not_awaited()

    async def test_scope_invalidated_during_callback_does_not_return_success(self):
        await self.ready()
        async def start(_):
            self.scopes.clear()
            return snapshot()
        self.start.side_effect = start
        response = await self.rpc('tools/call', {'name': 'website_sign_in'})
        self.assertEqual(response.status, 404)
        self.assertNotIn('structuredContent', await response.text())

    async def test_invalid_sessions_are_pruned_before_capacity_check(self):
        self.endpoint.max_sessions = 1
        await self.initialize()
        first = self.token
        response = await self.initialize()
        self.assertEqual(response.status, 503)
        self.endpoint.sessions[first].deadline = time.monotonic() - 1
        self.assertEqual((await self.initialize()).status, 200)
        self.assertNotEqual(first, self.token)
        self.assertEqual(len(self.endpoint.sessions), 1)

    async def test_request_limit_and_typed_request_ids(self):
        await self.ready()
        self.assertEqual((await self.rpc('ping', request_id='50')).status, 200)
        self.assertEqual((await (await self.rpc('ping', request_id=50)).json())['result'], {})
        session = self.endpoint.sessions[self.token]
        session.seen.update((str, str(i)) for i in range(MAX_REQUESTS))
        response = await self.rpc('tools/call', {'name': 'website_sign_in'})
        self.assertEqual((await response.json())['error']['code'], -32000)
        self.start.assert_not_awaited()

    async def test_invalid_json_envelopes_batches_and_unsolicited_responses(self):
        await self.ready()
        invalid = ['[{}]', '{"jsonrpc":"2.0","jsonrpc":"2.0"}', '{"x":NaN}',
                   json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}}),
                   json.dumps({'jsonrpc': '2.0', 'id': None, 'method': 'ping'}),
                   json.dumps({'jsonrpc': '2.0', 'id': True, 'method': 'ping'}),
                   json.dumps({'jsonrpc': '2.0', 'id': 1.25, 'method': 'ping'}),
                   json.dumps({'jsonrpc': '2.0', 'id': 'x', 'method': 'ping', 'params': []}),
                   ' ' * (MAX_BODY + 1), '{']
        for body in invalid:
            response = await self.client.post('/mcp', data=body,
                                              headers=self.headers(**{'Content-Type': 'application/json'}))
            self.assertEqual(response.status, 400)
        self.start.assert_not_awaited()

    async def test_notifications_have_no_jsonrpc_response_when_accepted(self):
        await self.ready()
        response = await self.client.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                          headers=self.headers())
        self.assertEqual(response.status, 202)
        self.assertEqual(await response.read(), b'')
        response = await self.client.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/unknown'},
                                          headers=self.headers())
        self.assertEqual(response.status, 400)

    async def test_tool_validation_and_private_callback_data_never_escape(self):
        await self.ready()
        for params in ({'name': []}, {'name': 'unknown'}, {'name': 'website_sign_in', 'arguments': []}):
            response = await self.rpc('tools/call', params)
            self.assertEqual((await response.json())['error']['code'], -32602)
        self.start.assert_not_awaited()
        for value in ({**snapshot(), 'password': 'private-canary'},
                      {**snapshot(), 'error': 'private-canary'},
                      {**snapshot(), 'calls': True}):
            self.start.return_value = value
            response = await self.rpc('tools/call', {'name': 'website_sign_in'})
            body = await response.text()
            self.assertNotIn('private-canary', body)
            self.assertTrue(json.loads(body)['result']['isError'])
            self.assertEqual(json.loads(body)['result']['structuredContent'],
                             {'state': 'requires_agent', 'error': 'callback_failed'})
        self.start.side_effect = RuntimeError('private-canary')
        self.assertNotIn('private-canary', await (await self.rpc('tools/call', {'name': 'website_sign_in'})).text())

    async def test_close_cancels_once_per_lease_and_cannot_restart(self):
        await self.ready()
        await self.initialize()  # second transport session, same already-bound lease
        self.assertEqual(len(self.endpoint.sessions), 2)
        await self.endpoint.close()
        self.cancel.assert_awaited_once_with(self.lease)
        self.assertFalse(self.endpoint.sessions)
        self.assertEqual((await self.rpc('ping')).status, 503)
        await self.endpoint.close()
        self.cancel.assert_awaited_once_with(self.lease)

    async def test_initialization_rejects_missing_or_model_supplied_scope_fields(self):
        params = {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                  'clientInfo': {'name': 'gateway', 'version': '1'}, '_meta': {'oak/lease': self.scope}}
        for changes in ({'_meta': {}}, {'_meta': {'oak/lease': {**self.scope, 'owner': 1}}},
                        {'clientInfo': {'name': 'gateway'}}, {'capabilities': []}, {'protocolVersion': 2025}):
            response = await self.rpc('initialize', {**copy.deepcopy(params), **changes})
            self.assertEqual((await response.json())['error']['code'], -32602)
        self.assertFalse(self.endpoint.sessions)
