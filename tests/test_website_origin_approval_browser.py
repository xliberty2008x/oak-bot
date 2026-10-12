"""Actual local TLS/Chromium adapter trials; no accounts, external traffic or model."""
import asyncio
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from oak.signin_providers import RecoverableFormError
from oak.website_agent import WebsiteAgentForms
from oak.website_navigation_transport import WebsiteNavigationTransport
from oak.website_scene import CATALOG_ID, CATALOG_VERSION


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in local TLS Chromium fixtures')
class OriginApprovalBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from playwright.async_api import async_playwright
        self.root = 'https://task.example.test'
        self.identity = 'https://identity.example.test'
        self.pins = {'task.example.test': '8.8.8.8', 'identity.example.test': '1.1.1.1'}
        self.received, self.connected = [], []
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        directory = Path(self.directory.name)
        cert, key = directory / 'cert.pem', directory / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=task.example.test', '-addext',
            'subjectAltName=DNS:task.example.test,DNS:identity.example.test',
            '-keyout', str(key), '-out', str(cert)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cert, key)

        async def fixture(request):
            self.received.append((request.host, request.method, request.path,
                                  request.headers.get('Cookie', '')))
            if request.path == '/redirect':
                return web.Response(status=302, headers={'Location': self.identity + '/entry?state=PRIVATE_ROUTE_CANARY'})
            if request.path == '/entry':
                body = '<form method="post" action="/email"><label>Email<input type="email" name="email" required></label><button>Next</button></form>'
            elif request.path == '/stale-form':
                body = '<form method="post" action="/unexpected"><label>Email<input type="email" name="email" required></label><button>Next</button></form>'
            elif request.path == '/email':
                self.assertEqual((await request.post())['email'], 'synthetic@example.test')
                body = '<form method="post" action="/finish"><label>Password<input type="password" name="password" required></label><button>Sign in</button></form>'
            elif request.path == '/finish':
                self.assertEqual((await request.post())['password'], 'SYNTHETIC_PASSWORD_CANARY')
                return web.Response(status=302, headers={'Location': self.root + '/complete?code=PRIVATE_CODE_CANARY'})
            elif request.path == '/complete':
                body = '<h1>Completed fixture</h1><button>Review result</button>'
            else:
                body = '<h1>Account</h1><a href="' + self.identity + '/entry?state=PRIVATE_ROUTE_CANARY">Use identity</a>'
            headers = {'Set-Cookie': 'task_cookie=synthetic; Path=/; Secure; SameSite=Lax'} if request.path == '/' else {}
            return web.Response(text=body, content_type='text/html', headers=headers)

        app = web.Application(); app.router.add_route('*', '/{tail:.*}', fixture)
        self.runner = web.AppRunner(app, access_log=None); await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, '127.0.0.1', 0, ssl_context=tls); await site.start()
        port = site._server.sockets[0].getsockname()[1]

        async def upstream(pin, target_port):
            self.assertEqual(target_port, 443); self.assertIn(pin, self.pins.values())
            self.connected.append(pin)
            return await asyncio.open_connection('127.0.0.1', port)

        async def resolve(host, port, **options):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (self.pins[host], port))]

        def transport(authorize, **options):
            return WebsiteNavigationTransport(authorize, upstream_factory=upstream, **options)

        self.agent = WebsiteAgentForms(self.root, origin_approval=True,
            resolver=resolve, navigation_factory=transport)
        self.agent._pinned_address = self.pins['task.example.test']
        await self.agent._navigation.start()
        driver = await async_playwright().start(); self.addAsyncCleanup(driver.stop)
        browser = await driver.chromium.launch(headless=True, chromium_sandbox=True,
            proxy=self.agent._navigation.browser_proxy(), args=self.agent._navigation.browser_args())
        self.addAsyncCleanup(browser.close)
        # Self-signed controlled local TLS only; production context keeps CA checks.
        context = await browser.new_context(ignore_https_errors=True, service_workers='block')
        self.addAsyncCleanup(context.close)
        self.agent.context = context; self.agent.page = await context.new_page()
        await self.agent._configure_context()
        await context.route('**/*', self.agent._route)
        await self.agent._configure_page()
        self.addAsyncCleanup(self.agent.close)

    async def pending(self):
        await asyncio.wait_for(self.agent._navigation_waiting.wait(), 3)
        form = await asyncio.wait_for(self.agent.snapshot(), 1)
        self.assertEqual(form['origin_approval'], {'origin': self.identity})
        self.assertEqual(form['current_origin'], self.root)
        self.assertEqual(form['fields'], []); self.assertEqual(form['actions'], [])
        self.assertFalse(form['can_complete'])
        observation = await self.agent.agent_observe()
        self.assertEqual(observation['stage'], 'device_approval')
        self.assertEqual(observation['scene']['stage'], 'wait')
        self.assertNotIn(self.identity, json.dumps(observation))
        self.assertNotIn('PRIVATE_ROUTE_CANARY', json.dumps(observation) + json.dumps(form))
        self.assertNotIn(self.pins['identity.example.test'], self.connected)
        return form

    def plan(self, observation, label):
        scene = observation['scene']
        anchor = next(action for action in scene['actions'] if action['label'] == label)
        surface = min((s for s in scene['surfaces'] if anchor['ref'] in s['actions']),
                      key=lambda s: len(s['controls']) + len(s['actions']))
        fields = [f for f in scene['controls'] if f['ref'] in surface['controls']]
        return {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
            'revision': observation['revision'], 'surface_ref': surface['ref'], 'anchor': anchor['ref'],
            'fields': [{'ref': f['ref'], 'component': {'email': 'EmailInput', 'password': 'PasswordInput'}[f['type']]}
                       for f in fields],
            'actions': [{'ref': a['ref'], 'presentation': 'primary' if a == anchor else 'secondary',
                         'field_refs': [f['ref'] for f in fields if f['form'] == a['form']]
                         if a['dispatch'] == 'submit' and a['permitted'] else []}
                        for a in scene['actions'] if a['ref'] in surface['actions']], 'message_ref': None}

    async def input(self, label, value):
        observation = await self.agent.agent_observe()
        await self.agent.agent_present(observation['revision'], self.plan(observation, label))
        form = await self.agent.snapshot()
        self.assertEqual(form['destination'], self.root)
        self.assertEqual(form['current_origin'], self.identity)
        action = next(a for a in form['actions'] if a['label'] == label)
        await self.agent.submit(form['revision'], action['id'], {name: value for name in action['field_refs']})

    async def test_real_link_consent_two_private_steps_and_callback_keep_context(self):
        await self.agent.page.goto(self.root + '/', wait_until='domcontentloaded')
        context = self.agent.context
        observation = await self.agent.agent_observe()
        link = next(a for a in observation['scene']['actions'] if a['label'] == 'Use identity')
        self.assertTrue(link['permitted'])
        result = await self.agent.agent_act(observation['revision'], link['ref'])
        self.assertEqual(result['stage'], 'device_approval')
        form = await self.pending()
        await self.agent.approve_origin(form['revision'])
        with self.assertRaises(RecoverableFormError):
            await self.agent.approve_origin(form['revision'])
        await self.agent.page.wait_for_url(self.identity + '/entry?state=PRIVATE_ROUTE_CANARY')
        await self.input('Next', 'synthetic@example.test')
        await self.agent.page.wait_for_url(self.identity + '/email')
        await self.input('Sign in', 'SYNTHETIC_PASSWORD_CANARY')
        await self.agent.page.wait_for_url(self.root + '/complete?code=PRIVATE_CODE_CANARY')
        observation = await self.agent.agent_observe()
        wire = json.dumps(observation) + json.dumps(self.agent.diagnostics())
        for canary in ('PRIVATE_ROUTE_CANARY', 'PRIVATE_CODE_CANARY', 'SYNTHETIC_PASSWORD_CANARY', 'synthetic@example.test'):
            self.assertNotIn(canary, wire)
        self.assertIs(self.agent.context, context)
        self.assertEqual([(host, method, path) for host, method, path, _ in self.received], [
            ('task.example.test', 'GET', '/'), ('identity.example.test', 'GET', '/entry'),
            ('identity.example.test', 'POST', '/email'), ('identity.example.test', 'POST', '/finish'),
            ('task.example.test', 'GET', '/complete')])
        self.assertEqual(self.received[-1][3], 'task_cookie=synthetic')
        self.assertTrue(all(not item[3] for item in self.received[1:4]))
        self.assertEqual((await self.agent.snapshot())['current_origin'], self.root)
        await self.agent.retain_session(900)
        self.assertEqual(set(self.agent._navigation._proxy._pins), {'task.example.test'})
        await self.agent.page.evaluate("""() => {
          const form=document.createElement('form');form.method='POST';form.action='/retained';
          document.body.append(form);form.submit();
        }""")
        await self.agent.page.wait_for_url(self.root + '/retained')
        self.assertEqual(self.received[-1][:3], ('task.example.test', 'POST', '/retained'))
        self.assertFalse(self.agent._blocked)

    async def test_initial_redirect_returns_attachment_before_private_consent(self):
        self.agent.config['url'] = self.root + '/redirect'
        await asyncio.wait_for(self.agent._initial_navigation(), 3)
        form = await self.pending()
        self.assertFalse(self.agent._initial_document_task.done())
        await self.agent.approve_origin(form['revision'])
        await asyncio.wait_for(self.agent._initial_document_task, 3)
        self.assertEqual((await self.agent.snapshot())['current_origin'], self.identity)

    async def test_cancel_paused_redirect_closes_transport_without_target_request(self):
        self.agent.config['url'] = self.root + '/redirect'
        await asyncio.wait_for(self.agent._initial_navigation(), 3)
        await self.pending()
        started = time.monotonic(); await self.agent.close()
        self.assertLess(time.monotonic() - started, 5)
        self.assertNotIn(self.pins['identity.example.test'], self.connected)
        self.assertTrue(all(host == 'task.example.test' for host, _, _, _ in self.received))
        self.assertEqual(self.agent._navigation.diagnostics()['pending'], 0)

    async def test_definite_stale_dispatch_revokes_native_post_admission(self):
        await self.agent.page.goto(self.root + '/stale-form', wait_until='domcontentloaded')
        observation = await self.agent.agent_observe()
        await self.agent.agent_present(observation['revision'], self.plan(observation, 'Next'))
        form = await self.agent.snapshot(); action = form['actions'][0]
        evaluate = self.agent.page.evaluate
        async def mutate_before_dispatch(script, arguments=None):
            if isinstance(arguments, dict) and arguments.get('operation') == 'submit':
                await evaluate("document.querySelector('button').textContent='Changed'")
            return await evaluate(script, arguments)
        with patch.object(self.agent.page, 'evaluate', mutate_before_dispatch):
            with self.assertRaises(RecoverableFormError):
                await self.agent.submit(form['revision'], action['id'],
                    {name: 'synthetic@example.test' for name in action['field_refs']})
        self.assertIsNone(self.agent._document_post)
        await self.agent.page.evaluate("document.querySelector('form').submit()")
        for _ in range(100):
            if self.agent._blocked:
                break
            await asyncio.sleep(.01)
        self.assertTrue(self.agent._blocked)
        self.assertEqual([method for _, method, _, _ in self.received], ['GET'])


if __name__ == '__main__': unittest.main()
