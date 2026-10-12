"""Actual parent/private frontend assets; intercepted HTTP, no real account/model."""

import asyncio
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PARENT = 'https://panel.example'
PRIVATE = 'https://private.example'
REQUEST = 'a' * 32


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class SigninShellBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright

        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.calls = []
        self.snapshot = {'state': 'active', 'form': {
            'provider': 'website', 'destination': 'https://example.test',
            'current_origin': 'https://example.test', 'form_schema': 'website-v2',
            'stage': 'form', 'revision': 'step:1', 'can_complete': False,
            'fields': [{'name': 'f1', 'type': 'password', 'label': 'Access phrase', 'max_length': 1024}],
            'actions': [{'id': 'a1', 'label': 'Continue', 'field_refs': ['f1'], 'available': True}],
            'message': '',
        }}
        await self.context.route('**/*', self.intercept)
        self.page = await self.context.new_page()
        await self.page.goto(PARENT)
        await self.page.evaluate('window.panel.open(' + json.dumps(REQUEST) + ')')
        self.ui = self.page.frame_locator('iframe.signin-broker')
        await self.ui.get_by_label('Access phrase', exact=True).wait_for()

    async def intercept(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        origin = parsed.scheme + '://' + parsed.netloc
        if parsed.path.startswith('/api/'):
            data = request.post_data_json or {}
            self.calls.append((origin, parsed.path, data))
            if origin == PRIVATE:
                if parsed.path == '/api/attach':
                    value = {'lease': 'L' * 43, 'provider': 'website', 'destination': 'https://example.test', 'form_schema': 'website-v2'}
                elif parsed.path == '/api/form':
                    value = self.snapshot
                elif parsed.path in {'/api/detach', '/api/cancel'}:
                    value = {'state': 'cancelled'}
                else:
                    raise AssertionError('Unexpected private action')
            elif parsed.path.endswith('/signin'):
                value = {'provider': 'website', 'simulated': False, 'request_id': REQUEST,
                         'origin': PRIVATE, 'ticket': 'T' * 43}
            elif data.get('decision') == 'cancel':
                value = {'outcome': 'cancelled', 'delivery': 'not_required'}
            else:
                value = {'id': REQUEST, 'kind': 'sign_in', 'revision': 1,
                         'outcome': getattr(self, 'parent_outcome', 'pending'),
                         'form': {'provider': 'website', 'simulated': False, 'capability': True,
                                  'destination': 'https://example.test'}, 'broker': {'phase': 'available'}}
            await route.fulfill(status=200, content_type='application/json', body=json.dumps(value))
            return
        if origin == PARENT and parsed.path == '/':
            # Use the shipped dialog markup and stylesheet, without app startup/auth.
            html = (ROOT / 'oak/web/index.html').read_text()
            dialog = html[html.index('  <dialog id="input-request"'):html.index('  </dialog>', html.index('  <dialog id="input-request"')) + len('  </dialog>')]
            body = '<!doctype html><html lang="uk"><meta name="viewport" content="width=device-width,initial-scale=1">' \
                '<link rel="stylesheet" href="/style.css"><body><dialog id="confirmation"></dialog>' + dialog + '''
                <script type="module">
                import {createInputRequests} from '/requests.js';
                window.panel = createInputRequests({epoch:()=>1,scope:()=> 'fixture',available:()=>true,
                  changed:()=>{},api:async(path,data)=>{
                    try {
                      const r=await fetch(path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},
                        ...(data?{body:JSON.stringify(data)}:{})});
                      return await r.json();
                    } finally {
                      if(path.includes('/signin?'))window.launchesCompleted=(window.launchesCompleted||0)+1;
                    }
                  }});
                </script></body></html>'''
            await route.fulfill(status=200, content_type='text/html', body=body)
            return
        base = 'oak/login_web' if origin == PRIVATE else 'oak/web'
        name = 'index.html' if parsed.path == '/' else parsed.path.lstrip('/')
        if origin == PRIVATE and name == 'form-controls.js':
            base = 'oak/web'
        target = ROOT / base / name
        self.assertTrue(target.is_relative_to(ROOT / base))
        body = target.read_text().replace('PARENT_ORIGIN', PARENT)
        await route.fulfill(status=200, content_type='text/javascript' if name.endswith('.js') else
                            'text/css' if name.endswith('.css') else 'text/html', body=body)

    async def test_one_private_card_resizes_and_rejects_untrusted_layout_messages(self):
        await self.page.wait_for_function("document.querySelector('iframe').style.height !== ''")
        self.assertEqual(sum(path.endswith('/signin') for _, path, _ in self.calls), 1,
                         'Opening the owner-bound request attaches once without a second launch click')
        self.assertTrue(await self.page.locator('#input-request').evaluate("e=>e.classList.contains('signin-host')"))
        self.assertFalse(await self.page.locator('#input-request-destination').is_visible())
        frame = self.page.locator('iframe')
        before = await frame.evaluate('e=>e.style.height')
        await self.page.evaluate("window.dispatchEvent(new MessageEvent('message',{origin:'https://evil.example',source:document.querySelector('iframe').contentWindow,data:{type:'oak-login-layout',height:1000}}))")
        self.assertEqual(await frame.evaluate('e=>e.style.height'), before)
        await self.page.evaluate("window.dispatchEvent(new MessageEvent('message',{origin:'https://private.example',source:window,data:{type:'oak-login-layout',height:1000}}))")
        self.assertEqual(await frame.evaluate('e=>e.style.height'), before)
        await self.page.evaluate("document.querySelector('iframe').contentWindow.postMessage({type:'unrelated'},'https://private.example')")
        private_frame = self.page.frames[1]
        for data in ({'type': 'oak-login-layout', 'height': 1000, 'values': 'not-allowed'},
                     {'type': 'oak-login-layout', 'height': 5000},
                     {'type': 'oak-login-layout', 'height': '1000'}):
            await private_frame.evaluate('(data)=>parent.postMessage(data,"https://panel.example")', data)
        self.assertEqual(await frame.evaluate('e=>e.style.height'), before)
        await private_frame.evaluate('()=>parent.postMessage({type:"oak-login-layout",height:520},"https://panel.example")')
        await self.page.wait_for_function("document.querySelector('iframe').style.height === '520px'")
        old_window = await self.page.evaluate_handle("document.querySelector('iframe').contentWindow")
        await self.page.evaluate('window.panel.reset()')
        self.assertFalse(await self.page.locator('#input-request').evaluate('e=>e.open'))
        self.assertEqual(await self.page.locator('iframe').count(), 0)
        await self.page.evaluate('(old)=>window.dispatchEvent(new MessageEvent("message",{origin:"https://private.example",source:old,data:{type:"oak-login-layout",height:1000}}))', old_window)
        self.assertFalse(await self.page.locator('#input-request').evaluate("e=>e.classList.contains('signin-host')"))

    async def test_escape_cancels_once_without_transferring_private_drafts(self):
        await self.ui.get_by_label('Access phrase', exact=True).fill('LOCAL_SYNTHETIC_DRAFT_819')
        # Escape inside the protected iframe is handled by its own cancel path;
        # focus the modal host to exercise the native dialog cancellation path.
        await self.page.locator('#input-request').focus()
        await self.page.keyboard.press('Escape')
        await self.page.wait_for_function("document.querySelector('iframe') === null")
        cancels = [data for origin, _, data in self.calls if origin == PARENT and data.get('decision') == 'cancel']
        self.assertEqual(len(cancels), 1)
        self.assertNotIn('LOCAL_SYNTHETIC_DRAFT_819', json.dumps([c for c in self.calls if c[0] == PARENT]))
        self.assertFalse(await self.page.locator('#input-request').evaluate("e=>e.classList.contains('signin-host')"))

    async def test_failed_private_load_keeps_touch_cancel_and_times_out_without_retry(self):
        await self.page.evaluate('window.panel.reset()')
        await self.page.clock.install()
        await self.context.route(PRIVATE + '/', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<body>Unavailable script</body>'))
        before = sum(path.endswith('/signin') for _, path, _ in self.calls)
        await self.page.evaluate('window.panel.open(' + json.dumps(REQUEST) + ')')
        await self.page.locator('iframe').wait_for(state='attached')
        self.assertTrue(await self.page.locator('#input-request-cancel').is_visible())
        self.assertTrue(await self.page.locator('#input-request-title').is_visible())
        await self.page.clock.fast_forward(30001)
        await self.page.wait_for_function("document.querySelector('iframe') === null")
        self.assertTrue(await self.page.locator('#input-request-cancel').is_visible())
        self.assertIn('Форма не завантажилась', await self.page.locator('#input-request-status').inner_text())
        self.assertEqual(sum(path.endswith('/signin') for _, path, _ in self.calls), before + 1)
        await self.page.locator('#input-request-cancel').click()
        await self.page.wait_for_function("document.querySelector('#input-request-status').textContent.includes('скасовано')")

    async def test_long_surface_clamps_layout_and_scrolls_in_a_short_viewport(self):
        fields = [{'name': 'f' + str(i), 'type': 'text', 'label': 'Field ' + str(i),
                   'max_length': 1024} for i in range(1, 33)]
        self.snapshot['form'].update(revision='step:2', fields=fields, actions=[
            {'id': 'a' + str(i), 'label': 'Method ' + str(i), 'field_refs': [], 'available': True}
            for i in range(1, 65)])
        await self.page.set_viewport_size({'width': 320, 'height': 260})
        await self.ui.get_by_label('Field 32', exact=True).wait_for(state='attached')
        await self.page.wait_for_function("document.querySelector('iframe').style.height === '4096px'")
        bounds = await self.page.locator('#input-request').bounding_box()
        self.assertLessEqual(bounds['height'], 260)
        self.assertGreaterEqual(bounds['y'], 0)
        self.assertLessEqual(bounds['y'] + bounds['height'], 260)
        self.assertEqual(await self.ui.locator('#fields input').count(), 32)
        self.assertEqual(await self.ui.locator('#actions button').count(), 64)
        await self.ui.locator('#cancel').scroll_into_view_if_needed()
        cancel = await self.ui.locator('#cancel').bounding_box()
        self.assertGreaterEqual(cancel['y'], 0)
        self.assertLessEqual(cancel['y'] + cancel['height'], 260)

    async def deferred_launch_race(self, *, terminal_watch=False, cancel_in_flight=False, reject_launch=False):
        await self.page.evaluate('window.panel.reset()')
        await self.page.clock.install()
        started, release = asyncio.Event(), asyncio.Event()
        cancel_started, cancel_release = asyncio.Event(), asyncio.Event()
        launches = []

        async def delayed_launch(route):
            launches.append(route.request.url)
            started.set(); await release.wait()
            if reject_launch:
                await route.abort('failed')
            else:
                await route.fulfill(status=200, content_type='application/json', body=json.dumps({
                    'provider': 'website', 'simulated': False, 'request_id': REQUEST,
                    'origin': PRIVATE, 'ticket': 'T' * 43}))

        async def delayed_cancel(route):
            data = route.request.post_data_json or {}
            if data.get('decision') != 'cancel':
                await route.fallback(); return
            self.calls.append((PARENT, '/api/requests/' + REQUEST, data))
            cancel_started.set(); await cancel_release.wait()
            await route.fulfill(status=200, content_type='application/json', body=json.dumps({
                'outcome': 'cancelled', 'delivery': 'not_required'}))

        await self.context.route(PARENT + '/api/requests/' + REQUEST + '/signin?conversation=fixture', delayed_launch)
        if cancel_in_flight:
            await self.context.route(PARENT + '/api/requests/' + REQUEST + '?conversation=fixture', delayed_cancel)
        attachments = sum(origin == PRIVATE and path == '/api/attach' for origin,path,_ in self.calls)
        completed = await self.page.evaluate('window.launchesCompleted||0')
        await self.page.evaluate('window.panel.open(' + json.dumps(REQUEST) + ')')
        await asyncio.wait_for(started.wait(), 5)
        self.assertEqual(await self.page.locator('iframe').count(), 0)
        if terminal_watch:
            self.parent_outcome = 'cancelled'
            await self.page.clock.fast_forward(2001)
        else:
            await self.page.locator('#input-request-cancel').click()
            if cancel_in_flight:
                await asyncio.wait_for(cancel_started.wait(), 5)
        if not cancel_in_flight:
            await self.page.wait_for_function("document.querySelector('#input-request-status').textContent.includes('скасовано')")

        try:
            release.set()
            await self.page.wait_for_function('(count)=>window.launchesCompleted===count', arg=completed+1)
            self.assertEqual(await self.page.locator('iframe').count(), 0,
                             'A late launch cannot recreate a cancelled or cancelling credential frame')
            self.assertFalse(await self.page.locator('#input-request').evaluate("e=>e.classList.contains('signin-host')"))
            self.assertEqual(sum(origin == PRIVATE and path == '/api/attach' for origin,path,_ in self.calls), attachments)
            self.assertEqual(len(launches), 1, 'A terminal request never retries a launch')
            if cancel_in_flight:
                cancel_release.set()
                await self.page.wait_for_function("document.querySelector('#input-request-status').textContent.includes('скасовано')")
            else:
                self.assertIn('скасовано', await self.page.locator('#input-request-status').inner_text())
        finally:
            release.set(); cancel_release.set()

    async def test_cancelled_request_rejects_late_launch_without_recreating_iframe(self):
        await self.deferred_launch_race()

    async def test_cancel_intent_rejects_late_launch_before_cancel_response(self):
        await self.deferred_launch_race(cancel_in_flight=True)

    async def test_terminal_watch_rejects_late_launch_without_recreating_iframe(self):
        await self.deferred_launch_race(terminal_watch=True)

    async def test_cancelled_request_keeps_terminal_status_after_late_launch_failure(self):
        await self.deferred_launch_race(reject_launch=True)


if __name__ == '__main__':
    unittest.main()
