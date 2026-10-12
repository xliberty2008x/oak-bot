"""The actual pinned Lit bundle under the production CSP, without live accounts."""

import copy
import asyncio
import base64
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import AsyncMock
import tempfile

from oak.bus import EventBus
from oak.controller import Controller
from oak.web import WebGateway
from oak.features import feature_manifest
from oak.a2ui import CATALOG, RENDERER, VERSION
from oak.a2ui_demo import form_messages, result_messages

ROOT = Path(__file__).resolve().parents[1]


def snapshot(messages=None, revision=1):
    components, model = {}, {}
    for message in messages or form_messages():
        if 'updateComponents' in message:
            components.update({item['id']: item for item in message['updateComponents']['components']})
        if 'updateDataModel' in message:
            value = message['updateDataModel']
            if value.get('path', '/') == '/':
                model = copy.deepcopy(value['value'])
            else:
                model[value['path'][1:]] = value['value']
    return {'protocol': VERSION, 'renderer': RENDERER, 'catalogId': CATALOG, 'busy': False,
            'surfaces': [{'id': 'oak-plan', 'revision': revision, 'consumed': False,
                          'expires': time.time() + 3600, 'components': components, 'dataModel': model}]}


class A2UIAssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_local_asset_routes_csp_and_materialized_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(AsyncMock(), ':memory:', Path(folder), AsyncMock(), config={'state_dir': folder})
            self.addCleanup(controller.db.close)
            gateway = WebGateway(controller, EventBus(controller), 'synthetic-token', [11], {})
            client = TestClient(TestServer(gateway.app))
            await client.start_server()
            self.addAsyncCleanup(client.close)
            for path, mime in (('/a2ui-theme.css', 'text/css'), ('/vendor/a2ui/runtime.js', 'text/javascript')):
                response = await client.get(path)
                self.assertEqual(response.status, 200)
                self.assertIn(mime, response.headers['Content-Type'])
                csp = response.headers['Content-Security-Policy']
                self.assertIn("style-src 'self'", csp)
                self.assertNotIn('unsafe-eval', csp)
                self.assertNotIn('unsafe-inline', csp)
                self.assertEqual(await response.read(), (ROOT / 'oak/web' / path[1:]).read_bytes())
            self.assertEqual((await client.get('/vendor/a2ui/other.js')).status, 404)
            self.assertEqual(len(feature_manifest(ROOT)['code_digest']), 64)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in intercepted Chromium fixtures')
class A2UIRendererTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(viewport={'width': 390, 'height': 844},
                                                      is_mobile=True, has_touch=True)
        self.addAsyncCleanup(self.context.close)
        self.current, self.actions, self.requests, self.errors = snapshot(), [], [], []
        self.image_requests, self.image_gate = [], None
        self.snapshot_gate = self.action_gate = None
        self.scoped_requests = []
        self.submit_result = 'accepted'
        self.submit_status = 200
        self.page = await self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda value: self.errors.append(value.text) if value.type == 'error' else None)
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        await self.context.route('**/*', self.serve)

    async def serve(self, route):
        path = route.request.url.split('a2ui.example.test', 1)[1].split('?', 1)[0]
        self.requests.append(path)
        headers = {'Content-Security-Policy': "default-src 'self'; script-src 'self' https://telegram.org; "
                   "connect-src 'self'; img-src 'self' data: blob:; media-src 'self'; style-src 'self'; "
                   "object-src 'none'; base-uri 'none'; frame-src 'none'"}
        if path == '/':
            await route.fulfill(content_type='text/html', headers=headers, body='''<!doctype html>
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/a2ui-theme.css">
              <main><section id="a2ui-panel" hidden><p id="a2ui-status" role="status"></p>
              <div id="a2ui-surfaces"></div></section></main><script type="module" src="/fixture.js"></script>''')
        elif path == '/fixture.js':
            app = (ROOT / 'oak/web/app.js').read_text()
            artifact = app[app.index('async function artifact('):app.index('\nasync function dynamicPanel()')]
            api = app[app.index('async function api('):app.index('\nfunction updateActions()')]
            await route.fulfill(content_type='text/javascript', body=artifact + api + '''
              import {createA2UIPanel} from '/a2ui.js';
              let sessionEpoch=1,sessionToken='synthetic-image-token',selectedSession='topic:42';
              window.changeScope=(topic,token)=>{sessionEpoch++;ownerEpoch++;selectedSession=topic;sessionToken=token;ownerBusy=false;panel.reset();};
              window.invalidateOwner=()=>changeScope(selectedSession,sessionToken);
              const requireLogin=()=>invalidateOwner();
              window.pollTimers=new Set();const interval=setInterval,clear=clearInterval;
              window.setInterval=(fn,ms)=>{const id=interval(fn,ms);pollTimers.add(id);return id;};
              window.clearInterval=id=>{pollTimers.delete(id);clear(id);};
              window.createdURLs=[];window.revokedURLs=[];
              const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
              URL.createObjectURL=blob=>{const url=create(blob);createdURLs.push(url);return url;};
              URL.revokeObjectURL=url=>{revokedURLs.push(url);revoke(url);};
              window.loadArtifact=artifact;
              window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective));
              window.ownerBusy=false;window.ownerEpoch=1;
              window.panel=createA2UIPanel({artifact:(...args)=>window.artifactOverride ? artifactOverride(...args) : artifact(...args),available:()=>true,epoch:()=>ownerEpoch,busy:()=>ownerBusy,
                setBusy:v=>{ownerBusy=v;},api});
              await panel.refresh();document.body.dataset.oakReady='true';''')
        elif path == '/api/a2ui':
            self.scoped_requests.append((route.request.url, route.request.headers.get('authorization')))
            body = json.dumps(self.current)
            gate, self.snapshot_gate = self.snapshot_gate, None
            if gate:
                await gate.wait()
            await route.fulfill(content_type='application/json', body=body)
        elif path == '/api/a2ui/action':
            self.scoped_requests.append((route.request.url, route.request.headers.get('authorization')))
            self.actions.append(json.loads(route.request.post_data))
            gate, self.action_gate = self.action_gate, None
            if gate:
                await gate.wait()
            await route.fulfill(status=self.submit_status, content_type='application/json',
                                body=json.dumps({'status': self.submit_result}))
        elif path.startswith('/api/artifacts/'):
            self.image_requests.append({'path': path, 'headers': route.request.headers})
            if self.image_gate:
                await self.image_gate.wait()
            if self.page.is_closed():
                return
            await route.fulfill(content_type='image/png', body=base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK1sAAAAASUVORK5CYII='))
        elif path in {'/style.css', '/a2ui-theme.css', '/a2ui.js', '/form-controls.js', '/vendor/a2ui/runtime.js'}:
            await route.fulfill(content_type='text/css' if path.endswith('.css') else 'text/javascript',
                                body=(ROOT / 'oak/web' / path[1:]).read_text())
        else:
            await route.abort()
            self.fail('Unexpected browser request')

    async def open(self):
        await self.page.goto('https://a2ui.example.test/')
        await self.page.locator('body[data-oak-ready=true]').wait_for(state='attached')

    async def transition(self, event):
        # Deterministic race coverage; the separate navigation test uses trusted events.
        await self.page.evaluate('(type)=>dispatchEvent(new PageTransitionEvent(type,{persisted:true}))', event)

    async def wait_js(self, expression):
        # Use the driver's evaluate boundary, not wait_for_function's nested eval,
        # so the page retains its production no-unsafe-eval CSP in current Chromium.
        for _ in range(200):
            if await self.page.evaluate(expression):
                return
            await asyncio.sleep(.01)
        self.fail(f'Browser condition did not become true: {expression}')

    async def wait_requests(self, path, count):
        for _ in range(200):
            if self.requests.count(path) >= count:
                return
            await asyncio.sleep(.01)
        self.fail(f'{path}: expected {count} requests, saw {self.requests.count(path)}')

    async def test_suspend_resume_retains_draft_and_one_poll_timer(self):
        await self.page.clock.install()
        await self.open()
        topic = self.page.get_by_label('Тема плану')
        button = self.page.get_by_role('button', name='Скласти план')
        await topic.fill('Чернетка після повернення')
        for visibility_only in (False, True, False):
            if visibility_only:
                await self.page.evaluate('Object.defineProperty(document,"hidden",{configurable:true,value:true});document.dispatchEvent(new Event("visibilitychange"))')
            else:
                await self.transition('pagehide')
            self.assertTrue(await self.page.locator('#a2ui-panel').is_visible())
            count = self.requests.count('/api/a2ui')
            self.assertEqual(await self.page.evaluate('pollTimers.size'), 0)
            await button.dispatch_event('click')
            await self.page.clock.fast_forward(6000)
            self.assertEqual(self.requests.count('/api/a2ui'), count)
            self.assertEqual(self.actions, [])
            gate = self.snapshot_gate = asyncio.Event()
            self.addAsyncCleanup(self.release_gate, gate)
            if visibility_only:
                await self.page.evaluate('delete document.hidden;document.dispatchEvent(new Event("visibilitychange"))')
            else:
                await self.transition('pageshow')
            await self.wait_requests('/api/a2ui', count + 1)
            await self.transition('pageshow')
            self.assertEqual(await self.page.evaluate('pollTimers.size'), 1)
            self.assertTrue(await button.is_disabled())
            await button.dispatch_event('click')
            self.assertEqual(self.actions, [])
            gate.set()
            await self.wait_js('!document.querySelector("button").disabled')
            self.assertEqual(await topic.input_value(), 'Чернетка після повернення')
            count = self.requests.count('/api/a2ui')
            await self.page.clock.fast_forward(3000)
            await self.wait_requests('/api/a2ui', count + 1)
            await asyncio.sleep(.1)
            self.assertEqual(self.requests.count('/api/a2ui'), count + 1)
        await button.click()
        await self.wait_js('!ownerBusy')
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(self.actions[0]['inputs']['/form/topic'], 'Чернетка після повернення')
        self.assertTrue(all('conversation=topic%3A42' in url and auth == 'Bearer synthetic-image-token'
                            for url, auth in self.scoped_requests))

    async def release_gate(self, gate):
        gate.set()

    async def test_resume_rejects_snapshot_started_before_hide(self):
        await self.open()
        gate = self.snapshot_gate = asyncio.Event()
        self.addAsyncCleanup(self.release_gate, gate)
        count = self.requests.count('/api/a2ui')
        await self.page.evaluate('void panel.refresh()')
        await self.wait_requests('/api/a2ui', count + 1)
        await self.transition('pagehide')
        self.current = snapshot(revision=2)
        self.current['surfaces'][0]['dataModel']['form']['topic'] = 'Нова підказка'
        await self.transition('pageshow')
        await self.wait_js('document.querySelector("input")?.value === "Нова підказка"')
        gate.set()
        await self.page.evaluate('new Promise(resolve=>setTimeout(resolve,100))')
        self.assertEqual(await self.page.get_by_label('Тема плану').input_value(), 'Нова підказка')

    async def test_pending_uncertain_action_survives_resume_without_replay_or_busy_leak(self):
        await self.open()
        gate = self.action_gate = asyncio.Event()
        self.addAsyncCleanup(self.release_gate, gate)
        self.submit_result = 'unknown'
        button = self.page.get_by_role('button', name='Скласти план')
        await button.click()
        await self.wait_requests('/api/a2ui/action', 1)
        await self.transition('pagehide')
        await self.transition('pageshow')
        self.assertTrue(await self.page.evaluate('ownerBusy'))
        await button.dispatch_event('click')
        gate.set()
        await self.wait_js('!ownerBusy')
        await self.page.evaluate('panel.refresh()')
        self.assertTrue(await button.is_disabled())
        await self.transition('pagehide')
        await self.transition('pageshow')
        await button.dispatch_event('click')
        self.assertEqual(len(self.actions), 1)
        self.assertIn('Результат передавання невідомий', await self.page.locator('#a2ui-status').inner_text())

    async def test_owner_topic_reset_rejects_old_action_and_detached_renderer(self):
        await self.open()
        gate = self.action_gate = asyncio.Event()
        self.addAsyncCleanup(self.release_gate, gate)
        # Retain an action already delivered by the component, past its disabled
        # DOM gate; a queued callback must not bind to a new same-ID surface.
        await self.page.evaluate('()=>{window.oldAction=document.querySelector("oak-a2ui-button").controller.props.action;}')
        await self.page.get_by_role('button', name='Скласти план').click()
        await self.wait_requests('/api/a2ui/action', 1)
        await self.transition('pagehide')
        await self.page.evaluate('changeScope("topic:77","synthetic-new-owner")')
        self.current = snapshot(revision=2)
        await self.transition('pageshow')
        await self.page.get_by_role('button', name='Скласти план').wait_for()
        await self.page.evaluate('ownerBusy=true')
        gate.set()
        await self.page.evaluate('new Promise(resolve=>setTimeout(resolve,100))')
        self.assertTrue(await self.page.evaluate('ownerBusy'))
        await self.page.evaluate('ownerBusy=false;oldAction()')
        await asyncio.sleep(.1)
        self.assertEqual(len(self.actions), 1)
        await self.page.evaluate('panel.disable()')
        await self.page.get_by_role('button', name='Скласти план').click()
        await self.wait_requests('/api/a2ui/action', 2)
        action_requests = [(url, auth) for url, auth in self.scoped_requests if '/action?' in url]
        self.assertEqual(action_requests[0][1], 'Bearer synthetic-image-token')
        self.assertIn('conversation=topic%3A42', action_requests[0][0])
        self.assertEqual(action_requests[1][1], 'Bearer synthetic-new-owner')
        self.assertIn('conversation=topic%3A77', action_requests[1][0])

    async def test_resume_revalidates_consumed_expired_and_removed_cards(self):
        for change in ('consumed', 'expired', 'removed'):
            with self.subTest(change=change):
                self.current = snapshot()
                await self.open()
                await self.transition('pagehide')
                if change == 'consumed': self.current['surfaces'][0]['consumed'] = True
                elif change == 'expired': self.current['surfaces'][0]['expires'] = time.time() - 1
                else: self.current['surfaces'] = []
                await self.transition('pageshow')
                await self.page.evaluate('new Promise(resolve=>setTimeout(resolve,100))')
                if change == 'removed':
                    self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
                else:
                    button = self.page.get_by_role('button', name='Скласти план')
                    self.assertTrue(await button.is_disabled())
                    await button.dispatch_event('click')
                self.assertEqual(self.actions, [])

    async def test_failed_resume_snapshot_keeps_actions_disabled_until_recovery(self):
        await self.open()
        await self.transition('pagehide')
        self.current['renderer'] = 'unsupported-renderer'
        await self.transition('pageshow')
        await self.page.get_by_text('Форми недоступні.', exact=False).wait_for()
        button = self.page.get_by_role('button', name='Скласти план')
        self.assertTrue(await button.is_disabled())
        await button.dispatch_event('click')
        self.assertEqual(self.actions, [])
        self.current = snapshot()
        await self.page.evaluate('panel.refresh()')
        self.assertFalse(await button.is_disabled())

    async def test_real_navigation_back_restores_bfcache_document_and_polling(self):
        # Network interception disables BFCache eligibility in Chromium. Serve the
        # same synthetic fixture over loopback, with real assets and no interception.
        await self.context.close()
        await self.browser.close()
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True,
            channel='chromium', ignore_default_args=['--disable-back-forward-cache'])
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(viewport={'width': 390, 'height': 844})
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

        async def handle(request):
            if request.path == '/away':
                return web.Response(text='<title>Other page</title><p>Back navigation fixture</p>', content_type='text/html')
            if request.path == '/favicon.ico':
                return web.Response(status=204)
            response = None

            async def fulfill(**kwargs):
                nonlocal response
                if request.path == '/':
                    kwargs['body'] += '<a href="/away">Leave card</a>'
                elif request.path == '/fixture.js':
                    kwargs['body'] += '''
                      window.documentIdentity=crypto.randomUUID();window.lifecycleEvents=[];
                      for(const name of ['pagehide','pageshow','visibilitychange'])
                        addEventListener(name,e=>lifecycleEvents.push({type:e.type,trusted:e.isTrusted,
                          persisted:e.persisted,hidden:document.hidden}));'''
                response = web.Response(**kwargs)

            await self.serve(SimpleNamespace(request=SimpleNamespace(
                url='https://a2ui.example.test' + request.path_qs,
                headers={key.lower(): value for key, value in request.headers.items()}, post_data=await request.text()), fulfill=fulfill))
            return response

        app = web.Application()
        app.router.add_route('*', '/{path:.*}', handle)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        await self.page.goto(str(server.make_url('/')))
        await self.wait_js('Boolean(window.documentIdentity)')
        identity = await self.page.evaluate('documentIdentity')
        await self.page.get_by_label('Тема плану').fill('Збережена браузером чернетка')
        for _ in range(2):
            await self.page.get_by_role('link', name='Leave card').click()
            await self.page.wait_for_url('**/away')
            count = self.requests.count('/api/a2ui')
            await self.page.go_back(wait_until='commit')
            await self.page.get_by_label('Тема плану').wait_for()
            self.assertEqual(await self.page.evaluate('documentIdentity'), identity, 'Must restore the cached document, not reload')
            await self.wait_requests('/api/a2ui', count + 1)
            self.assertEqual(await self.page.get_by_label('Тема плану').input_value(), 'Збережена браузером чернетка')
            self.assertEqual(await self.page.evaluate('pollTimers.size'), 1)
        events = await self.page.evaluate('lifecycleEvents')
        for name in ('pagehide', 'pageshow'):
            self.assertEqual(sum(e['type'] == name and e['trusted'] and e.get('persisted') for e in events), 2)
        self.assertTrue(any(e['type'] == 'visibilitychange' and e['trusted'] and e['hidden'] for e in events))
        count = self.requests.count('/api/a2ui')
        # Real wall time proves the restored interval fires after the immediate read.
        await asyncio.sleep(3.2)
        self.assertEqual(self.requests.count('/api/a2ui'), count + 1)
        await self.page.get_by_role('button', name='Скласти план').click()
        await self.wait_requests('/api/a2ui/action', 1)
        self.assertEqual(self.actions[0]['inputs']['/form/topic'], 'Збережена браузером чернетка')
        self.assertTrue(all('conversation=topic%3A42' in url and auth == 'Bearer synthetic-image-token'
                            for url, auth in self.scoped_requests))

    async def test_canonical_bindings_full_ui_incremental_update_and_drafts_under_csp(self):
        await self.open()
        topic, pace = self.page.get_by_label('Тема плану'), self.page.get_by_label('Темп')
        self.assertEqual(await topic.input_value(), 'Мій тиждень')
        self.assertEqual(await pace.input_value(), 'gentle')
        await topic.fill('Синтетичний план')
        await pace.select_option('focused')
        await topic.focus()
        await self.page.evaluate('panel.refresh()')
        self.assertEqual(await topic.input_value(), 'Синтетичний план')
        self.assertTrue(await topic.evaluate('node => node === document.activeElement'))
        await self.page.get_by_role('button', name='Скласти план').click()
        await self.page.get_by_text('Відповідь передано. Oak готує оновлення.', exact=True).wait_for()
        self.assertEqual(len(self.actions), 1)
        action = self.actions[0]
        self.assertEqual(action['inputs'], {'/form/topic': 'Синтетичний план', '/form/pace': ['focused']})
        self.assertEqual(action['message']['action']['context'], {'topic': 'Синтетичний план', 'pace': ['focused']})
        self.assertEqual(action['message']['action']['sourceComponentId'], 'submit')
        self.current = snapshot(form_messages() + result_messages({'topic': 'Синтетичний план', 'pace': ['focused']}), 2)
        await self.page.evaluate('panel.refresh()')
        await self.page.get_by_text('План готовий', exact=True).wait_for()
        self.assertEqual(await self.page.locator('input,select').count(), 0)
        self.assertIn('Синтетичний план · зосереджений темп.', await self.page.locator('main').inner_text())
        self.assertEqual(await self.page.evaluate('violations'), [])
        self.assertEqual(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)
        self.assertEqual(await self.page.get_by_role('button', name='Змінити вибір').evaluate('node=>getComputedStyle(node).color'), 'rgb(236, 245, 240)')

    async def test_required_cancel_duplicate_busy_expiry_and_reset(self):
        await self.open()
        await self.page.get_by_label('Тема плану').fill('')
        await self.page.get_by_role('button', name='Скласти план').click()
        self.assertEqual(self.actions, [])
        await self.page.get_by_role('button', name='Скасувати', exact=True).click()
        await self.page.get_by_text('Відповідь передано. Oak готує оновлення.', exact=True).wait_for()
        self.assertEqual(len(self.actions), 1)
        await self.page.get_by_role('button', name='Скасувати', exact=True).dispatch_event('click')
        self.assertEqual(len(self.actions), 1)
        self.current = snapshot(revision=2)
        await self.page.evaluate('panel.refresh()')
        self.current['busy'] = True
        await self.page.evaluate('panel.refresh()')
        self.assertTrue(await self.page.get_by_label('Тема плану').is_disabled())
        self.current['busy'] = False
        self.current['surfaces'][0]['expires'] = time.time() - 1
        await self.page.evaluate('panel.refresh()')
        self.assertTrue(await self.page.get_by_role('button', name='Скласти план').is_disabled())
        await self.page.evaluate('panel.reset()')
        self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
        self.assertTrue(await self.page.locator('#a2ui-panel').is_hidden())

    async def test_unknown_components_functions_and_properties_fail_closed(self):
        for change in ('unknown', 'function', 'style', 'metadata', 'accessibility', 'cycle', 'depth', 'limits'):
            with self.subTest(change=change):
                self.current = snapshot()
                components = self.current['surfaces'][0]['components']
                if change == 'unknown': components['title']['component'] = 'UnknownWidget'
                elif change == 'function': components['title']['text'] = {'call': 'openUrl', 'args': {'url': 'https://example.test'}}
                elif change == 'style': components['title']['style'] = {'color': 'red'}
                elif change in {'metadata', 'accessibility'}: components['title'][change] = {'label': 'Unexpected'}
                elif change == 'cycle': components['root']['child'] = 'root'
                elif change == 'depth':
                    components.clear()
                    components.update({('root' if i == 0 else f'deep{i}'): {'id': ('root' if i == 0 else f'deep{i}'),
                        'component': 'Card', 'child': f'deep{i+1}'} for i in range(18)})
                    components['deep18'] = {'id': 'deep18', 'component': 'Text', 'text': 'Deep'}
                else:
                    components.update({f'extra{i}': {'id': f'extra{i}', 'component': 'Text', 'text': 'Extra'} for i in range(65)})
                await self.open()
                self.assertEqual(await self.page.locator('button,input,select').count(), 0)
                self.assertIn('Форми недоступні', await self.page.locator('#a2ui-status').inner_text())
                self.assertEqual(self.actions, [])

    async def test_partial_surface_delete_and_literal_text(self):
        self.current['surfaces'][0]['components'] = {'root': {'id': 'root', 'component': 'Card', 'child': 'pending'}}
        self.current['surfaces'][0]['dataModel'] = {}
        await self.open()
        self.assertEqual(await self.page.locator('button,input,select').count(), 0)
        literal = '<script>window.changed=true</script><img src=x onerror=alert(1)>'
        self.current['surfaces'][0]['components']['pending'] = {'id': 'pending', 'component': 'Text', 'text': literal}
        self.current['surfaces'][0]['revision'] = 2
        await self.page.evaluate('panel.refresh()')
        await self.page.get_by_text(literal, exact=True).wait_for()
        self.assertEqual(await self.page.locator('oak-a2ui-surface script,oak-a2ui-surface img').count(), 0)
        self.assertIsNone(await self.page.evaluate('window.changed'))
        self.current['surfaces'] = []
        await self.page.evaluate('panel.refresh()')
        self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
        self.assertEqual(await self.page.evaluate('violations'), [])

    def display_snapshot(self, revision=1, image='a' * 32):
        result = snapshot(revision=revision)
        result['surfaces'][0]['components'] = {c['id']: c for c in [
            {'id': 'root', 'component': 'Card', 'child': 'body'},
            {'id': 'body', 'component': 'Column', 'children': ['title', 'badge', 'metrics', 'divider', 'progress', 'table', 'chart', 'image']},
            {'id': 'title', 'component': 'Text', 'variant': 'heading', 'text': 'Огляд тижня'},
            {'id': 'badge', 'component': 'Badge', 'tone': 'success', 'text': {'path': '/badge'}},
            {'id': 'metrics', 'component': 'Row', 'children': ['metric', 'other']},
            {'id': 'metric', 'component': 'Metric', 'label': 'Баланс', 'value': {'path': '/balance'}, 'hint': 'За цей тиждень'},
            {'id': 'other', 'component': 'Metric', 'label': 'Завершено', 'value': '12'},
            {'id': 'divider', 'component': 'Divider'},
            {'id': 'progress', 'component': 'Progress', 'label': 'Розподіл', 'segments': [{'label': 'Готово', 'value': 3}, {'label': 'У роботі', 'value': 1}]},
            {'id': 'table', 'component': 'Table', 'caption': 'Результати', 'columns': ['Період', 'Зміна'], 'rows': [['Пн', '-2'], ['Вт', '4']]},
            {'id': 'chart', 'component': 'Chart', 'kind': 'bar', 'title': 'Зміна за днями', 'labels': ['Пн', 'Вт'], 'unit': '₴', 'series': [{'label': 'Баланс', 'values': [-2, 4]}]},
            {'id': 'image', 'component': 'Image', 'alt': 'Синтетична ілюстрація', 'artifact': image},
        ]}
        result['surfaces'][0]['dataModel'] = {'badge': 'Оновлено', 'balance': '240 ₴'}
        return result

    async def test_display_catalogue_bound_values_negative_chart_and_image_lifecycle(self):
        self.current = self.display_snapshot()
        await self.open()
        image = self.page.get_by_role('img', name='Синтетична ілюстрація', exact=True)
        await image.wait_for()
        await self.wait_js('document.querySelector("img.a2ui-image")?.naturalWidth === 1')
        self.assertEqual(await self.page.get_by_text('240 ₴', exact=True).count(), 1)
        self.assertEqual(await self.page.get_by_text('Оновлено', exact=True).count(), 1)
        self.assertEqual(await self.page.get_by_role('table').count(), 1)
        self.assertEqual(await self.page.get_by_role('columnheader').count(), 2)
        chart = self.page.locator('oak-a2ui-chart svg')
        self.assertIn('Пн -2 ₴', await chart.get_attribute('aria-label'))
        geometry = await chart.evaluate('node=>({zero:+node.querySelector("line").getAttribute("y1"),bars:[...node.querySelectorAll("rect")].map(r=>({y:+r.getAttribute("y"),height:+r.getAttribute("height")}))})')
        self.assertAlmostEqual(geometry['bars'][0]['y'], geometry['zero'])
        self.assertGreater(geometry['bars'][0]['height'], 0)
        self.assertLess(geometry['bars'][1]['y'], geometry['zero'])
        self.assertEqual(self.image_requests[0]['headers'].get('authorization'), 'Bearer synthetic-image-token')
        self.assertTrue((await image.get_attribute('src')).startswith('blob:'))
        self.assertEqual(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)
        await self.page.evaluate('panel.refresh()')
        self.assertEqual(len(self.image_requests), 1)
        self.current = self.display_snapshot(revision=2, image='b' * 32)
        self.current['surfaces'][0]['dataModel']['balance'] = '280 ₴'
        self.current['surfaces'][0]['components']['chart']['kind'] = 'line'
        await self.page.evaluate('panel.refresh()')
        await self.page.get_by_text('280 ₴', exact=True).wait_for()
        await self.wait_js('createdURLs.length === 2 && revokedURLs.length === 1')
        self.assertEqual(await chart.locator('polyline').count(), 1)
        self.current['surfaces'] = []
        await self.page.evaluate('panel.refresh()')
        await self.wait_js('revokedURLs.length === 2')
        self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
        self.assertEqual(await self.page.evaluate('violations'), [])

    async def test_pending_image_is_aborted_on_owner_reset_without_late_url(self):
        self.image_gate = asyncio.Event()
        self.addAsyncCleanup(self.release_images)
        self.current = self.display_snapshot()
        await self.open()
        await self.wait_js('document.querySelector("oak-a2ui-image")')
        for _ in range(100):
            if self.image_requests:
                break
            await asyncio.sleep(.01)
        self.assertEqual(len(self.image_requests), 1)
        await self.page.evaluate('invalidateOwner()')
        self.image_gate.set()
        await self.page.evaluate('new Promise(resolve=>setTimeout(resolve,100))')
        self.assertEqual(await self.page.evaluate('createdURLs'), [])
        self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
        self.assertEqual(await self.page.evaluate('violations'), [])

    async def release_images(self):
        if self.image_gate:
            self.image_gate.set()

    async def test_removed_image_ignores_loader_that_resolves_after_abort(self):
        await self.open()
        await self.page.evaluate('''()=>{window.artifactOverride=(id,{signal})=>new Promise(resolve=>{
          window.pendingImageResolve=resolve;window.pendingImageSignal=signal;
        });}''')
        self.current = self.display_snapshot(revision=2)
        await self.page.evaluate('panel.refresh()')
        await self.wait_js('Boolean(window.pendingImageResolve)')
        await self.page.evaluate('invalidateOwner()')
        self.assertTrue(await self.page.evaluate('pendingImageSignal.aborted'))
        await self.page.evaluate('''async()=>{
          pendingImageResolve(new Blob([new Uint8Array([1])],{type:'image/png'}));
          await new Promise(resolve=>setTimeout(resolve,50));
        }''')
        self.assertEqual(await self.page.evaluate('createdURLs'), [])
        self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)

    async def test_new_display_constraints_fail_before_artifact_fetch(self):
        for change in ('path', 'invalid_artifact', 'too_many_images', 'ragged_table', 'mismatched_chart', 'huge_number', 'zero_progress'):
            with self.subTest(change=change):
                self.current = self.display_snapshot()
                components = self.current['surfaces'][0]['components']
                if change == 'path': components['image']['path'] = '/tmp/private.png'
                elif change == 'invalid_artifact': components['image']['artifact'] = 'https://example.test/image.png'
                elif change == 'too_many_images':
                    for i in range(8):
                        components[f'image{i}'] = {'id': f'image{i}', 'component': 'Image', 'alt': '', 'artifact': 'b' * 32}
                        components['body']['children'].append(f'image{i}')
                elif change == 'ragged_table': components['table']['rows'][0].pop()
                elif change == 'mismatched_chart': components['chart']['series'][0]['values'].pop()
                elif change == 'huge_number': components['chart']['series'][0]['values'][0] = 1e16
                else: components['progress']['segments'] = [{'label': 'Порожньо', 'value': 0}]
                await self.open()
                self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
                self.assertEqual(self.image_requests, [])
                self.assertIn('Форми недоступні', await self.page.locator('#a2ui-status').inner_text())

    async def test_artifact_stream_limits_type_and_post_body_owner_guard(self):
        await self.open()
        result = await self.page.evaluate('''async()=>{
          const real=fetch,results=[];let cancelled=0;
          const run=async(factory,changeOwner=false)=>{fetch=async()=>factory();
            try{const p=loadArtifact('a'.repeat(32));if(changeOwner)queueMicrotask(()=>invalidateOwner());await p;results.push('accepted');}
            catch{results.push('rejected');}};
          await run(()=>new Response(new Uint8Array(1),{headers:{'Content-Type':'image/svg+xml'}}));
          await run(()=>new Response(new Uint8Array(1),{headers:{'Content-Type':'image/png','Content-Length':'10485761'}}));
          await run(()=>new Response(new ReadableStream({pull(c){c.enqueue(new Uint8Array(6*1024*1024));},cancel(){cancelled++;}}),{headers:{'Content-Type':'image/png'}}));
          await run(()=>new Response(new ReadableStream({pull(c){queueMicrotask(()=>{invalidateOwner();c.enqueue(new Uint8Array(1));c.close();});},cancel(){cancelled++;}}),{headers:{'Content-Type':'image/png'}}));
          fetch=real;return {results,cancelled};
        }''')
        self.assertEqual(result['results'], ['rejected'] * 4)
        self.assertGreaterEqual(result['cancelled'], 1)
