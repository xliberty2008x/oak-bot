"""Native delegation lifecycle with a mocked MCP client; no model or HTTP claims."""

import asyncio
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from oak.requests import RequestUnavailable
from oak.website_task import validate_task_snapshot
from test_website_agent_gateway import AgentBroker
import test_website_login as fixture


def task_state(state='running', sequence=0, error=None):
    return {'state': state, 'phase': 'observing' if state == 'running' else 'waiting',
            'steps': sequence + 1, 'calls': sequence + 1,
            'progress': {'event_seq': sequence, 'outcome': 'submitted' if sequence else 'idle'},
            'error': error}


class DelegatedBroker(AgentBroker):
    def __init__(self, origin):
        super().__init__(origin)
        self.states = {}
        self.task_enabled = True

    async def call(self, route, data):
        if route == '/health':
            return {**await super().call(route, data), 'website_task': self.task_enabled}
        if route == '/status':
            self.calls.append((route, deepcopy(data)))
            state = self.states.get(data['request_id'], 'waiting')
            await asyncio.sleep(0)
            return self.status(data['request_id'], state=state,
                               cleanup='retained' if state in {'authenticated', 'owner_confirmed'} else 'waiting',
                               session_expires=time.time() + 300 if state in {'authenticated', 'owner_confirmed'} else 0)
        return await super().call(route, data)


class WebsiteTaskGatewayTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.WebsiteLoginTests.setUp
    finish = fixture.WebsiteLoginTests.finish
    row = fixture.WebsiteLoginTests.row
    launch = fixture.WebsiteLoginTests.launch

    async def asyncSetUp(self):
        self.broker = DelegatedBroker(self.bridge.origin)
        self.bridge.ipc.side_effect = self.broker.call
        self.bridge.boundary = lambda: True
        self.clients = []
        self.current_status = task_state()
        self.start_error = self.close_error = None
        self.status_hook = None
        self.polls = []
        self.client_patch = patch('oak.website_task_client.WebsiteTaskClient', side_effect=self.make_client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        for name, value in [('AGENT_POLL_SECONDS', .005), ('AGENT_HUMAN_WAIT_SECONDS', .4)]:
            patched = patch('oak.login.' + name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def make_client(self, transport, scope):
        client = type('FakeTaskClient', (), {})()
        client.scope = deepcopy(scope)
        client.initialize = AsyncMock()
        client.calls = []
        async def call(name, *, goal=None):
            client.calls.append(name)
            if name == 'website_sign_in' and self.start_error:
                raise self.start_error
            if name == 'website_task_status':
                self.polls.append(deepcopy(self.current_status))
                if self.status_hook:
                    await self.status_hook()
            return validate_task_snapshot(self.current_status)
        async def close():
            if self.close_error:
                raise self.close_error
        client.call = AsyncMock(side_effect=call)
        client.close = AsyncMock(side_effect=close)
        self.clients.append(client)
        return client

    async def until(self, predicate):
        for _ in range(200):
            if predicate():
                return
            await asyncio.sleep(.001)
        self.fail('Delegated gateway did not reach the expected state.')

    async def begin(self, *, arguments=None):
        native_id = self.next_id
        self.next_id += 1
        task = asyncio.create_task(self.runtime._handle_server_request({
            'id': native_id, 'method': 'item/tool/call', 'params': {
                'threadId': 'original-thread', 'turnId': 'original-turn',
                'tool': 'oak_website_task', 'arguments': arguments or {'url': 'https://shop.synthetic.invalid/'}}}))
        self.tasks.append(task)
        await self.until(lambda: self.c.db.execute('SELECT id FROM input_requests WHERE native_id=?',
                                                   (json.dumps(native_id),)).fetchone() is not None or task.done())
        row = self.c.db.execute('SELECT id FROM input_requests WHERE native_id=?', (json.dumps(native_id),)).fetchone()
        return task, row['id'] if row else None

    async def active(self, identifier):
        await self.launch(identifier)
        self.broker.states[identifier] = 'active'
        await self.until(lambda: self.bridge.attempt(identifier)['phase'] == 'active')
        await self.until(lambda: bool(self.clients and self.clients[-1].calls))

    async def result(self, task):
        await asyncio.wait_for(asyncio.shield(task), .7)
        response = self.runtime._send.await_args.args[0]['result']
        self.assertTrue(response['success'])
        return json.loads(response['contentItems'][0]['text'])

    async def test_native_dispatch_waits_for_attachment_before_one_mcp_job(self):
        task, identifier = await self.begin()
        self.assertFalse(task.done())
        self.assertFalse(self.clients)
        self.assertEqual(self.row(identifier)['delivery'], 'not_required')
        self.assertIn(identifier, self.c.requests.website_workspaces)
        self.assertFalse(any(route == '/create' for route, _ in self.broker.calls))
        await self.active(identifier)
        self.current_status = task_state('requires_agent', error='unsupported')
        result = await self.result(task)
        self.assertEqual(result['delegation'], 'requires_agent')
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(self.clients[0].calls.count('website_sign_in'), 1)
        self.clients[0].initialize.assert_awaited_once()
        self.clients[0].close.assert_awaited_once()
        self.runtime.request.assert_not_awaited()

    async def test_human_continuation_stays_in_single_parent_tool_call(self):
        self.current_status = task_state('waiting_for_owner')
        task, identifier = await self.begin()
        await self.active(identifier)
        await self.until(lambda: bool(self.polls))
        self.assertFalse(task.done())
        self.runtime._send.assert_not_awaited()
        # Only safe broker progress changes; there is no parent model call or
        # credential value in this test's continuation mechanism.
        self.current_status = task_state('waiting_for_owner', sequence=1)
        await self.until(lambda: any(value['progress']['event_seq'] == 1 for value in self.polls))
        self.assertFalse(task.done())
        self.broker.states[identifier] = 'owner_confirmed'
        result = await self.result(task)
        self.assertEqual(result['state'], 'owner_confirmed')
        self.assertIsNot(result.get('authenticated'), True)
        self.assertEqual(self.clients[0].calls.count('website_sign_in'), 1)
        self.runtime._send.assert_awaited_once()
        self.runtime.request.assert_not_awaited()

    async def test_native_task_objective_is_forwarded_once_through_mcp(self):
        goal = 'Complete the observed profile form.'
        task, identifier = await self.begin(arguments={'url': 'https://shop.synthetic.invalid/', 'goal': goal})
        await self.active(identifier)
        self.current_status = task_state('requires_agent', error='no_candidate')
        result = await self.result(task)
        self.assertEqual(result['delegation'], 'requires_agent')
        calls = self.clients[0].call.await_args_list
        starts = [item for item in calls if item.args == ('website_sign_in',)]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].kwargs, {'goal': goal})
        self.assertNotIn(goal, json.dumps(result))

    async def test_requires_agent_returns_sanitized_observation_in_same_workspace(self):
        self.current_status = task_state('requires_agent', error='no_candidate')
        task, identifier = await self.begin()
        await self.active(identifier)
        result = await self.result(task)
        self.assertEqual(result['request_id'], identifier)
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['delegation_error'], 'no_candidate')
        self.assertEqual(result['delegation_steps'], 1)
        self.assertEqual(result['delegation_calls'], 1)
        self.assertIn('observation', result)
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.assertFalse(any(route == '/cancel' for route, _ in self.broker.calls))
        continued = await self.bridge.agent({**self.native, 'requestId': 100},
                                            {'action': 'observe', 'request_id': identifier})
        self.assertEqual(continued['state'], 'active')
        self.assertEqual(sum(route == '/create' for route, _ in self.broker.calls), 1)

    async def test_mcp_cancelled_means_delegation_stopped_not_authenticated(self):
        self.current_status = task_state('cancelled')
        task, identifier = await self.begin()
        await self.active(identifier)
        result = await self.result(task)
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['delegation'], 'cancelled')
        self.assertIsNot(result.get('authenticated'), True)
        self.assertEqual(self.row(identifier)['outcome'], 'pending')

    async def test_only_broker_can_return_authenticated_or_cancelled_workspace(self):
        for terminal in ('authenticated', 'cancelled'):
            task, identifier = await self.begin()
            await self.active(identifier)
            self.broker.states[identifier] = terminal
            result = await self.result(task)
            self.assertEqual(result['state'], terminal)
            if terminal == 'cancelled':
                self.assertIsNot(result.get('authenticated'), True)
            # Finish the row to allow a separate explicit native task in this
            # fixture; production's background monitor owns this update.
            with self.c.db:
                self.c.db.execute('UPDATE input_requests SET outcome=? WHERE id=?', (terminal, identifier))

    async def test_uncertain_start_is_not_retried_or_replaced_by_new_workspace(self):
        self.start_error = RuntimeError('private-start-canary')
        task, identifier = await self.begin()
        await self.active(identifier)
        await asyncio.wait_for(task, .5)
        response = self.runtime._send.await_args.args[0]['result']
        self.assertFalse(response['success'])
        self.assertNotIn('private-start-canary', json.dumps(response))
        self.assertEqual(self.clients[0].calls, ['website_sign_in'])
        self.clients[0].close.assert_awaited_once()
        for metadata in (self.native, {**self.native, 'requestId': 999}):
            with self.assertRaises(RequestUnavailable):
                await self.bridge.delegated(metadata, 'https://shop.synthetic.invalid/')
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(sum(route == '/create' for route, _ in self.broker.calls), 1)

    async def test_gateway_injects_exact_scope_and_no_secrets_cross_parent_channel(self):
        original_status = self.broker.status
        self.broker.status = lambda *args, **kwargs: {
            **original_status(*args, **kwargs), 'fields': {'password': 'password-canary'},
            'mcp_session': 'mcp-session-canary'}
        self.current_status = task_state('requires_agent', error='unsupported')
        task, identifier = await self.begin()
        await self.active(identifier)
        result = await self.result(task)
        expected = {'request_id': identifier, 'session_id': 'a' * 32,
                    'runtime_epoch': self.runtime.runtime_epoch}
        self.assertEqual(self.clients[0].scope, expected)
        serialized = json.dumps(result) + '\n'.join(self.c.db.iterdump()) + json.dumps(self.bus.replay(11))
        for forbidden in ('T' * 43, self.panel, 'password-canary', 'mcp-session-canary'):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn('session_id', result)
        self.assertNotIn('runtime_epoch', result)
        self.assertTrue(self.broker.bindings[identifier]['agentic'])

    async def test_forged_mcp_authentication_status_cannot_authenticate_workspace(self):
        self.current_status = {**task_state(), 'state': 'authenticated'}
        task, identifier = await self.begin()
        await self.active(identifier)
        await asyncio.wait_for(task, .5)
        response = self.runtime._send.await_args.args[0]['result']
        self.assertFalse(response['success'])
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.assertEqual(self.bridge.attempt(identifier)['phase'], 'active')
        self.clients[0].close.assert_awaited_once()

    async def test_model_cannot_inject_scope_or_values_in_delegate_arguments(self):
        task, identifier = await self.begin(arguments={
            'url': 'https://shop.synthetic.invalid/', 'request_id': 'forged', 'values': {'password': 'private-canary'}})
        await asyncio.wait_for(task, .5)
        self.assertIsNone(identifier)
        response = self.runtime._send.await_args.args[0]['result']
        self.assertFalse(response['success'])
        self.assertNotIn('private-canary', json.dumps(response))
        self.assertFalse(self.clients)
        self.assertFalse(any(route == '/create' for route, _ in self.broker.calls))

    async def test_unconfigured_delegation_does_not_create_workspace(self):
        self.broker.task_enabled = False
        task, identifier = await self.begin()
        await asyncio.wait_for(task, .5)
        self.assertIsNone(identifier)
        self.assertFalse(self.clients)
        self.assertFalse(self.runtime._send.await_args.args[0]['result']['success'])

    async def test_owner_cancel_closes_single_mcp_session(self):
        task, identifier = await self.begin()
        await self.active(identifier)
        await self.c.requests.decide(11, 11, identifier,
                                     {'requestId': 'cancel-task', 'revision': 1, 'decision': 'cancel'})
        result = await self.result(task)
        self.assertEqual(result['state'], 'cancelled')
        self.assertFalse(result['authenticated'])
        self.clients[0].close.assert_awaited_once()
        await self.until(lambda: any(route == '/cancel' for route, _ in self.broker.calls))

    async def test_uncertain_mcp_cleanup_revokes_exact_private_lease(self):
        self.current_status = task_state('requires_agent', error='unsupported')
        self.close_error = RuntimeError('private-close-canary')
        task, identifier = await self.begin()
        await self.active(identifier)
        await self.result(task)
        await self.until(lambda: any(route == '/cancel' for route, _ in self.broker.calls))
        cancelled = [data for route, data in self.broker.calls if route == '/cancel']
        self.assertTrue(all(data == self.clients[0].scope for data in cancelled))

    async def test_native_cancellation_stops_mcp_without_restarting_job(self):
        task, identifier = await self.begin()
        await self.active(identifier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.clients[0].close.assert_awaited_once()
        self.assertEqual(self.clients[0].calls.count('website_sign_in'), 1)

    async def test_no_attach_returns_waiting_without_constructing_mcp_client(self):
        with patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', .02):
            task, identifier = await self.begin()
            result = await self.result(task)
        self.assertEqual(result, {'request_id': identifier, 'state': 'waiting_for_owner'})
        self.assertFalse(self.clients)
        self.assertFalse(any(route == '/create' for route, _ in self.broker.calls))
