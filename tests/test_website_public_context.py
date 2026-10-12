"""Pre-input page text is closed permanently by human input; no external API."""

import asyncio
from copy import deepcopy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_forms import ComposedProviderForms, WebsiteForms
from oak.website_planner import decision_payload
from oak.website_ui import CATALOG_ID, CATALOG_VERSION


ORIGIN = 'https://public-context-fixture.example'
HUMAN_VALUE = 'Human fixture input 739'
PASSWORD = 'Fixture password 826'
FORM = ('<h1>Access your workspace</h1><form method="post">'
        '<label>Member name<input autocomplete="username"></label>'
        '<label>Access phrase<input type="password" autocomplete="current-password"></label>'
        '<button type="button" onclick="window.clicked=(window.clicked||0)+1">Continue</button></form>')
FOLLOWUP = ('<h1>Private followup screen ' + HUMAN_VALUE + '</h1><form method="post">'
            '<label>Private followup field ' + HUMAN_VALUE + '<input type="password"></label>'
            '<button type="button">Private advance ' + HUMAN_VALUE + '</button></form>')


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class WebsitePublicContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)

    async def browser(self, html=FORM):
        context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(context.close)
        documents = {'/login': html, '/next': FOLLOWUP}
        async def route(request):
            from urllib.parse import urlsplit
            parsed = urlsplit(request.request.url)
            if parsed.scheme + '://' + parsed.netloc != ORIGIN:
                await request.abort()
                return
            await request.fulfill(status=200, content_type='text/html',
                                  body=documents.get(parsed.path, '<h1>Fixture</h1>'))
        await context.route('**/*', route)
        observations, payloads = [], []
        async def choose(observation):
            observations.append(deepcopy(observation))
            payloads.append(decision_payload(observation))  # Pure projection, no API.
            group = observation['groups'][0]
            return {'plan': {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
                    'revision': observation['revision'], 'group_id': group['id'],
                    'fields': [field['name'] for field in group['fields']],
                    'actions': [action['id'] for action in group['actions']]}}
        browser = WebsiteForms(ORIGIN + '/login', composer=SimpleNamespace(plan=choose))
        browser.context = context
        await browser._configure_context()
        browser.page = await context.new_page()
        await browser.page.goto(ORIGIN + '/login')
        return browser, observations, payloads

    def values(self, state):
        return {field['name']: PASSWORD if field['type'] == 'password' else HUMAN_VALUE
                for field in state['fields']}

    def assert_private_payload(self, payload):
        state = payload['state']
        self.assertNotIn('public_context', state)
        for group in state['groups']:
            for item in group['fields'] + group['actions']:
                self.assertNotIn('label', item)
        encoded = json.dumps(payload)
        for value in (HUMAN_VALUE, PASSWORD, 'Private followup screen',
                      'Private followup field', 'Private advance'):
            self.assertNotIn(value, encoded)

    async def test_anonymous_headings_and_labels_exclude_hidden_default_and_address_text(self):
        hidden = 'Hidden descendant material'
        default = 'Default field material'
        hidden_value = 'Hidden input material'
        checkbox = 'Checkbox default material'
        selected = 'Select default material'
        email, url, token = 'fixture-person@example.test', 'https://private.example/path', 'OpaqueTokenCanary0123456789abcdef'
        html = ('<h1>Sign in <span style="display:none">' + hidden + '</span></h1>'
                '<h2 style="display:none">Entire hidden heading material</h2>'
                '<h2>' + ' '.join((default, hidden_value, checkbox, selected, email, url, token)) + '</h2>'
                '<h2>' + ('Accessible heading ' * 20) + '</h2><h2>Fourth visible heading</h2>'
                '<form method="post"><label>Member <span style="display:none">' + hidden + '</span>'
                '<input value="' + default + '"></label><input type="hidden" value="' + hidden_value + '">'
                '<label>Remember<input type="checkbox" value="' + checkbox + '"></label>'
                '<label>Route<select><option value="' + selected + '">Option caption marker</option></select></label>'
                '<label>Phrase<input type="password"></label><button type="button">Continue</button></form>')
        browser, observed, payloads = await self.browser(html)
        await browser.snapshot()
        headings = observed[-1]['public_context']['headings']
        self.assertTrue(headings)
        self.assertIn('Sign in', headings[0])
        self.assertLessEqual(len(headings), 3)
        self.assertTrue(all(0 < len(value) <= 120 for value in headings))
        exported = json.dumps(payloads[-1])
        for canary in (hidden, 'Entire hidden heading material', default, hidden_value,
                       checkbox, selected, email, url, token):
            with self.subTest(canary=canary):
                self.assertNotIn(canary, exported)
        fields = payloads[-1]['state']['groups'][0]['fields']
        self.assertIn('label', fields[0])
        select_field = next(field for field in fields if field['type'] == 'select')
        self.assertNotIn('Option caption marker', select_field['label'])

    async def test_first_value_submit_succeeds_once_then_same_document_and_navigation_are_private(self):
        browser, observed, payloads = await self.browser()
        first = await browser.snapshot()
        self.assertIn('public_context', observed[-1])
        await browser.submit(first['revision'], first['actions'][0]['id'], self.values(first))
        self.assertEqual(await browser.page.evaluate('window.clicked'), 1)
        self.assertTrue(browser._human_values_entered)
        after = await browser.snapshot()
        self.assertNotEqual(after['revision'], first['revision'])
        self.assertNotIn('public_context', observed[-1])
        self.assert_private_payload(payloads[-1])
        # A fresh document cannot reset the Python-owned disclosure boundary.
        await browser.page.goto(ORIGIN + '/next')
        await browser.snapshot()
        self.assertNotIn('public_context', observed[-1])
        self.assert_private_payload(payloads[-1])
        self.assertTrue(browser._human_values_entered)

    async def test_empty_method_action_preserves_public_context(self):
        html = ('<h1>Choose a method</h1><button type="button" '
                'onclick="document.body.innerHTML=\'<h1>Enter your phrase</h1>'
                '<form method=post><input type=password><button type=button>Continue</button></form>\'">'
                'Use email</button>')
        browser, observed, payloads = await self.browser(html)
        first = await browser.snapshot()
        self.assertEqual(first['fields'], [])
        await browser.submit(first['revision'], first['actions'][0]['id'], {})
        await browser.snapshot()
        self.assertFalse(browser._human_values_entered)
        self.assertEqual(observed[-1]['public_context']['headings'], ['Enter your phrase'])
        self.assertIn('public_context', payloads[-1]['state'])

    async def test_stale_value_submit_never_fills_but_permanently_closes_public_context(self):
        browser, observed, payloads = await self.browser()
        first = await browser.snapshot()
        await browser.page.evaluate('document.querySelector("h1").textContent="Changed heading"')
        with self.assertRaises(RecoverableFormError) as error:
            await browser.submit(first['revision'], first['actions'][0]['id'], self.values(first))
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertTrue(browser._human_values_entered)
        self.assertEqual(await browser.page.input_value('input[type=password]'), '')
        self.assertFalse(await browser.page.evaluate('!!window.clicked'))
        await browser.snapshot()
        self.assertNotIn('public_context', observed[-1])
        self.assert_private_payload(payloads[-1])

    async def test_dispatch_failure_or_cancellation_cannot_restore_public_context(self):
        for failure in (RuntimeError('synthetic dispatch failure'), asyncio.CancelledError()):
            with self.subTest(kind=type(failure).__name__):
                browser, observed, payloads = await self.browser()
                first = await browser.snapshot()
                evaluate = browser.page.evaluate
                async def interrupted(script, arg=None):
                    if isinstance(arg, dict) and arg.get('operation') == 'submit':
                        self.assertTrue(browser._human_values_entered, 'Gate must close before dispatch')
                        raise failure
                    return await evaluate(script, arg)
                with patch.object(browser.page, 'evaluate', side_effect=interrupted):
                    with self.assertRaises(asyncio.CancelledError if isinstance(failure, asyncio.CancelledError)
                                           else SubmitUncertainError):
                        await browser.submit(first['revision'], first['actions'][0]['id'], self.values(first))
                self.assertTrue(browser._human_values_entered)
                self.assertFalse(await browser.page.evaluate('!!window.clicked'))
                await browser.snapshot()
                self.assertNotIn('public_context', observed[-1])
                self.assert_private_payload(payloads[-1])

    async def test_observation_timeout_or_cancellation_closes_gate_before_dispatch(self):
        for failure in (TimeoutError(), asyncio.CancelledError()):
            with self.subTest(kind=type(failure).__name__):
                browser, observed, payloads = await self.browser()
                first = await browser.snapshot()
                with patch.object(browser, '_observe', AsyncMock(side_effect=failure)):
                    with self.assertRaises(asyncio.CancelledError if isinstance(failure, asyncio.CancelledError)
                                           else ValueError):
                        await browser.submit(first['revision'], first['actions'][0]['id'], self.values(first))
                self.assertTrue(browser._human_values_entered)
                self.assertEqual(await browser.page.input_value('input[type=password]'), '')
                await browser.snapshot()
                self.assertNotIn('public_context', observed[-1])
                self.assert_private_payload(payloads[-1])

    async def test_cancellation_while_waiting_for_lock_still_closes_public_context(self):
        browser, observed, payloads = await self.browser()
        first = await browser.snapshot()
        await browser._lock.acquire()
        task = asyncio.create_task(browser.submit(first['revision'], first['actions'][0]['id'],
                                                  self.values(first)))
        try:
            # Let submit reach its lock acquisition; no DOM input is dispatched.
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            browser._lock.release()
        self.assertTrue(browser._human_values_entered)
        self.assertEqual(await browser.page.input_value('input[type=password]'), '')
        await browser.snapshot()
        self.assertNotIn('public_context', observed[-1])
        self.assert_private_payload(payloads[-1])

    async def test_empty_string_false_and_select_are_human_values(self):
        for control, value in (('<input>', ''), ('<input type="checkbox">', False),
                               ('<select><option value="letter">Letter</option></select>', None)):
            with self.subTest(control=control):
                browser, observed, payloads = await self.browser(
                    '<h1>Account access</h1><form method="post">' + control +
                    '<button type="button" onclick="window.clicked=true">Continue</button></form>')
                first = await browser.snapshot()
                field = first['fields'][0]
                selected = field['options'][0]['value'] if field['type'] == 'select' else value
                await browser.submit(first['revision'], first['actions'][0]['id'], {field['name']: selected})
                self.assertTrue(await browser.page.evaluate('window.clicked'))
                self.assertTrue(browser._human_values_entered)
                await browser.snapshot()
                self.assertNotIn('public_context', observed[-1])
                self.assert_private_payload(payloads[-1])

    async def test_known_fixed_prompt_rejects_values_and_still_closes_disclosure(self):
        browser = ComposedProviderForms('instagram', composer=SimpleNamespace(plan=AsyncMock()))
        context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(context.close)
        await context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html',
            body='<p>Save your login info</p><button onclick="window.clicked=true">Not now</button>'))
        browser.context = context
        await browser._configure_context()
        browser.page = await context.new_page()
        await browser.page.goto(browser.initial_url)
        first = await browser.snapshot()
        self.assertEqual(first['actions'][0]['id'], 'a0')
        with self.assertRaises(ValueError):
            await browser.submit(first['revision'], 'a0', {'unexpected': HUMAN_VALUE})
        self.assertTrue(browser._human_values_entered)
        self.assertFalse(await browser.page.evaluate('!!window.clicked'))
        browser._composer.plan.assert_not_awaited()
        await browser.page.set_content(FORM)
        state = await browser._inspect()
        self.assertNotIn('public_context', state)
        self.assertNotIn('public_context', browser._group_observation(state))

    async def test_cancelled_waiting_submit_discards_inflight_public_composition_cache(self):
        browser, _, _ = await self.browser()
        state = await browser._inspect()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def pending_choice(observation):
            calls.append(deepcopy(observation))
            entered.set()
            await release.wait()
            group = observation['groups'][0]
            return {'plan': {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
                    'revision': observation['revision'], 'group_id': group['id'],
                    'fields': [field['name'] for field in group['fields']],
                    'actions': [action['id'] for action in group['actions']]}}
        browser._composer.plan = pending_choice
        reading = asyncio.create_task(browser.snapshot())
        submitting = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            self.assertIn('public_context', calls[0])
            group = state['groups'][0]
            submitting = asyncio.create_task(browser.submit(state['revision'], group['actions'][0]['id'],
                {field['name']: PASSWORD if field['type'] == 'password' else HUMAN_VALUE
                 for field in group['fields']}))
            await asyncio.sleep(0)
            self.assertFalse(submitting.done())
            submitting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await submitting
            self.assertTrue(browser._human_values_entered)
            release.set()
            with self.assertRaises(RecoverableFormError) as error:
                await asyncio.wait_for(reading, 5)
            self.assertIn(error.exception.code, {'composition_unavailable', 'stale_form'})
            self.assertEqual(len(calls), 1, 'No automatic repeat of the pending composition')
            self.assertTrue(browser._compositions)
            self.assertTrue(all(cached is None or 'public_context' not in cached[1]
                                for cached in browser._compositions.values()))
            self.assertTrue(browser._composition_observation is None or
                            'public_context' not in browser._composition_observation)
            self.assertEqual(await browser.page.input_value('input[type=password]'), '')
            self.assertFalse(await browser.page.evaluate('!!window.clicked'))
        finally:
            release.set()
            for task in (reading, submitting):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (reading, submitting) if task is not None),
                                 return_exceptions=True)

    async def test_first_input_equal_to_field_caption_does_not_make_own_submit_stale(self):
        browser, observed, payloads = await self.browser(
            '<h1>Enter Email</h1><form method="post"><label>Email<input autocomplete="username"></label>'
            '<button type="button" onclick="window.clicked=(window.clicked||0)+1">Continue</button></form>')
        first = await browser.snapshot()
        self.assertEqual(first['fields'][0]['label'], 'Email')
        await browser.submit(first['revision'], first['actions'][0]['id'],
                             {first['fields'][0]['name']: 'Email'})
        self.assertEqual(await browser.page.evaluate('window.clicked'), 1)
        self.assertEqual(await browser.page.input_value('input'), 'Email')
        self.assertTrue(browser._human_values_entered)
        await browser.snapshot()
        self.assertNotIn('public_context', observed[-1])
        self.assert_private_payload(payloads[-1])

    async def test_post_input_select_value_overlapping_caption_does_not_make_submit_stale(self):
        browser, _, payloads = await self.browser()
        first = await browser.snapshot()
        await browser.submit(first['revision'], first['actions'][0]['id'], self.values(first))
        self.assertTrue(browser._human_values_entered)
        await browser.page.set_content('<h1>Choose delivery</h1><form method="post">'
            '<label>Delivery route<select><option value="mail">Email letter</option>'
            '<option value="code">One-time code</option></select></label>'
            '<button type="button" onclick="window.selected=(window.selected||0)+1">Continue</button></form>')
        second = await browser.snapshot()
        field = second['fields'][0]
        self.assertEqual(field['type'], 'select')
        self.assertEqual(await browser.page.input_value('select'), 'mail')
        self.assert_private_payload(payloads[-1])
        await browser.submit(second['revision'], second['actions'][0]['id'],
                             {field['name']: field['options'][1]['value']})
        self.assertEqual(await browser.page.input_value('select'), 'code')
        self.assertEqual(await browser.page.evaluate('window.selected'), 1)
        await browser.snapshot()
        self.assert_private_payload(payloads[-1])

    async def test_heading_caption_excludes_nested_control_text(self):
        option_caption = 'Nested option caption marker'
        textarea_value = 'Nested textarea material'
        browser, observed, payloads = await self.browser(
            '<form method="post"><h1>Enter <span>workspace</span>'
            '<select aria-label="Route"><option value="route">' + option_caption + '</option></select>'
            '<textarea aria-label="Note">' + textarea_value + '</textarea></h1>'
            '<label>Phrase<input type="password"></label><button type="button">Continue</button></form>')
        await browser.snapshot()
        self.assertEqual(observed[-1]['public_context']['headings'], ['Enter workspace'])
        self.assertNotIn(option_caption, json.dumps(payloads[-1]))
        self.assertNotIn(textarea_value, json.dumps(payloads[-1]))

    async def test_inflight_observation_cannot_reexport_public_context_after_gate_closes(self):
        browser, _, _ = await self.browser()
        evaluate = browser.page.evaluate
        async def close_during_observation(script, arg=None):
            result = await evaluate(script, arg)
            if isinstance(arg, dict) and arg.get('operation') == 'snapshot':
                self.assertIn('public_context', result)
                browser._human_values_entered = True
            return result
        with patch.object(browser.page, 'evaluate', side_effect=close_during_observation):
            state = await browser._inspect()
        self.assertNotIn('public_context', state)
        self.assertNotIn('public_context', browser._group_observation(state))


if __name__ == '__main__':
    unittest.main()
