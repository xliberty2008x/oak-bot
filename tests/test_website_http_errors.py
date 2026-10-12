"""Real intercepted Chromium documents: HTTP failures cannot imply login."""

import json
import os
import unittest
from unittest.mock import AsyncMock

from oak.website_forms import ComposedProviderForms, WebsiteForms


FORM = '<form method="post"><input type="password"><button type="button">Continue</button></form>'
ACCOUNT = ('<a href="https://accounts.google.com/SignOutOptions" '
           'aria-label="Google Account: Fixture fixture@example.test">Account</a>'
           '<a href="https://myaccount.google.com/personal-info">Personal information</a>')


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in local intercepted Chromium fixtures')
class WebsiteHTTPErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.resource = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.resource.close)

    async def browser(self, adapter=None):
        adapter = adapter or WebsiteForms('https://example.test/login')
        context = await self.resource.new_context(service_workers='block')
        self.addAsyncCleanup(context.close)
        adapter.context = context
        # The production start calls this hook before creating/navigating the page.
        await adapter._configure_context()
        adapter.page = await context.new_page()
        responses = {}

        async def intercept(route):
            response = responses.get(route.request.url, (200, '<h1>Fixture page</h1>'))
            if response is None:
                await route.abort('failed')
            else:
                await route.fulfill(status=response[0], content_type='text/html', body=response[1])

        await context.route('**/*', intercept)
        return adapter, responses

    async def assert_blocked(self, browser):
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'provider_blocked')
        self.assertEqual(snapshot['fields'], [])
        self.assertEqual(snapshot['actions'], [])
        self.assertFalse(await browser.confirmable(snapshot['revision']))
        self.assertFalse(await browser.verified())
        return snapshot

    async def accept_values_then_ready(self, browser, responses):
        responses['https://example.test/input'] = (200, FORM)
        await browser.page.goto('https://example.test/input')
        form = await browser.snapshot()
        await browser.submit(form['revision'], form['actions'][0]['id'],
                             {form['fields'][0]['name']: 'synthetic-form-input'})
        await browser.page.goto('https://example.test/ready')
        ready = await browser.snapshot()
        self.assertTrue(await browser.confirmable(ready['revision']))
        self.assertFalse(await browser.verified())
        return ready

    async def test_error_documents_never_offer_review_controls_or_completion(self):
        browser, responses = await self.browser()
        previous = await self.accept_values_then_ready(browser, responses)
        for index, (status, html) in enumerate(((403, '<h1>Access denied</h1>'),
                (403, '<button type="button">Continue</button>'), (403, FORM),
                (401, FORM), (404, '<h1>Not found</h1>'), (429, FORM), (503, FORM))):
            with self.subTest(status=status, layout=index):
                url = f'https://example.test/error-{index}?private=synthetic-query-canary'
                responses[url] = (status, html)
                await browser.page.goto(url)
                snapshot = await self.assert_blocked(browser)
                self.assertNotIn('synthetic-query-canary', json.dumps(snapshot))
                self.assertFalse(await browser.confirmable(previous['revision']))

    async def test_only_a_successful_main_document_clears_http_failure(self):
        browser, responses = await self.browser()
        responses['https://example.test/denied'] = (403, '<h1>Unavailable</h1>')
        await browser.page.goto('https://example.test/denied')
        await self.assert_blocked(browser)
        await browser.page.evaluate("fetch('/successful-asset').then(r => r.text())")
        await self.assert_blocked(browser)
        responses['https://example.test/login'] = (200, FORM)
        await browser.page.goto('https://example.test/login')
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'form')
        self.assertEqual([field['type'] for field in snapshot['fields']], ['password'])
        self.assertNotIn('confirm', [action['id'] for action in snapshot['actions']])
        await browser.submit(snapshot['revision'], snapshot['actions'][0]['id'],
                             {snapshot['fields'][0]['name']: 'synthetic-form-input'})
        await browser.page.goto('https://example.test/ready')
        self.assertTrue(await browser.confirmable((await browser.snapshot())['revision']))

    async def test_subresource_and_subframe_403_do_not_block_successful_document(self):
        browser, responses = await self.browser()
        responses['https://example.test/login'] = (200, FORM +
            '<script src="/script"></script><iframe src="/frame"></iframe>')
        for path in ('script', 'frame', 'fetch'):
            responses['https://example.test/' + path] = (403, '<h1>Denied asset</h1>')
        await browser.page.goto('https://example.test/login')
        self.assertEqual(await browser.page.evaluate("fetch('/fetch').then(r => r.status)"), 403)
        snapshot = await browser.snapshot()
        self.assertEqual(snapshot['stage'], 'form')
        self.assertEqual([field['type'] for field in snapshot['fields']], ['password'])

    async def test_failed_main_navigation_cannot_confirm_previous_page_and_can_recover(self):
        from playwright.async_api import Error
        browser, responses = await self.browser()
        previous = await self.accept_values_then_ready(browser, responses)
        responses['https://example.test/failed'] = None
        async with browser.page.expect_event('framenavigated'):
            with self.assertRaises(Error):
                await browser.page.goto('https://example.test/failed')
        await self.assert_blocked(browser)
        self.assertFalse(await browser.confirmable(previous['revision']))
        await browser.page.goto('https://example.test/recovered')
        recovered = await browser.snapshot()
        self.assertEqual(recovered['stage'], 'review')
        self.assertTrue(await browser.confirmable(recovered['revision']))

    async def test_http_error_skips_composer_and_rejects_previously_valid_action(self):
        browser, responses = await self.browser()
        responses['https://example.test/login'] = (200, FORM)
        await browser.page.goto('https://example.test/login')
        previous = await browser.snapshot()
        composer = AsyncMock()
        browser._composer = composer
        responses['https://example.test/error'] = (403, FORM)
        await browser.page.goto('https://example.test/error')
        await self.assert_blocked(browser)
        with self.assertRaises(ValueError):
            await browser.submit(previous['revision'], previous['actions'][0]['id'],
                                 {previous['fields'][0]['name']: ''})
        composer.plan.assert_not_awaited()

    async def test_named_provider_evidence_requires_successful_http_document(self):
        for adapter in (WebsiteForms('https://myaccount.google.com/'),
                        ComposedProviderForms('google', composer=AsyncMock())):
            with self.subTest(provider=adapter.provider):
                browser, responses = await self.browser(adapter)
                for status in (200, 403, 200):
                    url = f'https://myaccount.google.com/check-{status}'
                    responses[url] = (status, ACCOUNT)
                    await browser.page.goto(url)
                    if status == 403:
                        await self.assert_blocked(browser)
                    else:
                        snapshot = await browser.snapshot()
                        self.assertEqual(snapshot['stage'], 'authenticated')
                        self.assertTrue(await browser.verified())
                        self.assertFalse(await browser.confirmable(snapshot['revision']))

