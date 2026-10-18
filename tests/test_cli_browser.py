"""Opt-in real Chromium; synthetic codes and local HTTP only, no Google traffic."""

import asyncio
import json
import io
import logging
import os
import unittest
from urllib.parse import urlsplit

from aiohttp.test_utils import TestClient, TestServer

from tests.test_cli_login import CLIIntegrationFixture
from tests.test_cli_process import CANARY, CHALLENGE


@unittest.skipUnless(os.environ.get('OAK_TEST_CHROMIUM'), 'Set OAK_TEST_CHROMIUM for real local browser checks')
class CLIBrowserTests(CLIIntegrationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_canary_stays_outside_parent_model_db_logs_and_screenshot_pixels(self):
        from playwright.async_api import async_playwright
        from PIL import Image
        task, identifier = await self.begin()
        gateway = TestClient(TestServer(self.gateway.app))
        await gateway.start_server()
        self.addAsyncCleanup(gateway.close)
        console, logs, errors, submits = [], [], [], []
        class Capture(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())
        handler = Capture()
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        parent = '''<!doctype html><meta charset="utf-8"><style>
          body {margin:0;background:#131f18;color:white;font:16px system-ui} dialog{width:min(640px,90vw);max-height:95vh;overflow:auto}
          iframe{width:100%;border:0} [hidden]{display:none!important}
          </style><dialog id="confirmation"></dialog><dialog id="input-request">
          <h1 id="input-request-title"></h1><p id="input-request-description"></p><p id="input-request-destination"></p>
          <form id="input-request-form"><div id="input-request-fields"></div><p id="input-request-status"></p>
          <button id="input-request-submit">Надіслати</button><button type="button" id="input-request-cancel">Скасувати</button></form></dialog>
          <script type="module">
          import {createInputRequests} from '/requests.js';
          window.messages=[]; window.addEventListener('message',e=>messages.push(e.data));
          window.requests=createInputRequests({api:async(path,data)=>{
            const r=await fetch(path,{method:data?'POST':'GET',headers:{Authorization:'Bearer PANEL','Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
            if(!r.ok) throw new Error('Fixture request failed'); return r.json();
          },epoch:()=>1,scope:()=>'telegram',available:()=>true,changed:()=>{}});
          await requests.open('IDENTIFIER');
          </script>'''.replace('PANEL', self.panel).replace('IDENTIFIER', identifier)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(executable_path=os.environ['OAK_TEST_CHROMIUM'],
                headless=True, args=['--no-sandbox'])
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page(viewport={'width': 1100, 'height': 1100})
            page.on('console', lambda message: console.append(message.text))
            page.on('pageerror', lambda error: errors.append(str(error)))
            async def route(request_route):
                request = request_route.request
                parsed = urlsplit(request.url)
                if parsed.netloc == 'panel.example.test' and parsed.path == '/fixture':
                    await request_route.fulfill(status=200, content_type='text/html', body=parent)
                    return
                if parsed.netloc not in {'panel.example.test', 'private.example.test'}:
                    errors.append('Unexpected external request')
                    await request_route.abort()
                    return
                client = self.human if parsed.netloc == 'private.example.test' else gateway
                if parsed.path == '/cli/api/submit':
                    submits.append(1)  # Never record human request bodies.
                headers = {key: value for key, value in request.headers.items()
                           if key.lower() in {'authorization','content-type','origin'}}
                response = await client.request(request.method, parsed.path+('?' + parsed.query if parsed.query else ''),
                                                headers=headers, data=request.post_data_buffer)
                await request_route.fulfill(status=response.status, headers={key:value for key,value in response.headers.items()
                    if key.lower() not in {'content-length','transfer-encoding','content-encoding'}}, body=await response.read())
            await page.route('**/*', route)
            await page.goto('https://panel.example.test/fixture')
            await page.get_by_role('button', name='Відкрити приватний вхід у Colab').wait_for()
            self.assertFalse(self.broker.leases, 'Viewing the request must not start OAuth')
            await page.get_by_role('button', name='Відкрити приватний вхід у Colab').click()
            frame = page.frame_locator('iframe')
            code = frame.locator('#code')
            await code.wait_for(state='visible')
            self.assertEqual(await code.get_attribute('type'), 'password')
            self.assertEqual(await frame.locator('#authorize').get_attribute('href'), CHALLENGE)
            for width, height in ((1100,1100),(390,844)):
                await page.set_viewport_size({'width':width,'height':height})
                await asyncio.sleep(.2)
                self.assertTrue(await frame.locator('body').evaluate(
                    '()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth'))
            if os.environ.get('OAK_TEST_PRIVATE_SCREENSHOT'):
                # Before any input; the URL is a link target, never rendered text.
                await frame.locator('body').screenshot(path=os.environ['OAK_TEST_PRIVATE_SCREENSHOT'])
            await code.fill('0'*len(CANARY))
            await frame.locator('#title').click()
            await code.evaluate('(input)=>input.blur()')
            await asyncio.sleep(.2)
            baseline = await code.screenshot(animations='disabled')
            await code.fill(CANARY)
            await frame.locator('#title').click()
            await code.evaluate('(input)=>input.blur()')
            await asyncio.sleep(.2)
            masked = await code.screenshot(animations='disabled')
            first, second = Image.open(io.BytesIO(baseline)), Image.open(io.BytesIO(masked))
            self.assertEqual(first.size, second.size)
            # Exclude the rounded border's fractional-pixel clipping, not the text.
            region = (8, 4, first.width-8, first.height-4)
            self.assertEqual(first.crop(region).tobytes(), second.crop(region).tobytes(),
                             'Password text pixels must be identical for equal-length synthetic values')
            self.assertNotIn(CANARY, await page.locator('body').inner_text())
            self.assertEqual(await page.evaluate('Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
            self.assertEqual(await frame.locator('body').evaluate('()=>Object.keys(localStorage).length+Object.keys(sessionStorage).length'), 0)
            await frame.get_by_role('button', name='Передати код приватному процесу').click()
            try:
                await asyncio.wait_for(asyncio.shield(task), 5)
            except TimeoutError:
                lease = self.broker.leases.get(identifier)
                self.fail(str({'submits': len(submits), 'browser_errors': errors,
                    'broker_state': lease.state if lease else None,
                    'process_state': lease.process.state if lease and lease.process else None,
                    'bridge_phase': self.bridge.attempt(identifier)['phase']}))
            await page.wait_for_function("document.querySelector('#input-request-status').textContent.includes('Вхід підтверджено')")
            self.assertEqual(submits, [1])
            messages = await page.evaluate('messages')
            self.assertTrue(messages)
            for value in messages:
                self.assertIn(value.get('type'), {'oak-cli-ready','oak-cli-layout'})
                self.assertEqual(set(value), {'type'} if value['type'].endswith('ready') else {'type','height'})
            records = self.public_records()+json.dumps(messages)+'\n'.join(console+logs+errors)
            for forbidden in (CANARY, CHALLENGE):
                self.assertNotIn(forbidden, records)
            self.assertEqual(errors, [])
            if os.environ.get('OAK_TEST_SCREENSHOT'):
                # Captured only after the private form is removed, never while a code is present.
                await page.screenshot(path=os.environ['OAK_TEST_SCREENSHOT'], full_page=True)
            await page.close()
