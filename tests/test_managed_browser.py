"""Managed desktop/browser ownership and optional real headed Chromium checks."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.desktop import ManagedDesktop


class ManagedBrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_is_borrowed_only_while_browser_is_running(self):
        desktop = ManagedDesktop({'state_dir': '/tmp/synthetic', 'workspace': '/tmp/synthetic'})
        with self.assertRaises(RuntimeError):
            _ = desktop.context
        context = object()
        desktop._context = context
        self.assertIs(desktop.context, context)
        desktop._browser_closed.set()
        with self.assertRaises(RuntimeError):
            _ = desktop.context
        desktop._context = None

    async def test_cleanup_closes_context_before_driver_and_is_idempotent(self):
        desktop = ManagedDesktop({'state_dir': '/tmp/synthetic', 'workspace': '/tmp/synthetic'})
        closed = []

        async def close_context():
            closed.append('context')
            desktop._browser_closed.set()

        async def stop_driver():
            closed.append('driver')

        desktop._context = type('Context', (), {'close': staticmethod(AsyncMock(side_effect=close_context))})()
        desktop._playwright = type('Driver', (), {'stop': staticmethod(AsyncMock(side_effect=stop_driver))})()
        await desktop.close()
        self.assertEqual(closed, ['context', 'driver'])
        await desktop.close()
        self.assertEqual(closed, ['context', 'driver'])


@unittest.skipUnless(os.environ.get('OAK_MANAGED_BROWSER_TESTS') == '1',
                     'Set OAK_MANAGED_BROWSER_TESTS=1 for isolated headed Chromium checks.')
class ManagedBrowserRealTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_and_semantic_browser_share_page_upload_and_owned_profile(self):
        from aiohttp import web
        from oak.executor_worker import Worker

        if os.geteuid() == 0:
            self.fail('Run the managed browser acceptance check as a non-root user.')
        # Choose an unused X server; never connect to an existing owner display.
        display = next((f':{number}' for number in range(180, 240)
                        if not Path(f'/tmp/.X{number}-lock').exists()
                        and not Path(f'/tmp/.X11-unix/X{number}').exists()), None)
        self.assertIsNotNone(display)
        html = '''<!doctype html><title>Oak synthetic session</title>
<input id="note" autofocus oninput="document.querySelector('#echo').textContent=this.value">
<p id="echo"></p><input id="upload" type="file" hidden
onchange="document.querySelector('#filename').textContent=this.files[0].name">
<p id="filename"></p>'''
        app = web.Application()
        async def render(_request):
            return web.Response(text=html, content_type='text/html')
        app.router.add_get('/', render)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f'http://127.0.0.1:{port}/'
        try:
            with tempfile.TemporaryDirectory(prefix='oak-managed-browser-') as folder:
                profile = {'workspace': folder, 'runtime_home': folder, 'policy_digest': '0' * 64,
                           'display': display, 'desktop_state': folder}
                worker = Worker(profile)
                try:
                    opened = await worker.dispatch('browser.run', {'action': 'open', 'url': url})
                    self.assertEqual(opened['title'], 'Oak synthetic session')
                    context = worker.desktop.context
                    page = context.pages[-1]
                    await page.bring_to_front()
                    clicked = await worker.dispatch('browser.run', {'action': 'click', 'selector': '#note'})
                    self.assertTrue(clicked['ok'])
                    await page.wait_for_function("document.hasFocus() && document.activeElement.id === 'note'")
                    await worker.dispatch('computer.type', {'text': 'shared visible input'})
                    await page.wait_for_function("document.querySelector('#echo').textContent === 'shared visible input'")
                    observed = await worker.dispatch('browser.run', {'action': 'read'})
                    self.assertIn('shared visible input', observed['text'])
                    await worker.dispatch('browser.run', {'action': 'type', 'selector': '#note',
                                                         'text': 'semantic input'})
                    self.assertEqual(await page.locator('#note').input_value(), 'semantic input')
                    (Path(folder) / 'sample.txt').write_text('Synthetic upload bytes')
                    uploaded = await worker.dispatch('browser.run', {'action': 'upload',
                                                                   'selector': '#upload', 'files': ['sample.txt']})
                    self.assertIn('sample.txt', uploaded['text'])
                    await context.add_cookies([{'name': 'oak_synthetic_cookie', 'value': 'session-check',
                                               'url': url, 'expires': 4102444800}])
                    await worker.dispatch('browser.close', {})
                    self.assertIs(worker.desktop.context, context)
                    self.assertEqual(await page.title(), 'Oak synthetic session')
                    self.assertFalse((Path(folder) / 'browser-profile').exists())
                    self.assertTrue((Path(folder) / 'desktop/browser/Default').is_dir())
                    display_pids = [process.pid for _, process in worker.desktop.processes]
                    # A user closing the last window must not orphan the model's
                    # session or require a gateway restart. Manual control still
                    # wins, even when there is no browser window left to inspect.
                    await page.close()
                    await asyncio.wait_for(worker.desktop._browser_closed.wait(), 5)
                    worker.remote = {'id': 'synthetic-lease'}
                    try:
                        with self.assertRaisesRegex(ValueError, 'Remote owns desktop'):
                            await worker.dispatch('browser.run', {'action': 'read'})
                        self.assertTrue(worker.desktop._browser_closed.is_set())
                    finally:
                        worker.remote = None
                    recovered = await worker.dispatch('browser.run', {'action': 'read'})
                    self.assertTrue(recovered['ok'])
                    self.assertEqual(worker.desktop.context.pages[0].url, 'about:blank')
                    self.assertIsNot(worker.desktop.context, context)
                    self.assertEqual([process.pid for _, process in worker.desktop.processes], display_pids)
                    self.assertTrue(all(process.returncode is None for _, process in worker.desktop.processes))
                    cookies = await worker.desktop.context.cookies(url)
                    self.assertEqual(next(c['value'] for c in cookies if c['name'] == 'oak_synthetic_cookie'),
                                     'session-check')
                finally:
                    await worker.close()
                # Reopen only this test's own profile, never deployment state.
                reopened = Worker(profile)
                try:
                    await reopened.dispatch('browser.run', {'action': 'open', 'url': url})
                    cookies = await reopened.desktop.context.cookies(url)
                    self.assertEqual(next(c['value'] for c in cookies if c['name'] == 'oak_synthetic_cookie'),
                                     'session-check')
                finally:
                    await reopened.close()
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    unittest.main()
