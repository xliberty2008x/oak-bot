"""Mediated browser URL, credential and provider-to-content handoff checks."""

import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from oak.connected_browser import ConnectedBrowser, allowed_url
from oak.signin_providers import WebsiteBrowser


CANARY = 'connected-test-secret-not-for-errors'
REVISION = '12345678-1234-1234-1234-123456789abc:1'
HANDLE = 'control:' + 'c' * 32


class FakePage:
    def __init__(self):
        self.url = 'https://www.google.com/'
        self.main_frame = object()
        self.closed = False
        self.credentials = False
        self.events = {}
        self.target = SimpleNamespace(evaluate=AsyncMock(return_value=False), fill=AsyncMock(), click=AsyncMock())
        self.body = SimpleNamespace(inner_text=AsyncMock(return_value='fixture page'))
        self.controls = SimpleNamespace(evaluate_all=AsyncMock(return_value=[]))
        self.input_values = SimpleNamespace(evaluate_all=AsyncMock(return_value=[]))
        self.revision = REVISION
        self.title = AsyncMock(return_value='Fixture')
        self.screenshot = AsyncMock(return_value=b'fixture-png')

    def locator(self, selector):
        if selector.startswith('input[type="password"]'):
            return SimpleNamespace(count=AsyncMock(return_value=int(self.credentials)))
        if selector == 'input,textarea,select':
            return self.input_values
        return self.body if selector == 'body' else self.controls if selector.startswith('a,button,') else self.target

    async def goto(self, url, **kwargs):
        self.url = url
        if 'framenavigated' in self.events:
            self.events['framenavigated'](self.main_frame)
        await asyncio.sleep(0)

    async def evaluate(self, script, value):
        if value['operation'] == 'snapshot':
            return {'revision': self.revision, 'controls': await self.controls.evaluate_all('fixture')}
        if value['revision'] != self.revision:
            return {'accepted': False}
        if value['operation'] == 'type':
            await self.target.fill(value['text'])
        else:
            await self.target.click()
        self.revision = REVISION[:-1] + '2'
        return {'accepted': True}

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed

    def on(self, event, callback):
        self.events[event] = callback


class EventContext:
    def __init__(self, auth_page):
        self.pages = [auth_page]
        self.listeners = []
        self.route = AsyncMock()
        self.unroute = AsyncMock()

    def on(self, event, callback):
        self.listeners.append(callback)

    def remove_listener(self, event, callback):
        self.listeners.remove(callback)

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        for callback in tuple(self.listeners):
            callback(page)
        return page


class ConnectedBrowserTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self):
        website = SimpleNamespace(provider='google', context=None)
        adapter = ConnectedBrowser(website)
        adapter.page = FakePage()
        return adapter

    def test_fixed_application_origins_and_credentials_are_restricted(self):
        for url in ('https://www.youtube.com/', 'https://mail.google.com/mail/u/0/', 'https://drive.google.com/drive/'):
            self.assertTrue(allowed_url('google', url))
        for url in ('http://www.google.com/', 'https://www.google.com.evil.test/',
                    'https://user:password@www.google.com/', 'https://www.google.com:8443/',
                    'https://accounts.google.com/', 'https://myaccount.google.com/',
                    'https://www.google.com/login/', 'https://www.google.com/?access_token=fixture',
                    'https://www.google.com/?%61ccess_token=fixture', 'https://www.google.com/#token'):
            self.assertFalse(allowed_url('google', url), url)

    def test_encoded_private_paths_and_dot_segments_are_rejected(self):
        for path in ('/%61ccounts/edit/', '/accounts%2Fedit/', '/%2561ccounts/edit/',
                     '/safe/../accounts/edit/', '/safe%5caccounts/edit/', '/challenge/'):
            self.assertFalse(allowed_url('instagram', 'https://www.instagram.com' + path), path)

    def test_resources_require_real_domain_boundary(self):
        self.assertTrue(allowed_url('google', 'https://fonts.gstatic.com/font', resource=True))
        self.assertFalse(allowed_url('google', 'https://evilgstatic.com/font', resource=True))
        self.assertFalse(allowed_url('google', 'https://gstatic.com.evil.test/font', resource=True))
        self.assertFalse(allowed_url('instagram', 'https://www.google.com/'))

    async def test_handoff_removes_auth_popup_listener_before_creating_content_page(self):
        website = WebsiteBrowser('google')
        website.page = FakePage()
        auth_page = website.page
        website.context = EventContext(auth_page)
        website._page_listener = lambda page: asyncio.create_task(website._close_popup(page))
        website.context.on('page', website._page_listener)
        adapter = ConnectedBrowser(website)
        await adapter.start()
        await asyncio.sleep(0)
        self.assertTrue(auth_page.closed)
        self.assertFalse(adapter.page.closed)
        self.assertNotIn(website._page_listener, website.context.listeners)
        website.context.route.assert_awaited_once()
        website.context.unroute.assert_awaited_once_with('**/*', website._route)
        popup = await website.context.new_page()
        await asyncio.sleep(0)
        self.assertTrue(popup.closed)
        self.assertFalse(adapter.page.closed)

    async def test_redirect_or_inline_password_stops_page_capture(self):
        adapter = self.adapter()
        for url, credentials in (('https://accounts.google.com/', False), ('https://www.google.com/', True)):
            adapter.page.url, adapter.page.credentials = url, credentials
            with self.assertRaises(ValueError):
                await adapter.run({'action': 'read'})
            adapter.page.title.assert_not_awaited()
            adapter.page.body.inner_text.assert_not_awaited()

    async def test_navigation_guard_blocks_external_and_auth_destinations(self):
        adapter = self.adapter()
        for url in ('https://attacker.example/', 'https://accounts.google.com/signin/'):
            route = SimpleNamespace(request=SimpleNamespace(url=url, is_navigation_request=lambda: True),
                                    abort=AsyncMock(), continue_=AsyncMock())
            await adapter._route(route)
            route.abort.assert_awaited_once()
            route.continue_.assert_not_awaited()

    async def test_arbitrary_js_cookie_operations_and_extra_fields_rejected(self):
        adapter = self.adapter()
        for operation in ({'action': 'evaluate', 'script': 'document.cookie'}, {'action': 'cookies'},
                          {'action': 'read', 'password': CANARY}, {'action': 'open', 'url': 'https://evil.test/'},
                          {'action': 'type', 'selector': '#x', 'text': 'x' * 16001}):
            with self.assertRaises(ValueError):
                await adapter.run(operation)
        adapter.page.title.assert_not_awaited()

    async def test_credentials_never_filled_and_raw_browser_error_is_sanitized(self):
        adapter = self.adapter()
        adapter.page.controls.evaluate_all.return_value = [{'selector': HANDLE, 'tag': 'input', 'type': 'text', 'text': 'Task'}]
        state = await adapter.run({'action': 'read'})
        adapter.page.credentials = True
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'type', 'selector': HANDLE, 'revision': state['revision'], 'text': CANARY})
        adapter.page.target.fill.assert_not_awaited()
        adapter.page.credentials = False
        adapter.page.target.fill.side_effect = RuntimeError(CANARY)
        with self.assertRaises(ValueError) as error:
            await adapter.run({'action': 'type', 'selector': HANDLE, 'revision': state['revision'], 'text': CANARY})
        self.assertNotIn(CANARY, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    async def test_capture_is_discarded_if_navigation_changes_mid_read(self):
        adapter = self.adapter()
        async def changed():
            adapter._navigate(adapter.page.main_frame)
            return CANARY
        adapter.page.body.inner_text = changed
        with self.assertRaises(ValueError) as error:
            await adapter.run({'action': 'read'})
        self.assertNotIn(CANARY, str(error.exception))

    async def test_read_and_bounded_screenshot_are_available(self):
        adapter = self.adapter()
        result = await adapter.run({'action': 'read'})
        self.assertEqual(result['text'], 'fixture page')
        result = await adapter.run({'action': 'screenshot'})
        self.assertIn('image_base64', result)
        adapter.page.screenshot.return_value = b'x' * (1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'screenshot'})

    async def test_generic_handoff_retains_approved_task_document_and_same_origin(self):
        page = FakePage()
        page.url = 'https://example.com/task'
        website = SimpleNamespace(provider='website', destination='https://example.com', page=page,
                                  context=SimpleNamespace(route=AsyncMock()), _route=AsyncMock(), _redactions=[])
        adapter = ConnectedBrowser(website)
        await adapter.start()
        self.assertIs(adapter.page, page)
        self.assertFalse(page.closed)
        self.assertTrue(adapter.allowed('https://example.com/next'))
        for url in ('https://elsewhere.example/task', 'https://cdn.example.com/file',
                    'https://example.com/%61ccounts/security/', 'https://example.com/?token=fixture'):
            self.assertFalse(adapter.allowed(url), url)
        self.assertEqual((await adapter.run({'action': 'read'}))['text'], 'fixture page')

    async def test_generic_route_composes_with_existing_pinned_origin_guard(self):
        adapter = ConnectedBrowser(SimpleNamespace(provider='website', destination='https://example.com', context=None))
        route = SimpleNamespace(request=SimpleNamespace(url='https://example.com/task', is_navigation_request=lambda: True),
                                abort=AsyncMock(), continue_=AsyncMock(), fallback=AsyncMock())
        await adapter._route(route)
        route.fallback.assert_awaited_once()
        route.continue_.assert_not_awaited()
        route.abort.assert_not_awaited()

    async def test_password_echo_blocks_read_and_screenshot_without_returning_secret(self):
        adapter = self.adapter()
        adapter.website._credential_values = [CANARY]
        for location in ('body', 'title', 'url'):
            adapter.page.body.inner_text.return_value = CANARY if location == 'body' else 'fixture page'
            adapter.page.title.return_value = CANARY if location == 'title' else 'Fixture'
            adapter.page.url = 'https://www.google.com/' + (CANARY if location == 'url' else '')
            for action in ('read', 'screenshot'):
                with self.assertRaises(ValueError) as error:
                    await adapter.run({'action': action})
                self.assertNotIn(CANARY, str(error.exception))
        adapter.page.screenshot.assert_not_awaited()

    async def test_password_echo_in_control_metadata_is_discarded(self):
        adapter = self.adapter()
        adapter.website._credential_values = [CANARY]
        adapter.page.controls.evaluate_all.return_value = [{'selector': HANDLE, 'tag': 'button', 'type': None, 'text': CANARY}]
        with self.assertRaises(ValueError) as error:
            await adapter.run({'action': 'read'})
        self.assertNotIn(CANARY, str(error.exception))

    async def test_quoted_password_in_control_metadata_cannot_bypass_output_scan(self):
        adapter = self.adapter()
        quoted = 'fixture-"quoted\\password'
        adapter.website._credential_values = [quoted]
        adapter.page.controls.evaluate_all.return_value = [{'selector': HANDLE, 'tag': 'button', 'type': None, 'text': quoted}]
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'read'})

    async def test_long_password_cannot_leak_prefix_through_label_truncation(self):
        adapter = self.adapter()
        secret = 'long-password-' * 30
        adapter.website._credential_values = [secret]
        adapter.page.controls.evaluate_all.return_value = [{'selector': HANDLE, 'tag': 'button', 'type': None, 'text': secret}]
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'read'})

    async def test_screenshot_is_unavailable_after_any_credential_submission(self):
        adapter = self.adapter()
        adapter.website._credential_values = [CANARY]
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'screenshot'})
        adapter.page.screenshot.assert_not_awaited()

    async def test_password_echo_in_visible_text_input_blocks_handoff(self):
        adapter = self.adapter()
        adapter.website._credential_values = [CANARY]
        adapter.page.input_values.evaluate_all.return_value = [CANARY]
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'read'})

    async def test_only_current_issued_handles_can_trigger_actions(self):
        adapter = self.adapter()
        adapter.page.controls.evaluate_all.return_value = [{'selector': HANDLE, 'tag': 'input', 'type': 'text', 'text': 'Task'}]
        state = await adapter.run({'action': 'read'})
        for selector, revision in (("body:has(input[value^='a'])", state['revision']),
                                   ('control:' + 'd' * 32, state['revision']), (HANDLE, 'old')):
            with self.assertRaises(ValueError):
                await adapter.run({'action': 'click', 'selector': selector, 'revision': revision})
        adapter.page.target.click.assert_not_awaited()
        result = await adapter.run({'action': 'type', 'selector': HANDLE, 'revision': state['revision'], 'text': 'Task content'})
        adapter.page.target.fill.assert_awaited_once_with('Task content')
        self.assertNotEqual(result['revision'], state['revision'])
        with self.assertRaises(ValueError):
            await adapter.run({'action': 'click', 'selector': HANDLE, 'revision': state['revision']})


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in local intercepted Chromium fixtures')
class ConnectedBrowserDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.resource = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.resource.close)
        self.context = await self.resource.new_context(service_workers='block')
        await self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<html></html>'))
        self.page = await self.context.new_page()
        await self.page.goto('https://www.google.com/task')
        website = SimpleNamespace(provider='google', context=self.context, _credential_values=[])
        self.adapter = ConnectedBrowser(website)
        self.adapter.page = self.page

    async def test_shown_password_value_blocks_content_and_screenshot(self):
        self.adapter.website._credential_values = [CANARY]
        await self.page.set_content('<input type="text">')
        await self.page.locator('input').fill(CANARY)
        self.assertNotIn(CANARY, await self.page.locator('body').inner_text())
        for action in ('read', 'screenshot'):
            with self.assertRaises(ValueError):
                await self.adapter.run({'action': action})

    async def test_quoted_password_in_aria_label_is_rejected(self):
        secret = 'fixture-"quoted\\password'
        self.adapter.website._credential_values = [secret]
        await self.page.set_content('<button></button>')
        await self.page.locator('button').evaluate('(e,value)=>e.setAttribute("aria-label",value)', secret)
        self.assertNotIn(secret, await self.page.locator('body').inner_text())
        with self.assertRaises(ValueError):
            await self.adapter.run({'action': 'read'})

    async def test_long_password_label_cannot_leak_a_truncated_prefix(self):
        secret = 'long-password-' * 30
        self.adapter.website._credential_values = [secret]
        await self.page.set_content('<button></button>')
        await self.page.locator('button').evaluate('(e,value)=>e.setAttribute("aria-label",value)', secret)
        with self.assertRaises(ValueError):
            await self.adapter.run({'action': 'read'})

    async def test_ordinary_noncredential_document_keeps_screenshot(self):
        await self.page.set_content('<p>Ordinary task page</p>')
        result = await self.adapter.run({'action': 'screenshot'})
        self.assertTrue(result['image_base64'])

    async def test_real_control_handles_support_type_click_and_reject_css_oracle(self):
        await self.page.set_content('<form><input type="hidden" value="private-token"><input type="text" id="task">'
            '<div role="button" onclick="window.clicked=true">Save</div></form>')
        state = await self.adapter.run({'action': 'read'})
        self.assertEqual(len(state['controls']), 2)
        self.assertNotIn('private-token', str(state))
        with self.assertRaises(ValueError):
            await self.adapter.run({'action': 'click', 'selector': "body:has(input[value^='p'])", 'revision': state['revision']})
        field = next(control['selector'] for control in state['controls'] if control['tag'] == 'input')
        state = await self.adapter.run({'action': 'type', 'selector': field, 'revision': state['revision'], 'text': 'Task content'})
        self.assertEqual(await self.page.locator('#task').input_value(), 'Task content')
        button = next(control['selector'] for control in state['controls'] if control['tag'] == 'button')
        await self.adapter.run({'action': 'click', 'selector': button, 'revision': state['revision']})
        self.assertTrue(await self.page.evaluate('window.clicked'))

    async def test_dom_replacement_and_hidden_csrf_drift_invalidate_control_revision(self):
        html = '<input type="hidden" value="one"><button onclick="window.clicked=true">Save</button>'
        for change in ('document.querySelector("button").outerHTML="<button>Replacement</button>"',
                       'document.querySelector("input").value="two"'):
            await self.page.set_content(html)
            state = await self.adapter.run({'action': 'read'})
            await self.page.evaluate(change)
            with self.assertRaises(ValueError):
                await self.adapter.run({'action': 'click', 'selector': state['controls'][0]['selector'], 'revision': state['revision']})
            self.assertFalse(await self.page.evaluate('!!window.clicked'))


if __name__ == '__main__':
    unittest.main()
