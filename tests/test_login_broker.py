"""Human/control boundary and ephemeral login-lease lifecycle contracts."""

import json
import logging
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from oak.login_broker import LoginBroker, hashed
from oak.signin_providers import RecoverableFormError, SubmitUncertainError


CANARY = 'test-password-not-for-status-or-logs'


class FakeBrowser:
    def __init__(self, provider):
        self.provider = provider
        self.authenticated = False
        self.url = 'https://accounts.google.com/v3/signin/identifier'
        self.closed = 0
        self.submitted_fields = []
        self.failure = False

    async def start(self):
        pass

    async def snapshot(self):
        return {'provider': self.provider, 'stage': 'password', 'revision': 'fixture:1',
                'fields': [{'name': 'password', 'type': 'password', 'label': 'Пароль', 'max_length': 1024}],
                'actions': [{'id': 'submit', 'label': 'Продовжити'}], 'message': 'Введи пароль.'}

    async def submit(self, revision, action, values):
        self.submitted_fields.append(set(values))
        if self.failure:
            raise RuntimeError(CANARY)

    async def verified(self):
        return self.authenticated

    async def close(self):
        self.closed += 1


class LoginBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.boundary = True
        self.peer = True
        self.broker = LoginBroker('https://login.example.test', 'https://panel.example.test', 1001,
            browser_factory=FakeBrowser, boundary_check=lambda: self.boundary,
            peer_check=lambda request: self.peer,
            connected_factory=lambda browser: SimpleNamespace(start=AsyncMock(), run=AsyncMock(return_value={'text': 'fixture'})))
        self.human = TestClient(TestServer(self.broker.human))
        self.control = TestClient(TestServer(self.broker.control))
        await self.human.start_server()
        await self.control.start_server()
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.broker.close)
        self.binding = {'request_id': 'a' * 32, 'owner': 1002, 'chat_id': 1003,
                        'thread_id': 'fixture-thread', 'turn_id': 'fixture-turn', 'runtime_epoch': 'fixture-epoch',
                        'panel_hash': 'b' * 64, 'expires': time.time() + 240, 'provider': 'google'}
        self.headers = {'Origin': self.broker.origin}

    async def create(self):
        response = await self.control.post('/create', json=self.binding)
        self.assertEqual(response.status, 200)
        self.created = await response.json()
        self.lease = self.broker.leases[self.binding['request_id']]
        self.scope = {key: self.created[key] for key in ('request_id', 'session_id', 'runtime_epoch')}
        return self.created

    async def attach(self):
        if not hasattr(self, 'created'):
            await self.create()
        response = await self.human.post('/api/attach', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'ticket': self.created['ticket']})
        self.assertEqual(response.status, 200)
        self.attached = await response.json()
        self.human_token = self.attached['lease']
        self.headers['Authorization'] = 'Bearer ' + self.human_token
        return self.lease.browser

    async def form(self):
        return await self.human.post('/api/form', headers=self.headers, json={'request_id': self.binding['request_id']})

    async def authenticate(self):
        browser = await self.attach()
        browser.authenticated = True
        response = await self.form()
        self.assertEqual((await response.json())['state'], 'authenticated')
        return browser

    async def test_diagnostics_requires_control_scope_and_does_not_renew(self):
        browser = await self.attach()
        safe = {'composition_revisions': 2, 'scene_catalog': True,
                'model': {'api_calls': 4, 'events': []}}
        browser.diagnostics = lambda: safe
        deadline = self.lease.gateway_deadline
        response = await self.control.post('/diagnostics', json=self.scope)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['diagnostics'], safe)
        self.assertEqual(self.lease.gateway_deadline, deadline)
        wrong = {**self.scope, 'session_id': 'wrong'}
        self.assertEqual((await self.control.post('/diagnostics', json=wrong)).status, 409)
        self.peer = False
        self.assertEqual((await self.control.post('/diagnostics', json=self.scope)).status, 409)
        self.peer = True
        self.assertEqual((await self.human.post('/diagnostics', headers=self.headers, json=self.scope)).status, 404)
        self.assertNotIn(CANARY, json.dumps(safe))
        await self.control.post('/cancel', json=self.scope)
        response = await self.control.post('/diagnostics', json=self.scope)
        self.assertEqual((await response.json())['diagnostics'], safe)

    async def test_boundary_and_peer_reject_before_creation(self):
        for attribute in ('boundary', 'peer'):
            setattr(self, attribute, False)
            response = await self.control.post('/create', json=self.binding)
            self.assertEqual(response.status, 409)
            self.assertEqual(self.broker.leases, {})
            setattr(self, attribute, True)
        unconfigured = LoginBroker('https://login.example.test', 'https://panel.example.test', 1001)
        with self.assertRaises(ValueError):
            unconfigured.boundary()

    async def test_default_control_guard_rejects_tcp_peer(self):
        broker = LoginBroker('https://login.example.test', 'https://panel.example.test', 1001,
                             boundary_check=lambda: True)
        async with TestClient(TestServer(broker.control)) as client:
            response = await client.post('/health', json={})
            self.assertEqual(response.status, 409)

    async def test_binding_rejects_unknown_fields_and_invalid_scope(self):
        for changes in ({'owner': True}, {'turn_id': ''}, {'runtime_epoch': ''}, {'thread_id': ''},
                        {'panel_hash': 'wrong'}, {'provider': 'arbitrary'}, {'expires': time.time() - 1},
                        {'password': CANARY}):
            response = await self.control.post('/create', json={**self.binding, **changes})
            self.assertEqual(response.status, 409)
            self.assertEqual(self.broker.leases, {})

    async def test_single_use_ticket_is_hashed_and_bound_to_request(self):
        await self.create()
        self.assertEqual(self.lease.ticket_hash, hashed(self.created['ticket']))
        payload = {'request_id': 'c' * 32, 'ticket': self.created['ticket']}
        self.assertEqual((await self.human.post('/api/attach', headers=self.headers, json=payload)).status, 409)
        browser = await self.attach()
        payload['request_id'] = self.binding['request_id']
        self.assertEqual((await self.human.post('/api/attach', headers=self.headers, json=payload)).status, 409)
        self.assertEqual(self.lease.ticket_hash, '')
        self.assertIs(self.lease.browser, browser)
        duplicate = await self.control.post('/create', json=self.binding)
        self.assertEqual(duplicate.status, 409)

    async def test_human_origin_and_lease_are_required(self):
        await self.attach()
        for headers in ({}, {'Origin': 'https://attacker.example', 'Authorization': self.headers['Authorization']},
                        {'Origin': self.broker.origin, 'Authorization': 'Bearer wrong'}):
            response = await self.human.post('/api/form', headers=headers, json={'request_id': self.binding['request_id']})
            self.assertIn(response.status, (403, 409))
        self.assertEqual((await self.form()).status, 200)

    async def test_redirect_alone_and_client_success_flag_cannot_authenticate(self):
        browser = await self.attach()
        browser.url = 'https://myaccount.google.com/'
        result = await (await self.form()).json()
        self.assertEqual(result['state'], 'active')
        self.assertIsNone(self.lease.connected)
        response = await self.human.post('/api/form', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'authenticated': True})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.lease.state, 'active')

    async def test_connected_use_requires_exact_owner_chat_turn_epoch_and_session(self):
        await self.authenticate()
        payload = {**self.scope, **{key: self.binding[key] for key in ('owner', 'chat_id', 'turn_id')},
                   'operation': {'action': 'read'}}
        for key in ('owner', 'chat_id', 'turn_id', 'runtime_epoch', 'session_id', 'request_id'):
            response = await self.control.post('/use', json={**payload, key: 'wrong'})
            self.assertEqual(response.status, 409)
        self.lease.connected.run.assert_not_awaited()
        response = await self.control.post('/use', json=payload)
        self.assertEqual(response.status, 200)
        self.lease.connected.run.assert_awaited_once_with({'action': 'read'})

    async def test_detach_retains_authenticated_session_but_revokes_human_token(self):
        browser = await self.authenticate()
        response = await self.human.post('/api/detach', headers=self.headers, json={'request_id': self.binding['request_id']})
        self.assertEqual((await response.json())['state'], 'authenticated')
        self.assertEqual(browser.closed, 0)
        self.assertEqual(self.lease.human_hash, '')
        self.assertEqual((await self.form()).status, 409)
        result = await (await self.control.post('/status', json=self.scope)).json()
        self.assertEqual(result['cleanup'], 'retained')
        response = await self.control.post('/cancel', json=self.scope)
        self.assertEqual((await response.json())['state'], 'cancelled')
        self.assertEqual(browser.closed, 1)
        self.assertEqual(self.lease.cleanup, 'confirmed')

    async def test_detach_before_authentication_cancels_and_closes(self):
        browser = await self.attach()
        response = await self.human.post('/api/detach', headers=self.headers, json={'request_id': self.binding['request_id']})
        self.assertEqual((await response.json())['state'], 'cancelled')
        self.assertEqual(browser.closed, 1)
        self.assertIsNone(self.lease.browser)

    async def test_expired_gateway_or_session_cleans_up(self):
        browser = await self.authenticate()
        self.lease.session_deadline = time.time() - 1
        response = await self.control.post('/status', json=self.scope)
        value = await response.json()
        self.assertEqual((value['state'], value['cleanup']), ('expired', 'confirmed'))
        self.assertEqual(browser.closed, 1)

    async def test_gateway_heartbeat_cannot_resurrect_expired_active_form(self):
        browser = await self.attach()
        self.lease.gateway_deadline = time.time() - 1
        response = await self.control.post('/status', json=self.scope)
        self.assertEqual((await response.json())['state'], 'expired')
        self.assertEqual(browser.closed, 1)

    async def test_boundary_revocation_stops_human_and_control_access(self):
        browser = await self.attach()
        self.boundary = False
        self.assertEqual((await self.form()).status, 409)
        self.assertEqual((await self.control.post('/status', json=self.scope)).status, 409)
        self.assertEqual(browser.submitted_fields, [])

    async def test_credentials_absent_from_success_status_failure_and_logs(self):
        browser = await self.attach()
        payload = {'request_id': self.binding['request_id'], 'revision': 'fixture:1',
                   'action': 'submit', 'values': {'password': CANARY}}
        messages = []
        handler = logging.Handler()
        handler.emit = lambda record: messages.append(record.getMessage())
        logger = logging.getLogger()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        response = await self.human.post('/api/submit', headers=self.headers, json=payload)
        self.assertEqual(await response.json(), {'accepted': True})
        status = await (await self.control.post('/status', json=self.scope)).text()
        self.assertNotIn(CANARY, status)
        browser.failure = True
        response = await self.human.post('/api/submit', headers=self.headers, json=payload)
        self.assertEqual(response.status, 409)
        self.assertNotIn(CANARY, await response.text())
        self.assertNotIn(CANARY, json.dumps(messages))
        self.assertEqual(self.lease.state, 'failed')
        self.assertEqual(browser.closed, 1)
        self.assertEqual(browser.submitted_fields, [{'password'}, {'password'}])

    async def test_stale_submit_preserves_lease_and_requires_new_human_action(self):
        browser = await self.attach()
        browser.submit = AsyncMock(side_effect=[RecoverableFormError('stale_form'), None])
        payload = {'request_id': self.binding['request_id'], 'revision': 'fixture:1',
                   'action': 'submit', 'values': {'password': CANARY}}
        response = await self.human.post('/api/submit', headers=self.headers, json=payload)
        value = await response.json()
        self.assertEqual((response.status, value['recoverable'], value['code']), (409, True, 'stale_form'))
        self.assertNotIn(CANARY, json.dumps(value))
        self.assertEqual((self.lease.state, browser.closed), ('active', 0))
        self.assertEqual((await self.form()).status, 200)
        browser.submit.assert_awaited_once()
        self.assertEqual((await self.human.post('/api/submit', headers=self.headers, json=payload)).status, 200)
        self.assertEqual(browser.submit.await_count, 2)

    async def test_navigation_pending_read_preserves_binding_until_next_read(self):
        browser = await self.attach()
        browser.verified = AsyncMock(side_effect=[RecoverableFormError('navigation_pending'), False])
        response = await self.form()
        self.assertEqual((await response.json())['code'], 'navigation_pending')
        self.assertEqual((self.lease.state, browser.closed), ('active', 0))
        self.assertEqual((await self.form()).status, 200)
        self.assertEqual(browser.submitted_fields, [])

    async def test_composition_failure_preserves_cancellable_lease(self):
        browser = await self.attach()
        browser.verified = AsyncMock(side_effect=RecoverableFormError('composition_unavailable'))
        response = await self.form()
        self.assertEqual((await response.json())['code'], 'composition_unavailable')
        self.assertEqual((self.lease.state, browser.closed), ('active', 0))
        response = await self.control.post('/cancel', json=self.scope)
        self.assertEqual((await response.json())['state'], 'cancelled')
        self.assertEqual(browser.closed, 1)

    async def test_explicit_composer_is_bound_to_generic_lease(self):
        composer = object()
        self.broker.composer_factory = lambda: composer
        self.binding.update(provider='website', url='https://example.com/login')
        browser = FakeBrowser('website')
        with patch('oak.website_forms.WebsiteForms', return_value=browser) as generic:
            await self.attach()
        self.assertEqual(generic.call_args.kwargs, {'composer': composer})
        self.assertEqual(generic.call_args.args[0], 'https://example.com/login')
        self.assertEqual(self.lease.binding['provider'], 'website')
        self.assertEqual(self.lease.browser.provider, 'website')
        self.assertEqual(self.lease.binding['turn_id'], self.binding['turn_id'])

    async def test_no_composer_keeps_legacy_shortcut_schema(self):
        browser = await self.attach()
        self.assertEqual(browser.provider, 'google')
        self.assertEqual(self.attached['form_schema'], 'provider-v1')

    async def test_static_resources_require_explicit_generic_activation(self):
        self.assertFalse(self.broker.tiktok_static_resources)
        # Anonymous discovery is an agent-only option, even when the broker
        # enables it. The legacy request below keeps its explicit host policy.
        self.broker.anonymous_static_resources = True
        self.broker.tiktok_static_resources = True
        self.binding.update(provider='website', url='https://www.tiktok.com/login')
        with patch('oak.website_forms.WebsiteForms', return_value=FakeBrowser('website')) as generic:
            await self.attach()
        generic.assert_called_once_with('https://www.tiktok.com/login', static_resources=True)

    async def test_auth_backend_matches_only_explicit_generic_origin(self):
        self.assertEqual(self.broker.auth_hosts, {})
        self.broker.auth_hosts = {'https://login.example.com': ['auth.example.com'],
                                  'https://accounts.google.com': ['other.example.com']}
        with patch('oak.website_forms.WebsiteForms') as generic:
            await self.attach()
        generic.assert_not_called()
        self.binding.update(request_id='c' * 32, provider='website', url='https://login.example.com/login')
        del self.created
        with patch('oak.website_forms.WebsiteForms', return_value=FakeBrowser('website')) as generic:
            await self.attach()
        generic.assert_called_once_with('https://login.example.com/login', auth_hosts=['auth.example.com'])
        self.binding.update(request_id='d' * 32, url='https://neighbor.login.example.com/login')
        del self.created
        with patch('oak.website_forms.WebsiteForms', return_value=FakeBrowser('website')) as generic:
            await self.attach()
        generic.assert_called_once_with('https://neighbor.login.example.com/login')

    async def test_static_map_selects_only_exact_generic_origin(self):
        self.broker.static_resources = {'https://login.example.com': {'assets.example.com': ['script']},
                                        'https://other.example.com': {'other-assets.example.com': ['stylesheet']}}
        self.binding.update(provider='website', url='https://login.example.com/login')
        with patch('oak.website_forms.WebsiteForms', return_value=FakeBrowser('website')) as generic:
            await self.attach()
        generic.assert_called_once_with('https://login.example.com/login',
                                        resource_hosts={'assets.example.com': ['script']})

    async def test_static_map_does_not_apply_to_unlisted_origin_or_named_provider(self):
        self.broker.static_resources = {'https://unrelated.example.com': {'assets.example.com': ['script']},
                                        'https://accounts.google.com': {'configured.example.com': ['script']}}
        with patch('oak.website_forms.WebsiteForms') as generic:
            browser = await self.attach()
        self.assertEqual(browser.provider, 'google')
        generic.assert_not_called()
        # A separate fresh binding is still generic, but no suffix/neighbor rule applies.
        self.binding.update(request_id='c' * 32, provider='website', url='https://neighbor.unrelated.example.com/login')
        del self.created
        with patch('oak.website_forms.WebsiteForms', return_value=FakeBrowser('website')) as generic:
            await self.attach()
        generic.assert_called_once_with('https://neighbor.unrelated.example.com/login')

    async def test_static_map_constructor_copies_and_rejects_ambiguous_legacy_grants(self):
        policy = {'https://login.example.com': {'assets.example.com': ['script']}}
        broker = LoginBroker('https://human.example.com', 'https://parent.example.com', 1001,
                             static_resources=policy)
        self.addAsyncCleanup(broker.close)
        policy['https://login.example.com']['assets.example.com'].append('font')
        self.assertEqual(broker.static_resources['https://login.example.com']['assets.example.com'], ['script'])
        from oak.website_resources import TIKTOK_ORIGIN, STATIC_HOSTS
        with self.assertRaises(ValueError):
            LoginBroker('https://human.example.com', 'https://parent.example.com', 1001,
                static_resources={TIKTOK_ORIGIN: {next(iter(STATIC_HOSTS)): ['font']}},
                tiktok_static_resources=True)
        for invalid in (True, [], {'https://login.example.com': {'assets.example.com': ['fetch']}}):
            with self.assertRaises(ValueError):
                LoginBroker('https://human.example.com', 'https://parent.example.com', 1001,
                            static_resources=invalid)

    async def test_form_schema_cannot_change_after_lease_attachment(self):
        browser = await self.attach()
        snapshot = await browser.snapshot()
        snapshot['form_schema'] = 'website-v1'
        browser.snapshot = AsyncMock(return_value=snapshot)
        self.assertEqual((await self.form()).status, 409)
        self.assertEqual(self.lease.form_schema, 'provider-v1')
        self.assertNotIn(self.lease.state, {'authenticated', 'owner_confirmed'})

    async def test_optional_composer_preserves_named_provider_binding_and_disallows_confirmation(self):
        composer = object()
        self.broker.tiktok_static_resources = True
        self.broker.composer_factory = lambda: composer
        browser = FakeBrowser('google')
        browser.form_schema = 'website-v1'
        browser.confirmable = AsyncMock(return_value=True)
        browser.submit = AsyncMock(side_effect=ValueError('Website input rejected.'))
        with patch('oak.website_forms.ComposedProviderForms', return_value=browser) as composed:
            await self.attach()
        composed.assert_called_once_with('google', composer=composer)
        self.assertEqual(self.lease.binding['provider'], 'google')
        self.assertEqual(self.attached['provider'], 'google')
        self.assertEqual(self.attached['form_schema'], 'website-v1')
        self.assertEqual(self.attached['destination'], 'https://accounts.google.com')
        form = await (await self.form()).json()
        self.assertEqual(form['form']['form_schema'], 'website-v1')
        response = await self.human.post('/api/submit', headers=self.headers,
            json={'request_id': self.binding['request_id'], 'revision': 'fixture:1',
                  'action': 'confirm', 'values': {}})
        self.assertEqual(response.status, 409)
        browser.confirmable.assert_not_awaited()
        self.assertNotEqual(self.lease.state, 'owner_confirmed')

    async def test_uncertain_dispatched_input_remains_terminal_and_sanitized(self):
        browser = await self.attach()
        browser.submit = AsyncMock(side_effect=SubmitUncertainError())
        payload = {'request_id': self.binding['request_id'], 'revision': 'fixture:1',
                   'action': 'submit', 'values': {'password': CANARY}}
        response = await self.human.post('/api/submit', headers=self.headers, json=payload)
        value = await response.json()
        self.assertEqual(value['code'], 'submit_uncertain')
        self.assertNotIn('recoverable', value)
        self.assertNotIn(CANARY, json.dumps(value))
        self.assertEqual((self.lease.state, browser.closed), ('failed', 1))
        self.assertEqual((await self.human.post('/api/submit', headers=self.headers, json=payload)).status, 409)
        browser.submit.assert_awaited_once()

    async def test_recoverable_observation_cannot_extend_expired_lease(self):
        browser = await self.attach()
        async def pending():
            self.lease.human_deadline = time.time() - 1
            raise RecoverableFormError('navigation_pending')
        browser.verified = pending
        response = await self.form()
        self.assertEqual(response.status, 409)
        self.assertNotIn('recoverable', await response.json())
        self.assertEqual((self.lease.state, browser.closed), ('expired', 1))

    async def test_failed_cleanup_is_reported_uncertain_and_keeps_resource_owned(self):
        browser = await self.attach()
        browser.close = AsyncMock(side_effect=[RuntimeError(CANARY), None])
        response = await self.control.post('/cancel', json=self.scope)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.lease.cleanup, 'uncertain')
        self.assertIs(self.lease.browser, browser)
        self.assertEqual(self.lease.human_hash, '')
        self.assertNotIn(CANARY, await response.text())
        response = await self.control.post('/cancel', json=self.scope)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.lease.cleanup, 'confirmed')
        self.assertIsNone(self.lease.browser)
        self.assertEqual(browser.close.await_count, 2)

    async def test_generic_owner_confirmation_is_explicit_revision_checked_and_distinct(self):
        self.binding.update(provider='website', url='https://example.com/task')
        browser = FakeBrowser('website')
        browser.confirmable = AsyncMock(return_value=False)
        with patch('oak.website_forms.WebsiteForms', return_value=browser):
            await self.attach()
        payload = {'request_id': self.binding['request_id'], 'revision': 'fixture:1', 'action': 'confirm', 'values': {}}
        response = await self.human.post('/api/submit', headers=self.headers, json=payload)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.lease.state, 'active')
        self.assertIsNone(self.lease.connected)
        browser.confirmable.return_value = True
        response = await self.human.post('/api/submit', headers=self.headers, json={**payload, 'values': {'password': CANARY}})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.lease.state, 'active')
        response = await self.human.post('/api/submit', headers=self.headers, json=payload)
        self.assertEqual(response.status, 200)
        browser.confirmable.assert_awaited_with('fixture:1')
        self.assertEqual(self.lease.state, 'owner_confirmed')
        self.assertFalse(browser.authenticated)
        self.assertEqual(browser.submitted_fields, [])
        self.assertIsNotNone(self.lease.connected)
        response = await self.control.post('/status', json=self.scope)
        self.assertEqual((await response.json())['state'], 'owner_confirmed')
        await self.human.post('/api/detach', headers=self.headers, json={'request_id': self.binding['request_id']})
        self.assertEqual(browser.closed, 0)
        self.assertEqual(self.lease.human_hash, '')

    async def test_generic_url_is_part_of_validated_binding(self):
        for url in ('http://example.com/', 'https://user:password@example.com/',
                    'https://example.com/?access_token=fixture', 'https://example.com/#secret'):
            response = await self.control.post('/create', json={**self.binding, 'provider': 'website', 'url': url})
            self.assertEqual(response.status, 409)
        self.assertEqual(self.broker.leases, {})


if __name__ == '__main__':
    unittest.main()
