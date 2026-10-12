"""The actual pinned Lit bundle under the production CSP, without live accounts."""

import copy
import asyncio
import base64
import json
import os
from pathlib import Path
import time
import unittest

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
        self.submit_status = 200
        self.page = await self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda value: self.errors.append(value.text) if value.type == 'error' else None)
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        await self.context.route('**/*', self.serve)

    async def serve(self, route):
        path = route.request.url.split('a2ui.example.test', 1)[1]
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
            await route.fulfill(content_type='text/javascript', body=artifact + '''
              import {createA2UIPanel} from '/a2ui.js';
              let sessionEpoch=1,sessionToken='synthetic-image-token';window.invalidateOwner=()=>{sessionEpoch++;ownerEpoch++;panel.reset();};
              window.createdURLs=[];window.revokedURLs=[];
              const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
              URL.createObjectURL=blob=>{const url=create(blob);createdURLs.push(url);return url;};
              URL.revokeObjectURL=url=>{revokedURLs.push(url);revoke(url);};
              window.loadArtifact=artifact;
              window.violations=[];document.addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective));
              window.ownerBusy=false;window.ownerEpoch=1;
              window.panel=createA2UIPanel({artifact:(...args)=>window.artifactOverride ? artifactOverride(...args) : artifact(...args),available:()=>true,epoch:()=>ownerEpoch,busy:()=>ownerBusy,
                setBusy:v=>{ownerBusy=v;},api:async(path,data)=>{const response=await fetch(path,
                  data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});
                  if(!response.ok){const error=new Error('rejected');error.status=response.status;throw error;}
                  return response.json();}});
              await panel.refresh();document.body.dataset.oakReady='true';''')
        elif path == '/api/a2ui':
            await route.fulfill(content_type='application/json', body=json.dumps(self.current))
        elif path == '/api/a2ui/action':
            self.actions.append(json.loads(route.request.post_data))
            await route.fulfill(status=self.submit_status, content_type='application/json',
                                body=json.dumps({'status': 'accepted'}))
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
        await self.page.wait_for_function('document.querySelector("img.a2ui-image")?.naturalWidth === 1')
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
        await self.page.wait_for_function('createdURLs.length === 2 && revokedURLs.length === 1')
        self.assertEqual(await chart.locator('polyline').count(), 1)
        self.current['surfaces'] = []
        await self.page.evaluate('panel.refresh()')
        await self.page.wait_for_function('revokedURLs.length === 2')
        self.assertEqual(await self.page.locator('oak-a2ui-surface').count(), 0)
        self.assertEqual(await self.page.evaluate('violations'), [])

    async def test_pending_image_is_aborted_on_owner_reset_without_late_url(self):
        self.image_gate = asyncio.Event()
        self.addAsyncCleanup(self.release_images)
        self.current = self.display_snapshot()
        await self.open()
        await self.page.wait_for_function('document.querySelector("oak-a2ui-image")')
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
        await self.page.wait_for_function('Boolean(window.pendingImageResolve)')
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
