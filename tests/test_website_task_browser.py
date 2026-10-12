"""Actual Chromium + broker HTTP/MCP; intercepted site and mocked Decisions only."""

import asyncio
from copy import deepcopy
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from aiohttp.test_utils import TestClient, TestServer

from oak.login_broker import LoginBroker
from oak.website_agent import WebsiteAgentForms
from oak.website_planner import RESPONSE_MODEL
from oak.website_step_planner import StepPlanner
from oak.website_task import WebsiteTask
from oak.website_task_client import WebsiteTaskClient


SITE = 'https://task-fixture.example'
IDENTIFIER = 'private-identifier-731@example.invalid'
PASSWORD = 'private-phrase-392-test-only'


class Transport:
    def __init__(self, client):
        self.client, self.messages = client, []

    def post(self, url, **kwargs):
        self.messages.append(deepcopy(kwargs['json']))
        return self.client.post(urlsplit(url).path, **kwargs)

    def delete(self, url, **kwargs):
        return self.client.delete(urlsplit(url).path, **kwargs)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class WebsiteTaskBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.site_requests, self.payloads, self.statuses = [], [], []

        async def intercepted(route):
            self.site_requests.append(route.request.method)
            await route.fulfill(status=200, content_type='text/html', body='<body>Fixture</body>')
        await self.context.route('**/*', intercepted)
        self.page = await self.context.new_page()
        await self.page.goto(SITE)
        await self.page.set_content('<h1>Sign in</h1><form method="post">'
            '<label>Member email<input type="email" autocomplete="username" required></label>'
            '<button>Continue</button></form>')
        await self.page.evaluate(r'''() => {
          window.submits = 0;
          document.querySelector('form').onsubmit = e => {
            e.preventDefault(); window.submits++;
            const identifier = e.target.querySelector('input').value;
            document.body.innerHTML = '<h1></h1><form method="post"><label>Secret phrase'
              + '<input type="password" autocomplete="current-password" required></label>'
              + '<button>Sign in</button></form>';
            document.querySelector('h1').textContent = 'Continue for ' + identifier;
            document.querySelector('form').onsubmit = event => {
              event.preventDefault(); window.submits++;
              const phrase = event.target.querySelector('input').value;
              document.body.innerHTML = '<h1>Check the result</h1><p></p><a href="/">Home</a>';
              document.querySelector('p').textContent = 'Received ' + phrase;
            };
          };
        }''')
        self.planner = StepPlanner('SYNTHETIC_PRIVATE_KEY_CANARY')
        self.planner._post = AsyncMock(side_effect=self.decisions)
        self.broker = LoginBroker('https://private.example.test', 'https://panel.example.test', 1001,
            peer_check=lambda request: True, boundary_check=lambda: True,
            step_planner_factory=lambda: self.planner)
        self.control = TestClient(TestServer(self.broker.control))
        self.human = TestClient(TestServer(self.broker.human))
        await self.control.start_server(); await self.human.start_server()
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.broker.close)
        self.binding = {'request_id': 'a' * 32, 'owner': 1002, 'chat_id': 1003,
            'thread_id': 'fixture-thread', 'turn_id': 'fixture-turn', 'runtime_epoch': 'fixture-epoch',
            'panel_hash': 'b' * 64, 'expires': time.time() + 240, 'provider': 'website',
            'url': SITE, 'agentic': True}
        created = await self.control.post('/create', json=self.binding)
        self.assertEqual(created.status, 200)
        value = await created.json()
        self.scope = {key: value[key] for key in ('request_id', 'session_id', 'runtime_epoch')}
        context, page, chromium = self.context, self.page, self.chromium
        async def fixture_start(browser):
            # The document is already fulfilled by the local interceptor, so
            # replace launch/goto only. Start the real navigation lifecycle and
            # install CDP guards; no external socket is used by this context.
            browser.context, browser.page, browser.browser = context, page, chromium
            browser._pinned_address = '93.184.216.34'  # Synthetic trusted fixture pin, never connected.
            await browser._navigation.start()
            # Preloading bypassed the initial intercepted document admission,
            # which normally registers this same root pin with the proxy.
            browser._approved_origins[browser.destination] = browser._pinned_address
            browser._navigation._proxy.register(urlsplit(SITE).hostname, browser._pinned_address)
            await browser._configure_context()
            await browser._configure_page()
        with patch.object(WebsiteAgentForms, 'start', fixture_start):
            attached = await self.human.post('/api/attach', headers={'Origin': self.broker.origin},
                json={'request_id': self.binding['request_id'], 'ticket': value['ticket']})
        self.assertEqual(attached.status, 200)
        self.headers = {'Origin': self.broker.origin, 'Authorization': 'Bearer ' + (await attached.json())['lease']}
        self.lease = self.broker.leases[self.binding['request_id']]
        self.browser = self.lease.browser
        self.assertIsInstance(self.browser, WebsiteAgentForms)
        self.assertIsNotNone(self.browser._navigation._cdp)
        self.assertEqual(self.browser._navigation.diagnostics()['proxy']['registered'], 1)
        factory = lambda *args, **kwargs: WebsiteTask(*args, **kwargs, poll_seconds=.02)
        patcher = patch('oak.website_task.WebsiteTask', side_effect=factory)
        patcher.start(); self.addCleanup(patcher.stop)
        self.transport = Transport(self.control)
        self.mcp = WebsiteTaskClient(self.transport, self.scope)
        await self.mcp.initialize()

    def decisions(self, payload):
        """Deterministic typed replies test orchestration, not Jev quality."""
        self.payloads.append(deepcopy(payload))
        answers = {}
        # Surface Choice descriptions carry their own controls; their context
        # intentionally omits the duplicate flat controls table.
        controls = {control['ref']: control for control in payload['state'].get('controls', [])}
        for key, question in payload['questions'].items():
            if key == 'anchor':
                chosen = (next(option for option in question['criteria'] if option.startswith('present_'))
                          if controls else 'wait')
            elif key == 'surface':
                chosen = next(option for option in question['criteria'] if option != 'none')
            elif key.startswith('field_'):
                chosen = {'email': 'EmailInput', 'password': 'PasswordInput'}[controls[key[6:]]['type']]
            elif key.startswith('binding_'):
                chosen = 'none'
            else:
                chosen = 'none' if key == 'message' else 'omit'
            answers[key] = {'type': 'choice', 'choice': chosen, 'confidence': 0,
                'probabilities': {option: float(option == chosen) for option in question['criteria']}}
        return {'model': RESPONSE_MODEL, 'provider': 'TypeSafe', 'answers': answers,
                'usage': {'cost': .00001}}

    async def until(self, predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(.01)

    async def form(self, types):
        async with asyncio.timeout(5):
            while True:
                response = await self.human.post('/api/form', headers=self.headers,
                    json={'request_id': self.binding['request_id']})
                self.assertEqual(response.status, 200)
                value = await response.json()
                self.assertEqual(value['state'], 'active')
                current = value['form']
                if current['stage'] == 'form' and [field['type'] for field in current['fields']] == types:
                    return current
                await asyncio.sleep(.02)

    async def submit(self, form, value):
        response = await self.human.post('/api/submit', headers=self.headers, json={
            'request_id': self.binding['request_id'], 'revision': form['revision'],
            'action': form['actions'][0]['id'], 'values': {form['fields'][0]['name']: value}})
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {'accepted': True})

    async def test_real_private_identifier_dispatch_resumes_mcp_task_to_password(self):
        await self.mcp.call('website_sign_in')
        self.addAsyncCleanup(self.mcp.close)
        identifier = await self.form(['email'])
        self.assertEqual(await self.page.evaluate('window.submits'), 0)
        identifier_calls = self.planner.calls
        self.assertGreaterEqual(identifier_calls, 1)
        await self.submit(identifier, IDENTIFIER)
        password = await self.form(['password'])
        self.assertNotEqual(password['revision'], identifier['revision'])
        password_calls = self.planner.calls
        self.assertGreater(password_calls, identifier_calls)
        self.assertEqual(password_calls, len(self.payloads))
        self.assertEqual(await self.page.evaluate('window.submits'), 1)
        self.assertEqual(await self.page.input_value('input[type=password]'), '')
        status = await self.mcp.call('website_task_status'); self.statuses.append(status)
        self.assertEqual(status['progress'], {'event_seq': 1, 'outcome': 'submitted'})
        self.assertEqual(status['state'], 'waiting_for_owner')
        await asyncio.sleep(.08)
        self.assertEqual(self.planner.calls, password_calls)  # No inference while human input is pending.
        await self.submit(password, PASSWORD)
        await self.until(lambda: self.lease.delegated_task.snapshot()['progress']['event_seq'] == 2)
        private = await (await self.human.post('/api/form', headers=self.headers,
            json={'request_id': self.binding['request_id']})).json()
        self.assertEqual(private['state'], 'active')
        # The site deliberately reflects a password. Redaction hides it from
        # planner/UI; absence of controls never supplies authentication proof.
        self.assertEqual(private['form']['stage'], 'agent_working')
        self.assertEqual(private['form']['actions'], [])
        self.assertFalse(await self.browser.verified())
        self.assertEqual(await self.page.evaluate('window.submits'), 2)
        self.assertEqual(self.site_requests, ['GET'])  # All site traffic was locally intercepted.
        exported = json.dumps([self.payloads, self.transport.messages, self.statuses,
            self.lease.delegated_task.snapshot(), self.lease.delegated_task.diagnostics(), private])
        for secret in (IDENTIFIER, PASSWORD, 'SYNTHETIC_PRIVATE_KEY_CANARY'):
            self.assertNotIn(secret, exported)
        self.assertEqual(self.lease.state, 'active')  # No model-generated authentication.
        cancelled = await self.human.post('/api/cancel', headers=self.headers,
            json={'request_id': self.binding['request_id']})
        self.assertEqual(await cancelled.json(), {'state': 'cancelled'})
        self.assertEqual(self.lease.cleanup, 'confirmed')
        await self.mcp.close()

    async def test_explicit_private_completion_retains_browser_and_stops_real_mcp_task(self):
        connected = AsyncMock()
        self.broker.connected_factory = lambda browser: connected
        await self.mcp.call('website_sign_in')
        self.addAsyncCleanup(self.mcp.close)
        identifier = await self.form(['email'])
        self.assertFalse(identifier['can_complete'])
        async def complete(revision):
            return await self.human.post('/api/complete', headers=self.headers, json={
                'request_id': self.binding['request_id'], 'revision': revision})
        self.assertEqual((await complete(identifier['revision'])).status, 409)
        self.assertEqual(self.lease.state, 'active')
        await self.submit(identifier, IDENTIFIER)
        password = await self.form(['password'])
        self.assertTrue(password['can_complete'])
        self.assertEqual([field['type'] for field in password['fields']], ['password'])
        self.assertNotIn('confirm', [action['id'] for action in password['actions']])
        self.assertFalse(await self.browser.verified())
        stale = await complete(identifier['revision'])
        self.assertEqual((stale.status, (await stale.json())['code']), (409, 'stale_form'))
        self.assertEqual(self.lease.state, 'active')
        current = await self.form(['password'])
        response = await asyncio.wait_for(complete(current['revision']), 5)
        self.assertEqual((response.status, await response.json()), (200, {'accepted': True}))
        self.assertEqual((self.lease.state, self.lease.cleanup), ('owner_confirmed', 'retained'))
        self.assertIs(self.lease.browser, self.browser)
        self.assertIs(self.lease.browser.context, self.context)
        self.assertTrue(self.lease.delegated_task._task.done())
        connected.start.assert_awaited_once_with()
        self.assertTrue(self.browser._retained)
        self.assertTrue(self.browser._navigation.diagnostics()['proxy']['frozen'])
        self.assertGreater(self.browser._navigation._proxy._expires - time.monotonic(), 890)
        self.assertFalse(self.page.is_closed())
        self.assertEqual(await self.page.evaluate('window.submits'), 1)
        self.assertEqual(await self.page.input_value('input[type=password]'), '')
        deadline = self.lease.session_deadline
        self.assertEqual((await complete(current['revision'])).status, 409)
        self.assertEqual(self.lease.session_deadline, deadline)
        status = await (await self.control.post('/status', json=self.scope)).json()
        self.assertEqual((status['state'], status['cleanup']), ('owner_confirmed', 'retained'))
        detached = await self.human.post('/api/detach', headers=self.headers,
            json={'request_id': self.binding['request_id']})
        self.assertEqual((await detached.json())['state'], 'owner_confirmed')
        self.assertFalse(self.page.is_closed())
        await self.mcp.close()  # Closing the finished MCP channel does not revoke the retained browser.
        self.assertEqual(self.lease.state, 'owner_confirmed')
        exported = json.dumps([self.payloads, self.transport.messages, status, current])
        self.assertNotIn(IDENTIFIER, exported)
        self.assertNotIn('SYNTHETIC_PRIVATE_KEY_CANARY', exported)

    async def test_mcp_cancel_during_browser_callback_conservatively_closes_lease(self):
        entered = asyncio.Event()
        observe = self.browser.agent_observe
        async def suspended():
            await observe()  # Exercise the real Chromium observation before suspension.
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.browser, 'agent_observe', side_effect=suspended):
            await self.mcp.call('website_sign_in')
            self.addAsyncCleanup(self.mcp.close)
            await asyncio.wait_for(entered.wait(), 5)
            # Cancellation during guarded browser work is potentially uncertain;
            # bounded() closes the exact lease instead of promising to retain it.
            with self.assertRaises(ValueError):
                await asyncio.wait_for(self.mcp.call('website_task_cancel'), 5)
            await self.until(lambda: self.lease.cleanup == 'confirmed')
        self.assertEqual(self.lease.state, 'failed')
        self.assertIsNone(self.lease.browser)
        self.assertTrue(self.page.is_closed())
        self.assertTrue(self.lease.delegated_task._task.done())
        self.assertEqual(self.planner.calls, 0)
        status = await (await self.control.post('/status', json=self.scope)).json()
        self.assertEqual((status['state'], status['cleanup']), ('failed', 'confirmed'))
        self.assertNotIn(IDENTIFIER, json.dumps([self.transport.messages, status]))


if __name__ == '__main__':
    unittest.main()
