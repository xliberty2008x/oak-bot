"""Real MCP wire and broker task lifecycle; synthetic browser/model callbacks."""

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from oak.website_step_planner import StepPlanner
from oak.website_task import WebsiteTask
from oak.website_task_client import WebsiteTaskClient
import test_website_agent_broker as fixture
from test_website_step_planner import observation, response, scene


class Transport:
    """Use actual aiohttp HTTP with the production client's fixed endpoint path."""
    def __init__(self, client):
        self.client = client
        self.methods = []

    def post(self, url, **kwargs):
        self.methods.append(kwargs['json']['method'])
        return self.client.post(urlsplit(url).path, **kwargs)

    def delete(self, url, **kwargs):
        return self.client.delete(urlsplit(url).path, **kwargs)


class WebsiteTaskIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.WebsiteAgentBrokerTests.asyncSetUp
    attach = fixture.WebsiteAgentBrokerTests.attach
    operation = fixture.WebsiteAgentBrokerTests.operation

    async def setup_task(self):
        await self.attach()
        self.current = observation(scene())
        self.presented = []
        async def act(revision, ref):
            self.assertEqual((revision, ref), ('fixture:1', 'a1'))
            self.current = observation(scene(fields=True, revision='second:1'))
            return deepcopy(self.current)
        async def present(revision, plan):
            self.assertEqual(revision, self.current['revision'])
            self.presented.append(deepcopy(plan))
            self.current['stage'] = 'form'
            return deepcopy(self.current)
        self.browser.agent_observe.side_effect = lambda: deepcopy(self.current)
        self.browser.agent_act.side_effect = act
        self.browser.agent_present.side_effect = present
        self.planner = StepPlanner('SYNTHETIC_PRIVATE_KEY_CANARY')
        self.planner._post = AsyncMock(side_effect=lambda payload: response(payload,
            'present_a1' if payload['state']['controls'] else 'act_a1'))
        self.broker.step_planner_factory = lambda: self.planner
        factory = lambda *args, **kwargs: WebsiteTask(*args, **kwargs, poll_seconds=.005)
        patched = patch('oak.website_task.WebsiteTask', side_effect=factory)
        patched.start(); self.addCleanup(patched.stop)
        self.transport = Transport(self.control)
        self.mcp = WebsiteTaskClient(self.transport, self.scope)
        await self.mcp.initialize()
        self.addAsyncCleanup(self.mcp.close)

    async def until(self, predicate):
        for _ in range(200):
            if predicate():
                return
            await asyncio.sleep(.005)
        self.fail('Delegated task did not reach the expected state.')

    async def test_one_mcp_task_navigates_presents_and_resumes_private_step(self):
        await self.setup_task()
        await self.mcp.call('website_sign_in')
        await self.until(lambda: len(self.presented) == 1)
        self.assertEqual(self.browser.agent_act.await_count, 1)
        self.assertEqual(self.planner.calls, 3)
        first = await self.mcp.call('website_task_status')
        self.assertEqual(first['state'], 'waiting_for_owner')
        await self.mcp.call('website_sign_in')
        await asyncio.sleep(.03)
        self.assertEqual(self.planner.calls, 3)
        self.assertEqual(len(self.presented), 1)
        # Simulate the already-private browser reporting a new human step. No
        # submitted value is supplied to the executor or any MCP message.
        self.current = observation(scene(fields=True, after=True, revision='third:1'))
        self.current['progress'] = {'event_seq': 1, 'outcome': 'submitted'}
        self.lease.delegated_task.wake()
        await self.until(lambda: len(self.presented) == 2)
        self.assertEqual(self.planner.calls, 5)
        self.assertEqual(self.browser.agent_act.await_count, 1)
        status = await self.mcp.call('website_task_status')
        self.assertEqual(status['progress']['event_seq'], 1)
        self.assertNotIn('CANARY', json.dumps(status))
        self.assertEqual(self.transport.methods[:4],
            ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])
        await self.mcp.close()
        self.assertEqual(self.lease.delegated_task.snapshot()['state'], 'cancelled')
        self.assertEqual(self.lease.state, 'active')
        self.browser.close.assert_not_awaited()

    async def test_model_wait_does_not_hold_lease_lock_and_cancel_stops_pending_inference(self):
        await self.setup_task()
        pending = asyncio.Event()
        async def slow(payload):
            pending.set()
            await asyncio.Event().wait()
        self.planner._post.side_effect = slow
        await self.mcp.call('website_sign_in')
        await asyncio.wait_for(pending.wait(), 1)
        result = await asyncio.wait_for(self.control.post('/status', json=self.scope), .3)
        self.assertEqual(result.status, 200)
        rejected = await self.operation({'action': 'act', 'revision': 'fixture:1', 'action_ref': 'a1'})
        self.assertEqual((await rejected.json())['error']['code'], 'human_step_pending')
        await asyncio.wait_for(self.mcp.close(), .3)
        self.browser.agent_act.assert_not_awaited()
        self.assertEqual(self.lease.delegated_task.snapshot()['state'], 'cancelled')

    async def test_mcp_objective_is_bound_once_to_the_existing_lease_and_budget(self):
        await self.setup_task()
        goals = []
        goal = 'Complete the current profile form.'
        def factory(*, goal):
            goals.append(goal)
            return self.planner
        self.broker.step_planner_factory = factory
        await self.mcp.call('website_sign_in', goal=goal)
        await self.until(lambda: len(self.presented) == 1)
        calls = self.planner.calls
        task = self.lease.delegated_task
        await self.mcp.call('website_sign_in', goal=goal)
        self.assertEqual(goals, [goal])
        self.assertIs(self.lease.delegated_task, task)
        self.assertEqual(self.planner.calls, calls)
        self.assertEqual(self.lease.delegated_goal, goal)
        with self.assertRaises(ValueError):
            await self.mcp.call('website_sign_in', goal='Submit the registration form.')
        self.assertEqual(self.lease.delegated_goal, goal)
        self.assertEqual(self.planner.calls, calls)
        status = await self.mcp.call('website_task_status')
        self.assertNotIn(goal, json.dumps(status))

    async def test_failed_task_escalates_same_workspace_without_budget_reset(self):
        await self.setup_task()
        self.planner._post.side_effect = RuntimeError('PRIVATE_ERROR_CANARY')
        await self.mcp.call('website_sign_in')
        await self.until(lambda: self.lease.delegated_task.snapshot()['state'] == 'requires_agent')
        result = await self.mcp.call('website_task_status')
        self.assertEqual(result['error'], 'unavailable')
        await self.mcp.call('website_sign_in')
        self.assertEqual(self.planner.calls, 1)
        self.assertEqual(self.lease.state, 'active')
        self.assertNotIn('CANARY', json.dumps(result))
        # Parent reasoning can continue after executor escalation.
        reply = await self.operation({'action': 'act', 'revision': 'fixture:1', 'action_ref': 'a1'})
        self.assertEqual(reply.status, 200)
        self.browser.agent_act.assert_awaited_once()

    async def test_private_origin_cannot_access_mcp_and_lease_cancel_stops_job(self):
        await self.setup_task()
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        private = await self.human.post('/mcp', headers={'Origin': self.broker.origin}, json=request)
        self.assertEqual(private.status, 404)
        await self.mcp.call('website_sign_in')
        await self.until(lambda: bool(self.presented))
        cancelled = await self.control.post('/cancel', json=self.scope)
        self.assertEqual((await cancelled.json())['cleanup'], 'confirmed')
        self.assertEqual(self.lease.delegated_task.snapshot()['state'], 'cancelled')
        self.browser.close.assert_awaited_once()
        with self.assertRaises(ValueError):
            await self.mcp.call('website_task_status')

    async def test_client_accumulates_fragmented_http_json_and_bounds_total_bytes(self):
        large = False
        async def handler(request):
            value = await request.json()
            body = json.dumps({'jsonrpc': '2.0', 'id': value['id'],
                               'result': {'fixture': 'x' * (33000 if large else 20)}}).encode()
            reply = web.StreamResponse(headers={'Content-Type': 'application/json'})
            await reply.prepare(request)
            for start in range(0, len(body), 97):
                await reply.write(body[start:start + 97])
                await asyncio.sleep(0)
            await reply.write_eof()
            return reply
        app = web.Application()
        app.router.add_post('/mcp', handler)
        http = TestClient(TestServer(app))
        await http.start_server(); self.addAsyncCleanup(http.close)
        client = WebsiteTaskClient(Transport(http), {})
        self.assertEqual(await client.rpc('ping'), {'fixture': 'x' * 20})
        large = True
        with self.assertRaisesRegex(ValueError, 'bound'):
            await client.rpc('ping')


if __name__ == '__main__':
    unittest.main()
