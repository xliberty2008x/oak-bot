"""Native staged website continuation with mocked broker; no network/model calls."""
import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from oak.requests import InputRequests, RequestUnavailable
from oak.website_scene import model_context
from test_website_scene import scene, plan
import test_website_login as login_fixture


class AgentBroker(login_fixture.StatusBroker):
    def __init__(self, origin):
        super().__init__(origin)
        self.sequence = 0
        self.stage = 'agent_working'
        self.complete_on_observe = False
        self.override = None
        self.fail_dispatch = False

    def observation(self):
        value = scene()
        value['after_input'] = self.sequence > 0
        return {'revision': value['revision'], 'stage': self.stage, 'scene': model_context(value),
                'progress': {'event_seq': self.sequence, 'outcome': 'submitted' if self.sequence else 'idle'}}

    async def call(self, route, data):
        if route != '/agent':
            return await super().call(route, data)
        self.calls.append((route, deepcopy(data)))
        action = data['operation']['action']
        if self.fail_dispatch and action in {'act', 'present'}:
            raise RequestUnavailable('Synthetic uncertain transport.')
        if action == 'present':
            self.stage = 'form'
        elif action == 'observe' and self.complete_on_observe and self.stage == 'form':
            self.sequence += 1
            self.stage = 'agent_working'
            self.complete_on_observe = False
        if self.override is not None:
            return deepcopy(self.override)
        observation = self.observation()
        return {'state': 'active', 'progress': observation['progress'], 'observation': observation}


class WebsiteAgentGatewayTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the existing full gateway/runtime fixture without inheriting its tests.
    setUp = login_fixture.WebsiteLoginTests.setUp
    finish = login_fixture.WebsiteLoginTests.finish
    until = login_fixture.WebsiteLoginTests.until
    row = login_fixture.WebsiteLoginTests.row
    launch = login_fixture.WebsiteLoginTests.launch

    async def asyncSetUp(self):
        self.broker = AgentBroker(self.bridge.origin)
        self.bridge.ipc.side_effect = self.broker.call
        self.wait_patch = patch('oak.login.AGENT_WAIT_SECONDS', .06)
        self.poll_patch = patch('oak.login.AGENT_POLL_SECONDS', .005)
        self.human_patch = patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', .06)
        self.wait_patch.start(); self.poll_patch.start(); self.human_patch.start()
        self.addCleanup(self.wait_patch.stop); self.addCleanup(self.poll_patch.stop)
        self.addCleanup(self.human_patch.stop)

    def metadata(self):
        native = {**self.native, 'requestId': self.next_id}
        self.next_id += 1
        return native

    async def begin(self):
        result = await self.bridge.agent(self.metadata(), {'action': 'begin', 'url': 'https://shop.synthetic.invalid/'})
        self.assertEqual(result['state'], 'waiting_for_owner')
        return result['request_id']

    async def active(self, identifier):
        await self.launch(identifier)
        await self.broker.queues[identifier].put(self.broker.status(identifier, state='active'))
        await self.until(lambda: self.bridge.attempt(identifier)['phase'] == 'active')

    async def test_begin_returns_same_native_rpc_before_owner_attach(self):
        await asyncio.wait_for(self.runtime._handle_server_request({
            'id': 42, 'method': 'item/tool/call', 'params': {
                'threadId': 'original-thread', 'turnId': 'original-turn',
                'tool': 'oak_website_agent', 'arguments': {'action': 'begin', 'url': 'https://shop.synthetic.invalid/'}}}), 1)
        sent = self.runtime._send.await_args.args[0]
        self.assertEqual(sent['id'], 42)
        self.assertTrue(sent['result']['success'])
        result = json.loads(sent['result']['contentItems'][0]['text'])
        identifier = result['request_id']
        self.assertNotIn(identifier, self.c.requests.waiters)
        self.assertIn(identifier, self.c.requests.website_workspaces)
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.assertEqual(self.row(identifier)['delivery'], 'not_required')
        self.assertTrue(self.c.requests._live(self.row(identifier)))
        self.assertTrue(json.loads(self.row(identifier)['form'])['agentic'])
        self.assertFalse(any(route == '/create' for route, _ in self.broker.calls))
        self.runtime.request.assert_not_awaited()

    async def test_attach_marks_only_owned_workspace_agentic_and_observe_is_sanitized(self):
        identifier = await self.begin(); await self.active(identifier)
        self.assertIs(self.broker.bindings[identifier]['agentic'], True)
        result = await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertEqual(result['observation']['stage'], 'agent_working')
        self.assertNotIn('destination', result['observation']['scene'])
        self.assertNotIn('"values":', json.dumps(result))
        self.assertNotIn('T' * 43, json.dumps(result))
        self.assertEqual(self.row(identifier)['outcome'], 'pending')

    async def test_present_resumes_after_one_human_step_without_ending_workspace(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.complete_on_observe = True
        native = self.metadata()
        result = await self.bridge.agent(native, {'action': 'present', 'request_id': identifier,
            'revision': 'private_revision:1', 'plan_json': json.dumps(plan())})
        self.assertEqual(result['progress'], {'event_seq': 1, 'outcome': 'submitted'})
        self.assertEqual(result['observation']['stage'], 'agent_working')
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.assertTrue(self.c.requests._live(self.row(identifier)))
        self.assertFalse(any(route == '/cancel' for route, _ in self.broker.calls))
        self.assertEqual(sum(route == '/agent' and data['operation']['action'] == 'present' for route, data in self.broker.calls), 1)
        self.assertEqual(self.c.active[11], 'original-turn')
        self.runtime.request.assert_not_awaited()

    async def test_present_waits_past_attachment_poll_deadline_for_human_step(self):
        identifier = await self.begin(); await self.active(identifier)
        with patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', .5), patch('oak.login.AGENT_WAIT_SECONDS', .01):
            task = asyncio.create_task(self.bridge.agent(self.metadata(), {'action': 'present', 'request_id': identifier,
                'revision': 'private_revision:1', 'plan_json': json.dumps(plan())}))
            self.tasks.append(task)
            await asyncio.sleep(.05)
            self.assertFalse(task.done())
            self.broker.complete_on_observe = True
            result = await asyncio.wait_for(task, .5)
        self.assertEqual(result['progress']['outcome'], 'submitted')

    async def test_human_wait_deadline_respects_absolute_workspace_expiry(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.stage = 'form'
        import time
        with self.c.db:
            self.c.db.execute('UPDATE input_requests SET expires=? WHERE id=?', (time.time() + .03, identifier))
        with patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', 300):
            result = await asyncio.wait_for(self.bridge.agent(self.metadata(),
                {'action': 'wait', 'request_id': identifier, 'event_seq': 0}), .5)
        self.assertEqual(result['state'], 'expired')
        self.assertEqual(self.row(identifier)['outcome'], 'expired')

    async def test_present_native_rpc_returns_sanitized_step_to_original_turn(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.complete_on_observe = True
        await self.runtime._handle_server_request({'id': 43, 'method': 'item/tool/call', 'params': {
            'threadId': 'original-thread', 'turnId': 'original-turn', 'tool': 'oak_website_agent',
            'arguments': {'action': 'present', 'request_id': identifier, 'revision': 'private_revision:1',
                          'plan_json': json.dumps(plan())}}})
        result = self.runtime._send.await_args.args[0]
        self.assertEqual(result['id'], 43); self.assertTrue(result['result']['success'])
        step = json.loads(result['result']['contentItems'][0]['text'])
        self.assertEqual(step['progress']['outcome'], 'submitted')
        self.assertNotIn('authenticated', step)
        self.assertEqual(self.row(identifier)['outcome'], 'pending')

    async def test_wait_and_pre_attach_observe_are_bounded_without_dispatch(self):
        identifier = await self.begin()
        for args in ({'action': 'observe', 'request_id': identifier},
                     {'action': 'wait', 'request_id': identifier, 'event_seq': 0}):
            result = await asyncio.wait_for(self.bridge.agent(self.metadata(), args), .4)
            self.assertEqual(result['state'], 'waiting_for_owner')
        self.assertFalse(any(route == '/agent' for route, _ in self.broker.calls))
        await self.active(identifier)
        self.broker.stage = 'form'
        result = await asyncio.wait_for(self.bridge.agent(self.metadata(),
            {'action': 'wait', 'request_id': identifier, 'event_seq': 0}), .4)
        self.assertEqual(result['state'], 'waiting_for_owner')
        self.assertEqual(result['progress']['event_seq'], 0)

    async def test_wait_resumes_when_unpresented_page_becomes_actionable_without_human_event(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.stage = 'unsupported_layout'
        with patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', .5):
            task = asyncio.create_task(self.bridge.agent(self.metadata(),
                {'action': 'wait', 'request_id': identifier, 'event_seq': 0}))
            self.tasks.append(task)
            await asyncio.sleep(.04)
            self.assertFalse(task.done())
            self.broker.stage = 'agent_working'
            result = await asyncio.wait_for(task, .2)
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['progress'], {'event_seq': 0, 'outcome': 'idle'})
        self.assertEqual(result['observation']['stage'], 'agent_working')
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.assertFalse(any(route == '/agent' and data['operation']['action'] != 'observe'
                             for route, data in self.broker.calls))

    async def test_wait_before_attachment_returns_first_actionable_page(self):
        identifier = await self.begin()
        with patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', .5):
            task = asyncio.create_task(self.bridge.agent(self.metadata(),
                {'action': 'wait', 'request_id': identifier, 'event_seq': 0}))
            self.tasks.append(task)
            await asyncio.sleep(.04)
            self.assertFalse(task.done())
            await self.active(identifier)
            result = await asyncio.wait_for(task, .2)
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['progress']['event_seq'], 0)
        self.assertEqual(result['observation']['stage'], 'agent_working')

    async def test_wait_returns_actionable_review_without_fabricating_progress(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.stage = 'review'
        self.broker.sequence = 1
        result = await asyncio.wait_for(self.bridge.agent(self.metadata(),
            {'action': 'wait', 'request_id': identifier, 'event_seq': 1}), .4)
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['progress']['event_seq'], 1)
        self.assertEqual(result['observation']['stage'], 'review')
        self.assertEqual(self.row(identifier)['outcome'], 'pending')

    async def test_empty_postinput_wait_returns_unresolved_at_readiness_bound(self):
        identifier = await self.begin(); await self.active(identifier)
        value = self.broker.observation()
        value.update(stage='agent_working', progress={'event_seq': 1, 'outcome': 'submitted'})
        value['scene'].update(stage='unsupported_layout', controls=[], actions=[], after_input=True)
        self.broker.override = {'state': 'active', 'progress': value['progress'], 'observation': value}
        with patch('oak.login.AGENT_READINESS_SECONDS', .03), patch('oak.login.AGENT_HUMAN_WAIT_SECONDS', .5):
            result = await asyncio.wait_for(self.bridge.agent(self.metadata(),
                {'action': 'wait', 'request_id': identifier, 'event_seq': 1}), .2)
        self.assertEqual(result['state'], 'active')
        self.assertEqual(result['observation'], value)
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.assertFalse(any(route == '/agent' and data['operation']['action'] != 'observe'
                             for route, data in self.broker.calls))

    async def test_displayed_human_form_retains_owner_wait_beyond_readiness_bound(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.stage = 'form'
        with patch('oak.login.AGENT_READINESS_SECONDS', .01):
            start = asyncio.get_running_loop().time()
            result = await self.bridge.agent(self.metadata(),
                {'action': 'wait', 'request_id': identifier, 'event_seq': 0})
        self.assertGreaterEqual(asyncio.get_running_loop().time() - start, .05)
        self.assertEqual(result['state'], 'waiting_for_owner')
        self.assertEqual(result['observation']['stage'], 'form')

    async def test_surface_plan_transport_accepts_32k_and_rejects_larger_before_dispatch(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.override = {'state': 'active', 'progress': {'event_seq': 0, 'outcome': 'idle'},
                                'error': {'code': 'invalid_plan'}}
        encoded = json.dumps({'surface_ref': 's1', 'padding': ' ' * 9000})
        result = await self.bridge.agent(self.metadata(), {'action': 'present', 'request_id': identifier,
            'revision': 'private_revision:1', 'plan_json': encoded})
        self.assertEqual(result['error']['code'], 'invalid_plan')
        before = len(self.broker.calls)
        with self.assertRaises(RequestUnavailable):
            await self.bridge.agent(self.metadata(), {'action': 'present', 'request_id': identifier,
                'revision': 'private_revision:1', 'plan_json': json.dumps({'padding': ' ' * 32768})})
        self.assertEqual(len(self.broker.calls), before)

    async def test_same_native_action_never_dispatches_twice(self):
        identifier = await self.begin(); await self.active(identifier)
        native = self.metadata()
        args = {'action': 'act', 'request_id': identifier, 'revision': 'private_revision:1', 'action_ref': 'a2'}
        await self.bridge.agent(native, args)
        with self.assertRaises(RequestUnavailable): await self.bridge.agent(native, args)
        self.assertEqual(sum(route == '/agent' and data['operation']['action'] == 'act' for route, data in self.broker.calls), 1)
        receipt = self.c.db.execute('SELECT * FROM website_agent_actions').fetchone()
        self.assertEqual(receipt['phase'], 'completed')
        self.assertEqual(set(receipt.keys()), {'runtime_epoch', 'native_id', 'request_id', 'action', 'fingerprint', 'phase'})

    async def test_uncertain_and_cancelled_dispatch_are_not_replayed(self):
        identifier = await self.begin(); await self.active(identifier)
        native = self.metadata()
        args = {'action': 'act', 'request_id': identifier, 'revision': 'private_revision:1', 'action_ref': 'a2'}
        self.broker.fail_dispatch = True
        with self.assertRaises(RequestUnavailable): await self.bridge.agent(native, args)
        self.broker.fail_dispatch = False
        with self.assertRaises(RequestUnavailable): await self.bridge.agent(native, args)
        self.assertEqual(self.c.db.execute('SELECT phase FROM website_agent_actions').fetchone()[0], 'uncertain')
        self.assertEqual(sum(route == '/agent' and data['operation']['action'] == 'act' for route, data in self.broker.calls), 1)
        # A read can establish what happened; it cannot replay the action.
        observed = await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertEqual(observed['state'], 'active')

    async def test_closed_uncertain_action_preserves_reason_and_uncertain_receipt(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.override = {'state': 'failed', 'error': {'code': 'action_uncertain'}}
        native = self.metadata()
        args = {'action': 'act', 'request_id': identifier, 'revision': 'private_revision:1', 'action_ref': 'a2'}
        result = await self.bridge.agent(native, args)
        self.assertEqual(result, {'request_id': identifier, 'state': 'failed', 'error': {'code': 'action_uncertain'}})
        self.assertEqual(self.c.db.execute('SELECT phase FROM website_agent_actions').fetchone()[0], 'uncertain')
        with self.assertRaises(RequestUnavailable): await self.bridge.agent(native, args)
        self.assertEqual(sum(route == '/agent' and data['operation']['action'] == 'act' for route, data in self.broker.calls), 1)
        for error in ({'state': 'failed', 'error': {'code': 'invalid_plan'}},
                      {'state': 'authenticated', 'error': {'code': 'action_uncertain'}},
                      {'state': 'failed', 'error': {'code': 'action_uncertain', 'text': 'private-canary'}},
                      {'state': 'failed', 'progress': {'event_seq': 0, 'outcome': 'uncertain'}, 'error': {'code': 'action_uncertain'}}):
            with self.assertRaises(RequestUnavailable): self.bridge.agent_response(error)

    async def test_cancellation_during_dispatch_keeps_uncertain_receipt(self):
        identifier = await self.begin(); await self.active(identifier)
        dispatched = asyncio.Event()
        original = self.bridge.ipc.side_effect
        async def pending(route, data):
            if route == '/agent' and data['operation']['action'] == 'act':
                dispatched.set()
                await asyncio.Future()
            return await original(route, data)
        self.bridge.ipc.side_effect = pending
        native = self.metadata()
        args = {'action': 'act', 'request_id': identifier, 'revision': 'private_revision:1', 'action_ref': 'a2'}
        task = asyncio.create_task(self.bridge.agent(native, args))
        self.tasks.append(task)
        await dispatched.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.c.db.execute('SELECT phase FROM website_agent_actions').fetchone()[0], 'uncertain')
        self.bridge.ipc.side_effect = original
        with self.assertRaises(RequestUnavailable): await self.bridge.agent(native, args)

    async def test_late_reply_after_turn_changes_is_not_returned_to_model(self):
        identifier = await self.begin(); await self.active(identifier)
        original = self.bridge.ipc.side_effect
        async def change_turn(route, data):
            result = await original(route, data)
            if route == '/agent': self.c.active[11] = 'next-turn'
            return result
        self.bridge.ipc.side_effect = change_turn
        with self.assertRaises(RequestUnavailable):
            await self.bridge.agent(self.metadata(), {'action': 'act', 'request_id': identifier,
                'revision': 'private_revision:1', 'action_ref': 'a2'})
        self.assertEqual(self.c.db.execute('SELECT phase FROM website_agent_actions').fetchone()[0], 'uncertain')

    async def test_wrong_task_scope_and_legacy_request_cannot_access_workspace(self):
        identifier = await self.begin(); await self.active(identifier)
        for change in ({'threadId': 'other-thread'}, {'turnId': 'other-turn'}, {'runtimeEpoch': 'other-epoch'}):
            with self.subTest(change=change), self.assertRaises(RequestUnavailable):
                await self.bridge.agent({**self.metadata(), **change}, {'action': 'observe', 'request_id': identifier})
        self.c.requests.website_workspaces.remove(identifier)
        with self.assertRaises(RequestUnavailable):
            await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertFalse(any(route == '/agent' for route, _ in self.broker.calls))

    async def test_restart_expires_workspace_and_does_not_reconstruct_authority(self):
        identifier = await self.begin()
        self.c.requests = InputRequests(self.c)
        self.c.requests.website_login = self.bridge
        self.assertEqual(self.row(identifier)['outcome'], 'expired')
        self.assertFalse(self.c.requests.website_workspaces)
        with self.assertRaises(RequestUnavailable):
            await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})

    async def test_owner_cancel_without_waiter_closes_workspace(self):
        identifier = await self.begin(); await self.active(identifier)
        result = await self.c.requests.decide(11, 11, identifier,
            {'requestId': 'cancel-workspace', 'revision': 1, 'decision': 'cancel'})
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(result['delivery'], 'not_required')
        observed = await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertEqual(observed['state'], 'cancelled')
        self.assertFalse(observed['authenticated'])
        await self.until(lambda: any(route == '/cancel' for route, _ in self.broker.calls))

    async def test_terminal_workspace_has_no_undeliverable_whole_login_response(self):
        identifier = await self.begin(); await self.active(identifier)
        import time
        await self.broker.queues[identifier].put(self.broker.status(identifier, state='owner_confirmed',
            cleanup='retained', session_expires=time.time() + 300))
        async def completed():
            while self.row(identifier)['outcome'] == 'pending': await asyncio.sleep(.01)
        await asyncio.wait_for(completed(), 2)
        snapshot = self.c.requests.snapshot(11, 11, identifier)
        self.assertEqual(snapshot['outcome'], 'owner_confirmed')
        self.assertEqual(snapshot['delivery'], 'not_required')
        # Existing parent watch stops for terminal state when delivery != ready.
        self.assertNotEqual(snapshot['delivery'], 'ready')
        result = await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertEqual(result['confirmation'], 'owner')
        self.assertFalse(result['authenticated'])

    async def test_turn_end_revokes_workspace_and_prevents_late_actions(self):
        identifier = await self.begin(); await self.active(identifier)
        self.c.requests.invalidate_turn('original-thread', 'original-turn')
        self.assertEqual(self.row(identifier)['outcome'], 'cancelled')
        await self.until(lambda: any(route == '/cancel' for route, _ in self.broker.calls))
        self.c.active[11] = 'next-turn'
        with self.assertRaises(RequestUnavailable):
            await self.bridge.agent(self.metadata(), {'action': 'act', 'request_id': identifier,
                                                     'revision': 'private_revision:1', 'action_ref': 'a2'})

    async def test_unexpected_broker_values_never_enter_native_result(self):
        identifier = await self.begin(); await self.active(identifier)
        safe = {'state': 'active', 'progress': self.broker.observation()['progress'], 'observation': self.broker.observation()}
        for malformed in ({**safe, 'values': {'password': 'private-canary'}},
                          {**safe, 'observation': {**safe['observation'], 'values': 'private-canary'}},
                          {**safe, 'progress': {'event_seq': True, 'outcome': 'idle'}}):
            self.broker.override = malformed
            with self.assertRaises(RequestUnavailable):
                await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertNotIn('private-canary', '\n'.join(self.c.db.iterdump()))

    async def test_read_error_without_scene_returns_only_known_safe_metadata(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.override = {'state': 'active', 'progress': {'event_seq': 0, 'outcome': 'idle'},
                                'error': {'code': 'stale_form'}}
        result = await self.bridge.agent(self.metadata(), {'action': 'observe', 'request_id': identifier})
        self.assertEqual(result['error'], {'code': 'stale_form'})
        self.assertNotIn('observation', result)

    async def test_invalid_proposal_returns_reason_and_allows_corrected_next_native_step(self):
        identifier = await self.begin(); await self.active(identifier)
        self.broker.override = {'state': 'active', 'progress': {'event_seq': 0, 'outcome': 'idle'},
                                'error': {'code': 'invalid_plan'}}
        result = await self.bridge.agent(self.metadata(), {'action': 'present', 'request_id': identifier,
            'revision': 'private_revision:1', 'plan_json': json.dumps(plan())})
        self.assertEqual(result['error']['code'], 'invalid_plan')
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.broker.override = None; self.broker.complete_on_observe = True
        corrected = await self.bridge.agent(self.metadata(), {'action': 'present', 'request_id': identifier,
            'revision': 'private_revision:1', 'plan_json': json.dumps(plan())})
        self.assertEqual(corrected['progress']['outcome'], 'submitted')

    async def test_invalid_arguments_and_event_sequence_do_not_dispatch(self):
        identifier = await self.begin(); await self.active(identifier)
        cases = [
            {'action': 'act', 'request_id': identifier, 'revision': 'x', 'action_ref': 'button'},
            {'action': 'present', 'request_id': identifier, 'revision': 'x', 'plan_json': 'NaN'},
            {'action': 'wait', 'request_id': identifier, 'event_seq': True},
            {'action': 'observe', 'request_id': identifier, 'values': {}},
        ]
        for args in cases:
            with self.subTest(action=args['action']), self.assertRaises(RequestUnavailable):
                await self.bridge.agent(self.metadata(), args)
        self.assertFalse(any(route == '/agent' for route, _ in self.broker.calls))

    async def test_invalid_field_shape_returns_corrective_signature_without_echoing_input(self):
        identifier = await self.begin(); await self.active(identifier)
        canary = 'private-argument-canary-713'
        bad = {'action': 'present', 'request_id': identifier,
               'revision': 'private_revision:1', 'plan': canary, canary: canary}
        with self.assertRaises(RequestUnavailable) as error:
            await self.bridge.agent(self.metadata(), bad)
        self.assertEqual(str(error.exception),
            'Expected exactly these fields for present: action, plan_json, request_id, revision.')
        self.assertNotIn(canary, str(error.exception))
        self.assertNotIn(identifier, str(error.exception))
        self.assertFalse(any(route == '/agent' for route, _ in self.broker.calls))
        self.broker.complete_on_observe = True
        result = await self.bridge.agent(self.metadata(), {'action': 'present', 'request_id': identifier,
            'revision': 'private_revision:1', 'plan_json': json.dumps(plan())})
        self.assertEqual(result['progress']['outcome'], 'submitted')
        self.assertNotIn(canary, '\n'.join(self.c.db.iterdump()))
        with self.assertRaises(RequestUnavailable) as error:
            await self.bridge.agent(self.metadata(), {'action': canary})
        self.assertNotIn(canary, str(error.exception))
