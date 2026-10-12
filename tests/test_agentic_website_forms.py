"""Optional composition over intercepted DOM fixtures; no model/API calls."""

import asyncio
from copy import deepcopy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.signin_providers import RecoverableFormError
from oak.website_forms import WebsiteForms
from oak.website_ui import CATALOG_ID, CATALOG_VERSION


CANARY = 'private-composer-fixture-value'
TWO_FORMS = ('<form method="post"><input type="search"><input type="hidden" value="csrf-one">'
             '<button type="button" onclick="window.wrong=true">Search</button></form>'
             '<form method="post"><label>Email<input type="email"></label>'
             '<label>Password<input type="password"></label>'
             '<button type="button" onclick="window.completed=true">Sign in</button>'
             '<button type="button" onclick="window.other=true">Other</button></form>')


def result_for(observation, index=0, *, one_action=False):
    group = observation['groups'][index]
    return {'plan': {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
                    'revision': observation['revision'], 'group_id': group['id'],
                    'fields': [field['name'] for field in group['fields']],
                    'actions': [action['id'] for action in (group['actions'][:1] if one_action else group['actions'])]},
            'form': {'stage': 'authenticated'}, 'model': 'mock', 'elapsed_ms': 0}


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in intercepted Chromium fixtures')
class AgenticWebsiteFormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.resource = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.resource.close)
        self.context = await self.resource.new_context(service_workers='block')
        await self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<html></html>'))
        self.page = await self.context.new_page()
        await self.page.goto('https://example.com/login')
        self.composer = SimpleNamespace(plan=AsyncMock(side_effect=lambda observation: result_for(observation)))
        self.browser = WebsiteForms('https://example.com/login', composer=self.composer)
        self.browser.page, self.browser.context = self.page, self.context

    async def test_group_choice_and_action_subset_are_applied_only_after_human_submit(self):
        await self.page.set_content(TWO_FORMS)
        self.composer.plan.side_effect = lambda observation: result_for(observation, 1, one_action=True)
        state = await self.browser.snapshot()
        self.assertEqual([field['type'] for field in state['fields']], ['email', 'password'])
        self.assertEqual([action['label'] for action in state['actions']], ['Sign in'])
        self.assertEqual(state['stage'], 'form')  # The composer's supplied form is ignored.
        self.assertNotIn('groups', state)
        self.assertFalse(await self.page.evaluate('!!window.completed'))
        observed = self.composer.plan.call_args.args[0]
        self.assertEqual(len(observed['groups']), 2)
        self.assertNotIn(CANARY, json.dumps(observed))
        self.assertNotIn('values', json.dumps(observed))
        values = dict(zip([field['name'] for field in state['fields']], ['fixture@example.test', CANARY]))
        await self.browser.submit(state['revision'], state['actions'][0]['id'], values)
        self.assertTrue(await self.page.evaluate('window.completed'))
        self.assertFalse(await self.page.evaluate('!!window.wrong || !!window.other'))
        self.assertEqual(self.composer.plan.await_count, 1)

    async def test_poll_cache_does_not_repeat_composition_or_change_input(self):
        await self.page.set_content('<form method="post"><input type="password"><button type="button">Continue</button></form>')
        first = await self.browser.snapshot()
        await self.page.fill('input', CANARY)
        for _ in range(3):
            self.assertEqual(await self.browser.snapshot(), first)
        self.assertEqual(self.composer.plan.await_count, 1)
        self.assertEqual(await self.page.input_value('input'), CANARY)
        self.assertNotIn(CANARY, json.dumps(self.composer.plan.call_args.args[0]))

    async def test_autocomplete_roles_distinguish_same_types_without_changing_widgets(self):
        await self.page.set_content(
            '<form method="post"><input type="email" autocomplete="email">'
            '<input type="password" autocomplete="new-password"><button type="button">Start membership</button></form>'
            '<form method="post"><input type="email" autocomplete="username">'
            '<input type="password" autocomplete="section-' + CANARY + ' current-password webauthn">'
            '<button type="button">Access your account</button></form>')
        def choose_current_password(observation):
            index = next(i for i, group in enumerate(observation['groups'])
                         if 'current-password' in group.get('field_roles', {}).values())
            return result_for(observation, index)
        self.composer.plan.side_effect = choose_current_password
        state = await self.browser.snapshot()
        observed = self.composer.plan.call_args.args[0]
        self.assertEqual([[field['type'] for field in group['fields']] for group in observed['groups']],
                         [['email', 'password'], ['email', 'password']])
        self.assertEqual([list(group['field_roles'].values()) for group in observed['groups']],
                         [['email', 'new-password'], ['username', 'current-password']])
        self.assertEqual(state['actions'][0]['label'], 'Access your account')
        self.assertTrue(all(set(field) == {'name', 'type', 'label', 'max_length'} for field in state['fields']))
        self.assertNotIn(CANARY, json.dumps(observed))
        self.assertNotIn('autocomplete', json.dumps(observed))

    async def test_changed_autocomplete_role_invalidates_revision_before_input(self):
        await self.page.set_content('<form method="post"><input type="email" autocomplete="username">'
                                    '<input type="password" autocomplete="current-password">'
                                    '<button type="button" onclick="window.completed=true">Access account</button></form>')
        state = await self.browser.snapshot()
        await self.page.evaluate('document.querySelector("input[type=password]").autocomplete="new-password"')
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.submit(state['revision'], state['actions'][0]['id'],
                                      {field['name']: CANARY for field in state['fields']})
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertEqual(await self.page.input_value('input[type=password]'), '')
        self.assertFalse(await self.page.evaluate('!!window.completed'))
        self.assertEqual(self.composer.plan.await_count, 1)

    async def test_only_standard_type_compatible_roles_are_exposed(self):
        cases = [('password', CANARY, None), ('text', 'current-password', None),
                 ('password', 'username', None), ('email', 'tel', None),
                 ('text', 'home username', None), ('email', 'section-' + CANARY + ' shipping home email', 'email'),
                 ('text', 'section-' + CANARY + ' ONE-TIME-CODE', 'one-time-code'),
                 ('tel', 'mobile tel', 'tel'), ('text', 'username', 'username')]
        await self.page.set_content('<form method="post">' + ''.join(
            '<input type="%s" autocomplete="%s">' % (kind, autocomplete) for kind, autocomplete, _ in cases)
            + '<button type="button">Continue</button></form>')
        await self.browser.snapshot()
        observed = self.composer.plan.call_args.args[0]
        group = observed['groups'][0]
        self.assertEqual([group['field_roles'].get(field['name']) for field in group['fields']],
                         [expected for _, _, expected in cases])
        self.assertEqual(group['fields'][6]['type'], 'otp')
        self.assertNotIn(CANARY, json.dumps(observed))
        await self.page.set_content('<form method="post"><input autocomplete="' + CANARY + '">'
                                    '<button type="button">Continue</button></form>')
        await self.browser.snapshot()
        self.assertNotIn('field_roles', self.composer.plan.call_args.args[0]['groups'][0])

    async def test_unselected_group_change_invalidates_submit_without_new_inference(self):
        await self.page.set_content(TWO_FORMS)
        self.composer.plan.side_effect = lambda observation: result_for(observation, 1)
        old = await self.browser.snapshot()
        await self.page.evaluate('document.querySelector("input[type=hidden]").value="csrf-two"')
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.submit(old['revision'], old['actions'][0]['id'],
                                      {field['name']: CANARY for field in old['fields']})
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertEqual(self.composer.plan.await_count, 1)
        self.assertEqual(await self.page.input_value('input[type=password]'), '')
        fresh = await self.browser.snapshot()
        self.assertNotEqual(fresh['revision'], old['revision'])
        self.assertEqual(self.composer.plan.await_count, 2)

    async def test_unselected_actions_and_groups_cannot_be_submitted(self):
        await self.page.set_content(TWO_FORMS)
        self.composer.plan.side_effect = lambda observation: result_for(observation, 1, one_action=True)
        state = await self.browser.snapshot()
        groups = self.composer.plan.call_args.args[0]['groups']
        values = {field['name']: CANARY for field in state['fields']}
        for action in (groups[0]['actions'][0]['id'], groups[1]['actions'][1]['id']):
            with self.assertRaises(ValueError):
                await self.browser.submit(state['revision'], action, values)
        self.assertEqual(await self.page.input_value('input[type=password]'), '')
        self.assertFalse(await self.page.evaluate('!!window.completed || !!window.wrong || !!window.other'))

    async def test_model_failure_is_recoverable_and_cached_for_the_revision(self):
        await self.page.set_content(TWO_FORMS)
        self.composer.plan.side_effect = RuntimeError(CANARY)
        for _ in range(3):
            with self.assertRaises(RecoverableFormError) as error:
                await self.browser.snapshot()
            self.assertEqual(error.exception.code, 'composition_unavailable')
            self.assertNotIn(CANARY, str(error.exception))
        self.assertEqual(self.composer.plan.await_count, 1)
        self.assertFalse(await self.page.evaluate('!!window.completed'))

    async def test_composition_is_outside_the_short_dom_observation_timeout(self):
        await self.page.set_content(TWO_FORMS)
        original = self.browser._inspect
        cached = await original()
        # Isolate the timeout contract from browser scheduling on a busy host.
        self.browser._inspect = AsyncMock(return_value=cached)
        self.browser.OBSERVATION_SECONDS = 0.01
        async def slow_composition(observation):
            await asyncio.sleep(0.05)
            return result_for(observation)
        self.composer.plan.side_effect = slow_composition
        self.assertEqual((await self.browser.snapshot())['stage'], 'form')
        self.assertEqual(self.composer.plan.await_count, 1)

    async def test_restored_revision_reuses_its_success_or_failure_without_inference(self):
        await self.page.set_content(TWO_FORMS)
        old = await self.browser._inspect()
        self.browser._inspect = AsyncMock(return_value=old)
        first = await self.browser.snapshot()
        newer = deepcopy(old)
        newer['revision'] = old['revision'].split(':')[0] + ':999'
        self.browser._inspect.return_value = newer
        self.composer.plan.side_effect = RuntimeError(CANARY)
        with self.assertRaises(RecoverableFormError):
            await self.browser.snapshot()
        self.browser._inspect.return_value = old
        self.assertEqual(await self.browser.snapshot(), first)
        self.browser._inspect.return_value = newer
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.snapshot()
        self.assertEqual(error.exception.code, 'composition_unavailable')
        self.assertEqual(self.composer.plan.await_count, 2)

    async def test_composition_cache_is_bounded_and_does_not_retry_after_limit(self):
        await self.page.set_content(TWO_FORMS)
        initial = await self.browser._inspect()
        self.browser._inspect = AsyncMock(return_value=initial)
        self.browser.COMPOSITION_LIMIT = 2
        await self.browser.snapshot()
        for counter in (100, 101, 102):
            observed = deepcopy(initial)
            observed['revision'] = initial['revision'].split(':')[0] + ':' + str(counter)
            self.browser._inspect.return_value = observed
            if counter == 100:
                await self.browser.snapshot()
            else:
                with self.assertRaises(RecoverableFormError) as error:
                    await self.browser.snapshot()
                self.assertEqual(error.exception.code, 'composition_unavailable')
        self.assertEqual(self.composer.plan.await_count, 2)
        self.assertEqual(len(self.browser._compositions), 2)

    async def test_composer_cannot_mutate_authoritative_observation(self):
        await self.page.set_content(TWO_FORMS)
        def corrupt(observation):
            observation['groups'][0]['actions'][0]['id'] = 'a99999'
            return result_for(observation)
        self.composer.plan.side_effect = corrupt
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.snapshot()
        self.assertEqual(error.exception.code, 'composition_unavailable')
        self.assertIsNone(self.browser._composition_plan)

    async def test_dom_change_during_composition_requires_a_fresh_snapshot(self):
        await self.page.set_content(TWO_FORMS)
        async def change_page(observation):
            await self.page.set_content('<form method="post"><input type="password"><button type="button">Next</button></form>')
            return result_for(observation)
        self.composer.plan.side_effect = change_page
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.snapshot()
        self.assertEqual(error.exception.code, 'stale_form')
        self.composer.plan.side_effect = lambda observation: result_for(observation)
        fresh = await self.browser.snapshot()
        self.assertEqual([field['type'] for field in fresh['fields']], ['password'])
        self.assertEqual(self.composer.plan.await_count, 2)

    async def test_navigation_observe_choose_human_step_observe_uses_new_revision(self):
        posts = []
        async def next_page(route):
            posts.append(route.request.method)
            await route.fulfill(status=200, content_type='text/html', body=
                '<form method="post"><input type="password"><button type="button" onclick="document.body.innerHTML=\'<h1>Welcome</h1>\'">Continue</button></form>')
        await self.context.route('**/next', next_page)
        await self.page.set_content('<form method="post" action="/next"><input type="email" name="identifier"><button>Next</button></form>')
        initial = await self.browser.snapshot()
        await self.browser.submit(initial['revision'], initial['actions'][0]['id'],
                                  {initial['fields'][0]['name']: 'fixture@example.test'})
        await self.page.wait_for_url('https://example.com/next')
        password = await self.browser.snapshot()
        self.assertEqual([field['type'] for field in password['fields']], ['password'])
        self.assertNotEqual(initial['revision'], password['revision'])
        self.assertEqual(self.composer.plan.await_count, 2)
        await self.browser.submit(password['revision'], password['actions'][0]['id'],
                                  {password['fields'][0]['name']: CANARY})
        review = await self.browser.snapshot()
        self.assertEqual(review['stage'], 'review')
        self.assertEqual([action['id'] for action in review['actions']], ['confirm'])
        self.assertEqual(posts, ['POST'])
        self.assertEqual(self.composer.plan.await_count, 2)
        self.assertNotIn(CANARY, json.dumps([call.args[0] for call in self.composer.plan.await_args_list]))

    async def test_loose_navigation_and_input_groups_have_stable_distinct_ids(self):
        await self.page.set_content('<input type="text"><button type="button">Search</button>'
                                    '<a href="/login" onclick="event.preventDefault()">Use email</a>')
        await self.browser.snapshot()
        observed = deepcopy(self.composer.plan.call_args.args[0])
        self.assertEqual(len(observed['groups']), 2)
        self.assertEqual(len({group['id'] for group in observed['groups']}), 2)
        self.assertEqual([len(group['fields']) for group in observed['groups']], [0, 1])
        await self.browser.snapshot()
        self.assertEqual(self.composer.plan.await_count, 1)

    async def test_excess_groups_or_metadata_remain_unsupported_without_composition(self):
        many_groups = '<form method="post"><input><button>Continue</button></form>' * 9
        options = ''.join('<option value="option%d">%s</option>' % (i, 'Option label ' * 9) for i in range(32))
        large_group = '<form method="post">' + ('<select>' + options + '</select>') * 8 + '<button>Continue</button></form>'
        for html in (many_groups, large_group * 2):
            await self.page.set_content(html)
            state = await self.browser.snapshot()
            self.assertEqual(state['stage'], 'unsupported_layout')
            self.assertEqual(state['fields'], [])
            self.assertEqual(state['actions'], [])
        self.composer.plan.assert_not_awaited()

    async def test_non_form_status_and_known_authentication_do_not_compose(self):
        for html, stage in [('<h1>Welcome</h1>', 'unsupported_layout'),
                            ('<iframe src="https://example.com/captcha"></iframe>', 'unsupported_challenge')]:
            await self.page.set_content(html)
            self.assertEqual((await self.browser.snapshot())['stage'], stage)
        await self.page.goto('https://myaccount.google.com/')
        await self.page.set_content('<a aria-label="Google Account: fixture@example.test" href="https://accounts.google.com/SignOutOptions">Account</a>'
                                    '<a href="https://myaccount.google.com/security">Security</a>')
        browser = WebsiteForms('https://myaccount.google.com/', composer=self.composer)
        browser.page, browser.context = self.page, self.context
        self.assertEqual((await browser.snapshot())['stage'], 'authenticated')
        self.composer.plan.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
