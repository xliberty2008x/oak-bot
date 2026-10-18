"""Safe message assembly plus real local WebGateway/Chromium fixtures."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from aiohttp import web
from aiohttp.test_utils import TestServer

from oak.bus import EventBus
from oak.controller import Controller
from oak.tools import Tools
from oak.web import WebGateway

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('OAK_TEST_NODE') or shutil.which('node')


@unittest.skipUnless(NODE, 'Node is required for message assembly checks')
class MessageAssemblyTests(unittest.TestCase):
    def test_interleaved_runs_cursor_duplicates_final_replacement_and_bounds(self):
        script = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({TextEncoder,URL});
const dependency=new vm.SourceTextModule('export function createPanelPoll(){}',{context});
await dependency.link(()=>{});await dependency.evaluate();
const mod=new vm.SourceTextModule(SOURCE,{context});await mod.link(()=>dependency);await mod.evaluate();
const base={threadId:'thread',runId:'first',messageId:'same'};
const events=[{...base,sequence:1,type:'start'}, {...base,sequence:2,type:'delta',delta:'**unfinished'},
 {...base,runId:'second',sequence:3,type:'start'}, {...base,runId:'second',sequence:4,type:'delta',delta:'other'},
 {...base,sequence:5,type:'end',text:'authoritative replacement'}, {...base,sequence:6,type:'run_end',status:'completed'}];
const result=mod.namespace.assembleMessages([...events,events[1],events[4]]);
assert.equal(result.length,2);assert.equal(result[0].text,'authoritative replacement');
assert.equal(result[0].complete,true);assert.equal(result[0].partial,false);assert.equal(result[1].text,'other');
assert.equal(result[1].complete,false);assert.equal(result[1].status,'running');
const partial=mod.namespace.assembleMessages([{...base,sequence:3,type:'delta',delta:'tail'}]);assert.equal(partial[0].partial,true);
const large=mod.namespace.assembleMessages([{...base,sequence:1,type:'delta',delta:'🙂'.repeat(200001)}]);
assert.equal(large[0].truncated,true);assert.equal(large[0].text.length,200000);
const late=mod.namespace.assembleMessages([...events,{...base,sequence:7,type:'delta',delta:'late duplicate'}]);
assert.equal(late[0].text,'authoritative replacement');
'''
        source = (ROOT / 'oak/web/messages.js').read_text()
        result = subprocess.run([NODE, '--experimental-vm-modules', '-e', 'const SOURCE=' + json.dumps(source) + ';(async()=>{' + script + '})().catch(e=>{console.error(e);process.exit(1)});'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class FixtureRuntime:
    runtime_epoch = 'synthetic-runtime'
    _authenticated = True

    async def request(self, method, params):
        if method == 'model/list':
            return {'data': [{'model': 'gpt-6.1-sol', 'displayName': 'Fixture model', 'hidden': False,
                             'supportedReasoningEfforts': [], 'defaultReasoningEffort': 'low'}], 'nextCursor': None}
        if method in {'experimentalFeature/list', 'mcpServerStatus/list'}:
            return {'data': [], 'nextCursor': None}
        if method == 'app/installed':
            return {'apps': []}
        if method == 'plugin/installed':
            return {'marketplaces': []}
        raise AssertionError('Unexpected runtime operation: ' + method)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in local Chromium fixtures')
class MessageBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        (root / 'telegram-web-app.js').write_text("window.Telegram={WebApp:{colorScheme:'dark',ready(){},expand(){},onEvent(){},offEvent(){},BackButton:{show(){},hide(){},onClick(){},offClick(){}}}};")
        self.controller = Controller(FixtureRuntime(), ':memory:', root, AsyncMock(), config={'state_dir': str(root)})
        self.addCleanup(self.controller.close)
        self.controller.tools = Tools(self.controller, {})
        self.bus = EventBus(self.controller)
        self.scope = self.controller.sessions.resolve(7, 42, name='Other synthetic topic')
        self.gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [7], {})
        self.delay = None
        @web.middleware
        async def hold(request, handler):
            result = await handler(request)
            if request.path == '/api/render' and self.delay:
                entered, release = self.delay
                if getattr(self, 'stale_ast', False):
                    result = web.json_response({'type': 'document', 'children': [{'type': 'text', 'value': 'STALE_RESPONSE_AST'}]})
                entered.set()
                await release.wait()
            return result
        self.gateway.app.middlewares.insert(0, hold)
        self.server = TestServer(self.gateway.app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(viewport={'width': 390, 'height': 844}, service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.errors, self.external = [], []
        async def local_only(route):
            if urlsplit(route.request.url).hostname not in {'127.0.0.1', 'localhost'}:
                self.external.append(route.request.url)
                await route.abort()
            else:
                await route.continue_()
        await self.context.route('**/*', local_only)
        self.page = await self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        self.addCleanup(lambda: self.assertEqual(self.external, []))

    async def emit(self, kind, *, chat=7, run='r', **fields):
        await self.bus.emit(chat, {'type': kind, 'threadId': 'topic-' + str(chat), 'runId': run,
                           **({'messageId': 'same'} if kind.startswith('TEXT_MESSAGE') else {}), **fields})

    async def open(self):
        await self.page.goto(str(self.server.make_url('/')))
        await self.page.locator('#key').fill(self.gateway.keys['7'])
        await self.page.locator('#sign-in').click()
        await self.page.locator('#workspace').wait_for(state='visible')
        await self.page.locator('[data-view="messages"]').click()

    async def test_real_partial_delta_final_ast_tables_and_no_remote_content(self):
        from playwright.async_api import expect
        await self.emit('TEXT_MESSAGE_START', role='assistant')
        await self.emit('TEXT_MESSAGE_CONTENT', delta='**unfinished <img src=https://external.invalid/p onerror=alert(1)>')
        await self.open()
        await expect(self.page.locator('#messages-list')).to_contain_text('**unfinished')
        self.assertEqual(await self.page.locator('#messages-list strong, #messages-list img').count(), 0)
        final = '# Results\n\n<img src=x onerror="window.xss=1">\n\n[Safe](https://example.com) [Private](https://u:p@example.com) ![image alt](https://external.invalid/p)\n\n'
        final += '| ' + ' | '.join('Column ' + str(i) for i in range(8)) + ' |\n|' + '|'.join('---' for _ in range(8)) + '|\n|' + '|'.join('Value ' + str(i) for i in range(8)) + '|\n\n```html\n<script>window.xss=2</script>\n```'
        await self.emit('TEXT_MESSAGE_END', text=final)
        await self.emit('RUN_FINISHED')
        await self.page.locator('#refresh').click()
        await self.page.locator('.message-table table').wait_for()
        self.assertNotIn('unfinished', await self.page.locator('#messages-list').inner_text())
        self.assertEqual(await self.page.locator('#messages-list img, #messages-list script').count(), 0)
        self.assertEqual(await self.page.locator('#messages-list a[href]').count(), 1)
        self.assertIsNone(await self.page.evaluate('window.xss'))
        table = self.page.locator('.message-table')
        await table.focus()
        await self.page.keyboard.press('ArrowRight')
        for _ in range(20):
            if await table.evaluate('(element) => element.scrollLeft > 0'):
                break
            await self.page.wait_for_timeout(50)
        self.assertGreater(await table.evaluate('(element) => element.scrollLeft'), 0)
        self.assertEqual(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)
        artifacts = os.environ.get('OAK_QA_ARTIFACT_DIR')
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(Path(artifacts) / 'messages-mobile-dark.png'), full_page=True)
        await self.page.set_viewport_size({'width': 1440, 'height': 1000})
        await self.page.evaluate('window.Telegram.WebApp.colorScheme="light"; window.dispatchEvent(new PageTransitionEvent("pageshow"))')
        self.assertEqual(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), True)
        if artifacts:
            await self.page.screenshot(path=str(Path(artifacts) / 'messages-desktop-light.png'), full_page=True)
        defensive = await self.page.evaluate('''async () => {
            const {renderDocument} = await import('/messages.js');
            const unknown = renderDocument({type:'document',children:[{type:'img',value:'unsafe'}]}, '<literal>');
            const large = renderDocument({type:'document',children:Array.from({length:4001},()=>({type:'text',value:'x'}))}, 'whole literal source');
            const link = renderDocument({type:'document',children:[{type:'link',url:'javascript:alert(1)',children:[{type:'text',value:'safe label'}]}]}, 'source');
            return {unknown:unknown.textContent,images:unknown.querySelectorAll('img').length,
                large:large.textContent,links:link.querySelectorAll('a[href]').length};
        }''')
        self.assertEqual(defensive, {'unknown': '<literal>', 'images': 0, 'large': 'whole literal source', 'links': 0})

    async def test_late_ast_cannot_cross_session_reset_or_back_navigation(self):
        from playwright.async_api import expect
        await self.emit('TEXT_MESSAGE_START', role='assistant')
        await self.emit('TEXT_MESSAGE_END', text='**OLD_SCOPE_FINAL**')
        await self.emit('TEXT_MESSAGE_START', chat=self.scope, role='assistant')
        await self.emit('TEXT_MESSAGE_END', chat=self.scope, text='**NEW_SCOPE_FINAL**')
        entered, release = asyncio.Event(), asyncio.Event()
        self.delay = (entered, release)
        self.addAsyncCleanup(self.release, release)
        await self.open()
        await asyncio.wait_for(entered.wait(), 3)
        await self.page.locator('[data-view="sessions"]').click()
        await self.page.locator('[data-session="topic:42"]').click()
        await self.page.locator('[data-view="status"]').click()
        self.delay = None
        await self.page.locator('[data-view="messages"]').click()
        await expect(self.page.locator('#messages-list')).to_contain_text('NEW_SCOPE_FINAL')
        release.set()
        await self.page.wait_for_timeout(100)
        self.assertNotIn('OLD_SCOPE_FINAL', await self.page.locator('#messages-list').inner_text())
        await self.page.locator('[data-view="status"]').click()
        self.assertTrue(await self.page.locator('#view-messages').is_hidden())

    async def release(self, event):
        event.set()

    async def test_runtime_epoch_change_aborts_old_ast_and_refetches_history(self):
        from playwright.async_api import expect
        await self.emit('TEXT_MESSAGE_START', role='assistant')
        await self.emit('TEXT_MESSAGE_END', text='**DURABLE_FINAL**')
        entered, release = asyncio.Event(), asyncio.Event()
        self.delay, self.stale_ast = (entered, release), True
        self.addAsyncCleanup(self.release, release)
        await self.open()
        await asyncio.wait_for(entered.wait(), 3)
        self.controller.requests.epoch = 'fresh-runtime'
        self.delay = None
        await self.page.locator('#refresh').click()
        await expect(self.page.locator('#messages-list strong')).to_have_text('DURABLE_FINAL')
        release.set()
        await self.page.wait_for_timeout(100)
        self.assertNotIn('STALE_RESPONSE_AST', await self.page.locator('#messages-list').inner_text())


if __name__ == '__main__':
    unittest.main()
