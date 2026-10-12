"""Held-out generic DOM → mocked Decisions → actual private renderer loop.

All browser requests are intercepted. The tiny HTTP shim substitutes only the
broker transport; this does not test production binding, real Jev judgment,
Telegram or a live provider. No runtime file is changed for this novel site.
"""

import asyncio
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from oak.signin_providers import RecoverableFormError
from oak.website_composer import LeaseComposer
from oak.website_forms import WebsiteForms
from oak.website_planner import OpenRouterWebsitePlanner, RESPONSE_MODEL


ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://atrium-id.example'
HUMAN = 'https://heldout-private.example'
PARENT = 'https://heldout-oak.example'
PASSWORD = 'heldout-phrase-canary-574'
OTP = '847293'
HANDLE = 'heldout-member-canary'

ENTRY = '''<form method="post"><input type="search" aria-label="Find archive">
<button>Search</button></form><button onclick="location.href='/member'">Use email</button>'''
MEMBER = '''<script>
window.attempts=0;
function showMember(error) {
 document.body.innerHTML=`<form method="post" id="member">
  <label>Access phrase<input name="phrase" type="password" autocomplete="current-password"></label>
  <label>Membership handle<input name="handle" autocomplete="username"></label>
  <label>This is my own device<input name="device" type="checkbox"></label>
  ${error ? '<p role="alert">Choose a verification route.</p><label>Verification route<select name="route"><option value="mail">Email letter</option><option value="code">One-time code</option></select></label>' : ''}
  <button>Unlock workspace</button></form>
  <form method="post"><input type="search" aria-label="Index lookup"><button>Search</button></form>`;
 document.querySelector('#member').addEventListener('submit', e=>{
  e.preventDefault(); window.attempts++;
  if (!document.querySelector('select')) { showMember(true); return; }
  const selected=document.querySelector('select').value;
  if (selected==='code') {
   fetch('/session',{method:'POST',body:new FormData(e.target)}).then(()=>location.href='/verify');
  } else { document.body.innerHTML='<p>Check your mailbox.</p>'; }
 });
}
</script><body><script>showMember(false);</script></body>'''
VERIFY = '''<form method="post" action="/complete">
<label>Rotating proof<input autocomplete="one-time-code" name="proof"></label>
<input readonly value="Membership verification"><button>Finish entry</button></form>'''


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in fully intercepted Chromium fixture')
class HeldOutWebsiteLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_novel_flow_uses_existing_private_widgets_without_provider_adapter(self):
        from playwright.async_api import async_playwright

        driver = await async_playwright().start()
        self.addAsyncCleanup(driver.stop)
        chromium = await driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(chromium.close)
        context = await chromium.new_context(service_workers='block')
        self.addAsyncCleanup(context.close)
        calls, posts, forms, submissions = [], [], [], []
        completed = False
        planner = OpenRouterWebsitePlanner('synthetic-local-test-key')

        async def choose(payload):
            calls.append(payload)
            groups = payload['state']['groups']
            chosen = next((g for g in groups if any(f['type'] in {'password', 'otp'} for f in g['fields'])), None)
            if chosen is None:
                chosen = next(g for g in groups if any(a.get('intent') == 'use_email' for a in g['actions']))
            return {'model': RESPONSE_MODEL, 'answers': {'group': {
                'type': 'choice', 'choice': chosen['id'], 'confidence': 1,
                'probabilities': {key: float(key == chosen['id'])
                                  for key in payload['questions']['group']['criteria']}}}}

        planner._post = AsyncMock(side_effect=choose)
        adapter = WebsiteForms(SITE + '/access', composer=LeaseComposer(planner, 12))
        adapter.context = context
        self.assertIsNone(adapter._known_provider)
        self.assertIsNone(adapter._resources)

        async def intercept(route):
            nonlocal completed
            request = route.request
            parsed = urlsplit(request.url)
            origin = parsed.scheme + '://' + parsed.netloc
            if origin == SITE:
                if request.method == 'POST':
                    posts.append(parsed.path)  # Never retain request bodies.
                html = {'/access': ENTRY, '/member': MEMBER, '/session': 'accepted',
                        '/verify': VERIFY, '/complete': '<p>Workspace is ready.</p>'}.get(parsed.path)
                self.assertIsNotNone(html, 'Unexpected fixture navigation')
                await route.fulfill(status=200, content_type='text/html', body=html)
                return
            if origin == PARENT:
                html = '<script>window.receipts=[];addEventListener("message",e=>{receipts.push(e.data);' \
                    'if(e.data.type==="oak-login-ready")e.source.postMessage(' \
                    + json.dumps({'type': 'oak-login-attach', 'request_id': 'a' * 32, 'ticket': 'T' * 43}) \
                    + ',' + json.dumps(HUMAN) + ');});</script><iframe src="' + HUMAN + '/"></iframe>'
                await route.fulfill(status=200, content_type='text/html', body=html)
                return
            self.assertEqual(origin, HUMAN, 'Every external request must remain intercepted')
            if parsed.path.startswith('/api/'):
                data = request.post_data_json
                self.assertEqual(data['request_id'], 'a' * 32)
                if parsed.path == '/api/attach':
                    result = {'lease': 'L' * 43, 'provider': 'website',
                              'destination': SITE, 'form_schema': 'website-v1'}
                elif parsed.path == '/api/form':
                    if completed:
                        result = {'state': 'owner_confirmed'}
                    else:
                        value = await adapter.snapshot()
                        forms.append(value)
                        result = {'state': 'active', 'form': value}
                elif parsed.path == '/api/submit':
                    submissions.append(data['action'])  # Values stay only in this private call.
                    if data['action'] == 'confirm':
                        self.assertEqual(data['values'], {})
                        self.assertTrue(await adapter.confirmable(data['revision']))
                        completed = True
                    else:
                        await adapter.submit(data['revision'], data['action'], data['values'])
                    result = {'accepted': True}
                else:
                    result = {'state': 'cancelled'}
                await route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
                return
            path = {'/': 'oak/login_web/index.html', '/login.js': 'oak/login_web/login.js',
                    '/login.css': 'oak/login_web/login.css', '/form-controls.js': 'oak/web/form-controls.js'}[parsed.path]
            body = (ROOT / path).read_text().replace('PARENT_ORIGIN', PARENT)
            await route.fulfill(status=200, content_type=('text/javascript' if path.endswith('.js') else
                                'text/css' if path.endswith('.css') else 'text/html'), body=body)

        await context.route('**/*', intercept)
        adapter.page = await context.new_page()
        await adapter.page.goto(SITE + '/access')
        parent = await context.new_page()
        await parent.goto(PARENT)
        ui = parent.frame_locator('iframe')
        await ui.get_by_role('button', name='Use email', exact=True).click()
        await ui.get_by_label('Access phrase', exact=True).wait_for()
        self.assertEqual([f['type'] for f in forms[-1]['fields']], ['password', 'text', 'checkbox'])
        first = forms[-1]
        count = len(calls)
        self.assertEqual((await adapter.snapshot())['revision'], first['revision'])
        self.assertEqual(len(calls), count, 'Unchanged observation cannot spend another model call')

        async def fill_membership():
            await ui.get_by_label('Access phrase', exact=True).fill(PASSWORD)
            await ui.get_by_label('Membership handle', exact=True).fill(HANDLE)
            await ui.get_by_label('This is my own device', exact=True).check()

        await fill_membership()
        old_password = await ui.get_by_label('Access phrase', exact=True).element_handle()
        await ui.get_by_role('button', name='Unlock workspace', exact=True).click()
        await ui.locator('select').wait_for()
        self.assertEqual(await old_password.evaluate('(e)=>e.value'), '', 'Submitted secret widget must clear')
        self.assertEqual(await adapter.page.evaluate('window.attempts'), 1)
        self.assertEqual(posts, [], 'Validation failure must not auto-submit or navigate')
        second = forms[-1]
        self.assertNotEqual(second['revision'], first['revision'])
        self.assertEqual([f['type'] for f in second['fields']], ['password', 'text', 'checkbox', 'select'])
        with self.assertRaises(RecoverableFormError):
            await adapter.submit(first['revision'], first['actions'][0]['id'],
                                 {f['name']: False if f['type'] == 'checkbox' else '' for f in first['fields']})
        self.assertEqual(await adapter.page.evaluate('window.attempts'), 1)
        await fill_membership()
        await ui.locator('select').select_option(label='One-time code')
        await ui.get_by_role('button', name='Unlock workspace', exact=True).click()
        await ui.get_by_label('Rotating proof', exact=True).wait_for()
        self.assertEqual(adapter.page.url, SITE + '/verify')
        self.assertEqual(posts, ['/session'])
        self.assertNotEqual(forms[-1]['revision'].split(':')[0], second['revision'].split(':')[0])
        await ui.get_by_label('Rotating proof', exact=True).fill(OTP)
        old_otp = await ui.get_by_label('Rotating proof', exact=True).element_handle()
        await ui.get_by_role('button', name='Finish entry', exact=True).click()
        await ui.locator('button[value="confirm"]').wait_for()
        self.assertEqual(await old_otp.evaluate('(e)=>e.value'), '')
        self.assertEqual(posts, ['/session', '/complete'])
        self.assertEqual(forms[-1]['stage'], 'review')
        self.assertFalse(await adapter.verified(), 'A novel site cannot manufacture authentication proof')
        await ui.locator('button[value="confirm"]').click()
        await ui.get_by_text('Завершення дії підтверджено тобою.', exact=False).wait_for()
        self.assertTrue(completed)
        self.assertEqual(len(submissions), 5, 'Chooser, validation, corrected form, OTP, explicit confirmation only')
        self.assertEqual(len(calls), 4, 'One mocked decision for each actionable revision')
        exported = json.dumps({'model': calls, 'forms': forms,
                               'parent': await parent.evaluate('window.receipts')})
        for canary in (PASSWORD, OTP, HANDLE):
            self.assertNotIn(canary, exported)
        self.assertNotIn(SITE, json.dumps(calls))
        self.assertTrue(all(form['provider'] == 'website' for form in forms))


if __name__ == '__main__':
    unittest.main()
