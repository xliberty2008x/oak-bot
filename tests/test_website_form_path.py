"""Controlled native RPC → gateway → broker HTTP → real Chromium transitions.

No model inference, Telegram WebView, host attestation or real provider traffic.
Every browser request is intercepted; human values are synthetic canaries.
"""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from oak.login_broker import LoginBroker
from oak.signin_providers import WebsiteBrowser
from oak.website_forms import ComposedProviderForms, WebsiteForms
import test_website_login as gateway_fixtures


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in local intercepted Chromium fixtures')
class WebsiteFormPathTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the existing native RPC/owner fixture without inheriting its tests.
    setUp = gateway_fixtures.WebsiteLoginTests.setUp
    finish = gateway_fixtures.WebsiteLoginTests.finish
    until = gateway_fixtures.WebsiteLoginTests.until
    begin = gateway_fixtures.WebsiteLoginTests.begin
    launch = gateway_fixtures.WebsiteLoginTests.launch

    async def asyncSetUp(self):
        from playwright.async_api import async_playwright

        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.chromium = await self.playwright.chromium.launch(headless=True)
        self.addAsyncCleanup(self.chromium.close)
        self.posts = []
        self.control_payloads = []
        self.decoy_form = False

        def prepare(adapter):
            async def start():
                adapter.context = await self.chromium.new_context(service_workers='block')

                async def intercept(route):
                    if route.request.method == 'POST':
                        self.posts.append(adapter.provider)
                        await asyncio.sleep(0.02)
                        html = ('<form method="post"><input name="Passwd" type="password">'
                                '<button id="passwordNext">Next</button></form>')
                    else:
                        html = ('<form method="post" action="/v3/signin/challenge/pwd">'
                                '<input id="identifierId" name="identifier" type="email">'
                                '<button id="identifierNext" type="submit">Next</button></form>'
                                '<form method="get"><select aria-label="Language">'
                                '<option>English</option><option>Українська</option></select></form>')
                        if self.decoy_form:
                            html += '<form method="post"><input name="q"><button>Search</button></form>'
                    await route.fulfill(status=200, content_type='text/html', body=html)

                await adapter.context.route('**/*', intercept)
                adapter.page = await adapter.context.new_page()
                url = (adapter.initial_url if isinstance(adapter, WebsiteForms)
                       else 'https://accounts.google.com/v3/signin/identifier')
                await adapter.page.goto(url)

            adapter.start = start
            return adapter

        self.real_broker = LoginBroker(self.bridge.origin, self.gateway.public_url, 1001,
            browser_factory=lambda provider: prepare(WebsiteBrowser(provider)),
            boundary_check=lambda: True, peer_check=lambda request: True)
        self.human = TestClient(TestServer(self.real_broker.human))
        self.control = TestClient(TestServer(self.real_broker.control))
        await self.human.start_server()
        await self.control.start_server()
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.real_broker.close)
        generic = patch('oak.website_forms.WebsiteForms', side_effect=lambda url, **options: prepare(WebsiteForms(url, **options)))
        generic.start()
        self.addCleanup(generic.stop)
        composed = patch('oak.website_forms.ComposedProviderForms',
                         side_effect=lambda provider, **options: prepare(ComposedProviderForms(provider, **options)))
        composed.start()
        self.addCleanup(composed.stop)

        async def ipc(route, data):
            self.control_payloads.append((route, dict(data)))
            response = await self.control.post(route, json=data)
            self.assertEqual(response.status, 200)
            return await response.json()

        self.bridge.ipc = ipc

    async def test_identifier_post_updates_form_on_both_native_entry_paths(self):
        for provider in ('google', 'website'):
            with self.subTest(provider=provider):
                task, identifier = await self.begin(provider, url='https://accounts.google.com/ServiceLogin')
                launch = await self.launch(identifier)
                headers = {'Origin': self.bridge.origin}
                response = await self.human.post('/api/attach', headers=headers,
                    json={'request_id': identifier, 'ticket': launch['ticket']})
                self.assertEqual(response.status, 200)
                headers['Authorization'] = 'Bearer ' + (await response.json())['lease']

                async def form():
                    response = await self.human.post('/api/form', headers=headers,
                                                     json={'request_id': identifier})
                    self.assertEqual(response.status, 200)
                    return await response.json()

                first = (await form())['form']
                self.assertEqual(len(first['fields']), 1, 'Footer language form must not hide identifier')
                self.assertEqual(first['stage'], 'identifier' if provider == 'google' else 'form')
                canary = 'private-path-canary@example.test'
                response = await self.human.post('/api/submit', headers=headers,
                    json={'request_id': identifier, 'revision': first['revision'],
                          'action': first['actions'][0]['id'],
                          'values': {first['fields'][0]['name']: canary}})
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())['accepted'])
                after = await form()
                self.assertEqual(after['state'], 'active')
                self.assertEqual([field['type'] for field in after['form']['fields']], ['password'])
                self.assertNotEqual(after['form']['revision'], first['revision'])
                self.assertEqual(self.posts.count(provider), 1, 'Submit must never be replayed')
                self.assertFalse(task.done(), 'This implementation still waits for final login outcome')

                response = await self.human.post('/api/cancel', headers=headers,
                                                 json={'request_id': identifier})
                self.assertEqual((await response.json())['state'], 'cancelled')
                await asyncio.wait_for(asyncio.shield(task), 3)
                result = self.runtime._send.await_args.args[0]
                self.assertEqual(json.loads(result['result']['contentItems'][0]['text'])['outcome'], 'cancelled')
                self.assertEqual(self.real_broker.leases[identifier].cleanup, 'confirmed')
                ordinary = ('\n'.join(self.c.db.iterdump()) + json.dumps(self.bus.replay(11))
                            + json.dumps(self.control_payloads) + json.dumps(result))
                self.assertNotIn(canary, ordinary)

    async def test_jev_choice_replans_after_post_with_same_native_request_and_private_values(self):
        for provider in ('website', 'google', 'instagram'):
            with self.subTest(provider=provider):
                await self.jev_transition(provider)

    async def jev_transition(self, provider):
        from oak.website_composer import LeaseComposer
        from oak.website_planner import OpenRouterWebsitePlanner, RESPONSE_MODEL

        self.decoy_form = True
        self.posts.clear()
        planner = OpenRouterWebsitePlanner('synthetic-local-test-key')
        inference = []

        async def choose(payload):
            inference.append(payload)
            group = next(group for group in payload['state']['groups']
                         if any(field['type'] in {'email', 'password'} for field in group['fields']))
            choices = payload['questions']['group']['criteria']
            return {'model': RESPONSE_MODEL, 'provider': 'TypeSafe', 'answers': {'group': {
                'type': 'choice', 'choice': group['id'], 'confidence': 1,
                'probabilities': {key: float(key == group['id']) for key in choices}}},
                'usage': {'cost': 0.00001}}

        planner._post = AsyncMock(side_effect=choose)
        self.real_broker.composer_factory = lambda: LeaseComposer(planner, 12)
        task, identifier = await self.begin(provider, url='https://accounts.google.com/ServiceLogin')
        launch = await self.launch(identifier)
        headers = {'Origin': self.bridge.origin}
        response = await self.human.post('/api/attach', headers=headers,
            json={'request_id': identifier, 'ticket': launch['ticket']})
        self.assertEqual(response.status, 200)
        attached = await response.json()
        self.assertEqual(attached['provider'], provider)
        self.assertEqual(attached['form_schema'], 'website-v1')
        self.assertEqual(attached['destination'], 'https://www.instagram.com' if provider == 'instagram'
                         else 'https://accounts.google.com')
        headers['Authorization'] = 'Bearer ' + attached['lease']

        async def form():
            response = await self.human.post('/api/form', headers=headers, json={'request_id': identifier})
            self.assertEqual(response.status, 200)
            return (await response.json())['form']

        first = await form()
        self.assertEqual(first['provider'], provider)
        self.assertEqual(first['form_schema'], 'website-v1')
        self.assertEqual([field['type'] for field in first['fields']], ['email'])
        self.assertEqual(len(inference), 1)
        self.assertGreaterEqual(len(inference[0]['state']['groups']), 2)
        self.assertEqual((await form())['revision'], first['revision'])
        self.assertEqual(len(inference), 1, 'Polling must not repeatedly spend API calls')
        canary = 'private-model-path-canary@example.test'
        response = await self.human.post('/api/submit', headers=headers, json={
            'request_id': identifier, 'revision': first['revision'], 'action': first['actions'][0]['id'],
            'values': {first['fields'][0]['name']: canary}})
        self.assertEqual(response.status, 200)
        after = await form()
        self.assertEqual([field['type'] for field in after['fields']], ['password'])
        self.assertNotEqual(after['revision'], first['revision'])
        self.assertEqual(len(inference), 2)
        self.assertEqual(len(self.posts), 1)
        self.assertFalse(task.done())
        response = await self.human.post('/api/cancel', headers=headers, json={'request_id': identifier})
        self.assertEqual((await response.json())['state'], 'cancelled')
        await asyncio.wait_for(asyncio.shield(task), 3)
        result = self.runtime._send.await_args.args[0]
        self.assertEqual(json.loads(result['result']['contentItems'][0]['text'])['outcome'], 'cancelled')
        self.assertEqual(self.real_broker.leases[identifier].cleanup, 'confirmed')
        ordinary = ('\n'.join(self.c.db.iterdump()) + json.dumps(self.bus.replay(11))
                    + json.dumps(self.control_payloads) + json.dumps(inference) + json.dumps(result))
        self.assertNotIn(canary, ordinary)


if __name__ == '__main__':
    unittest.main()
