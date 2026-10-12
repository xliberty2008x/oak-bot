"""Generic DOM forms, public-address pinning and explicit completion checks."""

import asyncio
import json
import os
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_forms import WebsiteForms, public_address, validated_url


CANARY = 'generic-password-do-not-return'
REVISION = '12345678-1234-1234-1234-123456789abc:1'


class WebsiteFormContractTests(unittest.IsolatedAsyncioTestCase):
    def test_only_bounded_public_https_urls_without_credentials(self):
        self.assertEqual(validated_url('https://example.com:443/login?lang=uk'), 'https://example.com/login?lang=uk')
        for value in ('http://example.com/', 'https://localhost/', 'https://127.0.0.1/',
                      'https://169.254.169.254/', 'https://[::1]/', 'https://10.0.0.1/',
                      'https://host.local/', 'https://host.internal/', 'https://user:secret@example.com/',
                      'https://example.com:8443/', 'https://example.com/#token',
                      'https://example.com/?%61ccess_token=fixture', 'https://example.com/?password=fixture',
                      'https://example.com/?accessToken=fixture', 'https://example.com/?api_key=fixture',
                      'https://example.com\\@127.0.0.1/', 'https://example.com/\nfixture'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WebsiteForms(value)
        for address in ('127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', 'fe80::1', '224.0.0.1', '0.0.0.0'):
            self.assertFalse(public_address(address))

    async def test_dns_private_or_mixed_answers_fail_before_launch(self):
        for addresses in (['127.0.0.1'], ['93.184.216.34', '10.0.0.1'], []):
            factory = Mock()
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]
            browser = WebsiteForms('https://example.com/', playwright_factory=factory,
                                   resolver=AsyncMock(return_value=records))
            with self.assertRaises(ValueError):
                await browser.start()
            factory.assert_not_called()

    async def test_public_address_is_pinned_and_websockets_are_blocked_before_navigation(self):
        events = []
        async def sockets(*args):
            events.append('websockets-guarded')
        async def goto(*args, **kwargs):
            events.append('navigation')
        page = SimpleNamespace(goto=goto, main_frame=object())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), on=Mock(), route=AsyncMock(), route_web_socket=sockets)
        resource = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        chromium = SimpleNamespace(launch=AsyncMock(return_value=resource))
        driver = SimpleNamespace(chromium=chromium, stop=AsyncMock())
        manager = SimpleNamespace(start=AsyncMock(return_value=driver))
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        resolver = AsyncMock(return_value=records)
        browser = WebsiteForms('https://example.com/login', playwright_factory=lambda: manager, resolver=resolver)
        await browser.start()
        args = chromium.launch.call_args.kwargs
        self.assertTrue(args['chromium_sandbox'])
        self.assertIn('--host-resolver-rules=MAP example.com 93.184.216.34, MAP * ~NOTFOUND', args['args'])
        self.assertIn('--no-proxy-server', args['args'])
        self.assertEqual(events, ['websockets-guarded', 'navigation'])
        resolver.assert_awaited_once()
        await browser.close()

    async def test_exact_origin_guard_blocks_cdn_iframe_and_external_redirect(self):
        browser = WebsiteForms('https://example.com/login')
        browser.page = SimpleNamespace(main_frame=object())
        for url, navigation, frame in (('https://cdn.example.com/script.js', False, browser.page.main_frame),
                                       ('https://127.0.0.1/', True, browser.page.main_frame),
                                       ('https://example.com/frame', True, object()),
                                       ('https://example.com.evil.test/', True, browser.page.main_frame)):
            route = SimpleNamespace(request=SimpleNamespace(url=url, frame=frame, is_navigation_request=lambda: navigation),
                                    abort=AsyncMock(), continue_=AsyncMock())
            await browser._route(route)
            route.abort.assert_awaited_once()
            route.continue_.assert_not_awaited()

    async def test_submit_has_exact_opaque_fields_and_never_accepts_confirm_as_click(self):
        browser = WebsiteForms('https://example.com/')
        state = {'stage': 'form', 'revision': REVISION, 'origin': browser.destination, 'confirmable': False,
                 'fields': [{'name': 'f1', 'type': 'password', 'label': 'Пароль', 'max_length': 1024}],
                 'actions': [{'id': 'a2', 'label': 'Continue'}]}
        browser.page = SimpleNamespace(url=browser.initial_url, evaluate=AsyncMock(return_value=state))
        for action, values in (('confirm', {}), ('a2', {'password': CANARY}), ('a2', {'f1': CANARY, 'selector': '#x'}),
                               ('a2', {'f1': True}), ('a2', {'f1': 'x' * 1025})):
            with self.assertRaises(ValueError):
                await browser.submit(REVISION, action, values)
        self.assertEqual(browser._redactions, [])

    async def test_errors_are_sanitized_and_private_redactions_clear_on_close(self):
        browser = WebsiteForms('https://example.com/')
        browser.page = SimpleNamespace(url=browser.initial_url, evaluate=AsyncMock(side_effect=RuntimeError(CANARY)))
        with self.assertRaises(ValueError) as error:
            await browser.snapshot()
        self.assertNotIn(CANARY, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
        browser._redactions = [CANARY]
        browser._credential_values = [CANARY]
        await browser.close()
        self.assertEqual(browser._redactions, [])
        self.assertEqual(browser._credential_values, [])

    async def test_stale_revision_is_recoverable_before_dispatch(self):
        browser = WebsiteForms('https://example.com/')
        state = {'stage': 'form', 'revision': REVISION, 'origin': browser.destination, 'confirmable': False,
                 'fields': [{'name': 'f1', 'type': 'password', 'label': 'Пароль', 'max_length': 1024}],
                 'actions': [{'id': 'a2', 'label': 'Continue'}]}
        browser.page = SimpleNamespace(url=browser.initial_url, evaluate=AsyncMock(return_value=state))
        with self.assertRaises(RecoverableFormError) as error:
            await browser.submit(REVISION[:-1] + '0', 'a2', {'f1': CANARY})
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertEqual(browser.page.evaluate.await_count, 1)
        self.assertEqual(browser._redactions, [])

    async def test_navigation_read_retries_without_submitting(self):
        browser = WebsiteForms('https://example.com/')
        state = {'stage': 'unsupported_layout', 'revision': REVISION, 'origin': browser.destination,
                 'confirmable': False, 'fields': [], 'actions': []}
        browser.page = SimpleNamespace(url=browser.initial_url, evaluate=AsyncMock(side_effect=[
            RuntimeError('Execution context was destroyed'), state]))
        self.assertEqual((await browser.snapshot())['stage'], 'unsupported_layout')
        self.assertEqual(browser.page.evaluate.await_count, 2)
        for call in browser.page.evaluate.await_args_list:
            self.assertEqual(call.args[1]['operation'], 'snapshot')

    async def test_dispatched_input_exception_is_uncertain_and_never_retried(self):
        browser = WebsiteForms('https://example.com/')
        state = {'stage': 'form', 'revision': REVISION, 'origin': browser.destination, 'confirmable': False,
                 'fields': [{'name': 'f1', 'type': 'password', 'label': 'Пароль', 'max_length': 1024}],
                 'actions': [{'id': 'a2', 'label': 'Continue'}]}
        browser.page = SimpleNamespace(url=browser.initial_url, evaluate=AsyncMock(side_effect=[state, RuntimeError(CANARY)]))
        with self.assertRaises(SubmitUncertainError) as error:
            await browser.submit(REVISION, 'a2', {'f1': CANARY})
        self.assertEqual(error.exception.code, 'submit_uncertain')
        self.assertNotIn(CANARY, str(error.exception))
        self.assertEqual(browser.page.evaluate.await_count, 2)
        self.assertFalse(browser._submitted)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in local intercepted Chromium fixtures')
class WebsiteFormDOMTests(unittest.IsolatedAsyncioTestCase):
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
        self.browser = WebsiteForms('https://example.com/login')
        self.browser.page = self.page
        self.browser.context = self.context

    async def test_arbitrary_login_fields_and_buttons_are_opaque(self):
        await self.page.set_content('<form method="post"><label>Email<input name="arbitrary-user" type="email"></label>'
            '<label>Password<input name="arbitrary-secret" type="password"></label>'
            '<button type="button" onclick="window.completed=true">Sign in</button></form>')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'form')
        self.assertEqual([field['type'] for field in state['fields']], ['email', 'password'])
        self.assertNotIn('arbitrary-secret', json.dumps(state))
        values = dict(zip([field['name'] for field in state['fields']], ['fixture@example.test', CANARY]))
        await self.browser.submit(state['revision'], state['actions'][0]['id'], values)
        self.assertEqual(self.browser._credential_values, [CANARY])
        self.assertTrue(await self.page.evaluate('window.completed'))
        self.assertNotIn(CANARY, json.dumps(await self.browser.snapshot()))
        self.assertFalse(await self.browser.verified())

    async def test_checkbox_and_select_use_boolean_and_opaque_options(self):
        await self.page.set_content('<form method="post"><label>Remember<input name="remember" type="checkbox"></label>'
            '<label>Region<select name="region"><option value="private-a">Alpha</option><option value="private-b">Beta</option></select></label>'
            '<button type="button" onclick="window.completed=[this.form.remember.checked,this.form.region.value]">Continue</button></form>')
        state = await self.browser.snapshot()
        checkbox, select = state['fields']
        self.assertNotIn('private-b', json.dumps(state))
        await self.browser.submit(state['revision'], state['actions'][0]['id'],
            {checkbox['name']: False, select['name']: select['options'][1]['value']})
        self.assertEqual(await self.page.evaluate('window.completed'), [False, 'private-b'])

    async def test_dom_replacement_csrf_and_action_origin_drift_refuse_click(self):
        html = '<form method="post"><input type="hidden" value="one"><input type="password"><button type="button" onclick="window.completed=true">Go</button></form>'
        for mutation in ('document.querySelector("input[type=hidden]").value="two"',
                         'document.querySelector("input[type=password]").outerHTML="<input type=password>"',
                         'document.querySelector("form").action="https://evil.test/"'):
            await self.page.set_content(html)
            state = await self.browser.snapshot()
            await self.page.evaluate(mutation)
            with self.assertRaises(RecoverableFormError):
                await self.browser.submit(state['revision'], state['actions'][0]['id'], {state['fields'][0]['name']: CANARY})
            self.assertFalse(await self.page.evaluate('!!window.completed'))

    async def test_form_origin_drift_during_fill_refuses_submission(self):
        await self.page.set_content('<form method="post"><input type="password" oninput="this.form.action=\'https://evil.test/\'">'
            '<button type="button" onclick="window.completed=true">Go</button></form>')
        state = await self.browser.snapshot()
        with self.assertRaises(SubmitUncertainError):
            await self.browser.submit(state['revision'], state['actions'][0]['id'], {state['fields'][0]['name']: CANARY})
        self.assertFalse(await self.page.evaluate('!!window.completed'))

    async def test_initial_empty_page_never_offers_completion(self):
        for html in ('', '<h1>Welcome</h1>', '<div id="application-root"></div>'):
            await self.page.set_content(html)
            state = await self.browser.snapshot()
            self.assertEqual(state['stage'], 'unsupported_layout')
            self.assertEqual((state['fields'], state['actions']), ([], []))
            self.assertFalse(await self.browser.confirmable(state['revision']))
            self.assertFalse(await self.browser.verified())

    async def test_zero_field_chooser_then_empty_page_never_enables_completion(self):
        await self.page.set_content('<button type="button" '
            'onclick="document.body.innerHTML=\'<h1>Welcome</h1>\'">Use email</button>')
        state = await self.browser.snapshot()
        self.assertEqual(state['fields'], [])
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {})
        self.assertFalse(self.browser._submitted)
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'unsupported_layout')
        self.assertEqual((state['fields'], state['actions']), ([], []))
        self.assertFalse(await self.browser.confirmable(state['revision']))
        self.assertFalse(await self.browser.verified())

    async def test_unknown_success_requires_accepted_values_explicit_confirmation_and_fresh_revision(self):
        await self.page.set_content('<form method="post"><input type="password"><button type="button" '
            'onclick="document.body.innerHTML=\'<h1>Welcome</h1>\'">Continue</button></form>')
        await self.context.add_cookies([{'name': 'session', 'value': CANARY, 'url': 'https://example.com/'}])
        before = await self.browser.snapshot()
        self.assertFalse(await self.browser.confirmable(before['revision']))
        await self.browser.submit(before['revision'], before['actions'][0]['id'],
                                  {before['fields'][0]['name']: CANARY})
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'review')
        self.assertEqual(state['actions'][0]['id'], 'confirm')
        self.assertFalse(await self.browser.verified())
        self.assertTrue(await self.browser.confirmable(state['revision']))
        await self.page.set_content('<form method="post"><input type="password"><button>Continue</button></form>')
        self.assertFalse(await self.browser.confirmable(state['revision']))
        self.assertNotIn('confirm', [action['id'] for action in (await self.browser.snapshot())['actions']])

    async def test_task_form_confirmation_only_after_human_submit_and_without_credentials(self):
        await self.page.set_content('<form method="post"><label>Search<input type="search"></label><button type="button">Search</button></form>')
        state = await self.browser.snapshot()
        self.assertNotIn('confirm', [action['id'] for action in state['actions']])
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {state['fields'][0]['name']: 'fixture query'})
        state = await self.browser.snapshot()
        self.assertIn('confirm', [action['id'] for action in state['actions']])
        self.assertTrue(await self.browser.confirmable(state['revision']))
        await self.page.evaluate('document.body.insertAdjacentHTML("beforeend", \'<input type="password" hidden>\')')
        self.assertFalse(await self.browser.confirmable(state['revision']))

    async def test_show_password_as_text_never_allows_owner_confirmation(self):
        await self.page.set_content('<form method="post"><input name="arbitrary" type="password"><button type="button">Continue</button></form>')
        state = await self.browser.snapshot()
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {state['fields'][0]['name']: CANARY})
        await self.page.evaluate('document.querySelector("input").type="text"')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'form')
        self.assertNotIn('confirm', [action['id'] for action in state['actions']])
        self.assertFalse(await self.browser.confirmable(state['revision']))

    async def test_unknown_controls_and_external_form_are_layouts_not_challenges(self):
        for html in ('<form method="post"><input type="file"><button>Go</button></form>',
                     '<form method="post"><input type="radio"><button>Go</button></form>',
                     '<form method="post" action="https://evil.test/"><input><button>Go</button></form>'):
            await self.page.set_content(html)
            state = await self.browser.snapshot()
            self.assertEqual(state['stage'], 'unsupported_layout')
            self.assertEqual(state['actions'], [])

    async def test_detected_captcha_remains_a_challenge(self):
        await self.page.set_content('<iframe src="https://example.com/captcha"></iframe>')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'unsupported_challenge')
        self.assertEqual(state['actions'], [])

    async def test_input_echo_is_redacted_even_after_new_document(self):
        await self.page.set_content('<form method="post"><input type="password"><button type="button">Continue</button></form>')
        state = await self.browser.snapshot()
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {state['fields'][0]['name']: CANARY})
        await self.page.goto('https://example.com/next')
        await self.page.set_content('<form method="post"><label>' + CANARY + '<input></label><button type="button">' + CANARY + '</button></form>')
        state = await self.browser.snapshot()
        self.assertNotIn(CANARY, json.dumps(state))

    async def test_multiple_unrelated_forms_are_not_merged(self):
        await self.page.set_content('<form method="post"><input><button>First</button></form>'
            '<form method="post"><input type="password"><button>Second</button></form>')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'unsupported_layout')
        self.assertEqual(state['fields'], [])
        self.assertEqual(state['actions'], [])

    async def test_separate_get_language_form_does_not_hide_post_login(self):
        await self.page.set_content('<form method="post"><label>Email<input type="email"></label>'
            '<button type="button" onclick="window.completed=true">Next</button></form>'
            '<form method="get"><select aria-label="Language"><option value="en">English</option>'
            '<option value="uk">Українська</option></select></form>')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'form')
        self.assertEqual([field['type'] for field in state['fields']], ['email'])
        await self.page.evaluate('document.querySelector("option").value="changed-language-option"')
        self.assertEqual((await self.browser.snapshot())['revision'], state['revision'])
        await self.browser.submit(state['revision'], state['actions'][0]['id'],
                                  {state['fields'][0]['name']: 'fixture@example.test'})
        self.assertTrue(await self.page.evaluate('window.completed'))

    async def test_formless_role_link_chooser_with_language_select_opens_form(self):
        await self.page.set_content('<select aria-label="Language" onchange="window.languageChanged=true">'
            + '<option>Language</option>' * 64 + '</select>'
            '<div role="link" tabindex="0">Use phone / email / username</div>')
        await self.page.evaluate('''() => document.querySelector('[role=link]').onclick = () => {
            document.body.innerHTML = '<form method="post"><input type="email"><input type="password"><button type="button">Log in</button></form>';
        }''')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'form')
        self.assertEqual([action['label'] for action in state['actions']], ['Use phone / email / username'])
        self.assertEqual(state['fields'], [])
        await self.page.evaluate('document.querySelector("option").value="language-changed"')
        self.assertEqual((await self.browser.snapshot())['revision'], state['revision'])
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {})
        self.assertFalse(await self.page.evaluate('!!window.languageChanged'))
        after = await self.browser.snapshot()
        self.assertEqual(after['stage'], 'form')
        self.assertEqual([field['type'] for field in after['fields']], ['email', 'password'])

    async def test_formless_buttons_are_actions_not_completion(self):
        await self.page.set_content('<button type="button" onclick="window.completed=true">Use email</button>')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'form')
        self.assertEqual(state['fields'], [])
        self.assertNotIn('confirm', [action['id'] for action in state['actions']])
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {})
        self.assertTrue(await self.page.evaluate('window.completed'))

    async def test_link_actions_keep_exact_origin_and_target_restrictions(self):
        await self.page.set_content('<a href="/login/email" onclick="event.preventDefault();window.completed=true">Use email</a>'
            '<a href="https://evil.test/">External</a><a href="javascript:void(0)">Script</a>'
            '<a href="data:text/html,fixture">Data</a><a href="/file" download>Download</a>'
            '<a href="/login" target="_blank">New window</a><a href="https://user:secret@example.com/">Credentials</a>')
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'form')
        self.assertEqual([action['label'] for action in state['actions']], ['Use email'])
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {})
        self.assertTrue(await self.page.evaluate('window.completed'))

    async def test_link_destination_change_invalidates_action_before_click(self):
        await self.page.set_content('<a href="/first" onclick="event.preventDefault();window.completed=true">Continue</a>')
        state = await self.browser.snapshot()
        await self.page.evaluate('document.querySelector("a").href="/second"')
        with self.assertRaises(RecoverableFormError):
            await self.browser.submit(state['revision'], state['actions'][0]['id'], {})
        self.assertFalse(await self.page.evaluate('!!window.completed'))

    async def test_role_link_handler_cannot_navigate_outside_approved_origin(self):
        attempted = asyncio.Event()
        async def guard(route):
            await self.browser._route(SimpleNamespace(request=route.request, abort=route.abort,
                                                       continue_=route.fallback))
            if route.request.url == 'https://evil.test/':
                attempted.set()
        await self.context.route('**/*', guard)
        await self.page.set_content('<div role="link" tabindex="0" onclick="location.href=\'https://evil.test/\'">Continue</div>')
        state = await self.browser.snapshot()
        await self.browser.submit(state['revision'], state['actions'][0]['id'], {})
        await asyncio.wait_for(attempted.wait(), 2)
        self.assertTrue(self.browser._blocked)
        self.assertEqual((await self.browser.snapshot())['stage'], 'provider_blocked')

    async def test_oversized_chooser_is_an_unsupported_layout(self):
        await self.page.set_content('<button type="button">Choice</button>' * 9)
        state = await self.browser.snapshot()
        self.assertEqual(state['stage'], 'unsupported_layout')
        self.assertEqual(state['actions'], [])


if __name__ == '__main__':
    unittest.main()
