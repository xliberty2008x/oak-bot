"""Trusted agent control and the persistent private renderer step boundary."""
import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from oak.login_broker import LoginBroker
from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_scene import model_context
import test_login_ui as ui


def observation(sequence=0):
    scene = {'provider': 'website', 'destination': 'https://example.test',
        'revision': 'fixture:1', 'stage': 'form', 'controls': [], 'actions': [],
        'texts': [], 'after_input': False, 'overflow': {'controls': 0, 'actions': 0, 'texts': 0}}
    return {'revision': 'fixture:1', 'stage': 'agent_working', 'scene': model_context(scene),
            'progress': {'event_seq': sequence, 'outcome': 'submitted' if sequence else 'idle'}}


class WebsiteAgentBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer = self.boundary = True
        self.broker = LoginBroker('https://private.example.test', 'https://panel.example.test', 1001,
            peer_check=lambda request: self.peer, boundary_check=lambda: self.boundary,
            composer_factory=lambda: self.fail('Agent path must not invoke Jev'))
        self.control = TestClient(TestServer(self.broker.control))
        self.human = TestClient(TestServer(self.broker.human))
        await self.control.start_server()
        await self.human.start_server()
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.broker.close)
        self.binding = {'request_id': 'a' * 32, 'owner': 1002, 'chat_id': 1003,
            'thread_id': 'thread', 'turn_id': 'turn', 'runtime_epoch': 'epoch',
            'panel_hash': 'b' * 64, 'expires': time.time() + 240, 'provider': 'website',
            'url': 'https://example.test', 'agentic': True}

    async def attach(self):
        created = await self.control.post('/create', json=self.binding)
        self.assertEqual(created.status, 200)
        value = await created.json()
        self.scope = {key: value[key] for key in ('request_id', 'session_id', 'runtime_epoch')}
        self.browser = AsyncMock()
        self.browser.agent_observe.return_value = observation()
        self.browser.agent_act.return_value = observation()
        self.browser.agent_present.return_value = observation()
        self.browser.verified.return_value = False
        self.browser.owner_finishable.return_value = False
        self.browser.diagnostics = lambda: {'agent_mode': True}
        self.browser.agent_progress = lambda: observation()['progress']
        self.broker.auth_hosts = {'https://example.test': ['auth.example.test']}
        with patch('oak.website_agent.WebsiteAgentForms', return_value=self.browser) as constructor:
            response = await self.human.post('/api/attach', headers={'Origin': self.broker.origin},
                json={'request_id': self.binding['request_id'], 'ticket': value['ticket']})
            self.assertEqual(response.status, 200)
            attached = await response.json()
            self.assertEqual(attached['form_schema'], 'website-v2')
            self.headers = {'Origin': self.broker.origin, 'Authorization': 'Bearer ' + attached['lease']}
            options = {'auth_hosts': ['auth.example.test']}
            options['origin_approval'] = True
            options['navigation_seconds'] = constructor.call_args.kwargs['navigation_seconds']
            self.assertGreater(options['navigation_seconds'], 0)
            self.assertLessEqual(options['navigation_seconds'], 240)
            options['navigation_guard'] = constructor.call_args.kwargs['navigation_guard']
            self.assertTrue(callable(options['navigation_guard']))
            if self.broker.anonymous_static_resources:
                options['anonymous_static_resources'] = True
            constructor.assert_called_once_with('https://example.test', **options)
        self.lease = self.broker.leases[self.binding['request_id']]

    async def operation(self, operation, **scope):
        return await self.control.post('/agent', json={**self.scope, **scope, 'operation': operation})

    async def complete(self, **changes):
        return await self.human.post('/api/complete', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'revision': 'private:1', **changes})

    async def approve_origin(self, **changes):
        return await self.human.post('/api/approve-origin', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'revision': 'private:1', **changes})

    async def test_origin_approval_preserves_bound_workspace_and_does_not_authenticate(self):
        await self.attach()
        binding = dict(self.lease.binding)
        gateway_deadline = self.lease.gateway_deadline
        response = await self.approve_origin()
        self.assertEqual((response.status, await response.json()), (200, {'approved': True}))
        self.browser.approve_origin.assert_awaited_once_with('private:1')
        self.assertEqual(self.lease.binding, binding)
        self.assertEqual(self.lease.gateway_deadline, gateway_deadline)
        self.assertEqual((self.lease.state, self.lease.session_deadline), ('active', 0))
        self.assertIsNone(self.lease.connected)
        self.browser.verified.assert_not_awaited()
        self.browser.submit.assert_not_awaited()
        self.browser.agent_act.assert_not_awaited()
        self.browser.close.assert_not_awaited()

    async def test_origin_approval_stale_and_duplicate_keep_workspace_without_replay(self):
        await self.attach()
        pending = True
        continuations = 0
        async def approve(revision):
            nonlocal pending, continuations
            if revision != 'private:1' or not pending:
                raise RecoverableFormError('stale_form')
            pending = False
            await asyncio.sleep(0)
            continuations += 1
        self.browser.approve_origin.side_effect = approve
        stale = await self.approve_origin(revision='old:1')
        self.assertEqual((stale.status, (await stale.json())['code']), (409, 'stale_form'))
        responses = await asyncio.gather(self.approve_origin(), self.approve_origin())
        self.assertEqual(sorted(response.status for response in responses), [200, 409])
        self.assertEqual(continuations, 1)
        self.assertEqual(self.lease.state, 'active')
        self.browser.close.assert_not_awaited()

    async def test_origin_approval_is_private_exact_and_cannot_choose_destination(self):
        await self.attach()
        payload = {'request_id': self.binding['request_id'], 'revision': 'private:1'}
        for headers in ({}, {'Origin': 'https://other.example', 'Authorization': self.headers['Authorization']},
                        {**self.headers, 'Authorization': 'Bearer invalid'}):
            response = await self.human.post('/api/approve-origin', headers=headers, json=payload)
            self.assertNotEqual(response.status, 200)
        for extra in ({'origin': 'https://unapproved.example'}, {'url': 'https://unapproved.example/private?code=CANARY'},
                      {'values': {'password': 'PRIVATE_ORIGIN_CANARY'}}, {'approved': True}):
            response = await self.approve_origin(**extra)
            self.assertEqual(response.status, 409)
            self.assertNotIn('CANARY', await response.text())
        for revision in ('', 'x' * 129, None, [], 1):
            self.assertEqual((await self.approve_origin(revision=revision)).status, 409)
        for provider, agentic, schema in (('google', True, 'website-v2'),
                                         ('website', False, 'website-v2'),
                                         ('website', True, 'website-v1')):
            self.lease.binding.update(provider=provider, agentic=agentic)
            self.lease.form_schema = schema
            self.assertEqual((await self.approve_origin()).status, 409)
        self.lease.binding.update(provider='website', agentic=True)
        self.lease.form_schema = 'website-v2'
        self.assertEqual((await self.operation({'action': 'approve-origin', 'revision': 'private:1'})).status, 409)
        self.assertEqual((await self.control.post('/api/approve-origin', json=payload)).status, 409)
        self.browser.approve_origin.assert_not_awaited()
        self.assertEqual(self.lease.state, 'active')

    async def test_origin_approval_checks_boundary_and_expiry(self):
        await self.attach()
        self.boundary = False
        self.assertEqual((await self.approve_origin()).status, 409)
        self.browser.approve_origin.assert_not_awaited()
        self.boundary = True
        self.lease.binding['expires'] = time.time() - 1
        self.assertEqual((await self.approve_origin()).status, 409)
        self.assertEqual((self.lease.state, self.lease.cleanup), ('expired', 'confirmed'))
        self.browser.approve_origin.assert_not_awaited()
        self.assertEqual((await self.approve_origin()).status, 409)

    async def test_owner_cancel_before_origin_approval_does_not_restore_or_resume(self):
        await self.attach()
        response = await self.human.post('/api/cancel', headers=self.headers,
            json={'request_id': self.binding['request_id']})
        self.assertEqual((response.status, (await response.json())['state']), (200, 'cancelled'))
        self.assertEqual((await self.approve_origin()).status, 409)
        self.assertEqual((self.lease.state, self.lease.cleanup), ('cancelled', 'confirmed'))
        self.browser.approve_origin.assert_not_awaited()

    async def test_origin_approval_unpublic_dns_fails_closed_without_resume(self):
        await self.attach()
        self.browser.approve_origin.side_effect = ValueError('PRIVATE_DNS_CANARY')
        response = await self.approve_origin()
        self.assertEqual(response.status, 409)
        self.assertNotIn('PRIVATE_DNS_CANARY', await response.text())
        self.assertEqual((self.lease.state, self.lease.cleanup), ('failed', 'confirmed'))
        self.browser.close.assert_awaited_once()
        self.browser.submit.assert_not_awaited()

    async def test_explicit_owner_completion_requires_private_step_then_retains_session(self):
        await self.attach()
        response = await self.complete()
        self.assertEqual(response.status, 409)
        self.assertEqual(self.lease.state, 'active')
        self.browser.close.assert_not_awaited()
        self.browser.owner_finishable.return_value = True
        connected = AsyncMock()
        self.broker.connected_factory = lambda browser: connected
        self.lease.delegated_task = AsyncMock()
        response = await self.complete()
        self.assertEqual((response.status, await response.json()), (200, {'accepted': True}))
        self.assertEqual((self.lease.state, self.lease.cleanup), ('owner_confirmed', 'retained'))
        self.assertGreater(self.lease.session_deadline, time.time())
        self.assertIs(self.lease.connected, connected)
        self.lease.delegated_task.close.assert_awaited_once_with()
        connected.start.assert_awaited_once_with()
        self.browser.verified.assert_not_awaited()
        self.browser.submit.assert_not_awaited()
        self.browser.agent_act.assert_not_awaited()
        self.browser.close.assert_not_awaited()
        deadline = self.lease.session_deadline
        response = await self.complete()
        self.assertEqual(response.status, 409)
        self.assertEqual((self.lease.state, self.lease.session_deadline), ('owner_confirmed', deadline))
        connected.start.assert_awaited_once_with()
        self.assertEqual((await (await self.control.post('/status', json=self.scope)).json())['state'],
                         'owner_confirmed')

    async def test_owner_completion_stale_revision_preserves_active_workspace(self):
        await self.attach()
        self.browser.owner_finishable.side_effect = RecoverableFormError('stale_form')
        response = await self.complete(revision='old:1')
        value = await response.json()
        self.assertEqual((response.status, value['recoverable'], value['code']), (409, True, 'stale_form'))
        self.assertEqual(self.lease.state, 'active')
        self.browser.close.assert_not_awaited()
        self.browser.submit.assert_not_awaited()
        self.assertIsNone(self.lease.connected)

    async def test_retained_transport_starts_after_connected_root_validation(self):
        await self.attach()
        events = []
        async def connected_start():
            events.append('checked_root')
        async def retain(seconds):
            events.append(('retained', seconds))
        self.browser.owner_finishable.return_value = True
        self.browser.retain_session.side_effect = retain
        connected = AsyncMock()
        connected.start.side_effect = connected_start
        self.broker.connected_factory = lambda browser: connected
        response = await self.complete()
        self.assertEqual((response.status, await response.json()), (200, {'accepted': True}))
        self.assertEqual(events, ['checked_root', ('retained', 900)])
        self.assertEqual(self.lease.binding['url'], 'https://example.test')
        self.assertEqual(self.lease.state, 'owner_confirmed')
        self.browser.retain_session.assert_awaited_once_with(900)

    async def test_connected_root_validation_failure_cannot_extend_transport(self):
        await self.attach()
        self.browser.owner_finishable.return_value = True
        connected = AsyncMock()
        connected.start.side_effect = ValueError('PRIVATE_CONNECTED_ORIGIN_CANARY')
        self.broker.connected_factory = lambda browser: connected
        response = await self.complete()
        self.assertEqual(response.status, 409)
        self.assertNotIn('PRIVATE_CONNECTED_ORIGIN_CANARY', await response.text())
        self.browser.retain_session.assert_not_awaited()
        self.assertEqual((self.lease.state, self.lease.cleanup), ('failed', 'confirmed'))
        self.browser.close.assert_awaited_once()

    async def test_retention_failure_or_expiry_closes_the_bound_session(self):
        await self.attach()
        self.browser.owner_finishable.return_value = True
        connected = AsyncMock()
        self.broker.connected_factory = lambda browser: connected
        self.browser.retain_session.side_effect = ValueError('PRIVATE_RETENTION_CANARY')
        response = await self.complete()
        self.assertEqual(response.status, 409)
        self.assertNotIn('PRIVATE_RETENTION_CANARY', await response.text())
        self.assertEqual((self.lease.state, self.lease.cleanup), ('failed', 'confirmed'))
        connected.start.assert_awaited_once()
        self.browser.close.assert_awaited_once()

    async def test_gateway_expiry_after_connected_start_does_not_retain_transport(self):
        await self.attach()
        self.browser.owner_finishable.return_value = True
        connected = AsyncMock()
        async def expire():
            self.lease.gateway_deadline = time.time() - 1
        connected.start.side_effect = expire
        self.broker.connected_factory = lambda browser: connected
        response = await self.complete()
        self.assertEqual(response.status, 409)
        self.assertEqual((self.lease.state, self.lease.cleanup), ('expired', 'confirmed'))
        self.browser.retain_session.assert_not_awaited()
        self.browser.close.assert_awaited_once()

    async def test_owner_completion_rejects_nonhuman_authority_extra_values_and_wrong_schemas(self):
        await self.attach()
        self.browser.owner_finishable.return_value = True
        payload = {'request_id': self.binding['request_id'], 'revision': 'private:1'}
        for headers in ({}, {'Origin': 'https://other.example', 'Authorization': self.headers['Authorization']},
                        {**self.headers, 'Authorization': 'Bearer invalid'}):
            response = await self.human.post('/api/complete', headers=headers, json=payload)
            self.assertNotEqual(response.status, 200)
        for extra in ({'values': {'password': 'PRIVATE_COMPLETION_CANARY'}}, {'authenticated': True}):
            response = await self.complete(**extra)
            self.assertEqual(response.status, 409)
            self.assertNotIn('PRIVATE_COMPLETION_CANARY', await response.text())
        for provider, agentic, schema in (('google', True, 'website-v2'),
                                         ('website', False, 'website-v2'),
                                         ('website', True, 'website-v1')):
            self.lease.binding.update(provider=provider, agentic=agentic)
            self.lease.form_schema = schema
            self.assertEqual((await self.complete()).status, 409)
        self.lease.binding.update(provider='website', agentic=True)
        self.lease.form_schema = 'website-v2'
        self.assertEqual((await self.operation({'action': 'complete', 'revision': 'private:1'})).status, 409)
        self.assertEqual((await self.control.post('/api/complete', json=payload)).status, 409)
        self.browser.owner_finishable.assert_not_awaited()
        self.assertEqual(self.lease.state, 'active')

    async def test_cancelled_and_expired_workspaces_cannot_be_owner_completed(self):
        await self.attach()
        self.browser.owner_finishable.return_value = True
        self.lease.binding['expires'] = time.time() - 1
        self.assertEqual((await self.complete()).status, 409)
        self.assertEqual((self.lease.state, self.lease.cleanup), ('expired', 'confirmed'))
        self.browser.owner_finishable.assert_not_awaited()
        self.assertEqual((await self.complete()).status, 409)
        self.assertEqual(self.lease.state, 'expired')

    async def test_owner_cancel_then_late_completion_does_not_restore_session(self):
        await self.attach()
        self.browser.owner_finishable.return_value = True
        response = await self.human.post('/api/cancel', headers=self.headers,
            json={'request_id': self.binding['request_id']})
        self.assertEqual((await response.json())['state'], 'cancelled')
        self.assertEqual((await self.complete()).status, 409)
        self.assertEqual((self.lease.state, self.lease.cleanup), ('cancelled', 'confirmed'))
        self.browser.owner_finishable.assert_not_awaited()

    async def test_agent_attach_bypasses_jev_and_keeps_explicit_resource_policy(self):
        self.assertFalse(self.broker.anonymous_static_resources)
        await self.attach()
        deadline = self.lease.gateway_deadline
        for operation in ({'action': 'observe'}, {'action': 'act', 'revision': 'fixture:1', 'action_ref': 'a1'},
                          {'action': 'present', 'revision': 'fixture:1', 'plan': {'fixture': True}}):
            response = await self.operation(operation)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['observation'], observation())
        self.assertEqual(self.lease.gateway_deadline, deadline)
        self.assertEqual(self.lease.state, 'active')
        self.browser.agent_act.assert_awaited_once_with('fixture:1', 'a1')
        self.browser.agent_present.assert_awaited_once_with('fixture:1', {'fixture': True})

    async def test_surface_http_bounds_accept_advertised_private_fields_and_wrapped_plan(self):
        await self.attach()
        observed = []
        async def submit(revision, action, values):
            observed.append((len(values), {len(value) for value in values.values()}))
        self.browser.submit.side_effect = submit
        response = await self.human.post('/api/submit', headers={'Origin': self.broker.origin,
            'Authorization': 'Bearer ' + 'H' * 43}, json={})
        self.assertNotEqual(response.status, 200)
        # Use the actual lease hash without retaining or emitting a production
        # credential; this synthetic human token never leaves the local test.
        from oak.login_broker import hashed
        human = 'H' * 43
        self.lease.human_hash = hashed(human)
        response = await self.human.post('/api/submit', headers={'Origin': self.broker.origin,
            'Authorization': 'Bearer ' + human}, json={'request_id': self.binding['request_id'],
            'revision': 'fixture:1', 'action': 'a1', 'values': {'f' + str(i): 'Ж' * 1024 for i in range(32)}})
        self.assertEqual(response.status, 200)
        self.assertEqual(observed, [(32, {1024})])
        reply = await self.operation({'action': 'present', 'revision': 'fixture:1',
                                     'plan': {'bounded_fixture': ' ' * 32700}})
        self.assertEqual(reply.status, 200)

    async def test_unicode_option_response_fits_private_renderer_byte_limit(self):
        await self.attach()
        from oak.login_broker import hashed
        self.lease.human_hash = hashed('H' * 43)
        self.browser.snapshot.return_value = {'provider': 'website', 'destination': 'https://example.test',
            'stage': 'form', 'revision': 'fixture:1', 'fields': [
                {'name': 'f' + str(index), 'type': 'select', 'label': 'Варіант', 'max_length': 64,
                 'options': [{'value': 'o' + str(index * 8 + option), 'label': 'Ж' * 120}
                             for option in range(8)]} for index in range(24)],
            'actions': [{'id': 'a1', 'label': 'Продовжити', 'field_refs': ['f' + str(i) for i in range(24)],
                         'available': True}], 'message': ''}
        response = await self.human.post('/api/form', headers={'Origin': self.broker.origin,
            'Authorization': 'Bearer ' + 'H' * 43}, json={'request_id': self.binding['request_id']})
        self.assertEqual(response.status, 200)
        self.assertLess(len(await response.read()), 131072)
        self.assertEqual(len((await response.json())['form']['fields']), 24)

    async def test_anonymous_resources_require_broker_opt_in(self):
        self.broker.anonymous_static_resources = True
        await self.attach()
        self.assertEqual(self.broker.auth_hosts, {'https://example.test': ['auth.example.test']})
        for invalid in (None, 1, 'true', {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                LoginBroker('https://private.example.test', 'https://panel.example.test', 1001,
                            anonymous_static_resources=invalid)

    async def test_exact_scope_and_control_plane_only(self):
        await self.attach()
        self.assertEqual((await self.operation({'action': 'observe'}, session_id='wrong')).status, 409)
        self.assertEqual((await self.operation({'action': 'observe'}, runtime_epoch='wrong')).status, 409)
        self.assertEqual((await self.human.post('/agent', headers={'Origin': self.broker.origin}, json={})).status, 404)
        self.assertEqual((await self.operation({'action': 'observe', 'values': {'password': 'canary'}})).status, 409)
        self.peer = False
        self.assertEqual((await self.operation({'action': 'observe'})).status, 409)
        self.peer, self.boundary = True, False
        self.assertEqual((await self.operation({'action': 'observe'})).status, 409)
        self.browser.agent_observe.assert_not_awaited()

    async def test_recoverable_step_retains_scope_and_returns_progress(self):
        await self.attach()
        self.browser.agent_observe.side_effect = RecoverableFormError('navigation_pending')
        response = await self.operation({'action': 'observe'})
        self.assertEqual(await response.json(), {'state': 'active', 'progress': observation()['progress'],
                                                'error': {'code': 'navigation_pending'}})
        self.assertEqual(self.lease.state, 'active')
        self.browser.close.assert_not_awaited()

    async def test_model_proposal_rejection_does_not_destroy_private_workspace(self):
        from oak.website_agent import AgentProposalError
        await self.attach()
        for code in ('invalid_plan', 'human_step_pending', 'navigation_requires_owner'):
            self.browser.agent_present.side_effect = AgentProposalError(code)
            response = await self.operation({'action': 'present', 'revision': 'fixture:1', 'plan': {}})
            self.assertEqual((await response.json())['error'], {'code': code})
            self.assertEqual(self.lease.state, 'active')
        self.browser.close.assert_not_awaited()
        self.browser.agent_observe.assert_awaited_once()

    async def test_stale_and_invalid_proposals_include_one_fresh_read_with_matching_progress(self):
        from oak.login import WebsiteLogin
        from oak.website_agent import AgentProposalError
        await self.attach()
        for method, error, operation in (
                ('agent_act', RecoverableFormError('stale_form'),
                 {'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'}),
                ('agent_present', AgentProposalError('invalid_plan'),
                 {'action': 'present', 'revision': 'old:1', 'plan': {}})):
            with self.subTest(code=error.code):
                rejected = getattr(self.browser, method)
                rejected.side_effect = error
                self.browser.agent_observe.reset_mock()
                fresh = observation(1)
                fresh['revision'] = 'fresh:2'

                async def read():
                    self.assertTrue(self.lease.lock.locked())
                    return fresh

                self.browser.agent_observe.side_effect = read
                response = await self.operation(operation)
                value = await response.json()
                self.assertEqual(value, {'state': 'active', 'progress': fresh['progress'],
                    'observation': fresh, 'error': {'code': error.code}})
                self.assertEqual(WebsiteLogin.agent_response(value), value)
                rejected.assert_awaited_once()
                self.browser.agent_observe.assert_awaited_once_with()
                self.assertIs(self.lease.browser, self.browser)
        self.browser.close.assert_not_awaited()

    async def test_unsettled_refresh_returns_original_error_without_repeating_read(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')
        self.browser.agent_observe.side_effect = RecoverableFormError('navigation_pending')
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'active', 'progress': observation()['progress'],
            'error': {'code': 'stale_form'}})
        self.browser.agent_act.assert_awaited_once()
        self.browser.agent_observe.assert_awaited_once()
        self.browser.close.assert_not_awaited()

    async def test_refresh_failure_closes_without_exposing_exception_or_active_progress(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')
        self.browser.agent_observe.side_effect = RuntimeError('PRIVATE_EXCEPTION_CANARY')
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'failed'})
        self.assertNotIn('PRIVATE_EXCEPTION_CANARY', await response.text())
        self.browser.agent_act.assert_awaited_once()
        self.browser.close.assert_awaited_once()

    async def test_refresh_rejects_extra_private_properties_and_closes(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')
        self.browser.agent_observe.return_value = {**observation(), 'value': 'PRIVATE_OBSERVATION_CANARY'}
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'failed'})
        self.assertNotIn('PRIVATE_OBSERVATION_CANARY', await response.text())
        self.browser.close.assert_awaited_once()

    async def test_expiry_during_refresh_never_returns_active_observation(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')

        async def expired():
            self.lease.binding['expires'] = time.time() - 1
            return observation()

        self.browser.agent_observe.side_effect = expired
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'expired'})
        self.browser.close.assert_awaited_once()

    async def test_boundary_revoked_before_refresh_prevents_read(self):
        await self.attach()

        async def action(*_):
            self.boundary = False
            raise RecoverableFormError('stale_form')

        self.browser.agent_act.side_effect = action
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(response.status, 409)
        self.assertNotIn('observation', await response.json())
        self.browser.agent_observe.assert_not_awaited()

    async def test_boundary_revoked_during_refresh_does_not_export_observation(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')

        async def read():
            self.boundary = False
            return observation()

        self.browser.agent_observe.side_effect = read
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(response.status, 409)
        self.assertNotIn('observation', await response.json())
        self.browser.agent_observe.assert_awaited_once()

    async def test_close_during_refresh_returns_terminal_state_only(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')

        async def read():
            await self.broker.finish(self.lease, 'cancelled')
            return observation()

        self.browser.agent_observe.side_effect = read
        response = await self.operation({'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'cancelled'})
        self.browser.close.assert_awaited_once()

    async def test_cancelling_refresh_closes_once_and_releases_lease_lock(self):
        await self.attach()
        self.browser.agent_act.side_effect = RecoverableFormError('stale_form')
        reading = asyncio.Event()

        async def read():
            reading.set()
            await asyncio.Event().wait()

        self.browser.agent_observe.side_effect = read
        request = AsyncMock()
        request.json.return_value = {**self.scope,
            'operation': {'action': 'act', 'revision': 'old:1', 'action_ref': 'a1'}}
        task = asyncio.create_task(self.broker.agent(request))
        await asyncio.wait_for(reading.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.lease.state, 'failed')
        self.assertEqual(self.lease.cleanup, 'confirmed')
        self.assertFalse(self.lease.lock.locked())
        self.browser.agent_act.assert_awaited_once()
        self.browser.agent_observe.assert_awaited_once()
        self.browser.close.assert_awaited_once()

    async def test_projection_rejects_extra_private_data(self):
        await self.attach()
        self.browser.agent_observe.return_value = {**observation(), 'values': {'password': 'private-canary'}}
        response = await self.operation({'action': 'observe'})
        self.assertEqual(response.status, 409)
        self.assertNotIn('private-canary', await response.text())

    async def test_uncertain_agent_dispatch_closes_once_and_preserves_reason(self):
        await self.attach()
        self.browser.agent_act.side_effect = SubmitUncertainError()
        response = await self.operation({'action': 'act', 'revision': 'fixture:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'failed', 'error': {'code': 'action_uncertain'}})
        self.browser.close.assert_awaited_once()
        self.assertEqual(await (await self.operation({'action': 'observe'})).json(), {'state': 'failed'})

    async def test_agent_mode_requires_explicit_generic_binding(self):
        for changes in ({'agentic': False}, {'agentic': 1}, {'provider': 'google'}, {'agentic': 'true'}):
            response = await self.control.post('/create', json={**self.binding, **changes})
            self.assertEqual(response.status, 409)
        await self.attach()
        self.lease.binding.pop('agentic')
        self.assertEqual((await self.operation({'action': 'observe'})).status, 409)

    async def test_expired_or_cancelled_agent_cannot_dispatch(self):
        await self.attach()
        self.lease.gateway_deadline = time.time() - 1
        response = await self.operation({'action': 'act', 'revision': 'fixture:1', 'action_ref': 'a1'})
        self.assertEqual(await response.json(), {'state': 'expired'})
        self.browser.agent_act.assert_not_awaited()
        self.browser.close.assert_awaited_once()


@unittest.skipUnless(ui.NODE, 'Node is required for renderer checks')
class WebsiteAgentRendererTests(unittest.TestCase):
    def test_actual_adapter_plan_on_unchanged_dom_renders_without_cancelling(self):
        from oak.website_agent import WebsiteAgentForms
        from test_website_scene import scene, plan

        async def snapshots():
            observed = scene()
            observed['destination'] = 'https://example.test'
            browser = WebsiteAgentForms('https://example.test')
            browser._observe = AsyncMock(return_value={'stage': 'form', 'revision': observed['revision'],
                'scene': observed, 'fields': [], 'actions': [], 'confirmable': False})
            working = await browser.snapshot()
            await browser.agent_present(observed['revision'], plan())
            presented = await browser.snapshot()
            self.assertEqual((await browser.agent_observe())['revision'], observed['revision'])
            self.assertEqual(presented, await browser.snapshot())
            return [{'state': 'active', 'form': value} for value in (working, presented)]

        forms = asyncio.run(snapshots())
        ui.LoginUITests.run_ui(self, 'const forms=' + json.dumps(forms) + ';\n' + r'''
const ui=await createUI();
await ui.attach(forms[0], 'website', 'https://example.test', {form_schema:'website-v2'});
await ui.poll(forms[1]);
assert.equal(ui.ids.error.hidden,true);
assert.equal(ui.input('f1').type,'email');
assert.equal(ui.input('f2').type,'password');
assert.equal(ui.requests.filter(r=>r.url==='/api/cancel').length,0);
ui.input('f1').value='private-draft';
await ui.poll(forms[1]);
assert.equal(ui.input('f1').value,'private-draft');
assert.equal(ui.requests.filter(r=>r.url==='/api/attach').length,1);
''')

    def test_private_step_returns_to_agent_without_closing_or_losing_poll(self):
        ui.LoginUITests.run_ui(self, r'''
const ui = await createUI();
await ui.attach(website([], [], 'agent_working'), 'website');
assert.equal(ui.ids.fields.children.length, 0);
assert.equal(ui.ids.cancel.disabled, false);
assert.match(ui.ids.status.textContent, /Агент/);
await ui.poll(website([{name:'f1',type:'password',label:'Password',max_length:1024}],
  [{id:'a1',label:'Continue'}], 'form', 'page:2'));
const field=ui.input('f1'); field.value='private-step-canary';
ui.responses.push({accepted:true}, website([], [], 'agent_working', 'page:3'));
await ui.submit('a1');
assert.equal(field.value, '');
assert.equal(ui.ids.fields.children.length, 0);
assert.equal(ui.ids.cancel.disabled, false);
assert.ok(!JSON.stringify(ui.messages).includes('private-step-canary'));
await ui.poll(website([{name:'f2',type:'otp',label:'Code',max_length:16}],
  [{id:'a2',label:'Verify'}], 'form', 'page:4'));
assert.equal(ui.input('f2').inputMode, 'numeric');
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,1);
assert.equal(ui.requests.filter(r=>r.url==='/api/attach').length,1);
''')

    def test_surface_alternatives_send_no_email_password_or_other_form_drafts(self):
        ui.LoginUITests.run_ui(self, r'''
function surface() {
 const value=website([
  {name:'f1',type:'email',label:'Email',max_length:254},
  {name:'f2',type:'password',label:'Password',max_length:1024},
  {name:'f3',type:'otp',label:'Other form code',max_length:16}], [
  {id:'a1',label:'Continue',field_refs:['f1','f2'],available:true},
  {id:'a2',label:'Continue with another method',field_refs:[],available:true},
  {id:'a3',label:'Verify other form',field_refs:['f3'],available:true},
  {id:'a4',label:'External method',field_refs:[],available:false}]);
 value.form.form_schema='website-v2';return value;
}
for (const [action,expected] of [['a1',{f1:'reserved-email',f2:'secret-draft'}],['a2',{}],['a3',{f3:'123456'}]]) {
 const ui=await createUI();await ui.attach(surface(),'website','https://example.test',{form_schema:'website-v2'});
 assert.equal(ui.ids.actions.querySelectorAll('button').length,4);
 assert.equal(ui.ids.actions.querySelectorAll('button')[3].disabled,true);
 const explanation=ui.ids.actions.querySelectorAll('p').find(p=>p.id==='unavailable-a4');
 assert.ok(explanation,'the disabled action retains a visible explanation');
 assert.equal(explanation.textContent,'Ця дія зараз недоступна для цього браузера.');
 assert.equal(ui.ids.actions.querySelectorAll('button')[3].attributes['aria-describedby'],'unavailable-a4');
 ui.input('f1').value='reserved-email';ui.input('f2').value='secret-draft';ui.input('f3').value='123456';
 ui.responses.push({accepted:true}, {state:'active',form:{...surface().form,stage:'agent_working',revision:'page:2',fields:[],actions:[]}});
 await ui.submit(action);
 const posts=ui.requests.filter(r=>r.url==='/api/submit');assert.equal(posts.length,1);
 assert.deepEqual(posts[0].body.values,expected);
 assert.equal(ui.input('f2'),undefined);
 assert.ok(!JSON.stringify(ui.messages).includes('secret-draft'));
}
const ui=await createUI();await ui.attach(surface(),'website','https://example.test',{form_schema:'website-v2'});
await ui.poll(surface());
assert.equal(ui.ids.actions.querySelectorAll('button')[3].disabled,true,'poll cannot enable a denied method');
await ui.submit('a4');assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
''')

    def test_surface_requires_exact_known_action_bindings_and_explicit_schema(self):
        ui.LoginUITests.run_ui(self, r'''
for(const invalid of [
 {id:'a1',label:'Continue'},
 {id:'a1',label:'Continue',field_refs:['f2'],available:true},
 {id:'a1',label:'Continue',field_refs:['f1','f1'],available:true},
 {id:'a1',label:'Continue',field_refs:[],available:'yes'},
]) {
 const ui=await createUI();const value=website([{name:'f1',type:'email',label:'Email',max_length:254}],[invalid]);
 value.form.form_schema='website-v2';await ui.attach(value,'website','https://example.test',{form_schema:'website-v2'});
 assert.equal(ui.ids.login.hidden,true);assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
}
const ui=await createUI();const value=website([], [{id:'a1',label:'Continue',field_refs:[],available:true}]);
value.form.form_schema='website-v2';await ui.attach(value,'website','https://example.test',{form_schema:'website-v1'});
assert.equal(ui.ids.login.hidden,true,'surface cannot silently change a legacy lease schema');
''')

    def test_explicit_completion_is_separate_from_site_actions_and_never_sends_drafts(self):
        ui.LoginUITests.run_ui(self, r'''
const ui=await createUI();
const form=website([{name:'f1',type:'password',label:'Phrase',max_length:1024}],
 [{id:'a1',label:'Continue',field_refs:['f1'],available:true}]);
form.form.form_schema='website-v2';form.form.can_complete=false;
await ui.attach(form,'website','https://example.test',{form_schema:'website-v2'});
assert.equal(ui.ids.complete.hidden,true);
await ui.ids.complete.listeners.get('click')();
assert.equal(ui.requests.filter(r=>r.url==='/api/complete').length,0);
await ui.poll({state:'active',form:{...form.form,revision:'page:2',can_complete:true}});
assert.equal(ui.ids.complete.hidden,false);
assert.equal(ui.ids.actions.querySelectorAll('button').length,1,'completion does not replace observed actions');
const secret=ui.input('f1');secret.value='private-owner-completion-canary';
ui.responses.push({accepted:true},{state:'owner_confirmed'});
await ui.ids.complete.listeners.get('click')();
const posts=ui.requests.filter(r=>r.url==='/api/complete');assert.equal(posts.length,1);
assert.deepEqual(Object.keys(posts[0].body).sort(),['request_id','revision']);
assert.equal(secret.value,'');assert.equal(ui.ids.complete.hidden,true);
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,0);
assert.ok(!JSON.stringify(ui.messages).includes('private-owner-completion-canary'));
''')

    def test_stale_owner_completion_waits_for_refresh_without_replay(self):
        ui.LoginUITests.run_ui(self, r'''
const ui=await createUI();const form=website([],[],'agent_working','page:2');
form.form.form_schema='website-v2';form.form.can_complete=true;
await ui.attach(form,'website','https://example.test',{form_schema:'website-v2'});
ui.responses.push(new Response(JSON.stringify({recoverable:true,code:'stale_form',error:'Форма змінилася.'}),
 {status:409,headers:{'Content-Type':'application/json'}}));
await ui.ids.complete.listeners.get('click')();
assert.equal(ui.requests.filter(r=>r.url==='/api/complete').length,1);
assert.equal(ui.ids.complete.disabled,true);assert.equal(ui.ids.cancel.disabled,false);
await ui.poll({state:'active',form:{...form.form,revision:'page:3'}});
assert.equal(ui.ids.complete.disabled,false);
assert.equal(ui.requests.filter(r=>r.url==='/api/complete').length,1,'refresh cannot replay completion');
''')
