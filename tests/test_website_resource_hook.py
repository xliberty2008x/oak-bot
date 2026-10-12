"""Resource-policy integration without network or browser launch."""

import asyncio
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from oak.signin_providers import RecoverableFormError, WebsiteBrowser
from oak.website_forms import ComposedProviderForms, WebsiteForms


class WebsiteResourceHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_diagnostics_expose_only_resource_counters(self):
        from oak.website_agent import WebsiteAgentForms
        browser = WebsiteAgentForms('https://login.example.com/', anonymous_static_resources=True)
        policy = browser._resources
        policy._pins = {'private-host-canary.example': '93.184.216.34'}
        policy._anonymous_urls.add('https://private-host-canary.example/private-path-canary')
        self.assertEqual(browser.diagnostics()['resources'], {
            'requests': 0, 'max_requests': 512, 'bytes': 0, 'hosts': 1,
            'completed_urls': 1, 'frozen': False, 'failures': {'request_quota': 0}})
        self.assertNotIn('canary', str(browser.diagnostics()))
        self.assertIsNone(WebsiteForms('https://login.example.com/').diagnostics()['resources'])
        await browser.close()

    def policy(self):
        return SimpleNamespace(prepare=AsyncMock(), handle=AsyncMock(return_value=True),
                               freeze=Mock(), close=AsyncMock())

    async def test_generic_prepare_and_known_start_remain_separate(self):
        policy = self.policy()
        resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
        with patch('oak.website_forms.StaticWebsiteResources', return_value=policy) as factory:
            generic = WebsiteForms('https://www.tiktok.com/login', resolver=resolver, static_resources=True)
        factory.assert_called_once_with('https://www.tiktok.com', resolver=resolver)
        with patch.object(WebsiteBrowser, 'start', new_callable=AsyncMock):
            await generic.start()
        policy.prepare.assert_awaited_once()
        policy.prepare.reset_mock()
        with patch('oak.website_forms.StaticWebsiteResources', return_value=policy):
            known = ComposedProviderForms('google', composer=object())
        with patch.object(WebsiteBrowser, 'start', new_callable=AsyncMock):
            await known.start()
        policy.prepare.assert_not_awaited()
        self.assertIsNone(known._resources)

    async def test_static_dependencies_are_disabled_by_default_and_flag_is_exact_bool(self):
        with patch('oak.website_forms.StaticWebsiteResources') as factory:
            browser = WebsiteForms('https://www.tiktok.com/login')
        factory.assert_not_called()
        self.assertIsNone(browser._resources)
        for flag in ('true', 1, None, {}):
            with self.assertRaises(ValueError):
                WebsiteForms('https://www.tiktok.com/login', static_resources=flag)

    async def test_explicit_host_map_is_bound_copied_and_legacy_merge_is_validated(self):
        hosts = {'assets.example.com': ['script']}
        browser = WebsiteForms('https://login.example.com/login', resource_hosts=hosts)
        hosts['other.example.com'] = ['font']
        hosts['assets.example.com'].append('stylesheet')
        self.assertEqual(browser._resources._hosts, {'assets.example.com': frozenset({'script'})})
        self.assertEqual(browser._resources.origin, 'https://login.example.com')
        from oak.website_resources import STATIC_HOSTS
        combined = WebsiteForms('https://www.tiktok.com/login', static_resources=True,
            resource_hosts={'explicit.example.com': ['script']})
        self.assertEqual(set(combined._resources._hosts), set(STATIC_HOSTS) | {'explicit.example.com'})
        for hosts in ({}, {'assets.example.com': ['fetch']}, {'*.example.com': ['script']}, True, []):
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                WebsiteForms('https://login.example.com/login', resource_hosts=hosts)
        with self.assertRaises(ValueError):
            WebsiteForms('https://www.tiktok.com/login', static_resources=True,
                         resource_hosts={next(iter(STATIC_HOSTS)): ['font']})

    async def test_hook_only_receives_external_nonnavigation_requests(self):
        browser = WebsiteForms('https://www.tiktok.com/login')
        browser._resources = self.policy()
        browser.page = SimpleNamespace(main_frame=object())
        def route(url, navigation=False):
            return SimpleNamespace(request=SimpleNamespace(url=url, frame=browser.page.main_frame,
                is_navigation_request=lambda: navigation), abort=AsyncMock(), continue_=AsyncMock())
        same = route('https://www.tiktok.com/login/phone')
        await browser._route(same)
        same.continue_.assert_awaited_once()
        browser._resources.handle.assert_not_awaited()
        static = route('https://static.example/script.js')
        await browser._route(static)
        browser._resources.handle.assert_awaited_once_with(static)
        static.abort.assert_not_awaited()
        static.continue_.assert_not_awaited()
        browser._resources.handle.reset_mock()
        navigation = route('https://static.example/login', True)
        await browser._route(navigation)
        navigation.abort.assert_awaited_once()
        browser._resources.handle.assert_not_awaited()
        self.assertTrue(browser._blocked)

    async def test_close_releases_resources_even_when_browser_cleanup_fails(self):
        browser = WebsiteForms('https://www.tiktok.com/login')
        browser._resources = self.policy()
        browser._redactions = ['synthetic-private-value']
        with patch.object(WebsiteBrowser, 'close', new_callable=AsyncMock, side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                await browser.close()
        browser._resources.close.assert_awaited_once()
        self.assertEqual(browser._redactions, [])

    async def test_resource_set_freezes_before_any_private_input_dispatch(self):
        browser = WebsiteForms('https://www.tiktok.com/login')
        browser._resources = self.policy()
        state = {'stage': 'form', 'revision': 'fixture:1',
                 'fields': [{'name': 'f1', 'type': 'password', 'max_length': 1024}],
                 'actions': [{'id': 'a1'}]}
        browser._observe = AsyncMock(return_value=state)
        sequence = []
        browser._resources.freeze.side_effect = lambda: sequence.append('freeze')
        async def submit(script, arguments):
            sequence.append(arguments['operation'])
            return {'accepted': True}
        browser.page = SimpleNamespace(evaluate=AsyncMock(side_effect=submit))
        with self.assertRaises(RecoverableFormError):
            await browser.submit('stale:1', 'a1', {'f1': 'synthetic-private-value'})
        browser._resources.freeze.assert_called_once_with()
        sequence.clear()
        await browser.submit('fixture:1', 'a1', {'f1': 'synthetic-private-value'})
        self.assertEqual(sequence, ['freeze', 'submit'])
        self.assertEqual(browser._resources.freeze.call_args.args, ())

    async def test_invalid_and_cancelled_nonempty_values_freeze_even_without_dispatch(self):
        for values in ({'unknown': ''}, {'unknown': False}, {'unknown': 'o1'}):
            browser = WebsiteForms('https://login.example.com/login')
            browser._resources = self.policy()
            browser._observe = AsyncMock(return_value={'stage': 'form', 'revision': 'fixture:1',
                'fields': [], 'actions': [{'id': 'a1'}]})
            browser.page = SimpleNamespace(evaluate=AsyncMock())
            with self.assertRaises(ValueError):
                await browser.submit('fixture:1', 'a1', values)
            browser._resources.freeze.assert_called_once_with()
            browser.page.evaluate.assert_not_awaited()
        browser = WebsiteForms('https://login.example.com/login')
        browser._resources = self.policy()
        await browser._lock.acquire()
        pending = asyncio.create_task(browser.submit('fixture:1', 'a1', {'f1': ''}))
        try:
            await asyncio.sleep(0)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        finally:
            browser._lock.release()
        browser._resources.freeze.assert_called_once_with()

    async def test_empty_chooser_action_does_not_freeze_resources(self):
        browser = WebsiteForms('https://www.tiktok.com/login')
        browser._resources = self.policy()
        browser._observe = AsyncMock(return_value={'stage': 'form', 'revision': 'fixture:1',
            'fields': [], 'actions': [{'id': 'a1'}]})
        browser.page = SimpleNamespace(evaluate=AsyncMock(return_value={'accepted': True}))
        await browser.submit('fixture:1', 'a1', {})
        browser._resources.freeze.assert_not_called()


if __name__ == '__main__':
    unittest.main()
