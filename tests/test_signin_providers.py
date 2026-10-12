"""Provider contracts plus optional network-intercepted Chromium DOM fixtures."""

import asyncio
import copy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from oak.signin_providers import FORM_SCRIPT, PROVIDERS, WebsiteBrowser, RecoverableFormError, SubmitUncertainError


REVISION = '12345678-1234-1234-1234-123456789abc:1'
CANARY = 'fixture-secret-never-in-status'


class FakePage:
    def __init__(self, stage='password', provider='google'):
        self.url = PROVIDERS[provider]['url']
        self.main_frame = object()
        self.state = {'stage': stage, 'revision': REVISION, 'fields': [stage],
                      'origin': 'https://accounts.google.com', 'can_submit': True,
                      'invalid': False, 'dismiss': False}
        self.submissions = []
        self.drift = False

    async def evaluate(self, script, payload):
        if payload['operation'] == 'snapshot':
            return copy.deepcopy(self.state)
        if self.drift:
            self.state['revision'] = REVISION[:-1] + '2'
        if payload['revision'] != self.state['revision']:
            return {'accepted': False}
        self.submissions.append(payload['values'])
        self.state['revision'] = REVISION[:-1] + '2'
        return {'accepted': True}


class ProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_navigation_retries_observation_only(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        browser.page.evaluate = AsyncMock(side_effect=[RuntimeError('Execution context was destroyed'), browser.page.state])
        self.assertEqual((await browser.snapshot())['stage'], 'password')
        self.assertEqual(browser.page.evaluate.await_count, 2)
        self.assertTrue(all(call.args[1]['operation'] == 'snapshot' for call in browser.page.evaluate.await_args_list))

    async def test_unsettled_navigation_is_bounded_and_recoverable(self):
        browser = WebsiteBrowser('google')
        browser.OBSERVATION_SECONDS = 0.06
        browser.page = FakePage()
        browser.page.evaluate = AsyncMock(side_effect=RuntimeError('Execution context was destroyed ' + CANARY))
        with self.assertRaises(RecoverableFormError) as error:
            await browser.snapshot()
        self.assertEqual(error.exception.code, 'navigation_pending')
        self.assertNotIn(CANARY, str(error.exception))
        self.assertLessEqual(browser.page.evaluate.await_count, 4)

    async def test_stale_preflight_does_not_dispatch_input(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        with self.assertRaises(RecoverableFormError) as error:
            await browser.submit('old:revision', 'submit', {'password': CANARY})
        self.assertEqual(error.exception.code, 'stale_form')
        self.assertEqual(browser.page.submissions, [])
        self.assertEqual(browser._credential_values, [])

    async def test_dispatched_input_exception_is_uncertain_and_never_retried(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        browser.page.evaluate = AsyncMock(side_effect=[browser.page.state,
            RuntimeError('Execution context was destroyed ' + CANARY)])
        with self.assertRaises(SubmitUncertainError) as error:
            await browser.submit(REVISION, 'submit', {'password': CANARY})
        self.assertEqual(str(error.exception), 'submit_uncertain')
        self.assertEqual(browser.page.evaluate.await_count, 2)
        self.assertEqual([call.args[1]['operation'] for call in browser.page.evaluate.await_args_list], ['snapshot', 'submit'])

    async def test_construction_never_launches_and_rejects_arbitrary_provider(self):
        factory = Mock()
        browser = WebsiteBrowser('google', playwright_factory=factory)
        factory.assert_not_called()
        self.assertIsNone(browser.context)
        with self.assertRaises(ValueError):
            WebsiteBrowser('https://attacker.example/')

    async def test_snapshot_uses_only_fixed_labels_and_never_returns_values(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        browser.page.state['provider_body'] = CANARY
        value = await browser.snapshot()
        self.assertEqual(value['fields'][0]['name'], 'password')
        self.assertNotIn(CANARY, json.dumps(value))
        self.assertEqual(set(value), {'provider', 'stage', 'revision', 'fields', 'actions', 'message'})

    async def test_exact_field_validation_and_consumed_revision(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        for values in ({}, {'password': CANARY, 'url': 'https://evil.test'}, {'password': 42},
                       {'password': 'x' * 1025}, {'password': '\x00'}):
            with self.assertRaises(ValueError):
                await browser.submit(REVISION, 'submit', values)
        self.assertEqual(browser.page.submissions, [])
        await browser.submit(REVISION, 'submit', {'password': CANARY})
        self.assertEqual(browser._credential_values, [CANARY])
        with self.assertRaises(ValueError):
            await browser.submit(REVISION, 'submit', {'password': CANARY})
        self.assertEqual(len(browser.page.submissions), 1)
        await browser.close()
        self.assertEqual(browser._credential_values, [])

    async def test_origin_change_and_spa_revision_change_reject_before_action(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        browser.page.drift = True
        with self.assertRaises(ValueError):
            await browser.submit(REVISION, 'submit', {'password': CANARY})
        self.assertEqual(browser.page.submissions, [])
        browser.page.url = 'https://accounts.google.com.attacker.example/'
        self.assertEqual((await browser.snapshot())['stage'], 'provider_blocked')
        with self.assertRaises(ValueError):
            await browser.submit(REVISION, 'submit', {'password': CANARY})

    async def test_browser_errors_do_not_echo_credentials_or_provider_text(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        browser.page.evaluate = AsyncMock(side_effect=RuntimeError(CANARY))
        for operation in (browser.snapshot(), browser.submit(REVISION, 'submit', {'password': CANARY})):
            with self.assertRaises(ValueError) as error:
                await operation
            self.assertNotIn(CANARY, str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)

    async def test_known_challenges_are_not_authenticated(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        for stage in ('device_approval', 'unsupported_challenge', 'unsupported_layout', 'provider_blocked'):
            browser.page.state.update(stage=stage, fields=[], can_submit=False)
            self.assertFalse(await browser.verified())
            snapshot = await browser.snapshot()
            self.assertEqual(snapshot['fields'], [])
            self.assertEqual(snapshot['actions'], [])

    async def test_start_enables_sandbox_and_nonpersistent_context(self):
        page = SimpleNamespace(goto=AsyncMock(), main_frame=object())
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), on=Mock(), route=AsyncMock())
        resource = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        chromium = SimpleNamespace(launch=AsyncMock(return_value=resource))
        driver = SimpleNamespace(chromium=chromium, stop=AsyncMock())
        manager = SimpleNamespace(start=AsyncMock(return_value=driver))
        browser = WebsiteBrowser('instagram', playwright_factory=lambda: manager)
        await browser.start()
        self.assertTrue(chromium.launch.call_args.kwargs['chromium_sandbox'])
        self.assertNotIn('--no-sandbox', chromium.launch.call_args.kwargs['args'])
        self.assertNotIn('storage_state', resource.new_context.call_args.kwargs)
        self.assertEqual(page.goto.call_args.args, (PROVIDERS['instagram']['url'],))
        self.assertIs(browser.context, context)
        await browser.close()
        resource.close.assert_awaited_once()
        driver.stop.assert_awaited_once()

    async def test_navigation_guard_rejects_deceptive_host_and_plaintext(self):
        browser = WebsiteBrowser('google')
        browser.page = FakePage()
        for url in ('https://accounts.google.com.evil.test/', 'http://accounts.google.com/',
                    'https://user:secret@accounts.google.com/'):
            request = SimpleNamespace(url=url, frame=browser.page.main_frame, is_navigation_request=lambda: True)
            route = SimpleNamespace(request=request, abort=AsyncMock(), continue_=AsyncMock())
            await browser._route(route)
            route.abort.assert_awaited_once()
            route.continue_.assert_not_awaited()

    async def test_cancelled_acquisition_remains_owned_until_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        driver = SimpleNamespace(stop=AsyncMock())
        async def acquire():
            entered.set()
            await release.wait()
            return driver
        browser = WebsiteBrowser('google', playwright_factory=lambda: SimpleNamespace(start=acquire))
        task = asyncio.create_task(browser.start())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        cleanup = asyncio.create_task(browser.close())
        await asyncio.sleep(0)
        self.assertFalse(cleanup.done())
        release.set()
        await cleanup
        driver.stop.assert_awaited_once()

    async def test_failed_browser_and_driver_cleanup_retry_without_replaying_success(self):
        browser = WebsiteBrowser('google')
        resource = SimpleNamespace(close=AsyncMock(side_effect=[RuntimeError('temporary'), None]))
        driver = SimpleNamespace(stop=AsyncMock(side_effect=[RuntimeError('temporary'), None]))
        browser.browser, browser.driver = resource, driver
        browser._credential_values = [CANARY]
        with self.assertRaises(RuntimeError):
            await browser.close()
        self.assertIs(browser.browser, resource)
        self.assertEqual(browser._credential_values, [])
        with self.assertRaises(RuntimeError):
            await browser.close()
        self.assertIsNone(browser.browser)
        self.assertIs(browser.driver, driver)
        await browser.close()
        self.assertIsNone(browser.driver)
        self.assertEqual(resource.close.await_count, 2)
        self.assertEqual(driver.stop.await_count, 2)
        await browser.close()
        self.assertEqual(resource.close.await_count, 2)

    async def test_interrupted_close_keeps_running_cleanup_without_duplicate_call(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def close_resource():
            entered.set()
            await release.wait()
        resource = SimpleNamespace(close=AsyncMock(side_effect=close_resource))
        browser = WebsiteBrowser('google')
        browser.browser = resource
        first = asyncio.create_task(browser.close())
        await entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(browser.browser_close.done())
        release.set()
        await browser.close()
        resource.close.assert_awaited_once()


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in local intercepted Chromium fixtures')
class ProviderDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(locale='en-US', service_workers='block')
        # Every request is fulfilled locally, including the nominal provider URL.
        # No provider receives a request or any fixture value.
        await self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<html></html>'))
        self.page = await self.context.new_page()

    async def adapter(self, provider, html, path=None):
        browser = WebsiteBrowser(provider)
        browser.page = self.page
        browser.context = self.context
        await self.page.goto(path or ('https://accounts.google.com/v3/signin/identifier' if provider == 'google'
                                     else 'https://www.instagram.com/accounts/login/'))
        await self.page.set_content(html)
        return browser

    async def test_google_identifier_and_same_page_form_replacement(self):
        browser = await self.adapter('google', '<form method="post"><input id="identifierId" type="email">'
            '<div id="identifierNext"><button type="button" onclick="window.completed=true">Next</button></div></form>')
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'identifier')
        await self.page.evaluate('document.querySelector("input").outerHTML = \'<input id="identifierId" type="email">\'')
        with self.assertRaises(ValueError):
            await browser.submit(snapshot['revision'], 'submit', {'identifier': 'fixture@example.test'})
        self.assertFalse(await self.page.evaluate('!!window.completed'))
        fresh = await browser.snapshot()
        await browser.submit(fresh['revision'], 'submit', {'identifier': 'fixture@example.test'})
        self.assertTrue(await self.page.evaluate('window.completed'))

    async def test_post_navigation_settles_without_repeating_input(self):
        initial = '<form method="post" action="/v3/signin/challenge/pwd"><input id="identifierId" type="email">' \
                  '<button id="identifierNext" type="submit">Next</button></form>'
        password = '<form method="post"><input name="Passwd" type="password"><button id="passwordNext">Next</button></form>'
        posts = []
        async def intercept(route):
            if route.request.method == 'POST':
                posts.append('POST')
                await asyncio.sleep(0.02)
            await route.fulfill(status=200, content_type='text/html', body=password if route.request.method == 'POST' else initial)
        await self.context.unroute('**/*')
        await self.context.route('**/*', intercept)
        for index in range(6):
            await self.page.goto('https://accounts.google.com/v3/signin/identifier')
            adapter = WebsiteBrowser('google')
            adapter.page, adapter.context = self.page, self.context
            snapshot = await adapter.snapshot()
            await adapter.submit(snapshot['revision'], 'submit', {'identifier': 'fixture@example.test'})
            self.assertFalse(await adapter.verified())
            self.assertEqual((await adapter.snapshot())['stage'], 'password')
            self.assertEqual(len(posts), index + 1)

    async def test_csrf_drift_is_refreshable_only_before_any_fill(self):
        adapter = await self.adapter('google', '<form method="post"><input type="hidden" name="csrf" value="one">'
            '<input id="identifierId" type="email"><button id="identifierNext" type="button" onclick="window.clicked=true">Next</button></form>')
        old = await adapter.snapshot()
        await self.page.evaluate('document.querySelector("[name=csrf]").value="two"')
        with self.assertRaises(RecoverableFormError):
            await adapter.submit(old['revision'], 'submit', {'identifier': 'fixture@example.test'})
        self.assertEqual(await self.page.locator('#identifierId').input_value(), '')
        await self.page.evaluate('() => { document.querySelector("#identifierId").oninput=()=>document.querySelector("[name=csrf]").value="three"; }')
        fresh = await adapter.snapshot()
        with self.assertRaises(SubmitUncertainError):
            await adapter.submit(fresh['revision'], 'submit', {'identifier': 'fixture@example.test'})
        self.assertFalse(await self.page.evaluate('!!window.clicked'))

    async def test_instagram_combined_form_and_delayed_button_enable(self):
        browser = await self.adapter('instagram', '<form method="post"><input name="username">'
            '<input name="password" type="password" oninput="document.querySelector(\'button\').disabled=false">'
            '<button type="submit" disabled onclick="event.preventDefault();window.completed=true">Log in</button></form>')
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'login')
        self.assertEqual([field['name'] for field in snapshot['fields']], ['identifier', 'password'])
        await browser.submit(snapshot['revision'], 'submit', {'identifier': 'fixture-user', 'password': CANARY})
        self.assertTrue(await self.page.evaluate('window.completed'))
        self.assertNotIn(CANARY, json.dumps(await browser.snapshot()))

    async def test_current_google_direct_next_button(self):
        browser = await self.adapter('google', '<form method="post"><input id="identifierId" name="identifier" type="text">'
            '<button id="identifierNext" type="submit" onclick="event.preventDefault();window.completed=true">Next</button></form>')
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'identifier')
        await browser.submit(snapshot['revision'], 'submit', {'identifier': 'fixture@example.test'})
        self.assertTrue(await self.page.evaluate('window.completed'))

    async def test_current_instagram_role_button_and_cookie_decline(self):
        browser = await self.adapter('instagram', '<form id="login_form" method="post"><input name="email" type="text">'
            '<input name="pass" type="password" oninput="document.querySelector(\'[role=button]\').setAttribute(\'aria-disabled\',\'false\')">'
            '<div role="button" aria-disabled="true" onclick="window.completed=true">Log in</div>'
            '<div role="button">Log in with Facebook</div></form>'
            '<aside><button onclick="window.optional=true">Allow all cookies</button>'
            '<button onclick="this.parentElement.remove()">Decline optional cookies</button></aside>')
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'login')
        self.assertEqual(snapshot['fields'], [])
        await browser.submit(snapshot['revision'], 'submit', {})
        self.assertFalse(await self.page.evaluate('!!window.optional'))
        snapshot = await browser.snapshot()
        self.assertEqual([field['name'] for field in snapshot['fields']], ['identifier', 'password'])
        await browser.submit(snapshot['revision'], 'submit', {'identifier': 'fixture-user', 'password': CANARY})
        self.assertTrue(await self.page.evaluate('window.completed'))
        self.assertNotIn(CANARY, json.dumps(await browser.snapshot()))

    async def test_action_origin_changes_during_fill_prevent_submission(self):
        browser = await self.adapter('google', '<form method="post"><input id="identifierId" type="email" '
            'oninput="this.form.action=\'https://evil.example/steal\'">'
            '<div id="identifierNext"><button type="button" onclick="window.completed=true">Next</button></div></form>')
        snapshot = await browser.snapshot()
        with self.assertRaises(ValueError):
            await browser.submit(snapshot['revision'], 'submit', {'identifier': 'fixture@example.test'})
        self.assertFalse(await self.page.evaluate('!!window.completed'))

    async def test_hidden_csrf_drift_invalidates_revision(self):
        browser = await self.adapter('google', '<form method="post"><input type="hidden" name="csrf" value="one">'
            '<input name="Passwd" type="password"><div id="passwordNext"><button type="button">Next</button></div></form>',
            'https://accounts.google.com/v3/signin/challenge/pwd')
        snapshot = await browser.snapshot()
        await self.page.evaluate('document.querySelector("input[type=hidden]").value="two"')
        with self.assertRaises(ValueError):
            await browser.submit(snapshot['revision'], 'submit', {'password': CANARY})

    async def test_otp_challenges_and_unknown_fields(self):
        browser = await self.adapter('google', '<form method="post"><input id="totpPin" type="tel">'
            '<div id="totpNext"><button type="button">Next</button></div></form>',
            'https://accounts.google.com/v3/signin/challenge/totp')
        self.assertEqual((await browser.snapshot())['stage'], 'otp')
        await self.page.set_content('<p>Check your phone to approve this sign-in</p>')
        self.assertEqual((await browser.snapshot())['stage'], 'device_approval')
        await self.page.set_content('<p>This browser or app may not be secure</p>')
        self.assertEqual((await browser.snapshot())['stage'], 'provider_blocked')
        await self.page.set_content('<form method="post"><input name="unknown"><button>Next</button></form>')
        self.assertEqual((await browser.snapshot())['stage'], 'unsupported_layout')
        await self.page.set_content('<iframe title="Captcha" src="https://accounts.google.com/captcha"></iframe>')
        self.assertEqual((await browser.snapshot())['stage'], 'unsupported_challenge')

    async def test_authentication_needs_positive_account_evidence_and_keeps_context(self):
        browser = await self.adapter('google', '<h1>Welcome</h1>', 'https://myaccount.google.com/')
        await self.context.add_cookies([{'name': 'SID', 'value': CANARY, 'domain': '.google.com', 'path': '/'}])
        self.assertFalse(await browser.verified())
        await self.page.set_content('<a href="https://accounts.google.com/SignOutOptions" '
            'aria-label="Google Account: Fixture (fixture@example.test)">Account</a>'
            '<a href="https://myaccount.google.com/security">Security</a><input type="search">')
        self.assertTrue(await browser.verified())
        self.assertIs(browser.context, self.context)
        self.assertFalse(self.page.is_closed())
        self.assertNotIn('fixture@example.test', json.dumps(await browser.snapshot()))

    async def test_instagram_public_profile_is_not_authenticated(self):
        browser = await self.adapter('instagram', '<a href="/fixture/"><img alt="Fixture profile picture"></a>',
                                     'https://www.instagram.com/fixture/')
        self.assertFalse(await browser.verified())
        await self.page.set_content('<a href="/direct/inbox/">Messages</a><svg aria-label="New post"></svg>'
            '<a href="/fixture/"><img alt="Fixture profile picture" width="20" height="20"></a>')
        self.assertTrue(await browser.verified())

    async def test_instagram_save_prompt_only_offers_not_now(self):
        browser = await self.adapter('instagram', '<h1>Save your login info?</h1><button>Save info</button>'
            '<button onclick="window.dismissed=true">Not now</button>')
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['fields'], [])
        self.assertEqual(snapshot['actions'][0]['id'], 'submit')
        await browser.submit(snapshot['revision'], 'submit', {})
        self.assertTrue(await self.page.evaluate('window.dismissed'))


if __name__ == '__main__':
    unittest.main()
