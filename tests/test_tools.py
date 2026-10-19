import importlib.util
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.media import MediaTools
from oak.research import _Readable, public_url
from oak.tools import Tools


class ToolTests(unittest.TestCase):
    def test_workspace_boundary_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside.wav'
            outside.write_bytes(b'audio')
            tools = MediaTools(root / 'workspace')
            (tools.workspace / 'linked.wav').symlink_to(outside)
            for path in (outside, 'linked.wav'):
                with self.assertRaises(ValueError):
                    tools._input(path)

    def test_page_reader_omits_executable_content(self):
        page = _Readable()
        page.feed('<title>Oak</title><script>secret()</script><style>hidden</style>'
                  '<p>Привіт &amp; світ</p><a class="result__a" href="https://example.com">Example</a>')
        self.assertNotIn('secret', ''.join(page.parts))
        self.assertNotIn('hidden', ''.join(page.parts))
        self.assertIn('Привіт & світ', ''.join(page.parts))
        self.assertEqual(page.links, [{'url': 'https://example.com', 'title': 'Example'}])

    def test_research_rejects_local_and_credential_urls(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'https://[::1]/',
                    'https://user:secret@example.com/'):
            with self.assertRaises(ValueError):
                public_url(url)

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is optional')
    def test_titlecard_is_real_unicode_png(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            tools = MediaTools(folder)
            output = tools.generate_image('Дуб — нова історія', subtitle='Oak', width=640, height=360)
            with Image.open(output) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.size, (640, 360))
                self.assertGreater(len(image.getcolors(640 * 360)), 2)


class BrowserDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.controller = SimpleNamespace(
            cwd=self.folder.name, db=self.db, active={7: 'turn', 8: 'other'},
            threads={7: 'thread'}, chat_for_thread=lambda thread: 7,
            emit=AsyncMock(), stop=AsyncMock(), manual_owner=None,
            interactions=SimpleNamespace(ask=AsyncMock(return_value=True)),
            requests=SimpleNamespace(website_login=SimpleNamespace(use=AsyncMock(return_value={'ok': True}))))
        self.metadata = {'threadId': 'thread', 'turnId': 'turn'}

    def tools(self, approval='always', *, shared=False):
        config = {'browser_action_approval': approval}
        desktop = None
        if shared:
            config['computer'] = {'enabled': True, 'managed': True, 'display': ':99'}
            desktop = SimpleNamespace(enabled=True, browser_context=AsyncMock(return_value=object()))
        tools = Tools(self.controller, config, desktop=desktop)
        tools.browser.run = AsyncMock(return_value={'ok': True})
        if shared:
            tools.computer.run = AsyncMock(return_value={'ok': True})
        return tools

    async def test_direct_managed_browser_uses_desktop_context_provider(self):
        context = object()
        desktop = SimpleNamespace(enabled=True, browser_context=AsyncMock(return_value=context))
        tools = Tools(self.controller, {'computer': {'enabled': True, 'managed': True, 'display': ':99'}},
                      desktop=desktop)
        self.assertTrue(tools.shared_browser)
        self.assertIs(await tools.browser._context_provider(), context)
        desktop.browser_context.assert_awaited_once()

    async def test_owner_setting_removes_routine_approvals_in_both_browsers(self):
        tools = self.tools('never')
        for action in ('click', 'type', 'upload', 'download'):
            await tools.execute(7, 'oak_browser', {'action': action}, self.metadata)
        for action in ('click', 'type'):
            await tools.execute(7, 'oak_account_browser', {'provider': 'website', 'action': action}, self.metadata)
        self.controller.interactions.ask.assert_not_awaited()
        self.assertEqual(tools.browser.run.await_count, 4)
        self.assertEqual(self.controller.requests.website_login.use.await_count, 2)

    async def test_default_approval_does_not_publish_sensitive_arguments_and_denial_prevents_action(self):
        tools = Tools(self.controller, {})
        tools.browser.run = AsyncMock()
        self.controller.interactions.ask.return_value = False
        args = {'action': 'type', 'selector': '#private-selector', 'text': 'private-input',
                'url': 'https://example.test/?token=private-token'}
        for name, values in [('oak_browser', args), ('oak_account_browser', dict(args, provider='website'))]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                await tools.execute(7, name, values, dict(self.metadata, arguments=values, tool=name))
            prompt = self.controller.interactions.ask.await_args.args[-1]
            for value in ('private-selector', 'private-input', 'private-token'):
                self.assertNotIn(value, prompt)
                self.assertNotIn(value, json.dumps(self.controller.interactions.ask.await_args.args[0]))
            self.assertEqual(self.controller.interactions.ask.await_args.args[0], self.metadata)
        tools.browser.run.assert_not_awaited()
        self.controller.requests.website_login.use.assert_not_awaited()

    def test_browser_approval_setting_rejects_mistyped_values(self):
        for value in (True, False, None, {}, 'approve'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.tools(value)

    async def test_browser_screenshot_is_visible_to_model_without_owner_delivery(self):
        tools = self.tools('never')
        output = Path(self.folder.name) / 'screen.png'
        output.write_bytes(b'png-test-image')
        tools.browser.run.return_value = {'ok': True, 'path': str(output)}
        response = await tools.handle(dict(self.metadata, tool='oak_browser', arguments={'action': 'screenshot'}))
        self.assertTrue(response['success'])
        self.assertEqual([item['type'] for item in response['contentItems']], ['inputText', 'inputImage'])
        self.assertTrue(response['contentItems'][1]['imageUrl'].startswith('data:image/png;base64,'))
        self.controller.emit.assert_not_awaited()
        response = await tools.handle(dict(self.metadata, tool='oak_browser', arguments={'action': 'download'}))
        self.assertEqual([item['type'] for item in response['contentItems']], ['inputText'])
        self.controller.emit.assert_not_awaited()
        await tools.execute(7, 'oak_send_file', {'path': str(output)}, self.metadata)
        self.controller.emit.assert_awaited_once()

    async def test_browser_failure_keeps_recovery_evidence_and_reports_tool_failure(self):
        tools = self.tools('never')
        failure = {'ok': False, 'outcome': 'uncertain', 'error': {
            'code': 'timeout', 'message': 'No download observed.', 'recovery': 'Read the page before retrying.'}}
        tools.browser.run.return_value = failure
        response = await tools.handle(dict(self.metadata, tool='oak_browser', arguments={'action': 'download'}))
        self.assertFalse(response['success'])
        self.assertEqual(json.loads(response['contentItems'][0]['text']), failure)

    async def test_real_upload_preflight_remains_failed_and_actionable_in_native_tool_response(self):
        from oak.browser import MAX_UPLOAD
        tools = Tools(self.controller, {'browser_action_approval': 'never'})
        source = Path(self.folder.name) / 'private-file-canary.mp4'
        with source.open('wb') as stream:
            stream.truncate(MAX_UPLOAD + 1)
        tools.browser._start = AsyncMock(side_effect=AssertionError('Must not start browser'))
        response = await tools.handle(dict(self.metadata, tool='oak_browser', arguments={
            'action': 'upload', 'selector': '#file', 'files': [str(source)]}))
        self.assertFalse(response['success'])
        result = json.loads(response['contentItems'][0]['text'])
        self.assertEqual(result['outcome'], 'not_attempted')
        self.assertEqual(result['error']['code'], 'payload_too_large')
        self.assertEqual(result['error']['observed_bytes'], MAX_UPLOAD + 1)
        self.assertEqual(result['error']['max_bytes'], MAX_UPLOAD)
        self.assertEqual(result['error']['stage'], 'upload_preflight')
        self.assertNotIn(source.name, response['contentItems'][0]['text'])
        tools.browser._start.assert_not_awaited()
        self.controller.emit.assert_not_awaited()

    async def test_shared_browser_and_computer_hold_one_turn_owner_and_respect_takeover(self):
        tools = self.tools('never', shared=True)
        await tools.execute(7, 'oak_browser', {'action': 'read'}, self.metadata)
        with self.assertRaises(RuntimeError):
            await tools.execute(8, 'oak_computer', {'action': 'screenshot'}, {'turnId': 'other'})
        await tools.execute(7, 'oak_computer', {'action': 'screenshot'}, self.metadata)
        self.controller.manual_owner = 7
        for action in ('read', 'screenshot', 'upload'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                await tools.execute(7, 'oak_browser', {'action': action}, self.metadata)
        self.controller.manual_owner = None
        self.controller.active.pop(7)
        with self.assertRaises(ValueError):
            await tools.execute(7, 'oak_browser', {'action': 'read'}, self.metadata)
        await tools.execute(8, 'oak_browser', {'action': 'read'}, {'turnId': 'other'})
        self.assertEqual(tools.browser.run.await_count, 2)

    async def test_switch_disables_and_cancels_shared_browser_operation(self):
        tools = self.tools('never', shared=True)
        started = asyncio.Event()

        async def run(**args):
            started.set()
            await asyncio.Event().wait()

        tools.browser.run.side_effect = run
        operation = asyncio.create_task(tools.execute(7, 'oak_browser', {'action': 'download'}, self.metadata))
        await started.wait()
        await tools.set_computer_enabled(7, False)
        self.assertTrue(operation.cancelled())
        self.controller.stop.assert_awaited_once_with(7)
        with self.assertRaises(ValueError):
            await tools.execute(7, 'oak_browser', {'action': 'read'}, self.metadata)

    async def test_semantic_and_visual_actions_do_not_overlap(self):
        tools = self.tools('never', shared=True)
        started, release = asyncio.Event(), asyncio.Event()

        async def run(**args):
            started.set()
            await release.wait()
            return {'ok': True}

        tools.browser.run.side_effect = run
        semantic = asyncio.create_task(tools.execute(7, 'oak_browser', {'action': 'click'}, self.metadata))
        await started.wait()
        visual = asyncio.create_task(tools.execute(7, 'oak_computer', {'action': 'click'}, self.metadata))
        await asyncio.sleep(0)
        tools.computer.run.assert_not_awaited()
        release.set()
        await asyncio.gather(semantic, visual)
        tools.computer.run.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
