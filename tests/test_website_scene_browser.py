"""Intercepted Chromium integration for grounded scenes; no model or account calls."""

import base64
from copy import deepcopy
import json
import os
import unittest
from urllib.parse import quote, urlsplit

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_forms import WebsiteForms
from oak.website_planner import WebsitePlannerError
from oak.website_scene import CATALOG_ID, CATALOG_VERSION, model_context


COMPONENT = {'text': 'TextInput', 'email': 'EmailInput', 'password': 'PasswordInput',
             'otp': 'CodeInput', 'select': 'Select', 'checkbox': 'Checkbox'}


class FixtureComposer:
    scene_capable = True

    def __init__(self):
        self.scenes = []
        self.anchor_label = 'Continue'
        self.omit_labels = set()
        self.include_labels = set()
        self.components = {}
        self.before_return = None

    async def plan(self, scene):
        self.scenes.append(deepcopy(scene))
        anchor = next(item for item in scene['actions'] if item['label'] == self.anchor_label)
        fields = [item for item in scene['controls']
                  if item['form'] == anchor['form'] and item['label'] not in self.omit_labels]
        errors = [item for item in scene['texts'] if item['kind'] == 'error']
        result = {'plan': {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
            'revision': scene['revision'], 'anchor': anchor['ref'],
            'fields': [{'ref': item['ref'], 'component': self.components.get(item['label'], COMPONENT[item['type']])}
                       for item in fields],
            'actions': [{'ref': anchor['ref'], 'presentation': 'primary'}] +
                       [{'ref': item['ref'], 'presentation': 'secondary'} for item in scene['actions']
                        if item['ref'] != anchor['ref'] and item['label'] in self.include_labels],
            'message_ref': errors[0]['ref'] if errors else None}}
        if self.before_return:
            await self.before_return()
        return result


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class WebsiteSceneBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.requests = []
        self.documents = {}

        async def intercept(route):
            parsed = urlsplit(route.request.url)
            self.requests.append((route.request.method, route.request.resource_type, parsed.path))
            await route.fulfill(status=200, content_type='text/html',
                                body=self.documents.get(parsed.path, '<body>Fixture</body>'))

        await self.context.route('**/*', intercept)
        self.page = await self.context.new_page()
        await self.page.goto('https://scene-fixture.example/login')
        self.requests.clear()
        self.composer = FixtureComposer()
        self.browser = WebsiteForms('https://scene-fixture.example/login', composer=self.composer)
        self.browser.context, self.browser.page = self.context, self.page
        self.addAsyncCleanup(self.browser.close)

    async def fixture(self, html, script=None):
        await self.page.set_content(html)
        if script:
            await self.page.evaluate(script)
        return await self.browser.snapshot()

    def values(self, form, by_label=None):
        by_label = by_label or {}
        return {field['name']: by_label.get(field['label'],
                False if field['type'] == 'checkbox' else
                field['options'][0]['value'] if field['type'] == 'select' else 'fixture-value')
                for field in form['fields']}

    async def submit(self, form, by_label=None):
        await self.browser.submit(form['revision'], form['actions'][0]['id'], self.values(form, by_label))

    async def test_more_than_eight_actions_allow_grounded_subset_and_optional_omission(self):
        self.composer.omit_labels = {'Unrelated optional preference'}
        self.composer.include_labels = {'Recover account'}
        form = await self.fixture('<form method="post" onsubmit="event.preventDefault();window.sent=true">'
            '<label>Member<input required></label><label>Unrelated optional preference<input></label>'
            + ''.join('<button type="button">Unrelated action ' + str(index) + '</button>' for index in range(10))
            + '<button type="button">Recover account</button><button>Continue</button></form>')
        self.assertEqual(len(self.composer.scenes[-1]['actions']), 12)
        self.assertEqual([item['label'] for item in form['actions']], ['Continue', 'Recover account'])
        self.assertEqual([item['label'] for item in form['fields']], ['Member'])
        await self.submit(form)
        self.assertTrue(await self.page.evaluate('window.sent'))
        self.assertEqual(await self.page.locator('input').nth(1).input_value(), '')
        self.assertEqual(self.requests, [])

    async def test_required_native_control_cannot_be_omitted_by_composer(self):
        self.composer.omit_labels = {'Required agreement'}
        with self.assertRaises(RecoverableFormError) as error:
            await self.fixture('<form method="post"><label>Member<input></label>'
                               '<label>Required agreement<input type="checkbox" required></label>'
                               '<button>Continue</button></form>')
        self.assertEqual(error.exception.code, 'composition_unavailable')
        self.assertEqual(await self.page.locator('input').first.input_value(), '')
        self.assertEqual(self.requests, [])

    async def test_unrepresented_required_native_control_disallows_action(self):
        with self.assertRaises(RecoverableFormError):
            await self.fixture('<form method="post"><label>Member<input></label>'
                               '<input required type="file"><button>Continue</button></form>')
        self.assertFalse(self.composer.scenes[-1]['actions'][0]['permitted'])
        self.assertEqual(self.requests, [])

    async def test_error_semantics_survive_but_reflected_input_is_redacted_after_navigation(self):
        secret = 'Private phrase 864 qv'
        encoded = quote(secret, safe="~()*!.'-")
        base64_secret = base64.b64encode(secret.encode()).decode()
        form = await self.fixture('<form method="post"><label>Member<input></label><button>Continue</button></form>',
            """() => document.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();const entered=document.querySelector('input').value;
                document.body.innerHTML='<h1>Check membership</h1><p role="alert">Try another member: '+entered+'</p>'+
                  '<form method="post"><label>Member '+entered+'<input></label><button>Continue</button></form>';
            })""")
        await self.submit(form, {'Member': secret})
        next_form = await self.browser.snapshot()
        self.assertIn(secret, await self.page.locator('[role=alert]').inner_text())
        self.assertIn('Try another member', next_form['message'])
        self.assertNotIn(secret, json.dumps(next_form))
        self.assertTrue(self.composer.scenes[-1]['after_input'])
        self.documents['/fresh'] = ('<h2>Choose another method</h2><p role="alert">Identifier was rejected: '
            + secret + ' ' + encoded + ' ' + base64_secret + '</p><form method="post">'
            '<label>Replacement member<input></label><button>Continue</button></form>')
        await self.page.goto('https://scene-fixture.example/fresh')
        await self.browser.snapshot()
        projected = model_context(self.composer.scenes[-1])
        serialized = json.dumps(projected)
        self.assertTrue(projected['after_input'])
        self.assertIn('Identifier was rejected', serialized)
        self.assertIn('Choose another method', serialized)
        for value in (secret, encoded, base64_secret, 'scene-fixture.example'):
            self.assertNotIn(value, serialized)
        self.assertEqual(self.requests, [('GET', 'document', '/fresh')])

    async def test_checked_checkbox_explicit_native_value_stays_private_after_navigation(self):
        private_value = 'private-check-927'
        self.documents['/checked'] = ('<h2>Choose another method</h2><p role="alert">Membership flag '
            + private_value + ' needs another step.</p><form method="post">'
            '<label>Next identifier<input></label><button>Continue</button></form>')
        form = await self.fixture('<form method="post"><label>Use membership<input type="checkbox" '
            'name="membership" value="' + private_value + '"></label><button>Continue</button></form>',
            """() => document.querySelector('form').onsubmit=event=>{
                event.preventDefault();
                if(event.target.elements.membership.checked)location.href='/checked';
            }""")
        self.assertEqual(form['fields'][0]['type'], 'checkbox')
        self.assertNotIn(private_value, json.dumps(self.composer.scenes[-1]))
        await self.submit(form, {'Use membership': True})
        await self.page.wait_for_url('https://scene-fixture.example/checked')
        self.assertIn(private_value, await self.page.locator('[role=alert]').inner_text())
        self.assertIn(private_value, self.browser._redactions)
        after = await self.browser.snapshot()
        projected = model_context(self.composer.scenes[-1])
        self.assertTrue(projected['after_input'])
        self.assertIn('needs another step', after['message'])
        self.assertNotIn(private_value, json.dumps(projected))
        self.assertNotIn(private_value, json.dumps(after))
        self.assertEqual(self.requests, [('GET', 'document', '/checked')])

    async def test_unchecked_checkbox_does_not_capture_its_unsubmitted_native_value(self):
        private_value = 'unused-check-468'
        form = await self.fixture('<form method="post" onsubmit="event.preventDefault();window.sent=true">'
            '<label>Use membership<input type="checkbox" name="membership" value="' + private_value
            + '" checked></label><button>Continue</button></form>')
        await self.submit(form, {'Use membership': False})
        self.assertTrue(await self.page.evaluate('window.sent'))
        self.assertFalse(await self.page.locator('input').is_checked())
        self.assertNotIn(private_value, self.browser._redactions)
        self.assertEqual(self.requests, [])

    async def test_page_split_override_cannot_disclose_reflected_short_password_to_composer(self):
        secret = 'qv7-Sun'
        form = await self.fixture('<form method="post"><label>Phrase<input type="password"></label>'
            '<button>Continue</button></form>', """() => {
                const originalSplit=String.prototype.split;
                document.querySelector('form').onsubmit=event=>{
                    event.preventDefault();const entered=document.querySelector('input').value;
                    String.prototype.split=function(separator,...rest){
                        if(separator===entered)return [String(this)];
                        return originalSplit.call(this,separator,...rest);
                    };
                    document.body.innerHTML='<h1>New method for '+entered+'</h1>'+
                        '<p role="alert">Try a different proof '+entered+'</p>'+
                        '<form method="post"><label>New identifier<input></label><button>Continue</button></form>';
                };
            }""")
        await self.submit(form, {'Phrase': secret})
        self.assertIn(secret, await self.page.locator('h1').inner_text())
        self.assertEqual(await self.page.evaluate('(s)=>s.split(s).join("masked")', secret), secret,
                         'The hostile page must actually bypass its JavaScript redactor')
        after = await self.browser.snapshot()
        projected = model_context(self.composer.scenes[-1])
        self.assertTrue(projected['after_input'])
        self.assertIn('Try a different proof', after['message'])
        self.assertNotIn(secret, json.dumps(projected))
        self.assertNotIn(secret, json.dumps(after))
        self.assertIn(secret, self.browser._credential_values)
        self.assertEqual(self.requests, [])

    async def test_dashboard_navigation_after_values_allows_only_explicit_owner_confirmation(self):
        original_plan = self.composer.plan
        abstentions = []

        async def compose(scene):
            if not scene['controls']:
                abstentions.append('no_candidate')
                raise WebsitePlannerError('no_candidate')
            return await original_plan(scene)

        self.composer.plan = compose
        self.documents['/dashboard'] = ('<h1>Membership home</h1>'
            '<a href="/settings">Settings</a><a href="/help">Help</a>')
        form = await self.fixture('<form method="post"><label>Member<input></label><button>Continue</button></form>',
            """() => document.querySelector('form').onsubmit=event=>{
                event.preventDefault();location.href='/dashboard';
            }""")
        await self.submit(form)
        await self.page.wait_for_url('https://scene-fixture.example/dashboard')
        after = await self.browser.snapshot()
        self.assertEqual(after['fields'], [])
        self.assertEqual([action['id'] for action in after['actions']], ['confirm'])
        self.assertTrue(await self.browser.confirmable(after['revision']))
        self.assertFalse(await self.browser.verified())
        self.assertEqual(await self.page.locator('a').count(), 2)
        self.assertTrue(self.browser._submitted)
        self.assertTrue(not abstentions or abstentions == ['no_candidate'])
        self.assertEqual(self.requests, [('GET', 'document', '/dashboard')])

    async def test_default_get_delegated_post_once_and_native_submit_blocked(self):
        form = await self.fixture('<form><label>Member<input name="member"></label><button>Continue</button></form>',
            """() => document.querySelector('form').addEventListener('submit', event => {
                fetch('/session', {method:'POST', body:new FormData(event.target)});
            })""")
        await self.submit(form)
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.requests, [('POST', 'fetch', '/session')])
        self.assertEqual(self.page.url, 'https://scene-fixture.example/login')
        after = await self.browser.snapshot()
        self.assertEqual((after['stage'], after['fields'], after['actions']), ('unsupported_layout', [], []))
        self.assertFalse(await self.browser.verified())

    async def test_prototype_native_get_guard_precedes_first_input_handler(self):
        form = await self.fixture('<form><label>Member<input name="member"></label>'
            '<label>Phrase<input type="password"></label><button>Continue</button></form>',
            """() => {
                document.querySelector('button').onclick=()=>window.clicked=true;
                document.querySelector('input').oninput=event=>{
                    window.guarded=!!document.querySelector('meta[http-equiv="Content-Security-Policy"]');
                    window.remainingEmpty=document.querySelector('input[type=password]').value==='';
                    HTMLFormElement.prototype.submit.call(event.target.form);
                };
            }""")
        try:
            await self.submit(form)
        except SubmitUncertainError:
            self.assertFalse(await self.page.evaluate('!!window.clicked'))
        await self.page.wait_for_timeout(100)
        self.assertTrue(await self.page.evaluate('window.guarded'))
        self.assertTrue(await self.page.evaluate('window.remainingEmpty'))
        self.assertEqual(self.requests, [])
        self.assertEqual((await self.browser.snapshot())['stage'], 'unsupported_layout')

    async def test_open_shadow_root_controls_keep_native_owner_and_submit_once(self):
        form = await self.fixture('<div id="host"></div>', """() => {
            const root=document.querySelector('#host').attachShadow({mode:'open'});
            root.innerHTML='<form method="post"><label>Shadow member<input></label><button>Continue</button></form>';
            root.querySelector('form').onsubmit=event=>{event.preventDefault();window.submits=(window.submits||0)+1};
        }""")
        self.assertEqual([field['label'] for field in form['fields']], ['Shadow member'])
        scene = self.composer.scenes[-1]
        self.assertIsNotNone(scene['controls'][0]['form'])
        self.assertEqual(scene['controls'][0]['form'], scene['actions'][0]['form'])
        await self.submit(form)
        self.assertEqual(await self.page.evaluate('window.submits'), 1)
        with self.assertRaises(RecoverableFormError):
            await self.submit(form)
        self.assertEqual(await self.page.evaluate('window.submits'), 1)

    async def test_stale_dom_replacement_cannot_fill_or_click(self):
        form = await self.fixture('<form method="post"><label>Member<input></label><button>Continue</button></form>')
        await self.page.evaluate("""() => {
            const form=document.querySelector('form');form.replaceWith(form.cloneNode(true));
            document.querySelector('button').onclick=()=>window.clicked=true;
        }""")
        with self.assertRaises(RecoverableFormError) as error:
            await self.submit(form)
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertEqual(await self.page.locator('input').input_value(), '')
        self.assertFalse(await self.page.evaluate('!!window.clicked'))
        self.assertTrue(self.browser._human_values_entered)
        self.assertEqual(self.requests, [])

    async def test_dom_changed_during_composition_is_not_executable(self):
        async def mutate():
            await self.page.locator('button').evaluate('(e)=>e.textContent="Changed action"')
        self.composer.before_return = mutate
        with self.assertRaises(RecoverableFormError) as error:
            await self.fixture('<form method="post"><label>Member<input></label><button>Continue</button></form>')
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertEqual(await self.page.locator('input').input_value(), '')
        self.assertEqual(self.requests, [])

    async def test_midfill_replacement_is_uncertain_and_never_clicks_or_fills_remainder(self):
        form = await self.fixture('<form method="post"><label>Member<input></label>'
            '<label>Phrase<input type="password"></label><button>Continue</button></form>',
            """() => {
                document.querySelector('button').onclick=()=>window.clicked=true;
                document.querySelector('input').oninput=()=>{
                    const remaining=document.querySelector('input[type=password]');
                    remaining.replaceWith(remaining.cloneNode(true));
                };
            }""")
        with self.assertRaises(SubmitUncertainError):
            await self.submit(form)
        self.assertEqual(await self.page.locator('input').first.input_value(), 'fixture-value')
        self.assertEqual(await self.page.locator('input[type=password]').input_value(), '')
        self.assertFalse(await self.page.evaluate('!!window.clicked'))
        self.assertEqual(self.requests, [])

    async def test_code_component_on_text_is_tracked_as_secret_and_blocks_reflected_confirmation(self):
        self.composer.components = {'Membership proof': 'CodeInput'}
        form = await self.fixture('<form method="post"><label>Membership proof<input name="proof"></label>'
            '<button>Continue</button></form>', """() => document.querySelector('form').onsubmit=event=>{
                event.preventDefault();const proof=document.querySelector('input').value;
                document.body.innerHTML='<p>'+proof+'</p>';
            }""")
        self.assertEqual(self.composer.scenes[-1]['controls'][0]['type'], 'text')
        self.assertEqual((form['fields'][0]['type'], form['fields'][0]['max_length']), ('otp', 16))
        await self.submit(form, {'Membership proof': '418297'})
        self.assertIn('418297', self.browser._credential_values)
        after = await self.browser.snapshot()
        self.assertEqual(after['stage'], 'unsupported_layout')
        self.assertNotIn('confirm', [action['id'] for action in after['actions']])
        self.assertFalse(await self.browser.confirmable(after['revision']))
        self.assertFalse(await self.browser.verified())

    async def test_disabled_submit_can_be_enabled_by_selected_field_fill(self):
        form = await self.fixture('<form method="post" onsubmit="event.preventDefault();window.submits=(window.submits||0)+1">'
            '<label>Member<input oninput="this.form.querySelector(\'button\').disabled=!this.value"></label>'
            '<button disabled>Continue</button></form>')
        self.assertFalse(self.composer.scenes[-1]['actions'][0]['enabled'])
        await self.submit(form)
        self.assertEqual(await self.page.evaluate('window.submits'), 1)
        self.assertEqual(self.requests, [])

    async def test_still_disabled_submit_aborts_uncertain_after_fill_without_click(self):
        form = await self.fixture('<form method="post"><label>Member<input></label>'
                                 '<button disabled onclick="window.clicked=true">Continue</button></form>')
        with self.assertRaises(SubmitUncertainError):
            await self.submit(form)
        self.assertEqual(await self.page.locator('input').input_value(), 'fixture-value')
        self.assertFalse(await self.page.evaluate('!!window.clicked'))
        self.assertEqual(self.requests, [])
