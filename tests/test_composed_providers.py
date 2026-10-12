"""Known identity/evidence with dynamic private widgets; intercepted traffic only."""

import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.connected_browser import ConnectedBrowser
from oak.signin_providers import PROVIDERS, RecoverableFormError, WebsiteBrowser
from oak.website_forms import ComposedProviderForms
from oak.website_ui import CATALOG_ID, CATALOG_VERSION


def selection(observation):
    group = next(group for group in observation['groups']
                 if any(field['type'] == 'password' for field in group['fields']))
    return {'plan': {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
                    'revision': observation['revision'], 'group_id': group['id'],
                    'fields': [field['name'] for field in group['fields']],
                    'actions': [action['id'] for action in group['actions']]},
            'form': {'stage': 'authenticated'}}  # Never trusted as authentication.


class ComposedProviderPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_network_reuse_only_existing_known_policy(self):
        for provider in ('google', 'instagram'):
            browser = ComposedProviderForms(provider, composer=object())
            self.assertIs(browser.config, PROVIDERS[provider])
            self.assertIsNone(browser._pinned_address)
            self.assertEqual(browser._launch_options(), WebsiteBrowser(provider)._launch_options())
            self.assertTrue(browser._launch_options()['chromium_sandbox'])
            with patch.object(WebsiteBrowser, 'start', new_callable=AsyncMock) as start:
                await browser.start()
                start.assert_awaited_once_with(browser)
            with patch.object(WebsiteBrowser, '_route', new_callable=AsyncMock) as route:
                await browser._route('sentinel')
                route.assert_awaited_once_with(browser, 'sentinel')
            self.assertFalse(await browser.confirmable('any:1'))

    def test_connected_scope_is_provider_identity_not_widget_schema(self):
        google = ConnectedBrowser(ComposedProviderForms('google', composer=object()))
        self.assertEqual(google.provider, 'google')
        self.assertTrue(google.allowed('https://mail.google.com/mail/u/0/'))
        self.assertTrue(google.allowed('https://www.youtube.com/'))
        self.assertFalse(google.allowed('https://accounts.google.com/'))
        self.assertFalse(google.allowed('https://attacker.example/'))
        instagram = ConnectedBrowser(ComposedProviderForms('instagram', composer=object()))
        self.assertTrue(instagram.allowed('https://www.instagram.com/'))
        self.assertFalse(instagram.allowed('https://www.instagram.com/accounts/login/'))
        self.assertFalse(instagram.allowed('https://mail.google.com/'))


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in intercepted Chromium fixtures')
class ComposedProviderBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.resource = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.resource.close)
        self.context = await self.resource.new_context(service_workers='block')
        await self.context.route('**/*', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<html></html>'))
        self.page = await self.context.new_page()
        self.composer = SimpleNamespace(plan=AsyncMock(side_effect=selection))

    async def adapter(self, provider):
        browser = ComposedProviderForms(provider, composer=self.composer)
        browser.page, browser.context = self.page, self.context
        await self.page.goto('https://accounts.google.com/v3/signin/password' if provider == 'google'
                             else 'https://www.instagram.com/accounts/login/')
        return browser

    async def test_dynamic_named_forms_keep_identity_and_catalog_is_separate(self):
        for provider in ('google', 'instagram'):
            browser = await self.adapter(provider)
            self.composer.plan.reset_mock()
            await self.page.set_content(
                '<form method="post"><input type="search"><button>Search</button></form>'
                '<form method="post"><input type="password" autocomplete="current-password">'
                '<button type="button" onclick="window.sent=(window.sent||0)+1">Next</button></form>')
            first = await browser.snapshot()
            self.assertEqual(first['provider'], provider)
            self.assertEqual(first['form_schema'], 'website-v1')
            self.assertEqual(first['stage'], 'form')
            self.assertEqual([field['type'] for field in first['fields']], ['password'])
            self.assertTrue(first['fields'][0]['name'].startswith('f'))
            self.assertFalse(await browser.verified())
            self.assertEqual((await browser.snapshot())['revision'], first['revision'])
            observed = self.composer.plan.await_args.args[0]
            self.assertEqual(observed['provider'], 'website')
            self.assertEqual(len(observed['groups']), 2)
            self.assertFalse(await self.page.evaluate('!!window.sent'))
            await browser.submit(first['revision'], first['actions'][0]['id'],
                                 {first['fields'][0]['name']: 'synthetic-provider-password'})
            self.assertEqual(await self.page.evaluate('window.sent'), 1)
            self.assertEqual(self.composer.plan.await_count, 1)
            with self.assertRaises(RecoverableFormError):
                await browser.submit(first['revision'], first['actions'][0]['id'],
                                     {first['fields'][0]['name']: 'synthetic-provider-password'})
            self.assertEqual(await self.page.evaluate('window.sent'), 1)
            self.assertNotEqual(browser._state_key, browser._evidence._state_key)

    async def test_google_redirect_and_empty_page_are_not_authentication(self):
        browser = await self.adapter('google')
        await self.page.goto('https://myaccount.google.com/')
        self.assertFalse(await browser.verified())
        state = await browser.snapshot()
        self.assertEqual(state['stage'], 'unsupported_layout')
        self.assertEqual(state['actions'], [])
        self.assertFalse(await browser.confirmable(state['revision']))
        self.assertFalse(browser._blocked, 'Allowed evidence redirect must not poison the policy')
        await self.page.set_content('<a href="https://accounts.google.com/SignOutOptions" '
            'aria-label="Google Account: Fixture (fixture@example.test)">Account</a>'
            '<a href="https://myaccount.google.com/security">Security</a>')
        self.assertTrue(await browser.verified())
        self.assertEqual((await browser.snapshot())['stage'], 'authenticated')
        self.composer.plan.assert_not_awaited()

    async def test_instagram_authentication_still_requires_positive_account_evidence(self):
        browser = await self.adapter('instagram')
        await self.page.goto('https://www.instagram.com/')
        self.assertFalse(await browser.verified())
        self.assertEqual((await browser.snapshot())['stage'], 'unsupported_layout')
        await self.page.set_content('<a href="/direct/inbox/">Inbox</a><svg aria-label="New post"></svg>'
                                    '<a href="/fixture/"><img alt="Fixture profile picture"></a>')
        self.assertTrue(await browser.verified())
        self.assertEqual((await browser.snapshot())['actions'], [])
        self.composer.plan.assert_not_awaited()

    async def test_verified_composed_google_handoff_destroys_credential_document(self):
        browser = await self.adapter('google')
        await self.page.goto('https://myaccount.google.com/')
        await self.page.set_content('<a href="https://accounts.google.com/SignOutOptions" '
            'aria-label="Google Account: Fixture (fixture@example.test)">Account</a>'
            '<a href="https://myaccount.google.com/security">Security</a>')
        self.assertTrue(await browser.verified())
        browser._credential_values = ['synthetic-provider-password']
        connected = ConnectedBrowser(browser)
        # Retain the test's universal network interception. Production route
        # policy itself is independently checked by test_connected_browser.py.
        with patch.object(self.context, 'route', new_callable=AsyncMock) as route, \
                patch.object(self.context, 'unroute', new_callable=AsyncMock) as unroute:
            await connected.start()
        route.assert_awaited_once()
        unroute.assert_awaited_once_with('**/*', browser._route)
        self.assertTrue(self.page.is_closed())
        self.assertEqual(connected.page.url, 'https://www.google.com/')
        self.assertEqual(connected.provider, 'google')
        self.assertTrue(connected.allowed('https://drive.google.com/drive/'))
        self.assertIs(connected.context, self.context)
        self.composer.plan.assert_not_awaited()

    async def test_known_challenges_and_blocks_never_invoke_composition(self):
        for provider in ('google', 'instagram'):
            browser = await self.adapter(provider)
            for html, stage in (
                ('<p>Check your phone</p>', 'device_approval'),
                ('<p>This browser or app may not be secure</p>', 'provider_blocked'),
                ('<iframe src="/captcha"></iframe>', 'unsupported_challenge')):
                await self.page.set_content(html)
                self.assertFalse(await browser.verified())
                state = await browser.snapshot()
                self.assertEqual(state['stage'], stage)
                self.assertEqual((state['fields'], state['actions']), ([], []))
        self.composer.plan.assert_not_awaited()

    async def test_instagram_fixed_prompt_keeps_only_known_action_without_model(self):
        browser = await self.adapter('instagram')
        for text, label in (('Decline optional cookies', 'Відхилити необов’язкові кукі'),
                            ('Not now', 'Не зараз')):
            await self.page.set_content('<p>Save your login info</p>'
                '<button onclick="window.wrong=true">Accept all</button>'
                '<button onclick="window.dismissed=(window.dismissed||0)+1">' + text + '</button>')
            state = await browser.snapshot()
            self.assertEqual(state['fields'], [])
            self.assertEqual(state['actions'], [{'id': 'a0', 'label': label}])
            self.assertFalse(await browser.verified())
            await browser.submit(state['revision'], 'a0', {})
            self.assertTrue(await self.page.evaluate('!!window.dismissed'))
            self.assertFalse(await self.page.evaluate('!!window.wrong'))
        self.composer.plan.assert_not_awaited()

    async def test_model_failure_has_no_silent_known_template_fallback(self):
        for provider in ('google', 'instagram'):
            browser = await self.adapter(provider)
            self.composer.plan.reset_mock()
            self.composer.plan.side_effect = ValueError('synthetic-private-error')
            await self.page.set_content('<form method="post"><input name="Passwd" type="password">'
                                        '<button id="passwordNext">Next</button></form>')
            for _ in range(2):
                with self.assertRaises(RecoverableFormError) as error:
                    await browser.snapshot()
                self.assertEqual(error.exception.code, 'composition_unavailable')
            self.assertFalse(await browser.verified())
            self.composer.plan.assert_awaited_once()

    async def test_unknown_origin_and_owner_confirmation_never_grant_authentication(self):
        browser = await self.adapter('google')
        await self.page.goto('https://attacker.example/')
        state = await browser.snapshot()
        self.assertEqual(state['stage'], 'provider_blocked')
        self.assertFalse(await browser.verified())
        self.assertFalse(await browser.confirmable(state['revision']))
        with self.assertRaises(ValueError):
            await browser.submit(state['revision'], 'confirm', {})
        self.composer.plan.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
