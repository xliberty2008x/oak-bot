"""Real Chromium iframe + private HTTP + MCP, with synthetic typed model replies.

These known local fixtures test reconstruction and privacy, not live model
quality or owner authentication. Every provider request is intercepted locally.
"""
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
from test_website_task_browser import Transport


SITE = 'https://changing-fixture.example'
PRIVATE = 'https://private.example.test'
PARENT = 'https://panel.example.test'
IDENTIFIER = 'synthetic-member-813@example.invalid'
PASSWORD = 'synthetic-phrase-462'
CODE = '123456'

FLOW = r"""() => {
  window.submits=0;window.stage='chooser';
  const shell=content=>document.body.innerHTML='<section role="dialog" aria-modal="true">'+content+'</section>';
  const alternatives='<button type="button" data-alternative>Choose another method</button>'+
    '<a href="https://unapproved.example.test/" target="_blank">Other provider</a>';
  const form=(type,label,action,extra='')=>shell(extra+'<form method="post"><label>'+label+
    '<input type="'+type+'" '+(type==='text'?'autocomplete="one-time-code"':'')+' required></label>'+
    '<button>'+action+'</button>'+alternatives+'</form>');
  const code=error=>{
    window.stage=error?'code-error':'code';
    form('text','Verification digits','Verify',error?'<p role="alert">That code did not match. Try again.</p>':'');
    document.querySelector('form').onsubmit=e=>{
      e.preventDefault();window.submits++;
      if(!error){code(true);return;}
      window.stage='next-choice';
      shell('<button type="button">Continue to account</button><button type="button">Use another method</button>');
    };
  };
  const password=()=>{
    window.stage='password';form('password','Private phrase','Sign in');
    document.querySelector('form').onsubmit=e=>{
      e.preventDefault();window.submits++;window.receivedPhrase=e.target.querySelector('input').value;code(false);
    };
  };
  const email=()=>{
    window.stage='email';form('email','Member email','Next');
    document.querySelector('form').onsubmit=e=>{
      e.preventDefault();window.submits++;window.receivedIdentifier=e.target.querySelector('input').value;
      window.stage='loading';document.body.replaceChildren();setTimeout(password,450);
    };
  };
  shell('<button type="button">Use member email</button><button type="button">Use access code</button>'+
        '<a href="https://unapproved.example.test/" target="_blank">Other provider</a>');
  document.querySelector('button').onclick=()=>{window.submits++;email();};
}"""


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in local Chromium')
class WebsiteDynamicStepsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start(); self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.context = await self.chromium.new_context(service_workers='block')
        self.site_methods = []
        async def site(route):
            self.site_methods.append(route.request.method)
            await route.fulfill(status=200, content_type='text/html', body='<body>Local fixture</body>')
        await self.context.route('**/*', site)
        self.page = await self.context.new_page(); await self.page.goto(SITE)
        self.payloads, self.private_forms, self.private_submits, self.parent_events = [], [], [], []
        self.planner = StepPlanner('SYNTHETIC_DYNAMIC_MODEL_KEY')
        self.planner._post = AsyncMock(side_effect=self.decisions)
        self.broker = LoginBroker(PRIVATE, PARENT, 1001, peer_check=lambda r: True,
            boundary_check=lambda: True, step_planner_factory=lambda: self.planner)
        self.control = TestClient(TestServer(self.broker.control))
        self.human = TestClient(TestServer(self.broker.human))
        await self.control.start_server(); await self.human.start_server()
        self.addAsyncCleanup(self.control.close); self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.broker.close)
        self.binding = {'request_id': 'c'*32, 'owner': 1002, 'chat_id': 1003, 'thread_id': 'dynamic-fixture',
            'turn_id': 'dynamic-turn', 'runtime_epoch': 'dynamic-epoch', 'panel_hash': 'd'*64,
            'expires': time.time()+240, 'provider': 'website', 'url': SITE, 'agentic': True}
        created = await self.control.post('/create', json=self.binding)
        self.assertEqual(created.status, 200); self.created = await created.json()
        self.scope = {k: self.created[k] for k in ('request_id','session_id','runtime_epoch')}
        async def fixture_start(browser):
            browser.context, browser.page, browser.browser = self.context, self.page, self.chromium
            browser._pinned_address = '93.184.216.34'  # Fixture pin; no upstream connection.
            await browser._navigation.start()
            browser._approved_origins[browser.destination] = browser._pinned_address
            browser._navigation._proxy.register(urlsplit(SITE).hostname, browser._pinned_address)
            await browser._configure_context(); await browser._configure_page()
        start_patch = patch.object(WebsiteAgentForms, 'start', fixture_start)
        start_patch.start(); self.addCleanup(start_patch.stop)
        task_factory = lambda *a, **kw: WebsiteTask(*a, **kw, poll_seconds=.03)
        task_patch = patch('oak.website_task.WebsiteTask', side_effect=task_factory)
        task_patch.start(); self.addCleanup(task_patch.stop)
        self.transport = Transport(self.control); self.mcp = WebsiteTaskClient(self.transport, self.scope)
        self.addAsyncCleanup(self.mcp.close)
        self.ui_browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.ui_browser.close)
        self.ui_context = await self.ui_browser.new_context(service_workers='block')
        self.ui_page = await self.ui_context.new_page()
        async def bridge(route):
            request = route.request; parsed = urlsplit(request.url)
            if parsed.netloc == urlsplit(PARENT).netloc:
                await route.fulfill(status=200, content_type='text/html', body=
                    '<iframe src="'+PRIVATE+'/"></iframe><script>window.events=[];addEventListener("message",e=>window.events.push(e.data));</script>')
                return
            self.assertEqual(parsed.netloc, urlsplit(PRIVATE).netloc)
            headers = {key: value for key,value in request.headers.items()
                       if key.lower() in {'origin','authorization','content-type'}}
            async with self.human.request(request.method, parsed.path,
                    headers=headers, data=request.post_data_buffer) as response:
                body = await response.read()
                if parsed.path == '/api/form' and response.status == 200:
                    self.private_forms.append(json.loads(body))
                if parsed.path == '/api/attach' and response.status == 200:
                    self.human_token = json.loads(body)['lease']
                if parsed.path == '/api/submit':
                    # Synthetic test-only values remain in this private test
                    # collector; the public/MCP assertions below exclude them.
                    self.private_submits.append(json.loads(request.post_data))
                await route.fulfill(status=response.status, body=body, headers={
                    key:value for key,value in response.headers.items()
                    if key.lower() not in {'transfer-encoding','content-length','content-encoding','connection'}})
        await self.ui_context.route('**/*', bridge)

    def decisions(self, payload):
        self.payloads.append(deepcopy(payload)); answers = {}
        controls = {c['ref']:c for c in payload['state'].get('controls',[])}
        actions = {a['ref']:a for a in payload['state'].get('actions',[])}
        for key, question in payload['questions'].items():
            options = question['criteria']
            if key == 'anchor': chosen = next(k for k in options if k.startswith('present_'))
            elif key == 'surface':
                chosen = max((k for k in options if k!='none'),
                             key=lambda k:len(options[k]['controls'])+len(options[k]['actions']))
            elif key.startswith('field_'):
                chosen = {'text':'TextInput','email':'EmailInput','password':'PasswordInput','otp':'CodeInput'}[controls[key[6:]]['type']]
            elif key.startswith('binding_'):
                chosen = 'fields' if actions[key[8:]]['label']=='Continue securely' else 'none'
            elif key == 'message':
                errors = [t['ref'] for t in payload['state']['texts'] if t['kind']=='error' and t['ref'] in options]
                chosen = errors[0] if errors else 'none'
            else: raise AssertionError('Unexpected typed question')
            answers[key] = {'type':'choice','choice':chosen,'confidence':0,
                'probabilities':{option:float(option==chosen) for option in options}}
        return {'model':RESPONSE_MODEL,'provider':'TypeSafe','answers':answers,'usage':{'cost':.00001}}

    async def open_ui(self):
        await self.ui_page.goto(PARENT)
        self.ui = self.ui_page.frame_locator('iframe')
        await self.ui.locator('#status').wait_for()
        await self.ui_page.evaluate('''({id,ticket,origin})=>document.querySelector('iframe').contentWindow.postMessage(
            {type:'oak-login-attach',request_id:id,ticket},origin)''',
            {'id':self.binding['request_id'],'ticket':self.created['ticket'],'origin':PRIVATE})
        async with asyncio.timeout(8):
            while self.broker.leases[self.binding['request_id']].state != 'active': await asyncio.sleep(.02)
        self.lease = self.broker.leases[self.binding['request_id']]
        await self.mcp.initialize()
        await self.mcp.call('website_sign_in')

    async def expect_field(self, label, kind):
        field = self.ui.get_by_label(label, exact=True)
        await field.wait_for(timeout=12000)
        self.assertEqual(await field.get_attribute('type'), kind)
        self.assertEqual(await field.input_value(), '')
        self.assertEqual(await self.ui.locator('#fields input').count(), 1)
        return field

    async def click(self, label):
        button = self.ui.get_by_role('button', name=label, exact=True)
        await button.click(timeout=12000)

    async def assert_private_boundary(self):
        status = await self.mcp.call('website_task_status')
        events = await self.ui_page.evaluate('window.events')
        exported = json.dumps([self.payloads,self.transport.messages,status,events,self.private_forms,
                               self.lease.delegated_task.diagnostics()])
        for secret in (IDENTIFIER,PASSWORD,CODE,'SYNTHETIC_DYNAMIC_MODEL_KEY'):
            self.assertNotIn(secret, exported)
        self.assertFalse(await self.lease.browser.verified())
        self.assertEqual(self.lease.state,'active')
        self.assertEqual(self.site_methods,['GET'])

    async def test_private_renderer_rebuilds_method_email_password_code_error_and_action_only(self):
        await self.page.evaluate(FLOW); await self.open_ui()
        await self.ui.get_by_role('button', name='Use member email', exact=True).wait_for(timeout=12000)
        self.assertEqual(await self.ui.locator('#fields input').count(),0)
        self.assertEqual(await self.ui.locator('#actions button').count(),3)
        self.assertTrue(await self.ui.get_by_role('button',name='Other provider',exact=True).is_disabled())
        await self.click('Use member email')
        email = await self.expect_field('Member email','email'); await email.fill(IDENTIFIER)
        polls = len(self.private_forms)
        async with asyncio.timeout(5):
            while len(self.private_forms)==polls: await asyncio.sleep(.05)
        self.assertEqual(await email.input_value(),IDENTIFIER)  # Stable poll preserves draft.
        await self.click('Next')
        phrase = await self.expect_field('Private phrase','password')
        self.assertEqual(await self.ui.get_by_label('Member email',exact=True).count(),0)
        self.assertTrue(any(v['form']['stage']=='agent_working' and not v['form']['fields']
                            for v in self.private_forms if v.get('state')=='active'))
        self.assertEqual(await self.page.evaluate('window.receivedIdentifier'),IDENTIFIER)
        await phrase.fill(PASSWORD); await self.click('Sign in')
        digits = await self.expect_field('Verification digits','text')
        self.assertEqual(await self.ui.get_by_label('Private phrase',exact=True).count(),0)
        self.assertEqual(await self.page.evaluate('window.receivedPhrase'),PASSWORD)
        await digits.fill(CODE); await self.click('Verify')
        await self.ui.get_by_text('That code did not match. Try again.',exact=True).wait_for(timeout=12000)
        digits = await self.expect_field('Verification digits','text')
        self.assertEqual(await self.ui.locator('#actions button').count(),3)
        await digits.fill(CODE); await self.click('Verify')
        await self.ui.get_by_role('button',name='Continue to account',exact=True).wait_for(timeout=12000)
        self.assertEqual(await self.ui.locator('#fields input').count(),0)
        self.assertEqual(await self.ui.locator('#actions button').count(),2)
        self.assertEqual(await self.page.evaluate('window.submits'),5)
        self.assertEqual(len(self.private_submits),5)
        self.assertEqual(self.private_submits[0]['values'],{})
        self.assertEqual([list(v['values'].values()) for v in self.private_submits[1:]],
                         [[IDENTIFIER],[PASSWORD],[CODE],[CODE]])
        self.assertLessEqual(self.planner.calls,12)
        await self.assert_private_boundary()
        await self.ui.locator('#cancel').click(timeout=12000)
        async with asyncio.timeout(5):
            while self.lease.cleanup!='confirmed': await asyncio.sleep(.02)
        self.assertEqual(self.lease.state,'cancelled')
        self.assertTrue(self.page.is_closed())

    async def test_changed_shadow_scene_removes_old_drafts_and_rejects_stale_dispatch(self):
        await self.page.evaluate(r'''() => {
          const root=document.body.appendChild(document.createElement('section')).attachShadow({mode:'open'});
          root.innerHTML='<div role="dialog" aria-modal="true"><label>Member email<input type="email"></label>'+
            '<button type="button">Continue securely</button></div>';
          window.submits=0;root.querySelector('button').onclick=()=>window.submits++;
          window.replaceStep=()=>{
            root.querySelector('div').innerHTML='<label>Updated member email<input type="email"></label>'+
              '<button type="button">Continue securely</button>';
            root.querySelector('button').onclick=()=>window.submits++;
          };
        }''')
        await self.open_ui()
        first = await self.expect_field('Member email','email'); await first.fill(IDENTIFIER)
        old = next(v['form'] for v in reversed(self.private_forms) if v.get('form',{}).get('stage')=='form')
        await self.page.evaluate('window.replaceStep()')
        await self.expect_field('Updated member email','email')
        self.assertEqual(await self.ui.get_by_label('Member email',exact=True).count(),0)
        current = next(v['form'] for v in reversed(self.private_forms) if v.get('form',{}).get('stage')=='form')
        self.assertNotEqual(old['revision'],current['revision'])
        self.assertNotEqual(old['fields'][0]['name'],current['fields'][0]['name'])
        # Private HTTP sends an obsolete proposal once; broker must not replay it.
        response = await self.human.post('/api/submit',headers={'Origin':PRIVATE,'Authorization':'Bearer '+self.human_token},
            json={'request_id':self.binding['request_id'],'revision':old['revision'],
                  'action':old['actions'][0]['id'],'values':{old['fields'][0]['name']:IDENTIFIER}})
        self.assertEqual(response.status,409)
        self.assertEqual((await response.json())['code'],'stale_form')
        self.assertEqual(await self.page.evaluate('window.submits'),0)
        await self.assert_private_boundary()

    async def test_semantic_email_text_choices_preserve_native_validation_and_length(self):
        native_decisions = self.decisions

        def semantic_decisions(payload):
            result = native_decisions(payload)
            controls = {c['ref']:c for c in payload['state'].get('controls',[])}
            for key, answer in result['answers'].items():
                if key.startswith('field_'):
                    kind = controls[key[6:]]['type']
                    selected = {'text':'EmailInput', 'email':'TextInput'}[kind]
                    answer['choice'] = selected
                    answer['probabilities'] = {option:float(option==selected)
                        for option in payload['questions'][key]['criteria']}
            return result

        self.planner._post.side_effect = semantic_decisions
        await self.page.evaluate(r'''() => {
          window.submits=0;window.receivedIdentifierLength=0;
          document.body.innerHTML='<section role="dialog" aria-modal="true"><form method="post">'+
            '<label>Account identifier<input type="text" maxlength="700" required></label>'+
            '<button>Continue securely</button></form></section>';
          document.querySelector('form').onsubmit=e=>{
            e.preventDefault();window.submits++;
            window.receivedIdentifierLength=e.target.querySelector('input').value.length;
            document.querySelector('section').innerHTML='<form method="post">'+
              '<label>Contact address<input type="email" maxlength="90" required></label>'+
              '<button>Continue securely</button></form>';
            document.querySelector('form').onsubmit=e=>{
              e.preventDefault();window.submits++;
              document.querySelector('section').innerHTML='<button type="button">Account ready</button>';
            };
          };
        }''')
        await self.open_ui()
        identifier = await self.expect_field('Account identifier', 'text')
        self.assertEqual(await identifier.get_attribute('maxlength'),'700')
        long_username = 'synthetic-member-'+'x'*300
        await identifier.fill(long_username)
        self.assertTrue(await identifier.evaluate('(e)=>e.checkValidity()'))
        await self.click('Continue securely')
        email = await self.expect_field('Contact address','email')
        self.assertEqual(await email.get_attribute('maxlength'),'90')
        self.assertEqual(await self.page.evaluate('window.receivedIdentifierLength'),len(long_username))
        self.assertEqual(await self.page.evaluate('window.submits'),1)
        await email.fill('synthetic-invalid-address')
        self.assertFalse(await email.evaluate('(e)=>e.checkValidity()'))
        await self.click('Continue securely')
        self.assertEqual(await self.page.evaluate('window.submits'),1)
        self.assertEqual(len(self.private_submits),1,'HTML native email validation stops the invalid draft')
        valid_email = 'synthetic-contact-432@example.invalid'
        await email.fill(valid_email); await self.click('Continue securely')
        await self.ui.get_by_role('button',name='Account ready',exact=True).wait_for(timeout=12000)
        self.assertEqual(await self.page.evaluate('window.submits'),2)
        self.assertEqual([list(v['values'].values()) for v in self.private_submits],
                         [[long_username],[valid_email]])
        await self.assert_private_boundary()
        exported=json.dumps([self.payloads,self.transport.messages,self.private_forms,
                             await self.ui_page.evaluate('window.events')])
        for canary in (long_username,valid_email,'synthetic-invalid-address'):
            self.assertNotIn(canary,exported)
        await self.ui.locator('#cancel').click(timeout=12000)
        async with asyncio.timeout(5):
            while self.lease.cleanup!='confirmed': await asyncio.sleep(.02)
        self.assertEqual(self.lease.state,'cancelled')


if __name__ == '__main__': unittest.main()
