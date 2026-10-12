"""Private origin consent with the real adapter and an offline transport seam.

No browser, model, public DNS, provider account or external service is used.
The pending original request is represented by the transport's immutable item.
"""

import asyncio
import json
from types import SimpleNamespace
import socket
import time
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from oak.login_broker import LoginBroker
from oak.signin_providers import RecoverableFormError
from oak.website_agent import WebsiteAgentForms
from oak.website_navigation_transport import NavigationRequest


ROOT_ORIGIN = 'https://relying-party.example'
AUTH_ORIGIN = 'https://identity.example'
PRIVATE_CONTINUATION = AUTH_ORIGIN + '/authorize?state=PRIVATE_NAVIGATION_CANARY&code=PRIVATE_CODE_CANARY'
PIN = '93.184.216.34'


class OfflineNavigation:
    def __init__(self, authorize, *, on_block, limits):
        self.authorize, self.on_block, self.limits = authorize, on_block, limits
        self.closed = False
        self.retained = []

    async def start(self):
        pass

    async def close(self):
        self.closed = True

    def diagnostics(self):
        return {'closed': self.closed}

    def retain_session(self, seconds, hosts):
        self.retained.append((seconds, set(hosts)))


class WebsiteOriginApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PIN, 443))])
        self.browser = WebsiteAgentForms(ROOT_ORIGIN, origin_approval=True,
            navigation_factory=OfflineNavigation, resolver=self.resolver)
        self.browser._pinned_address = PIN
        self.main_frame = SimpleNamespace(url=ROOT_ORIGIN + '/')
        self.browser.page = SimpleNamespace(main_frame=self.main_frame)
        self.pending_tasks = []
        self.addAsyncCleanup(self.browser.close)
        self.addAsyncCleanup(self.cancel_tasks)

    async def cancel_tasks(self):
        for task in self.pending_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.pending_tasks, return_exceptions=True)

    def request(self, url=PRIVATE_CONTINUATION, method='GET', **changes):
        return NavigationRequest(url, method, changes.get('request_id', 'request-1'),
            changes.get('redirected_from'), 'main', 'Document', changes.get('main_frame', True))

    async def pending(self, **changes):
        task = asyncio.create_task(self.browser._authorize_navigation(self.request(**changes)))
        self.pending_tasks.append(task)
        for _ in range(20):
            if self.browser._pending_navigation is not None or task.done():
                break
            await asyncio.sleep(0)
        self.assertFalse(task.done(), 'Original navigation must wait for the private owner')
        self.assertIsNotNone(self.browser._pending_navigation)
        return task, await self.browser.snapshot()

    async def test_pending_consent_is_private_fresh_and_disables_completion(self):
        self.browser._agent_human_completed = True
        task, private = await self.pending()
        self.assertEqual(private['destination'], ROOT_ORIGIN)
        self.assertEqual(private['current_origin'], ROOT_ORIGIN)
        self.assertEqual(private['origin_approval'], {'origin': AUTH_ORIGIN})
        self.assertEqual(private['stage'], 'device_approval')
        self.assertEqual((private['fields'], private['actions'], private['can_complete']), ([], [], False))
        self.assertFalse(await self.browser.owner_finishable(private['revision']))
        self.assertFalse(await self.browser.verified())
        model = await self.browser.agent_observe()
        self.assertEqual(model['stage'], 'device_approval')
        self.assertEqual(model['scene']['controls'], [])
        self.assertEqual(model['scene']['actions'], [])
        self.assertEqual(model['scene']['texts'], [])
        for forbidden in (AUTH_ORIGIN, ROOT_ORIGIN, 'PRIVATE_NAVIGATION_CANARY', 'PRIVATE_CODE_CANARY', '/authorize'):
            self.assertNotIn(forbidden, json.dumps(model))
        for forbidden in ('PRIVATE_NAVIGATION_CANARY', 'PRIVATE_CODE_CANARY', '/authorize'):
            self.assertNotIn(forbidden, json.dumps(private))
        self.resolver.assert_not_awaited()
        self.assertFalse(task.done())
        self.assertEqual((await self.browser.snapshot())['revision'], private['revision'])

    async def test_local_block_revokes_pending_consent_and_keeps_valid_blocked_snapshot(self):
        original, private = await self.pending()
        self.browser._block('popup')
        blocked = await self.browser.snapshot()
        self.assertEqual(blocked['stage'], 'provider_blocked')
        self.assertNotIn('origin_approval', blocked)
        self.assertFalse(blocked['can_complete'])
        observed = await self.browser.agent_observe()
        self.assertEqual(observed['stage'], 'provider_blocked')
        with self.assertRaises(RecoverableFormError):
            await self.browser.approve_origin(private['revision'])
        self.assertNotIn(AUTH_ORIGIN, self.browser._approved_origins)
        self.resolver.assert_not_awaited()
        self.assertFalse(original.done())

    async def test_approval_is_revision_bound_consumed_before_dns_and_continues_original_once(self):
        original, private = await self.pending()
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args, **kwargs):
            entered.set()
            await release.wait()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PIN, 443))]
        self.resolver.side_effect = resolve
        with self.assertRaises(RecoverableFormError) as stale:
            await self.browser.approve_origin('old:1')
        self.assertEqual(stale.exception.code, 'stale_form')
        first = asyncio.create_task(self.browser.approve_origin(private['revision']))
        self.pending_tasks.append(first)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(self.browser._pending_navigation['consumed'])
        self.assertFalse(original.done())
        duplicate = asyncio.create_task(self.browser.approve_origin(private['revision']))
        self.pending_tasks.append(duplicate)
        release.set()
        self.assertIsNone(await first)
        self.assertEqual(await original, PIN)
        with self.assertRaises(RecoverableFormError) as repeated:
            await duplicate
        self.assertEqual(repeated.exception.code, 'stale_form')
        self.resolver.assert_awaited_once_with('identity.example', 443, type=socket.SOCK_STREAM)
        self.assertEqual(self.browser.destination, ROOT_ORIGIN)
        self.assertEqual(self.browser.document_origin, ROOT_ORIGIN)
        self.assertIsNone(self.browser._pending_navigation)

    async def test_committed_origin_keeps_root_identity_and_invalidates_old_plan(self):
        original, private = await self.pending()
        await self.browser.approve_origin(private['revision'])
        await original
        old_plan = {'private_fixture': True}
        self.browser._composition_plan = old_plan
        self.browser._agent_ui_current = ('old:1', 'native:1', old_plan)
        self.main_frame.url = PRIVATE_CONTINUATION
        self.browser._document_committed(self.main_frame)
        self.assertEqual(self.browser.document_origin, AUTH_ORIGIN)
        self.assertEqual(self.browser.destination, ROOT_ORIGIN)
        self.assertEqual(self.browser.config['origins'], {ROOT_ORIGIN})
        self.assertIsNone(self.browser._composition_plan)
        self.assertIsNone(self.browser._agent_ui_current)
        current = self.browser._private_snapshot({'stage': 'agent_working', 'revision': 'committed:1',
            'fields': [], 'actions': []})
        self.assertEqual((current['destination'], current['current_origin']), (ROOT_ORIGIN, AUTH_ORIGIN))
        self.assertNotIn('origin_approval', current)
        self.assertNotIn('PRIVATE_CODE_CANARY', json.dumps(current))
        self.main_frame.url = ROOT_ORIGIN + '/callback?code=PRIVATE_CODE_CANARY'
        self.browser._document_committed(self.main_frame)
        self.assertEqual(self.browser.document_origin, ROOT_ORIGIN)
        self.assertFalse(self.browser._blocked)

    async def test_closing_pending_approval_cancels_owned_transition_and_rejects_late_consent(self):
        original, private = await self.pending()
        self.browser.page = None  # No real browser objects exist in this offline fixture.
        await self.browser.close()
        await asyncio.wait_for(asyncio.gather(original, return_exceptions=True), 1)
        self.assertTrue(self.browser._navigation.closed)
        self.assertTrue(original.done())
        self.assertTrue(original.cancelled() or original.exception() is not None)
        self.assertIsNone(self.browser._pending_navigation)
        with self.assertRaises((RecoverableFormError, ValueError)):
            await self.browser.approve_origin(private['revision'])
        self.resolver.assert_not_awaited()

    async def test_identity_document_cannot_finish_root_task_after_an_accepted_step(self):
        original, private = await self.pending()
        await self.browser.approve_origin(private['revision'])
        await original
        self.main_frame.url = PRIVATE_CONTINUATION
        self.browser._document_committed(self.main_frame)
        self.browser._agent_human_completed = True
        state = {'stage': 'agent_working', 'revision': 'identity:1', 'fields': [], 'actions': []}
        with patch.object(self.browser, '_observe', AsyncMock(return_value=state)):
            current = await self.browser.snapshot()
            self.assertFalse(current['can_complete'])
            self.assertFalse(await self.browser.owner_finishable(current['revision']))
        self.assertEqual(current['current_origin'], AUTH_ORIGIN)
        self.assertEqual(current['destination'], ROOT_ORIGIN)

    async def test_authority_expiry_during_dns_does_not_release_or_register_origin(self):
        authority = True
        def guard():
            if not authority:
                raise ValueError('Navigation authority expired.')
        self.browser._navigation_guard = guard
        original, private = await self.pending()
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args, **kwargs):
            entered.set()
            await release.wait()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PIN, 443))]
        self.resolver.side_effect = resolve
        approval = asyncio.create_task(self.browser.approve_origin(private['revision']))
        self.pending_tasks.append(approval)
        await asyncio.wait_for(entered.wait(), 1)
        authority = False
        release.set()
        with self.assertRaises(ValueError):
            await approval
        await asyncio.gather(original, return_exceptions=True)
        self.assertTrue(original.cancelled())
        self.assertNotIn(AUTH_ORIGIN, self.browser._approved_origins)
        self.assertIsNone(self.browser._pending_navigation)

    async def test_root_retention_is_once_root_only_and_keeps_post_redirect_replay_denied(self):
        self.browser._approved_origins.update({ROOT_ORIGIN: PIN, AUTH_ORIGIN: PIN})
        await self.browser.retain_session(900)
        self.assertEqual(self.browser._navigation.retained, [(900, {'relying-party.example'})])
        self.assertEqual(self.browser._approved_origins, {ROOT_ORIGIN: PIN})
        self.assertEqual(await self.browser._authorize_navigation(self.request(url=ROOT_ORIGIN + '/article', method='POST')), PIN)
        for request in (self.request(url=ROOT_ORIGIN + '/article', method='POST', redirected_from='prior'),
                        self.request(url=AUTH_ORIGIN + '/entry')):
            with self.assertRaises(ValueError):
                await self.browser._authorize_navigation(request)
        self.assertIsNone(self.browser._pending_navigation)
        with self.assertRaises(ValueError):
            await self.browser.retain_session(900)
        self.assertEqual(len(self.browser._navigation.retained), 1)

    async def test_mixed_dns_and_cross_origin_post_cannot_be_approved_or_replayed(self):
        for request in (self.request(method='POST'), self.request(method='POST', redirected_from='old-request'),
                        self.request(main_frame=False), self.request(url='http://identity.example/')):
            with self.assertRaises(ValueError):
                await self.browser._authorize_navigation(request)
        self.assertIsNone(self.browser._pending_navigation)
        original, private = await self.pending()
        self.resolver.return_value.append((socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443)))
        with self.assertRaises(ValueError):
            await self.browser.approve_origin(private['revision'])
        await asyncio.gather(original, return_exceptions=True)
        self.assertNotIn(AUTH_ORIGIN, self.browser._approved_origins)
        with self.assertRaises(RecoverableFormError):
            await self.browser.approve_origin(private['revision'])

    async def test_second_origin_needs_new_private_revision_and_monotonic_redactions(self):
        self.browser._human_values_entered = True
        self.browser._redactions = ['PRIVATE_VALUE_CANARY']
        original, private = await self.pending()
        await self.browser.approve_origin(private['revision'])
        await original
        second, next_private = await self.pending(url='https://second-identity.example/challenge?state=PRIVATE_NAVIGATION_CANARY')
        self.assertNotEqual(next_private['revision'], private['revision'])
        self.assertEqual(next_private['origin_approval'], {'origin': 'https://second-identity.example'})
        with self.assertRaises(RecoverableFormError):
            await self.browser.approve_origin(private['revision'])
        self.assertFalse(second.done())
        self.assertTrue(self.browser._human_values_entered)
        self.assertEqual(self.browser._redactions, ['PRIVATE_VALUE_CANARY'])
        self.assertNotIn('PRIVATE_VALUE_CANARY', json.dumps(await self.browser.agent_observe()))

    async def private_http(self):
        broker = LoginBroker('https://private.example.test', 'https://panel.example.test', 1001,
            boundary_check=lambda: True, peer_check=lambda request: True)
        self.addAsyncCleanup(broker.close)
        control = TestClient(TestServer(broker.control))
        human = TestClient(TestServer(broker.human))
        await control.start_server()
        await human.start_server()
        self.addAsyncCleanup(control.close)
        self.addAsyncCleanup(human.close)
        binding = {'request_id': 'a' * 32, 'owner': 1002, 'chat_id': 1003, 'thread_id': 'thread',
            'turn_id': 'turn', 'runtime_epoch': 'epoch', 'panel_hash': 'b' * 64,
            'expires': time.time() + 240, 'provider': 'website', 'url': ROOT_ORIGIN, 'agentic': True}
        created = await control.post('/create', json=binding)
        ticket = (await created.json())['ticket']
        with patch('oak.website_agent.WebsiteAgentForms', return_value=self.browser) as constructor, \
                patch.object(self.browser, 'start', AsyncMock()):
            attached = await human.post('/api/attach', headers={'Origin': broker.origin},
                json={'request_id': binding['request_id'], 'ticket': ticket})
        self.assertEqual(attached.status, 200)
        self.browser._navigation_guard = constructor.call_args.kwargs['navigation_guard']
        headers = {'Origin': broker.origin, 'Authorization': 'Bearer ' + (await attached.json())['lease']}
        return broker, human, headers, binding

    async def test_real_private_endpoint_approves_only_the_rendered_pending_revision(self):
        broker, human, headers, binding = await self.private_http()
        original, private = await self.pending()
        form = await human.post('/api/form', headers=headers, json={'request_id': binding['request_id']})
        self.assertEqual((await form.json())['form']['revision'], private['revision'])
        payload = {'request_id': binding['request_id'], 'revision': private['revision']}
        denied = await human.post('/api/approve-origin', headers=headers, json={**payload, 'origin': AUTH_ORIGIN})
        self.assertEqual(denied.status, 409)
        self.assertFalse(original.done())
        accepted = await human.post('/api/approve-origin', headers=headers, json=payload)
        self.assertEqual((accepted.status, await accepted.json()), (200, {'approved': True}))
        self.assertEqual(await original, PIN)
        repeated = await human.post('/api/approve-origin', headers=headers, json=payload)
        self.assertEqual((repeated.status, (await repeated.json())['code']), (409, 'stale_form'))
        lease = broker.leases[binding['request_id']]
        self.assertEqual(lease.binding, binding)
        self.assertEqual((lease.state, lease.session_deadline), ('active', 0))

    async def test_private_endpoint_gateway_expiry_during_dns_never_releases_held_request(self):
        broker, human, headers, binding = await self.private_http()
        original, private = await self.pending()
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args, **kwargs):
            entered.set()
            await release.wait()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PIN, 443))]
        self.resolver.side_effect = resolve
        approval = asyncio.create_task(human.post('/api/approve-origin', headers=headers,
            json={'request_id': binding['request_id'], 'revision': private['revision']}))
        self.pending_tasks.append(approval)
        await asyncio.wait_for(entered.wait(), 1)
        lease = broker.leases[binding['request_id']]
        lease.gateway_deadline = time.time() - 1
        release.set()
        response = await approval
        self.assertEqual(response.status, 409)
        await asyncio.gather(original, return_exceptions=True)
        self.assertTrue(original.cancelled())
        self.assertNotIn(AUTH_ORIGIN, self.browser._approved_origins)
        self.assertEqual(lease.cleanup, 'confirmed')
        self.assertNotEqual(lease.state, 'active')
        self.assertNotIn('PRIVATE_NAVIGATION_CANARY', await response.text())


if __name__ == '__main__':
    unittest.main()
