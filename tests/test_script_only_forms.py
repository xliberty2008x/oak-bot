"""Browser-enforced native-form blocking; every network request is intercepted."""

import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_forms import WebsiteForms
from oak.website_planner import decision_payload
from oak.website_ui import CATALOG_ID, CATALOG_VERSION


def choose_first(observation):
    group = observation['groups'][0]
    return {'plan': {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
        'revision': observation['revision'], 'group_id': group['id'],
        'fields': [field['name'] for field in group['fields']],
        'actions': [action['id'] for action in group['actions']]}}


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in intercepted Chromium fixtures')
class ScriptOnlyFormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.resource = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.resource.close)
        self.context = await self.resource.new_context(service_workers='block')
        self.requests = []
        async def intercept(route):
            self.requests.append((route.request.method, route.request.resource_type))
            await route.fulfill(status=200, content_type='text/html', body='<html><body>Fixture</body></html>')
        await self.context.route('**/*', intercept)
        self.page = await self.context.new_page()
        await self.page.goto('https://example.com/login')
        self.requests.clear()
        self.browser = WebsiteForms('https://example.com/login')
        self.browser.page, self.browser.context = self.page, self.context

    async def fixture(self, attributes='', button='Submit', script='', controls=None):
        if controls is None:
            controls = '<input name="username" type="text"><input name="password" type="password">'
        await self.page.set_content('<form ' + attributes + '>' + controls +
                                    '<button type="submit">' + button + '</button></form>')
        if script:
            await self.page.evaluate(script)
        return await self.browser.snapshot()

    async def submit(self, state):
        await self.browser.submit(state['revision'], state['actions'][0]['id'],
            {field['name']: 'synthetic-user' if field['type'] == 'text' else 'synthetic-password'
             for field in state['fields']})
        await self.page.wait_for_timeout(100)

    async def test_snapshot_exposes_eligible_fields_without_installing_policy(self):
        state = await self.fixture()
        self.assertEqual(state['stage'], 'form')
        self.assertEqual([field['type'] for field in state['fields']], ['text', 'password'])
        self.assertEqual(await self.page.locator('meta[http-equiv="Content-Security-Policy"]').count(), 0)
        self.assertEqual(self.requests, [])

    async def test_explicit_get_action_and_target_stay_rejected_for_identifiers(self):
        for attributes in ('method="get"', 'method=""', 'action=""', 'action="/login"',
                           'target=""', 'target="_self"', 'target="_blank"',
                           'method="get" action="https://attacker.example/"'):
            state = await self.fixture(attributes, controls='<input name="identifier" type="email">')
            self.assertEqual(state['stage'], 'unsupported_layout', attributes)
            self.assertEqual((state['fields'], state['actions']), ([], []))
        self.assertEqual(self.requests, [])

    async def test_default_get_requires_nonempty_supported_active_controls(self):
        for controls in ('', '<input type="hidden">', '<input disabled>', '<input readonly>',
                         '<input type="file">', '<input type="email"><input type="file">'):
            state = await self.fixture(controls=controls)
            self.assertEqual(state['stage'], 'unsupported_layout', controls)
            self.assertEqual((state['fields'], state['actions']), ([], []))
        self.assertEqual(self.requests, [])

    async def test_identifier_only_delegated_javascript_posts_once_without_native_get(self):
        state = await self.fixture(controls='<input name="identifier" type="email">', script="""() => {
            document.addEventListener('submit', e => {
                e.preventDefault(); fetch('/session', {method:'POST', body:new FormData(e.target)});
            });
        }""")
        self.assertEqual([field['type'] for field in state['fields']], ['email'])
        self.assertEqual(await self.page.locator('meta[http-equiv="Content-Security-Policy"]').count(), 0)
        await self.browser.submit(state['revision'], state['actions'][0]['id'],
                                  {state['fields'][0]['name']: 'fixture-user@example.test'})
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.requests, [('POST', 'fetch')])
        self.assertEqual(self.page.url, 'https://example.com/login')
        with self.assertRaises(RecoverableFormError):
            await self.submit(state)
        self.assertEqual(self.requests, [('POST', 'fetch')])

    async def test_identifier_missing_prevent_default_blocks_native_get_after_javascript_post(self):
        state = await self.fixture(controls='<input name="identifier">', script="""() => {
            document.querySelector('form').addEventListener('submit', e => {
                fetch('/session', {method:'POST', body:new FormData(e.target)});
            });
        }""")
        await self.submit(state)
        self.assertEqual(self.requests, [('POST', 'fetch')])
        self.assertEqual(self.page.url, 'https://example.com/login')
        after = await self.browser.snapshot()
        self.assertEqual(after['stage'], 'unsupported_layout')
        self.assertEqual((after['fields'], after['actions']), ([], []))
        self.assertFalse(await self.browser.verified())

    async def test_identifier_prototype_submit_on_first_input_is_blocked(self):
        state = await self.fixture(controls='<input name="identifier">', script="""() => {
            document.querySelector('button').addEventListener('click', () => { window.clicked=true; });
            document.querySelector('input').addEventListener('input', e => {
                window.guardPresent=!!document.querySelector('meta[http-equiv="Content-Security-Policy"]');
                HTMLFormElement.prototype.submit.call(e.target.form);
            });
        }""")
        try:
            await self.submit(state)
        except SubmitUncertainError:
            # CSP may report its violation before the remaining transaction.
            # An early abort must not dispatch the human's final click.
            self.assertFalse(await self.page.evaluate('!!window.clicked'))
            await self.page.wait_for_timeout(100)
        self.assertTrue(await self.page.evaluate('window.guardPresent'))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.page.url, 'https://example.com/login')
        self.assertEqual((await self.browser.snapshot())['stage'], 'unsupported_layout')

    async def test_identifier_submission_permanently_excludes_reflected_text_from_model_payload(self):
        payloads = []
        async def choose(observation):
            payloads.append(decision_payload(observation))  # Projection only; no API.
            return choose_first(observation)
        self.browser._composer = SimpleNamespace(plan=choose)
        state = await self.fixture(controls='<label>Member identifier<input name="identifier"></label>',
            script="""() => {
                document.querySelector('form').addEventListener('submit', e => {
                    e.preventDefault();
                    const value=document.querySelector('input').value;
                    document.body.innerHTML='<h1>Followup '+value+'</h1><form>'
                        +'<label>Private field '+value+'<input type="password"></label>'
                        +'<button type="button">Private action '+value+'</button></form>';
                });
            }""")
        self.assertIn('public_context', payloads[-1]['state'])
        human_value = 'Human identifier fixture 927'
        await self.browser.submit(state['revision'], state['actions'][0]['id'],
                                  {state['fields'][0]['name']: human_value})
        self.assertIn(human_value, await self.page.locator('h1').inner_text())
        await self.browser.snapshot()
        self.assertTrue(self.browser._human_values_entered)
        for navigate in (False, True):
            if navigate:
                await self.page.goto('https://example.com/fresh')
                await self.fixture(controls='<label>Private field ' + human_value +
                                   '<input name="identifier"></label>', button='Private action ' + human_value)
            payload = payloads[-1]
            self.assertNotIn('public_context', payload['state'])
            for group in payload['state']['groups']:
                for item in group['fields'] + group['actions']:
                    self.assertNotIn('label', item)
            for text in (human_value, 'Followup', 'Private field', 'Private action'):
                self.assertNotIn(text, json.dumps(payload))
        self.assertEqual(self.requests, [('GET', 'document')])

    async def test_native_get_submission_is_blocked(self):
        state = await self.fixture()
        await self.submit(state)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.page.url, 'https://example.com/login')
        after = await self.browser.snapshot()
        self.assertEqual(after['stage'], 'unsupported_layout')
        self.assertEqual((after['fields'], after['actions']), ([], []))
        state = await self.page.evaluate('(key)=>({blocked:window[key].nativeFormBlocked, '
                                         'hasURI:Object.hasOwn(window[key],"blockedURI")})', self.browser._state_key)
        self.assertEqual(state, {'blocked': True, 'hasURI': False})
        self.assertFalse(await self.browser.verified())

    async def test_direct_prototype_submit_is_blocked_before_first_input_handler(self):
        state = await self.fixture(script="""() => {
            document.querySelector('button').addEventListener('click', () => { window.clicked=true; });
            document.querySelector('input').addEventListener('input', e => {
                window.guardPresent=!!document.querySelector('meta[http-equiv="Content-Security-Policy"]');
                window.remainingEmpty=document.querySelector('input[type=password]').value==='';
                HTMLFormElement.prototype.submit.call(e.target.form);
            });
        }""")
        try:
            await self.submit(state)
        except SubmitUncertainError:
            # The violation event can arrive at either awaited fill boundary.
            # That is a correct uncertain abort, never permission to retry.
            self.assertFalse(await self.page.evaluate('!!window.clicked'))
            await self.page.wait_for_timeout(100)
        self.assertTrue(await self.page.evaluate('window.guardPresent'))
        self.assertTrue(await self.page.evaluate('window.remainingEmpty'))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.page.url, 'https://example.com/login')
        self.assertEqual((await self.browser.snapshot())['stage'], 'unsupported_layout')

    async def test_removing_meta_cannot_restore_native_submission(self):
        state = await self.fixture(script="""() => { document.querySelector('button').onclick=()=>{
            document.querySelector('meta[http-equiv="Content-Security-Policy"]').remove();
            HTMLFormElement.prototype.submit.call(document.querySelector('form'));
        }; }""")
        await self.submit(state)
        self.assertEqual(await self.page.locator('meta[http-equiv="Content-Security-Policy"]').count(), 0)
        self.assertEqual(self.requests, [])

    async def test_restriction_is_document_wide_until_navigation(self):
        state = await self.fixture()
        await self.submit(state)
        await self.page.evaluate("""() => {
            const form=document.createElement('form'); form.method='post'; form.action='/session';
            document.body.append(form); HTMLFormElement.prototype.submit.call(form);
        }""")
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.requests, [])
        await self.page.goto('https://example.com/fresh')
        self.requests.clear()
        await self.page.set_content('<form method="post" action="/session"><button>Continue</button></form>')
        await self.page.click('button')
        self.assertEqual(self.requests, [('POST', 'document')])

    async def test_same_document_post_transition_is_unavailable_until_navigation(self):
        state = await self.fixture(script="""() => {
            document.querySelector('form').addEventListener('submit', e => {
                e.preventDefault();
                document.body.innerHTML='<form method="post" action="/session"><input name="code">'
                    +'<button>Next</button></form>';
            });
        }""")
        await self.submit(state)
        next_state = await self.browser.snapshot()
        self.assertEqual(next_state['stage'], 'unsupported_layout')
        self.assertEqual((next_state['fields'], next_state['actions']), ([], []))
        with self.assertRaises(ValueError):
            await self.browser.submit(next_state['revision'], 'a1', {})
        self.assertEqual(self.requests, [])
        await self.page.goto('https://example.com/fresh')
        state = await self.fixture('method="post"')
        self.assertEqual(state['stage'], 'form')

    async def test_delegated_javascript_posts_once_with_no_native_get_or_replay(self):
        state = await self.fixture(script="""() => {
            const button=document.querySelector('button'); button.disabled=true;
            document.querySelector('form').addEventListener('input',()=>{
                button.disabled=![...document.querySelectorAll('input')].every(e=>e.value);
            });
            document.addEventListener('submit',e=>{
                window.wasPrevented=e.defaultPrevented;
                e.preventDefault();
                fetch('/session',{method:'POST',body:new FormData(e.target)});
            });
        }""")
        await self.submit(state)
        self.assertFalse(await self.page.evaluate('window.wasPrevented'))
        self.assertEqual(self.requests, [('POST', 'fetch')])
        with self.assertRaises(RecoverableFormError):
            await self.submit(state)
        self.assertEqual(self.requests, [('POST', 'fetch')])

    async def test_stale_eligibility_changes_reject_before_fill_or_policy_install(self):
        for attribute, value in (('method', 'get'), ('action', ''), ('target', '')):
            state = await self.fixture()
            await self.page.evaluate('([attribute,value])=>document.querySelector("form").setAttribute(attribute,value)',
                                     [attribute, value])
            with self.assertRaises(RecoverableFormError) as error:
                await self.submit(state)
            self.assertEqual(error.exception.code, 'stale_form')
            self.assertEqual(await self.page.input_value('input[name=username]'), '')
            self.assertEqual(await self.page.locator('meta[http-equiv="Content-Security-Policy"]').count(), 0)
        self.assertEqual(self.requests, [])

    async def test_selecting_post_group_does_not_install_other_groups_script_policy(self):
        self.browser._composer = SimpleNamespace(plan=AsyncMock(side_effect=choose_first))
        await self.page.set_content('<form method="post" action="/session"><input name="username">'
            '<input name="password" type="password"><button>Sign in</button></form>'
            '<form><input type="password"><button>Other sign in</button></form>')
        state = await self.browser.snapshot()
        observed = self.browser._composer.plan.await_args.args[0]
        self.assertEqual(len(observed['groups']), 2)
        self.assertEqual(await self.page.locator('meta[http-equiv="Content-Security-Policy"]').count(), 0)
        await self.submit(state)
        self.assertEqual(self.requests, [('POST', 'document')])

    async def test_script_group_with_competing_post_does_not_invalidate_itself(self):
        self.browser._composer = SimpleNamespace(plan=AsyncMock(side_effect=choose_first))
        await self.page.set_content('<form><input name="username"><input name="password" type="password">'
            '<button>Sign in</button></form><form method="post" action="/other">'
            '<input type="password"><button>Other sign in</button></form>')
        await self.page.evaluate("""() => {
            document.forms[0].addEventListener('submit', e => {
                e.preventDefault(); fetch('/session', {method:'POST',body:new FormData(e.target)});
            });
        }""")
        state = await self.browser.snapshot()
        self.assertEqual(len(self.browser._composer.plan.await_args.args[0]['groups']), 2)
        await self.submit(state)
        self.assertEqual(self.requests, [('POST', 'fetch')])
        next_state = await self.browser.snapshot()
        self.assertEqual(next_state['stage'], 'form')
        self.assertEqual(len(self.browser._composer.plan.await_args.args[0]['groups']), 1)

    async def test_method_mutation_during_first_fill_stops_remaining_inputs_and_click(self):
        state = await self.fixture(script="""() => {
            document.querySelector('input').addEventListener('input', e => {
                e.target.form.method='post';
            });
            document.querySelector('button').addEventListener('click', () => { window.clicked=true; });
        }""")
        with self.assertRaises(SubmitUncertainError):
            await self.submit(state)
        self.assertEqual(await self.page.input_value('input[type=password]'), '')
        self.assertFalse(await self.page.evaluate('!!window.clicked'))
        self.assertEqual((await self.browser.snapshot())['stage'], 'unsupported_layout')
        self.assertEqual(self.requests, [])

    async def test_otp_default_form_is_eligible_but_explicit_get_override_is_not(self):
        await self.page.set_content('<form><input autocomplete="one-time-code"><button>Verify</button></form>')
        state = await self.browser.snapshot()
        self.assertEqual([field['type'] for field in state['fields']], ['otp'])
        await self.page.evaluate('document.querySelector("button").setAttribute("formmethod","get")')
        self.assertEqual((await self.browser.snapshot())['stage'], 'unsupported_layout')


if __name__ == '__main__':
    unittest.main()
