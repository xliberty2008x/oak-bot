#!/usr/bin/env python3
"""Actual Chromium/private widgets with REAL Jev and a synthetic site/API shim.

Default validates code only. --execute-real-jev is mandatory for any key read,
browser launch or model call. There is no model transport override or retry.
Candidate source is pinned by the operator's --expected-code-digest before run.
This reuses an established regression fixture; it is not a new held-out site.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from urllib.parse import urlsplit

CODE_FILES = ('oak/__init__.py','oak/website_forms.py','oak/signin_providers.py',
              'oak/website_resources.py','oak/website_planner.py','oak/website_composer.py',
              'oak/website_ui.py','oak/login_web/login.js','oak/login_web/index.html',
              'oak/login_web/login.css','oak/web/form-controls.js')
SOURCE_TEST_SHA256 = 'e0d4b24b4c71f5ebe039c6226b39a4fbc8bd94b675aa230640de9afc0fc14847'
MAX_ATTEMPTS = 4
ROOT = None
PLANNER = None
RECEIPT = None
EXPECTED_DIGEST = None
ATTEMPTS = 0
API_REQUESTS = 0
PRIVATE_PAYLOAD_CHECKS = 0
HUMAN_SUBMISSION_SEEN = False
DECISIONS = []
CLOSED = {'context':False,'browser':False,'driver':False}
PHASE = 'not_started'


def code_hashes(root):
    for name in CODE_FILES:
        if not (root/name).is_file():
            raise ValueError
    values={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root/'oak').rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    digest=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
    return values,digest


def emit(value):
    RECEIPT.write(json.dumps(value,sort_keys=True,allow_nan=False)+'\n')
    RECEIPT.flush()
    os.fsync(RECEIPT.fileno())


async def close_resource(resource, method, name):
    await getattr(resource,method)()
    CLOSED[name]=True


class RecordingPlanner:
    """Only a bounded audit wrapper; the client's plan/_post remain unchanged."""
    def __init__(self):
        self.payloads=[]

    async def plan(self, observation):
        global ATTEMPTS
        if ATTEMPTS >= MAX_ATTEMPTS:
            raise WebsitePlannerError('unavailable')
        if code_hashes(ROOT)[1] != EXPECTED_DIGEST:
            raise WebsitePlannerError('unavailable')
        frozen=deepcopy(observation)
        # Same pure projection the real client uses; no model response supplied.
        payload=decision_payload(frozen)
        self.payloads.append(payload)
        ATTEMPTS += 1
        attempt=ATTEMPTS
        emit({'event':'attempt_started','attempt':attempt,'phase':PHASE,
              'group_count':payload['state']['group_count'],'real_transport':True,
              'post_input':HUMAN_SUBMISSION_SEEN})
        started=time.monotonic()
        try:
            result=await PLANNER.plan(frozen)
        except asyncio.CancelledError:
            emit({'event':'attempt_interrupted','attempt':attempt,'code':'uncertain_no_retry',
                  'elapsed_ms':round((time.monotonic()-started)*1000)})
            raise
        except WebsitePlannerError as error:
            record={'event':'attempt_finished','attempt':attempt,
                    'status':'abstained' if error.code=='no_candidate' else 'failed',
                    'code':error.code,'elapsed_ms':round((time.monotonic()-started)*1000),
                    'cost_usd':None}
            DECISIONS.append(record); emit(record)
            raise
        except Exception:
            record={'event':'attempt_finished','attempt':attempt,'status':'failed',
                    'code':'local_failure','elapsed_ms':round((time.monotonic()-started)*1000),
                    'cost_usd':None}
            DECISIONS.append(record); emit(record)
            raise WebsitePlannerError('unavailable') from None
        record={'event':'attempt_finished','attempt':attempt,'status':'selected',
                'model':result['model'],'decision':result['decision'],
                'elapsed_ms':result['elapsed_ms'],'cost_usd':result['usage'].get('cost')}
        DECISIONS.append(record); emit(record)
        return result


def validate_outgoing_payload(payload):
    """No raw human input ever; after input, only the established structural state."""
    global PRIVATE_PAYLOAD_CHECKS
    serialized=json.dumps(payload)
    if any(canary in serialized for canary in (PASSWORD,OTP,HANDLE)) or SITE in serialized:
        raise ValueError('private_payload_rejected')
    state=payload['state']
    if HUMAN_SUBMISSION_SEEN:
        if set(state) != {'catalog','stage','group_count','groups'}:
            raise ValueError('private_payload_rejected')
        for group in state['groups']:
            if set(group) != {'id','fields','actions','field_count','action_count'}:
                raise ValueError('private_payload_rejected')
            if any(set(field) - {'name','type','semantic_role','option_count'} for field in group['fields']):
                raise ValueError('private_payload_rejected')
            if any(set(action) - {'id','intent'} for action in group['actions']):
                raise ValueError('private_payload_rejected')
        PRIVATE_PAYLOAD_CHECKS += 1


class QuietResult(unittest.TestResult):
    def __init__(self):
        super().__init__(); self.codes=[]
    def addError(self,test,error):
        self.errors.append((test,'redacted'))
        self.codes.append('fixture_timeout' if type(error[1]).__name__=='TimeoutError' else 'fixture_error')
    def addFailure(self,test,error):
        self.failures.append((test,'redacted')); self.codes.append('fixture_assertion')

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


class HeldOutWebsiteLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_novel_flow_uses_existing_private_widgets_without_provider_adapter(self):
        global PHASE
        PHASE = 'chooser'
        from playwright.async_api import async_playwright

        driver = await async_playwright().start()
        self.addAsyncCleanup(close_resource,driver,'stop','driver')
        chromium = await driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(close_resource,chromium,'close','browser')
        context = await chromium.new_context(service_workers='block')
        self.addAsyncCleanup(close_resource,context,'close','context')
        calls, posts, forms, submissions = [], [], [], []
        completed = False
        planner = RecordingPlanner()
        calls = planner.payloads
        adapter = WebsiteForms(SITE + '/access', composer=LeaseComposer(planner, MAX_ATTEMPTS))
        adapter.context = context
        self.assertIsNone(adapter._known_provider)
        self.assertIsNone(adapter._resources)

        async def dispatch(route):
            nonlocal completed
            global HUMAN_SUBMISSION_SEEN
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
                    if isinstance(data.get('values'), dict) and data['values']:
                        HUMAN_SUBMISSION_SEEN = True
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

        async def intercept(route):
            try:
                await dispatch(route)
            except RecoverableFormError as error:
                await route.fulfill(status=409,content_type='application/json',body=json.dumps(
                    {'recoverable':True,'code':error.code,'error':'Fixture form unavailable.'}))
            except Exception:
                try:
                    await route.abort()
                except Exception:
                    pass

        await context.route('**/*', intercept)
        adapter.page = await context.new_page()
        await adapter.page.goto(SITE + '/access')
        parent = await context.new_page()
        await parent.goto(PARENT)
        ui = parent.frame_locator('iframe')
        PHASE = 'method_navigation'
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

        PHASE = 'validation'
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
        PHASE = 'conditional_route'
        await fill_membership()
        await ui.locator('select').select_option(label='One-time code')
        await ui.get_by_role('button', name='Unlock workspace', exact=True).click()
        await ui.get_by_label('Rotating proof', exact=True).wait_for()
        self.assertEqual(adapter.page.url, SITE + '/verify')
        self.assertEqual(posts, ['/session'])
        self.assertNotEqual(forms[-1]['revision'].split(':')[0], second['revision'].split(':')[0])
        PHASE = 'one_time_proof'
        await ui.get_by_label('Rotating proof', exact=True).fill(OTP)
        old_otp = await ui.get_by_label('Rotating proof', exact=True).element_handle()
        await ui.get_by_role('button', name='Finish entry', exact=True).click()
        await ui.locator('button[value="confirm"]').wait_for()
        self.assertEqual(await old_otp.evaluate('(e)=>e.value'), '')
        self.assertEqual(posts, ['/session', '/complete'])
        self.assertEqual(forms[-1]['stage'], 'review')
        self.assertFalse(await adapter.verified(), 'A novel site cannot manufacture authentication proof')
        PHASE = 'owner_confirmation'
        await ui.locator('button[value="confirm"]').click()
        await ui.get_by_text('Завершення дії підтверджено тобою.', exact=False).wait_for()
        self.assertTrue(completed)
        self.assertEqual(len(submissions), 5, 'Chooser, validation, corrected form, OTP, explicit confirmation only')
        self.assertEqual(len(calls), 4, 'One real decision for each actionable revision')
        self.assertEqual(API_REQUESTS, 4, 'Four real transport dispatches, no mocked answers')
        self.assertEqual(PRIVATE_PAYLOAD_CHECKS, 2, 'Validation and OTP followups have no free page text')
        exported = json.dumps({'model': calls, 'forms': forms,
                               'parent': await parent.evaluate('window.receipts')})
        for canary in (PASSWORD, OTP, HANDLE):
            self.assertNotIn(canary, exported)
        self.assertNotIn(SITE, json.dumps(calls))
        self.assertTrue(all(form['provider'] == 'website' for form in forms))



def main():
    global ROOT,EXPECTED_DIGEST,PLANNER,RECEIPT
    global RecoverableFormError,WebsitePlannerError,WebsiteForms,LeaseComposer,decision_payload
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root',type=Path,required=True)
    parser.add_argument('--expected-code-digest')
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--key-owner-uid',type=int,default=os.geteuid())
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--execute-real-jev',action='store_true')
    args=parser.parse_args()
    try:
        ROOT=args.code_root.resolve()
        hashes,digest=code_hashes(ROOT)
        if args.expected_code_digest is not None and digest!=args.expected_code_digest:
            raise ValueError
        if not args.execute_real_jev:
            print(json.dumps({'status':'validation_only','code_digest':digest,'source_test_sha256':SOURCE_TEST_SHA256,
                              'max_attempts':MAX_ATTEMPTS,'model_calls':0,'key_files_read':0}))
            return 0
        if (args.expected_code_digest is None or args.key_file is None or args.receipt is None or
            args.key_owner_uid<0):
            raise ValueError
        EXPECTED_DIGEST=args.expected_code_digest
        sys.path.insert(0,str(ROOT))
        from oak.signin_providers import RecoverableFormError
        from oak.website_forms import WebsiteForms
        from oak.website_composer import LeaseComposer,read_key
        from oak.website_planner import OpenRouterWebsitePlanner,WebsitePlannerError,decision_payload
        class CountedPlanner(OpenRouterWebsitePlanner):
            async def _post(self, payload):
                global API_REQUESTS
                if API_REQUESTS >= MAX_ATTEMPTS:
                    raise WebsitePlannerError('unavailable')
                try:
                    validate_outgoing_payload(payload)
                except Exception:
                    emit({'event':'payload_rejected','code':'private_payload_rejected',
                          'phase':PHASE,'api_requests':API_REQUESTS})
                    raise WebsitePlannerError('invalid_plan') from None
                API_REQUESTS += 1
                emit({'event':'api_dispatch','api_request':API_REQUESTS,'phase':PHASE,
                      'post_input':HUMAN_SUBMISSION_SEEN,
                      'public_context_present':'public_context' in payload['state']})
                # Count/check only: invoke the original implementation unchanged.
                return await super()._post(payload)
        PLANNER=CountedPlanner(read_key(str(args.key_file),args.key_owner_uid))
        descriptor=os.open(args.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,'w') as stream:
            RECEIPT=stream
            emit({'event':'evaluation_started','code_digest':digest,'code_sha256':hashes,
                  'source_test_sha256':SOURCE_TEST_SHA256,'max_attempts':MAX_ATTEMPTS,
                  'real_jev_transport':True,'production_broker':False,
                  'site_and_private_api':'fully intercepted synthetic fixture',
                  'private_renderer':'actual candidate login.js and form-controls.js',
                  'owner_account_used':False,'retries':0,'fixture_is_regression':True,
                  'post_input_state':'no public_context, headings or field/action labels'})
            started=time.monotonic()
            result=QuietResult()
            case=HeldOutWebsiteLoopTests('test_novel_flow_uses_existing_private_widgets_without_provider_adapter')
            case.run(result)
            costs=[r['cost_usd'] for r in DECISIONS if r.get('cost_usd') is not None]
            stable=code_hashes(ROOT)[1]==EXPECTED_DIGEST
            passed=result.wasSuccessful() and stable and all(CLOSED.values())
            summary={'event':'evaluation_finished','status':'passed' if passed else 'failed',
                     'phase':PHASE,'attempts':ATTEMPTS,'api_requests':API_REQUESTS,
                     'post_input_payload_checks':PRIVATE_PAYLOAD_CHECKS,'errors':result.codes,
                     'selected':sum(r['status']=='selected' for r in DECISIONS),
                     'abstained':sum(r['status']=='abstained' for r in DECISIONS),
                     'failed':sum(r['status']=='failed' for r in DECISIONS),
                     'known_cost_usd':sum(costs),'unknown_cost_attempts':ATTEMPTS-len(costs),
                     'unknown_cost_requests':max(0,API_REQUESTS-len(costs)),
                     'elapsed_ms':round((time.monotonic()-started)*1000),'closed':CLOSED,
                     'code_unchanged':stable,'real_provider_authentication_tested':False}
            emit(summary)
            print(json.dumps(summary,sort_keys=True))
            return 0 if passed else 1
    except KeyboardInterrupt:
        if RECEIPT is not None and not RECEIPT.closed:
            emit({'event':'interrupted','phase':PHASE,'attempts':ATTEMPTS,'code':'uncertain_no_retry'})
        print(json.dumps({'status':'interrupted','code':'uncertain_no_retry'})); return 130
    except Exception:
        print(json.dumps({'status':'not_started_or_local_failure','code':'guard_or_fixture_unavailable'})); return 2


if __name__=='__main__':
    raise SystemExit(main())
