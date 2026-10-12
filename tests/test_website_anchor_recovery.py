"""Actual broker/Chromium/private UI recovery, with deterministic Jev replies.

Every browser request is intercepted. The actual planner and lease quota run,
but its HTTP transport is mocked; no model, key, account or provider is used.
These are protocol regression tests, not evidence of real model quality.
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
from oak.signin_providers import RecoverableFormError
from oak.website_composer import LeaseComposer
from oak.website_forms import WebsiteForms
from oak.website_planner import RESPONSE_MODEL, WebsitePlannerError
from oak.website_scene_planner import ScenePlanner


SITE = 'https://recovery-fixture.example'
HUMAN = 'https://private-recovery.example'
PARENT = 'https://recovery-panel.example'
REQUEST = 'e' * 32
CANARY = 'private-recovery-phrase-753'
PAGE = '''<!doctype html><h1>Existing membership</h1>
<form method="post" action="/done">
  <label>Member address<input type="email" autocomplete="username" required></label>
  <label>Access phrase<input type="password" autocomplete="current-password" required></label>
  <button>Continue membership</button>
</form>
<form method="post" action="/register"><button>Register a membership</button></form>
<script>window.events={input:0,click:0,submit:0};
for(const kind of Object.keys(events))document.addEventListener(kind,()=>events[kind]++);
</script>'''


class LegacySceneProjectionTests(unittest.TestCase):
    def test_only_website_v2_exposes_surface_contract_to_composer(self):
        from oak.website_agent import WebsiteAgentForms
        from test_website_surface import surface_scene

        state = {'scene': surface_scene()}
        original = deepcopy(state)
        legacy = WebsiteForms(SITE, composer=ScenePlanner('SYNTHETIC_UNUSED_TEST_KEY'))
        agent = WebsiteAgentForms(SITE)
        projected = legacy._group_observation(state)
        self.assertNotIn('surfaces', projected)
        self.assertEqual(agent._group_observation(state)['surfaces'], state['scene']['surfaces'])
        self.assertEqual(state, original)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium/private-renderer fixtures')
class WebsiteAnchorRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.html = PAGE
        self.site_requests = []
        self.private_requests = []
        self.route_errors = []
        self.payloads = []
        self.component_failure = False
        self.before_components = None
        self.anchor_choices = []
        self.planner = ScenePlanner('SYNTHETIC_UNUSED_TEST_KEY')
        self.planner._post = AsyncMock(side_effect=self.model_reply)
        self.composer = LeaseComposer(self.planner, 12)
        self.broker = LoginBroker(HUMAN, PARENT, 1001, boundary_check=lambda: True,
                                  peer_check=lambda request: True,
                                  composer_factory=lambda: self.composer)
        self.human = TestClient(TestServer(self.broker.human))
        self.control = TestClient(TestServer(self.broker.control))
        await self.human.start_server()
        await self.control.start_server()
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.broker.close)
        factory = patch('oak.website_forms.WebsiteForms', side_effect=self.prepare)
        factory.start()
        self.addCleanup(factory.stop)
        self.binding = {'request_id': REQUEST, 'owner': 11, 'chat_id': 11,
                        'thread_id': 'same-thread', 'turn_id': 'same-turn',
                        'runtime_epoch': 'same-epoch', 'panel_hash': 'f' * 64,
                        'expires': time.time() + 299, 'provider': 'website', 'url': SITE + '/login'}
        response = await self.control.post('/create', json=self.binding)
        self.assertEqual(response.status, 200)
        self.launch = await response.json()
        self.headers = {'Origin': HUMAN}

    async def model_reply(self, payload):
        self.payloads.append(deepcopy(payload))
        if 'anchor' in payload['questions']:
            selected = self.anchor_choices.pop(0) if self.anchor_choices else None
            choice = next((action['ref'] for action in payload['state']['actions']
                           if action['label'] == selected), 'none')
            choices = {'anchor': choice}
        else:
            if self.before_components:
                await self.before_components()
            if self.component_failure:
                raise RuntimeError('PRIVATE_FAILURE_CANARY')
            controls = {item['ref']: item for item in payload['state']['controls']}
            components = {'text': 'TextInput', 'email': 'EmailInput', 'password': 'PasswordInput',
                          'otp': 'CodeInput', 'select': 'Select', 'checkbox': 'Checkbox'}
            choices = {key: components[controls[key[6:]]['type']] if key.startswith('field_')
                       else 'none' if key == 'message' else 'omit'
                       for key in payload['questions']}
        return {'model': RESPONSE_MODEL, 'provider': 'TypeSafe',
                'usage': {'cost': 0.00001}, 'answers': {
                    key: {'type': 'choice', 'choice': choices[key], 'confidence': 1,
                          'probabilities': {option: float(option == choices[key])
                                            for option in question['criteria']}}
                    for key, question in payload['questions'].items()}}

    def prepare(self, url, **options):
        adapter = WebsiteForms(url, **options)
        self.adapter = adapter

        async def start():
            adapter.context = await self.chromium.new_context(service_workers='block')

            async def site_route(route):
                parsed = urlsplit(route.request.url)
                self.assertEqual(parsed.scheme + '://' + parsed.netloc, SITE)
                self.assertFalse(parsed.query)
                self.site_requests.append((route.request.method, parsed.path))
                body = self.html if parsed.path == '/login' else '<h1>Membership home</h1>'
                await route.fulfill(status=200, content_type='text/html', body=body)

            await adapter.context.route('**/*', site_route)
            adapter.page = await adapter.context.new_page()
            await adapter.page.goto(url)

        adapter.start = start
        return adapter

    async def call(self, path, data=None, *, status=200, headers=None):
        response = await self.human.post(path, headers=self.headers if headers is None else headers,
                                         json={'request_id': REQUEST, **(data or {})})
        result = await response.json()
        self.assertEqual(response.status, status, result)
        return result

    async def attach(self):
        result = await self.call('/api/attach', {'ticket': self.launch['ticket']})
        self.headers['Authorization'] = 'Bearer ' + result['lease']
        return (await self.call('/api/form'))['form']

    @staticmethod
    def choice(form, label='Continue membership'):
        return next(item['value'] for item in form['fields'][0]['options'] if item['label'] == label)

    async def choose(self, form, *, status=200, label='Continue membership'):
        return await self.call('/api/choose-action',
                               {'revision': form['revision'], 'choice': self.choice(form, label)}, status=status)

    async def assert_untouched(self):
        self.assertEqual(await self.adapter.page.evaluate('window.events'), {'input': 0, 'click': 0, 'submit': 0})
        self.assertEqual(await self.adapter.page.locator('input').evaluate_all('(xs)=>xs.map(x=>x.value)'), ['', ''])
        self.assertEqual(self.site_requests, [('GET', '/login')])
        self.assertFalse(self.adapter._human_values_entered)
        self.assertFalse(self.adapter._submitted)
        self.assertEqual(self.adapter._scene_history, [])
        self.assertEqual(self.adapter._redactions, [])

    async def private_ui(self):
        context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(context.close)

        async def route_request(route):
            parsed = urlsplit(route.request.url)
            request_origin = parsed.scheme + '://' + parsed.netloc
            if request_origin == PARENT:
                self.assertEqual(parsed.path, '/')
                html = '<script>window.receipts=[];addEventListener("message",e=>{' \
                    'if(e.origin!==' + json.dumps(HUMAN) + ')return;receipts.push(e.data);' \
                    'if(e.data.type==="oak-login-ready")e.source.postMessage(' \
                    + json.dumps({'type': 'oak-login-attach', 'request_id': REQUEST,
                                  'ticket': self.launch['ticket']}) \
                    + ',' + json.dumps(HUMAN) + ');});</script><iframe src="' + HUMAN + '/"></iframe>'
                await route.fulfill(content_type='text/html', body=html)
                return
            self.assertEqual(request_origin, HUMAN)
            if parsed.path.startswith('/api/'):
                self.assertEqual(route.request.method, 'POST')
                value = route.request.post_data_json
                # Record request shape only; values and tokens are not diagnostics.
                self.private_requests.append({'path': parsed.path, 'keys': sorted(value),
                                              'value_keys': sorted(value.get('values', {}))})
                headers = {key: val for key, val in route.request.headers.items()
                           if key.lower() in ('origin', 'authorization', 'content-type')}
                response = await self.human.post(parsed.path, json=value, headers=headers)
                result = await response.json()
                if parsed.path == '/api/attach' and response.status == 200:
                    self.headers['Authorization'] = 'Bearer ' + result['lease']
                await route.fulfill(status=response.status, content_type='application/json', body=json.dumps(result))
            else:
                response = await self.human.get(parsed.path)
                await route.fulfill(status=response.status, headers=dict(response.headers), body=await response.read())

        async def guarded(route):
            try:
                await route_request(route)
            except Exception as error:
                self.route_errors.append(type(error).__name__)
                await route.abort()

        await context.route('**/*', guarded)
        parent = await context.new_page()
        parent.set_default_timeout(7000)
        await parent.goto(PARENT)
        return parent, parent.frame_locator('iframe')

    async def test_private_renderer_choice_only_prepares_then_explicit_submit_posts_once(self):
        parent, ui = await self.private_ui()
        select = ui.locator('select')
        await select.wait_for()
        await select.select_option(label='Continue membership')
        selected = await select.input_value()
        await asyncio.sleep(1.7)  # Same-revision polling preserves the human's choice.
        self.assertEqual(await select.input_value(), selected)
        self.assertEqual(self.composer.calls, 1)
        await self.assert_untouched()
        await ui.get_by_role('button', name='Показати поля', exact=True).click()
        await ui.get_by_label('Access phrase', exact=True).wait_for()
        await self.assert_untouched()
        self.assertEqual(self.composer.calls, 2)
        prepared = (await self.call('/api/form'))['form']
        self.assertEqual(prepared['form_schema'], 'website-v1')
        self.assertTrue(all(set(action) == {'id', 'label'} for action in prepared['actions']))
        self.assertTrue(all('surfaces' not in payload['state'] for payload in self.payloads))
        self.assertNotIn('anchor', self.payloads[1]['questions'], 'Human anchor skips another anchor judgment')
        choices = [item for item in self.private_requests if item['path'] == '/api/choose-action']
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]['keys'], ['choice', 'request_id', 'revision'])
        self.assertFalse(any(item['path'] == '/api/submit' for item in self.private_requests))
        await ui.get_by_label('Member address', exact=True).fill('fixture@member.invalid')
        secret = ui.get_by_label('Access phrase', exact=True)
        await secret.fill(CANARY)
        handle = await secret.element_handle()
        await ui.get_by_role('button', name='Continue membership', exact=True).click()
        await ui.get_by_role('button', name='Підтвердити завершення', exact=True).wait_for()
        self.assertEqual(await handle.evaluate('(input)=>input.value'), '')
        self.assertEqual(self.site_requests, [('GET', '/login'), ('POST', '/done')])
        self.assertEqual(sum(item['path'] == '/api/submit' for item in self.private_requests), 1)
        self.assertEqual(self.composer.calls, 2)
        self.assertEqual(self.broker.leases[REQUEST].state, 'active')
        parent_messages = await parent.evaluate('window.receipts')
        for private in (CANARY, self.headers['Authorization'][7:], 'Continue membership', 'fixture@member.invalid'):
            self.assertNotIn(private, json.dumps(parent_messages))
            self.assertNotIn(private, json.dumps(self.composer.diagnostics()))
        for private in (CANARY, self.headers['Authorization'][7:], 'fixture@member.invalid'):
            self.assertNotIn(private, json.dumps(self.payloads))
        self.assertFalse(self.route_errors)

    async def test_private_renderer_blocks_repeated_choice_while_components_are_in_flight(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked():
            entered.set()
            await release.wait()

        self.before_components = blocked
        _, ui = await self.private_ui()
        await ui.locator('select').wait_for()
        await ui.locator('select').select_option(label='Continue membership')
        button = ui.get_by_role('button', name='Показати поля', exact=True)
        try:
            await button.click()
            await asyncio.wait_for(entered.wait(), 3)
            self.assertTrue(await button.is_disabled())
            await ui.locator('#login').evaluate('(form)=>form.requestSubmit()')
            self.assertEqual(sum(item['path'] == '/api/choose-action' for item in self.private_requests), 1)
            self.assertEqual(self.composer.calls, 2)
            await self.assert_untouched()
        finally:
            release.set()
        await ui.get_by_label('Access phrase', exact=True).wait_for()
        self.assertEqual(sum(item['path'] == '/api/choose-action' for item in self.private_requests), 1)
        self.assertEqual(self.composer.calls, 2)
        self.assertFalse(self.route_errors)

    async def test_private_renderer_stale_choice_refreshes_without_site_or_model_replay(self):
        parent, ui = await self.private_ui()
        select = ui.locator('select')
        await select.wait_for()
        await select.select_option(label='Continue membership')
        await self.adapter.page.locator('h1').evaluate('(h)=>h.textContent="Revised membership"')
        await ui.get_by_role('button', name='Показати поля', exact=True).click()
        # A stale request cannot compose old fields; the renderer fetches a new
        # clarification and clears its synthetic selection rather than replaying it.
        for _ in range(100):
            if self.composer.calls == 2 and await select.input_value() == '':
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.composer.calls, 2)
        self.assertEqual(await select.input_value(), '')
        self.assertEqual(await ui.locator('input[type=password]').count(), 0)
        self.assertEqual(sum(item['path'] == '/api/choose-action' for item in self.private_requests), 1)
        await self.assert_untouched()
        await select.select_option(label='Continue membership')
        await ui.get_by_role('button', name='Показати поля', exact=True).click()
        await ui.get_by_label('Access phrase', exact=True).wait_for()
        self.assertEqual(self.composer.calls, 3)
        self.assertFalse(self.route_errors)
        await self.assert_untouched()

    async def test_private_renderer_component_failure_does_not_autoretry_and_remains_cancellable(self):
        parent, ui = await self.private_ui()
        await ui.locator('select').wait_for()
        await ui.locator('select').select_option(label='Continue membership')
        self.component_failure = True
        await ui.get_by_role('button', name='Показати поля', exact=True).click()
        await ui.locator('#error').wait_for(state='visible')
        await asyncio.sleep(1.7)  # Includes the renderer's next real polling tick.
        self.assertEqual(self.composer.calls, 2)
        self.assertEqual(sum(item['path'] == '/api/choose-action' for item in self.private_requests), 1)
        self.assertTrue(await ui.get_by_role('button', name='Показати поля', exact=True).is_disabled())
        self.assertFalse(await ui.locator('#cancel').is_disabled())
        self.assertNotIn('PRIVATE_FAILURE_CANARY', await ui.locator('body').inner_text())
        await self.assert_untouched()
        await ui.locator('#cancel').click()
        for _ in range(100):
            if self.broker.leases[REQUEST].cleanup == 'confirmed':
                break
            await asyncio.sleep(0.02)
        self.assertEqual(self.broker.leases[REQUEST].state, 'cancelled')
        self.assertEqual(self.broker.leases[REQUEST].cleanup, 'confirmed')
        self.assertFalse(self.route_errors)
        self.assertNotIn('PRIVATE_FAILURE_CANARY', json.dumps(await parent.evaluate('window.receipts')))

    async def test_post_input_clarification_keeps_reflected_values_private_and_does_not_submit_again(self):
        self.anchor_choices = ['Continue membership']
        self.html = PAGE + '''<script>
document.querySelector('form').onsubmit=event=>{
 event.preventDefault();const secret=document.querySelector('input[type=password]').value;
 document.body.innerHTML='<h1>Try another proof for '+secret+'</h1>'+
 '<p role="alert">That method needs a different proof '+secret+'</p>'+
 '<form method="post"><label>Replacement proof<input required></label>'+
 '<button>Try another proof '+secret+'</button></form>';
};</script>'''
        first = await self.attach()
        self.assertEqual(first['stage'], 'form')
        values = {field['name']: CANARY if field['type'] == 'password' else 'fixture@member.invalid'
                  for field in first['fields']}
        await self.call('/api/submit', {'revision': first['revision'],
                        'action': first['actions'][0]['id'], 'values': values})
        form = (await self.call('/api/form'))['form']
        self.assertEqual(form['stage'], 'clarification')
        self.assertIn(CANARY, await self.adapter.page.locator('h1').inner_text())
        self.assertNotIn(CANARY, json.dumps(form))
        self.assertTrue(self.adapter._human_values_entered)
        self.assertTrue(self.adapter._submitted)
        before = (await self.adapter.page.evaluate('window.events'),
                  deepcopy(self.adapter._scene_history), list(self.adapter._redactions),
                  list(self.adapter._credential_values))
        result = await self.call('/api/choose-action',
                                 {'revision': form['revision'], 'choice': form['fields'][0]['options'][0]['value']})
        self.assertTrue(result['accepted'])
        prepared = (await self.call('/api/form'))['form']
        self.assertEqual(prepared['stage'], 'form')
        self.assertEqual(before, (await self.adapter.page.evaluate('window.events'),
                                 self.adapter._scene_history, self.adapter._redactions,
                                 self.adapter._credential_values))
        self.assertEqual(await self.adapter.page.locator('input').input_value(), '')
        self.assertEqual(self.site_requests, [('GET', '/login')])
        self.assertEqual(self.composer.calls, 4)
        for private in (CANARY, 'fixture@member.invalid'):
            self.assertNotIn(private, json.dumps(self.payloads))
            self.assertNotIn(private, json.dumps(prepared))
            self.assertNotIn(private, json.dumps(self.adapter.diagnostics()))
        self.assertTrue(all(payload['state']['after_input'] for payload in self.payloads[2:]))

    async def test_stale_choice_refuses_before_composition_and_can_show_fresh_clarification(self):
        old = await self.attach()
        await self.adapter.page.locator('h1').evaluate('(h)=>h.textContent="Membership details changed"')
        denied = await self.choose(old, status=409)
        self.assertEqual(denied['code'], 'stale_form')
        self.assertEqual(self.composer.calls, 1)
        await self.assert_untouched()
        fresh = (await self.call('/api/form'))['form']
        self.assertEqual(fresh['stage'], 'clarification')
        self.assertNotEqual(fresh['revision'], old['revision'])
        await self.choose(fresh)
        self.assertEqual((await self.call('/api/form'))['form']['stage'], 'form')
        self.assertEqual(self.composer.calls, 3)
        await self.assert_untouched()

    async def test_scene_replaced_during_components_never_publishes_stale_fields(self):
        form = await self.attach()

        async def mutate():
            await self.adapter.page.locator('h1').evaluate('(h)=>h.textContent="Different membership"')

        self.before_components = mutate
        denied = await self.choose(form, status=409)
        self.assertEqual(denied['code'], 'stale_form')
        self.before_components = None
        await self.assert_untouched()
        await self.choose(form, status=409)
        self.assertEqual(self.composer.calls, 2, 'A spent clarification must never repeat composition')

    async def test_successful_duplicate_is_idempotent_but_different_choice_never_replans(self):
        form = await self.attach()
        self.assertEqual(form['stage'], 'clarification')
        self.assertTrue((await self.choose(form))['accepted'])
        prepared = (await self.call('/api/form'))['form']
        self.assertTrue((await self.choose(form))['accepted'])
        self.assertEqual((await self.call('/api/form'))['form'], prepared)
        await self.choose(form, label='Register a membership', status=409)
        self.assertEqual(self.composer.calls, 2)
        await self.assert_untouched()

    async def test_component_failure_is_cached_without_poll_or_choice_retry(self):
        form = await self.attach()
        self.component_failure = True
        denied = await self.choose(form, status=409)
        self.assertEqual(denied['code'], 'composition_unavailable')
        for _ in range(2):
            await self.choose(form, status=409)
            await self.call('/api/form', status=409)
        self.assertEqual(self.composer.calls, 2)
        self.assertEqual(self.broker.leases[REQUEST].state, 'active')
        self.assertNotIn('PRIVATE_FAILURE_CANARY', json.dumps(denied))
        await self.assert_untouched()

    async def test_exhausted_model_quota_does_not_dispatch_components(self):
        self.composer.max_calls = 1
        form = await self.attach()
        await self.choose(form, status=409)
        await self.call('/api/form', status=409)
        self.assertEqual(self.composer.calls, 1)
        self.assertEqual(self.planner._post.await_count, 1)
        await self.assert_untouched()

    async def test_transport_failure_is_not_misrepresented_as_human_clarification(self):
        self.planner._post.side_effect = RuntimeError('PRIVATE_TRANSPORT_CANARY')
        attached = await self.call('/api/attach', {'ticket': self.launch['ticket']})
        self.headers['Authorization'] = 'Bearer ' + attached['lease']
        for _ in range(2):
            failed = await self.call('/api/form', status=409)
            self.assertEqual(failed['code'], 'composition_unavailable')
            self.assertNotIn('PRIVATE_TRANSPORT_CANARY', json.dumps(failed))
        self.assertEqual(self.planner._post.await_count, 1)
        await self.assert_untouched()

    async def test_missing_or_corrupt_model_abstention_evidence_cannot_offer_choice(self):
        mutation = 'missing'

        async def invalid_evidence(scene):
            error = WebsitePlannerError('no_candidate')
            if mutation != 'missing':
                from oak.website_scene_planner import anchor_payload
                criteria = anchor_payload(scene)['questions']['anchor']['criteria']
                answer = {'choice': 'none', 'confidence': 1,
                          'probabilities': {item: float(item == 'none') for item in criteria}}
                if mutation == 'unknown_reference':
                    answer['probabilities']['a999999'] = 0
                elif mutation == 'invalid_confidence':
                    answer['confidence'] = True
                else:
                    answer['choice'] = next(item for item in criteria if item != 'none')
                error.decision = {'anchor': {'anchor': answer}}
            raise error

        self.composer.plan = AsyncMock(side_effect=invalid_evidence)
        attached = await self.call('/api/attach', {'ticket': self.launch['ticket']})
        self.headers['Authorization'] = 'Bearer ' + attached['lease']
        for index, mutation in enumerate(('missing', 'unknown_reference', 'invalid_confidence', 'not_abstention')):
            with self.subTest(mutation=mutation):
                await self.adapter.page.locator('h1').evaluate('(h,text)=>h.textContent=text', 'Membership ' + str(index))
                for _ in range(2):
                    failed = await self.call('/api/form', status=409)
                    self.assertEqual(failed['code'], 'composition_unavailable')
                self.assertEqual(self.composer.plan.await_count, index + 1)
                self.assertEqual(self.adapter.diagnostics()['clarification']['status'], 'none')
        self.assertEqual(self.planner._post.await_count, 0)
        await self.assert_untouched()

    async def test_choice_endpoint_rejects_cross_origin_lease_and_unexpected_private_fields(self):
        form = await self.attach()
        payload = {'revision': form['revision'], 'choice': self.choice(form)}
        await self.call('/api/choose-action', payload, headers={**self.headers, 'Origin': PARENT}, status=403)
        await self.call('/api/choose-action', payload,
                        headers={**self.headers, 'Authorization': 'Bearer ' + 'x' * 43}, status=409)
        await self.call('/api/choose-action', {**payload, 'request_id': 'd' * 32}, status=409)
        await self.call('/api/choose-action', {**payload, 'values': {'f0': CANARY}}, status=409)
        await self.call('/api/choose-action', {**payload, 'action': 'a0'}, status=409)
        self.assertEqual(self.composer.calls, 1)
        await self.assert_untouched()
        await self.choose(form)
        self.assertEqual(self.composer.calls, 2)

    async def test_invalid_opaque_choice_cannot_select_arbitrary_or_raw_dom_action(self):
        form = await self.attach()
        raw = self.payloads[0]['state']['actions'][0]['ref']
        for choice in ('o999', raw, 'Continue membership', {'choice': self.choice(form)}):
            await self.call('/api/choose-action', {'revision': form['revision'], 'choice': choice}, status=409)
        self.assertEqual(self.composer.calls, 1)
        await self.assert_untouched()

    async def test_expired_choice_cannot_compose_or_execute(self):
        form = await self.attach()
        self.broker.leases[REQUEST].idle_deadline = time.time() - 1
        await self.choose(form, status=409)
        self.assertEqual(self.composer.calls, 1)
        self.assertEqual(self.site_requests, [('GET', '/login')])

    async def test_cancelled_component_attempt_cannot_be_replayed(self):
        form = await self.attach()
        entered = asyncio.Event()

        async def wait_forever():
            entered.set()
            await asyncio.Event().wait()

        self.before_components = wait_forever
        task = asyncio.create_task(self.adapter.choose_action(form['revision'], self.choice(form)))
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.before_components = None
        with self.assertRaises(RecoverableFormError):
            await self.adapter.choose_action(form['revision'], self.choice(form))
        self.assertEqual(self.composer.calls, 2)
        await self.assert_untouched()

    async def test_more_than_32_candidates_is_unavailable_without_silent_truncation(self):
        self.html = '<h1>Choose membership</h1>' + ''.join(
            '<form method="post"><button>Choice ' + str(index) + '</button></form>' for index in range(33))
        attached = await self.call('/api/attach', {'ticket': self.launch['ticket']})
        self.headers['Authorization'] = 'Bearer ' + attached['lease']
        failed = await self.call('/api/form', status=409)
        self.assertEqual(failed['code'], 'composition_unavailable')
        self.assertEqual(len(self.payloads[0]['state']['anchor_candidates']), 33)
        self.assertEqual(self.site_requests, [('GET', '/login')])


if __name__ == '__main__':
    unittest.main()
