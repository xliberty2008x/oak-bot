"""Workspace boundaries and opt-in real Chromium browser capability checks."""

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.browser import BrowserTools, _safe_url


class BrowserFileBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.browser = BrowserTools(self.workspace)
        (self.workspace / 'sample.txt').write_text('original content')

    def test_upload_takes_immutable_payload_and_never_browser_path(self):
        payload = self.browser._upload_payloads(['sample.txt'])[0]
        (self.workspace / 'sample.txt').write_text('changed after snapshot')
        self.assertEqual(payload, {'name': 'sample.txt', 'mimeType': 'text/plain', 'buffer': b'original content'})

    def test_upload_rejects_private_outside_and_symlink_sources(self):
        (self.workspace / 'secrets').mkdir()
        (self.workspace / 'secrets' / 'token.txt').write_text('private')
        (self.workspace / 'alias.txt').symlink_to(self.workspace / 'sample.txt')
        (self.workspace / 'directory-alias').symlink_to(self.workspace, target_is_directory=True)
        (self.workspace / '.hidden').write_text('private')
        for name in ['../sample.txt', '/etc/passwd', 'secrets/token.txt', '.hidden', 'alias.txt',
                     'directory-alias/sample.txt', 'secrets']:
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.browser._upload_payloads([name])

    def test_upload_rejects_hardlink_and_aggregate_limit(self):
        os.link(self.workspace / 'sample.txt', self.workspace / 'hardlink.txt')
        with self.assertRaises(ValueError):
            self.browser._upload_payloads(['hardlink.txt'])
        (self.workspace / 'hardlink.txt').unlink()
        with patch('oak.browser.MAX_UPLOAD', 20), self.assertRaises(ValueError):
            self.browser._upload_payloads(['sample.txt', 'sample.txt'])

    def test_upload_detects_file_changed_while_reading(self):
        original_read = os.read
        changed = False
        def read_and_mutate(fd, amount):
            nonlocal changed
            data = original_read(fd, amount)
            if not changed:
                changed = True
                (self.workspace / 'sample.txt').write_text('changed bytes')
            return data
        with patch('oak.browser.os.read', side_effect=read_and_mutate), self.assertRaises(ValueError):
            self.browser._upload_payloads(['sample.txt'])

    def test_artifact_receipt_has_exact_bytes_and_private_permissions(self):
        receipt = self.browser._save_artifact(io.BytesIO(b'hello'), '.bin')
        self.assertEqual(Path(receipt['path']).read_bytes(), b'hello')
        self.assertEqual(receipt['sha256'], hashlib.sha256(b'hello').hexdigest())
        self.assertEqual(receipt['size'], 5)
        self.assertEqual(Path(receipt['path']).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.browser._save_artifact(io.BytesIO(b'too large'), '.bin', maximum=3)
        self.assertEqual(len(list(self.browser.artifacts.iterdir())), 1)

    def test_artifact_symlink_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            self.browser.artifacts.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                self.browser._save_artifact(io.BytesIO(b'hello'), '.bin')
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_observed_urls_omit_authorization_material(self):
        self.assertEqual(_safe_url('https://user:pass@example.org/page?token=private#code'), 'https://example.org/page')
        self.assertEqual(_safe_url('https://[::1]:443/page?token=x'), 'https://[::1]:443/page')
        self.assertEqual(_safe_url('data:text/plain,secret'), 'data:')


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in actual Chromium fixtures')
class BrowserCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from playwright.async_api import async_playwright
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        (self.workspace / 'sample.txt').write_text('actual uploaded content')
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.context = await self.chromium.new_context(accept_downloads=True)
        self.addAsyncCleanup(self.context.close)
        self.browser = BrowserTools(self.workspace, context_provider=self.get_context)
        self.addAsyncCleanup(self.browser.close)
        self.download_data = b'Oak verified artifact\x00\xff' * 4096
        async def route(request):
            if request.path == '/download':
                return web.Response(body=self.download_data, content_type='application/octet-stream',
                                    headers={'Content-Disposition': 'attachment; filename="report.bin"'})
            if request.path == '/frame':
                return web.Response(text='<h2>Embedded upload</h2><input type="file" id="inner" hidden '
                    'onchange="document.querySelector(\'p\').textContent=\'Frame selected \' + this.files.length">'
                    '<p>Frame waiting</p><button onclick="this.textContent=\'Frame clicked\'">Frame button</button>',
                    content_type='text/html')
            if request.path == '/popup':
                return web.Response(text='<h1>Popup page</h1><button>Popup action</button>', content_type='text/html')
            if request.path == '/missing-browser':
                return web.Response(status=500)
            html = '''<!doctype html><title>Browser fixture</title><h1>Browser capabilities</h1>
              <input type="hidden" value="SHOULD_NOT_APPEAR"><button style="display:none">Hidden button</button>
              <input type="file" id="file" hidden onchange="document.querySelector('#status').textContent='Selected ' + this.files.length">
              <p id="status">Waiting for file</p>
              <button id="chooser" onclick="const i=document.createElement('input'); i.type='file'; i.hidden=true; i.onchange=()=>document.querySelector('#status').textContent='Chooser selected ' + i.files.length; document.body.append(i); i.click()">Custom upload</button>
              <button id="delay" onclick="setTimeout(()=>{const b=document.createElement('button'); b.id='delayed'; b.textContent='Ready later'; document.body.append(b)}, 100)">Prepare action</button>
              <button id="nochooser" onclick="window.effects=(window.effects||0)+1;document.querySelector('#status').textContent='Action reached site'">Not an upload</button>
              <button id="disabled" disabled>Disabled control</button>
              <a id="download" href="/download">Download artifact</a>
              <a id="popup" href="/popup" target="_blank">Open popup</a>
              <div contenteditable="true" id="editor"></div>
              <input type="password" id="password" oninput="document.querySelector('#echo').textContent=this.value"><p id="echo"></p>
              <iframe src="http://localhost:PORT/frame" title="Independent frame"></iframe>'''
            return web.Response(text=html.replace('PORT', str(self.port)), content_type='text/html')
        app = web.Application()
        app.router.add_route('*', '/{tail:.*}', route)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.origin = 'http://127.0.0.1:' + str(self.port)
        self.page = await self.context.new_page()
        result = await self.browser.run('open', url=self.origin + '/?token=URL_CANARY#secret')
        self.assertTrue(result['ok'], result)
        await self.page.locator('iframe').wait_for()
        await self.page.frame_locator('iframe').locator('#inner').wait_for(state='attached')

    async def get_context(self):
        return self.context

    async def test_hidden_input_and_cross_origin_iframe_are_actionable(self):
        observed = await self.browser.run('read')
        self.assertNotIn('SHOULD_NOT_APPEAR', json.dumps(observed))
        self.assertNotIn('URL_CANARY', json.dumps(observed))
        self.assertNotIn('Hidden button', [item['text'] for item in observed['controls']])
        uploads = [item for item in observed['controls'] if item['type'] == 'file']
        self.assertEqual(len(uploads), 2)
        inner = next(item for item in uploads if any(frame['id'] == item['frame'] and not frame['main'] for frame in observed['frames']))
        self.assertFalse(inner['visible'])
        result = await self.browser.run('upload', selector=inner['selector'], frame=inner['frame'], files=['sample.txt'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['receipt']['verified'])
        self.assertIn('Frame selected 1', result['text'])
        frame = next(item for item in self.page.frames if item is not self.page.main_frame)
        self.assertEqual(await frame.locator('#inner').evaluate('async e => await e.files[0].text()'), 'actual uploaded content')
        button = next(item for item in result['controls'] if item['text'] == 'Frame button')
        result = await self.browser.run('click', selector=button['selector'], frame=button['frame'])
        self.assertIn('Frame clicked', result['text'])

    async def test_dynamic_file_chooser_uses_same_context_and_exact_file_bytes(self):
        result = await self.browser.run('upload', selector='#chooser', files=[str(self.workspace / 'sample.txt')])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['receipt']['selected_files'], 1)
        self.assertIn('Chooser selected 1', result['text'])
        self.assertEqual(await self.page.locator('input[type=file]').last.evaluate('async e => await e.files[0].text()'), 'actual uploaded content')

    async def test_reactive_upload_input_replacement_is_not_a_false_failure(self):
        for mode in ('remove', 'replace', 'reset'):
            with self.subTest(mode=mode):
                await self.page.set_content('<input id="reactive" type="file" hidden><p id="state"></p>')
                await self.page.evaluate("""mode => {
                    document.querySelector('#reactive').onchange = function() {
                        window.fileSelections = (window.fileSelections || 0) + 1;
                        document.querySelector('#state').textContent = 'Received ' + this.files[0].name;
                        if (mode === 'remove') this.remove();
                        if (mode === 'replace') this.outerHTML = '<button id="reactive">Uploaded preview</button>';
                        if (mode === 'reset') this.value = '';
                    };
                }""", mode)
                before = await self.page.evaluate('window.fileSelections || 0')
                result = await self.browser.run('upload', selector='#reactive', files=['sample.txt'])
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['receipt']['verification'], 'browser_file_selection')
                self.assertFalse(result['receipt']['server_upload_verified'])
                self.assertEqual(result['receipt']['selected_files'], 1)
                self.assertIn('Received sample.txt', result['text'])
                self.assertEqual(await self.page.evaluate('window.fileSelections'), before + 1)

    async def test_dynamic_chooser_reset_is_not_claimed_as_verified_server_upload(self):
        await self.page.set_content('<button id="chooser">Upload</button><p id="state"></p>')
        await self.page.evaluate("""() => {
            document.querySelector('#chooser').onclick = () => {
                const input = document.createElement('input'); input.type = 'file'; input.hidden = true;
                input.onchange = () => {document.querySelector('#state').textContent = 'Selection processed'; input.value = ''; input.remove()};
                document.body.append(input); input.click();
            };
        }""")
        result = await self.browser.run('upload', selector='#chooser', files=['sample.txt'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['receipt']['verification'], 'browser_file_selection')
        self.assertFalse(result['receipt']['server_upload_verified'])
        self.assertIn('Selection processed', result['text'])

    async def test_download_receipt_matches_actual_saved_bytes(self):
        result = await self.browser.run('download', selector='#download')
        self.assertTrue(result['ok'], result)
        self.assertEqual(Path(result['path']).read_bytes(), self.download_data)
        self.assertEqual(result['receipt']['sha256'], hashlib.sha256(self.download_data).hexdigest())
        self.assertEqual(result['receipt']['size'], len(self.download_data))
        self.assertTrue(Path(result['path']).is_relative_to(self.workspace / 'artifacts'))
        self.assertNotIn('queued_for_delivery', result)

    async def test_delayed_controls_can_be_waited_and_observed(self):
        await self.browser.run('click', selector='#delay')
        result = await self.browser.run('wait', selector='#delayed', timeout_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertIn('Ready later', result['text'])

    async def test_missing_disabled_and_ambiguous_controls_give_fresh_errors(self):
        result = await self.browser.run('click', selector='#not-there', timeout_ms=150)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'selector_not_found')
        self.assertEqual(result['outcome'], 'not_attempted')
        self.assertIn('Browser capabilities', result['text'])
        result = await self.browser.run('click', selector='button', timeout_ms=150)
        self.assertEqual(result['error']['code'], 'ambiguous_selector')
        result = await self.browser.run('click', selector='#disabled', timeout_ms=150)
        self.assertEqual(result['error']['code'], 'control_disabled')

    async def test_unknown_external_action_is_never_replayed(self):
        result = await self.browser.run('upload', selector='#nochooser', files=['sample.txt'], timeout_ms=200)
        self.assertFalse(result['ok'])
        self.assertEqual(result['outcome'], 'uncertain')
        self.assertIn('verify before repeating', result['error']['recovery'])
        self.assertIn('Action reached site', result['text'])
        self.assertEqual(await self.page.evaluate('window.effects'), 1)

    async def test_typed_values_and_exception_context_are_redacted(self):
        secret = 'PASSWORD_CANARY_827'
        result = await self.browser.run('type', selector='#password', text=secret)
        self.assertTrue(result['receipt']['filled'])
        self.assertNotIn(secret, json.dumps(result))
        result = await self.browser.run('click', selector='#' + secret, timeout_ms=100)
        self.assertNotIn(secret, json.dumps(result))
        result = await self.browser.run('read')
        self.assertNotIn(secret, json.dumps(result))

    async def test_contenteditable_and_short_values_preserve_actionable_references(self):
        result = await self.browser.run('type', selector='#editor', text='a')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['receipt']['filled'])
        for control in result['controls']:
            self.assertTrue(control['frame'].startswith('frame-'))
            self.assertTrue(control['selector'].startswith(':is(a,button'))
        result = await self.browser.run('click', selector='#delay')
        self.assertTrue(result['ok'], result)

    async def test_reactive_typing_reports_dispatch_without_reading_replacement_field(self):
        for mode in ('advance', 'replace', 'reset'):
            with self.subTest(mode=mode):
                await self.page.set_content('<input id="reactive"><p id="state"></p>')
                await self.page.evaluate("""mode => {
                    document.querySelector('#reactive').oninput = function() {
                        window.inputEvents = (window.inputEvents || 0) + 1;
                        if (mode === 'advance') document.body.innerHTML = '<h1>Next step reached</h1>';
                        if (mode === 'replace') this.outerHTML = '<input id="reactive"><p>Replacement field ready</p>';
                        if (mode === 'reset') {this.value = ''; document.querySelector('#state').textContent = 'Input consumed'};
                    };
                }""", mode)
                before = await self.page.evaluate('window.inputEvents || 0')
                result = await self.browser.run('type', selector='#reactive', text='synthetic value', timeout_ms=250)
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['receipt']['dispatched'])
                self.assertEqual(result['receipt']['verification'], 'observed_original_control')
                self.assertEqual(result['receipt']['filled'], mode != 'reset')
                self.assertEqual(await self.page.evaluate('window.inputEvents'), before + 1)
                if mode == 'advance':
                    self.assertIn('Next step reached', result['text'])
                if mode == 'replace':
                    self.assertEqual(await self.page.locator('#reactive').input_value(), '')
                    self.assertIn('Replacement field ready', result['text'])
                if mode == 'reset':
                    self.assertIn('Input consumed', result['text'])

    async def test_popup_switch_and_borrowed_context_lifecycle(self):
        first = (await self.browser.run('tabs'))['tab']
        result = await self.browser.run('click', selector='#popup')
        self.assertTrue(result['ok'], result)
        self.assertIn('Popup page', result['text'])
        self.assertEqual(len(result['tabs']), 2)
        self.assertNotEqual(result['tab'], first)
        result = await self.browser.run('switch', tab=first)
        self.assertIn('Browser capabilities', result['text'])
        await self.browser.close()
        self.assertFalse(self.page.is_closed())
        self.assertEqual(len(self.context.pages), 2)

    async def test_screenshot_is_a_verified_local_artifact(self):
        result = await self.browser.run('screenshot')
        self.assertTrue(result['ok'], result)
        self.assertGreater(result['receipt']['size'], 100)
        self.assertEqual(Path(result['path']).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')

    async def test_provider_unavailable_does_not_launch_another_account_session(self):
        async def unavailable():
            return None
        self.browser._context_provider = unavailable
        result = await self.browser.run('read')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'browser_unavailable')
        self.assertIsNone(self.browser._playwright)

    async def test_stale_frame_id_fails_without_clicking_main_document(self):
        result = await self.browser.run('click', selector='#delay', frame='frame-missing', timeout_ms=100)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'frame_not_found')
        self.assertEqual(await self.page.locator('#delayed').count(), 0)
