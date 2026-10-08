import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from oak.bus import EventBus
from oak.controller import Controller
from oak.runtime import RuntimeClient
from oak.tools import Tools
from oak.signin_broker import Binding, Broker, origin
from oak.web import WebGateway


class FakeBrowser:
    instances = []
    failure = False

    def __init__(self, destination):
        self.authenticated = False
        self.closed = 0
        self.epoch = 1
        self.inputs = []
        self.instances.append(self)

    async def start(self):
        if self.failure:
            raise RuntimeError('synthetic-error-canary-password')

    async def event(self, value):
        if value['epoch'] != self.epoch:
            raise ValueError('synthetic-navigation-canary')
        self.inputs.append(value)

    async def frame(self):
        return self.epoch, b'synthetic-pixels'

    async def verified(self):
        return self.authenticated

    async def close(self):
        self.closed += 1
        self.inputs.clear()


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeBrowser.instances.clear()
        FakeBrowser.failure = False
        self.broker = Broker('http://127.0.0.1:18780', 'http://127.0.0.1:18779',
                             'http://127.0.0.1:18781', browser_factory=FakeBrowser)
        self.human = TestClient(TestServer(self.broker.human))
        self.control = TestClient(TestServer(self.broker.control))  # unit transport; actual demo uses UnixSite
        await self.human.start_server()
        await self.control.start_server()
        self.broker.human_origin = str(self.human.make_url('')).rstrip('/')
        self.addAsyncCleanup(self.human.close)
        self.addAsyncCleanup(self.control.close)
        self.addAsyncCleanup(self.broker.close)
        self.binding = {'request_id': 'a' * 32, 'owner': 11, 'chat_id': 11, 'thread_id': 'thread',
                        'turn_id': 'turn', 'runtime_epoch': 'epoch', 'panel_hash': 'b' * 64,
                        'expires': time.time() + 299, 'provider': 'oak_synthetic'}

    async def launch(self):
        response = await self.control.post('/create', json=self.binding)
        self.assertEqual(response.status, 200)
        return await response.json()

    async def attach(self, launch, **headers):
        return await self.human.post('/api/attach', json={'request_id': launch['request_id'], 'ticket': launch['ticket']},
                                     headers={'Origin': self.broker.human_origin, **headers})

    async def active(self):
        launch = await self.launch()
        response = await self.attach(launch)
        self.assertEqual(response.status, 200)
        human = await response.json()
        headers = {'Origin': self.broker.human_origin, 'Authorization': 'Bearer ' + human['lease']}
        return launch, headers

    async def test_exact_origin_one_use_ticket_and_separate_control_plane(self):
        launch = await self.launch()
        for headers in ({}, {'Origin': 'https://attacker.test'}):
            response = await self.human.post('/api/attach', json={'request_id': launch['request_id'], 'ticket': launch['ticket']}, headers=headers)
            self.assertEqual(response.status, 403)
        self.assertEqual((await self.attach(launch)).status, 200)
        self.assertEqual((await self.attach(launch)).status, 409)
        self.assertEqual((await self.human.post('/create', json=self.binding, headers={'Origin': self.broker.human_origin})).status, 404)
        self.assertEqual((await self.control.post('/create', json=self.binding)).status, 409)
        self.assertEqual(len(FakeBrowser.instances), 1)

    async def test_production_provider_and_arbitrary_destinations_fail_before_browser(self):
        for change in ({'provider':'instagram'}, {'destination':'https://www.instagram.com'}, {'owner':'11'}):
            self.assertEqual((await self.control.post('/create', json={**self.binding, **change})).status, 409)
        self.assertFalse(FakeBrowser.instances)
        for value in ('http://example.com', 'https://host.test/evil', 'https://x\";evil.test'):
            with self.assertRaises(ValueError):
                origin(value, loopback=True)

    async def test_ticket_scope_substitution_expiry_and_replay(self):
        launch = await self.launch()
        wrong = {**launch, 'request_id': 'c' * 32}
        self.assertEqual((await self.attach(wrong)).status, 409)
        self.broker.leases[launch['request_id']].ticket_deadline = time.time() - 1
        self.assertEqual((await self.attach(launch)).status, 409)
        self.assertFalse(FakeBrowser.instances)

    async def test_live_pixels_never_grant_model_control_or_accept_forged_success(self):
        launch, headers = await self.active()
        value = {'request_id': launch['request_id']}
        response = await self.human.post('/api/frame', json=value, headers=headers)
        self.assertEqual(await response.read(), b'synthetic-pixels')
        self.assertEqual(response.headers['X-Oak-Verified-Origin'], 'http://127.0.0.1:18781')
        response = await self.human.post('/api/frame', json={**value, 'authenticated':True}, headers=headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.broker.leases[launch['request_id']].state, 'active')
        self.assertEqual((await self.human.post('/api/event', json={**value, 'event':{'kind':'text','epoch':1,'text':'canary'}}, headers={'Origin':self.broker.human_origin})).status, 409)

    async def test_navigation_race_revokes_lease_and_redacts_failure(self):
        launch, headers = await self.active()
        response = await self.human.post('/api/event', headers=headers, json={'request_id':launch['request_id'],
            'event':{'kind':'text','epoch':0,'text':'synthetic-credential-canary'}})
        self.assertEqual(response.status, 409)
        self.assertNotIn('canary', await response.text())
        self.assertEqual(self.broker.leases[launch['request_id']].state, 'failed')
        self.assertEqual(FakeBrowser.instances[-1].closed, 1)

    async def test_idle_hard_expiry_and_cancel_destroy_browser_once(self):
        launch, headers = await self.active()
        lease = self.broker.leases[launch['request_id']]
        lease.idle_deadline = time.time() - 1
        response = await self.human.post('/api/frame', json={'request_id':launch['request_id']}, headers=headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(lease.state, 'expired')
        self.assertEqual(FakeBrowser.instances[-1].closed, 1)
        scope = {'request_id':launch['request_id'], 'session_id':launch['session_id'], 'runtime_epoch':'epoch'}
        await self.control.post('/cancel', json=scope)
        self.assertEqual(FakeBrowser.instances[-1].closed, 1)

    async def test_verified_adapter_result_clears_human_secrets(self):
        launch, headers = await self.active()
        FakeBrowser.instances[-1].authenticated = True
        response = await self.human.post('/api/frame', json={'request_id':launch['request_id']}, headers=headers)
        self.assertEqual(await response.json(), {'state':'authenticated','simulated':True})
        lease = self.broker.leases[launch['request_id']]
        self.assertEqual(lease.ticket_hash, '')
        self.assertEqual(lease.human_hash, '')
        self.assertIsNone(lease.browser)

    async def test_start_failure_is_constant_and_ticket_cannot_be_retried(self):
        FakeBrowser.failure = True
        launch = await self.launch()
        response = await self.attach(launch)
        self.assertEqual(response.status, 409)
        self.assertNotIn('canary', await response.text())
        self.assertEqual((await self.attach(launch)).status, 409)

    async def test_slow_start_and_frame_cannot_cross_hard_deadline(self):
        class SlowBrowser(FakeBrowser):
            async def start(self):
                await asyncio.sleep(0.05)
        self.broker.browser_factory = SlowBrowser
        launch = await self.launch()
        lease = self.broker.leases[launch['request_id']]
        lease.binding = replace(lease.binding,expires=time.time()+0.01)
        response = await self.attach(launch)
        self.assertEqual(response.status,409)
        self.assertEqual(lease.state,'expired')
        self.assertEqual(lease.cleanup,'confirmed')
        self.assertEqual(lease.human_hash,'')

    async def test_slow_frame_never_emits_pixels_after_expiry(self):
        launch,headers = await self.active()
        lease = self.broker.leases[launch['request_id']]
        async def slow():
            await asyncio.sleep(0.05)
            return 1,b'late-canary-pixels'
        lease.browser.frame = slow
        lease.binding = replace(lease.binding,expires=time.time()+0.01)
        response = await self.human.post('/api/frame',json={'request_id':launch['request_id']},headers=headers)
        self.assertEqual(response.status,409)
        self.assertNotIn('late-canary',await response.text())
        self.assertEqual(lease.cleanup,'confirmed')

    async def test_cancelled_close_keeps_tracked_resource_until_confirmed(self):
        release = asyncio.Event()
        class SlowClose(FakeBrowser):
            async def close(self):
                self.closed += 1
                await release.wait()
        self.broker.browser_factory = SlowClose
        launch,_ = await self.active()
        lease = self.broker.leases[launch['request_id']]
        task = asyncio.create_task(self.broker.finish(lease,'cancelled'))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(lease.browser)
        self.assertEqual(lease.cleanup,'uncertain')
        release.set()
        await self.broker.finish(lease,'cancelled')
        self.assertIsNone(lease.browser)
        self.assertEqual(lease.cleanup,'confirmed')
        self.assertEqual(FakeBrowser.instances[-1].closed,1)

    async def test_missing_heartbeat_expires_without_poll_extending_idle(self):
        launch,headers = await self.active()
        lease = self.broker.leases[launch['request_id']]
        original_idle = lease.idle_deadline
        response = await self.human.post('/api/frame',json={'request_id':launch['request_id']},headers=headers)
        self.assertEqual(response.status,200)
        self.assertEqual(lease.idle_deadline,original_idle)
        lease.heartbeat_deadline = time.time()-1
        response = await self.human.post('/api/frame',json={'request_id':launch['request_id']},headers=headers)
        self.assertEqual(response.status,409)
        self.assertEqual(lease.state,'expired')

    async def test_unconfirmed_shutdown_keeps_resource_for_retry(self):
        class FailedClose(FakeBrowser):
            async def close(self):
                raise RuntimeError('synthetic-close-canary')
        self.broker.browser_factory = FailedClose
        launch,_ = await self.active()
        self.assertFalse(await self.broker.close())
        lease = self.broker.leases[launch['request_id']]
        self.assertEqual(lease.cleanup,'uncertain')
        self.assertIsNotNone(lease.browser)
        lease.browser.close = AsyncMock()
        self.assertTrue(await self.broker.close())


class SignInIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_broker_socket_cannot_be_imported_by_workspace_inbox_or_local_api(self):
        from oak.signin import SignInBridge
        cache = self.root / 'telegram-api-cache'
        cache.mkdir()
        alias = self.root / 'cache-alias'
        alias.symlink_to(cache, target_is_directory=True)
        external_inbox = self.root / 'external-inbox'
        external_inbox.mkdir()
        (self.root / 'inbox').symlink_to(external_inbox, target_is_directory=True)
        self.c.config['telegram_api_directory'] = str(cache)
        for directory in (Path(self.c.cwd), self.root / 'inbox', external_inbox, cache, alias):
            with self.subTest(directory=directory), self.assertRaisesRegex(ValueError, 'outside model and Telegram'):
                SignInBridge(self.gateway, {'socket': str(directory / 'control.sock'),
                    'origin': self.broker.human_origin, 'synthetic_only': True})
        self.assertIs(self.c.requests.broker, self.gateway.signin)

    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.runtime = RuntimeClient()
        self.runtime._send = AsyncMock()
        self.runtime.request = AsyncMock()
        self.c = Controller(self.runtime, self.root / 'state.sqlite', self.root / 'workspace', None,
                            config={'state_dir':str(self.root)})
        self.addCleanup(self.c.close)
        self.bus = EventBus(self.c)
        self.c.emit = self.bus.emit
        self.c.threads[11] = 'original-thread'
        self.c.active[11] = 'original-turn'
        FakeBrowser.instances.clear()
        FakeBrowser.failure = False
        self.broker = Broker('http://127.0.0.1:18780','http://127.0.0.1:18779',
                             'http://127.0.0.1:18781',browser_factory=FakeBrowser)
        self.human = TestClient(TestServer(self.broker.human))
        await self.human.start_server()
        self.broker.human_origin = str(self.human.make_url('')).rstrip('/')
        self.addAsyncCleanup(self.human.close)
        self.control = web.AppRunner(self.broker.control,access_log=None)
        await self.control.setup()
        self.addAsyncCleanup(self.control.cleanup)
        socket = self.root / 'control.sock'
        await web.UnixSite(self.control,str(socket)).start()
        socket.chmod(0o600)
        self.gateway = WebGateway(self.c,self.bus,'synthetic-token',[11,22],{'credential_broker':{
            'socket':str(socket),'origin':self.broker.human_origin,'synthetic_only':True}})
        self.web = TestClient(TestServer(self.gateway.app))
        await self.web.start_server()
        self.addAsyncCleanup(self.web.close)
        self.addAsyncCleanup(self.gateway.signin.close)
        self.addAsyncCleanup(self.broker.close)
        self.headers = {'Authorization':'Bearer synthetic-owner-session'}
        with self.c.db:
            self.c.db.execute('INSERT INTO web_sessions VALUES (?,?,?,?)',
                              (hashlib.sha256(b'synthetic-owner-session').hexdigest(),11,time.time()+300,'telegram'))
        self.c.tools = Tools(self.c,{})
        self.runtime.tool_handler = self.c.tools.handle
        self.tasks = []
        self.addAsyncCleanup(self.finish)

    async def finish(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)

    async def begin(self):
        task = asyncio.create_task(self.runtime._handle_server_request({'id':42,'method':'item/tool/call','params':{
            'threadId':'original-thread','turnId':'original-turn','tool':'oak_request_input',
            'arguments':{'template':'synthetic_sign_in'}}}))
        self.tasks.append(task)
        for _ in range(20):
            await asyncio.sleep(0)
            if self.c.requests.waiters:
                return task,next(iter(self.c.requests.waiters))
        self.fail('Request did not bind')

    async def launch(self, identifier):
        response = await self.web.post('/api/requests/'+identifier+'/signin',json={},headers=self.headers)
        self.assertEqual(response.status,200)
        value = await response.json()
        response = await self.human.post('/api/attach',json={'request_id':identifier,'ticket':value['ticket']},
                                        headers={'Origin':self.broker.human_origin})
        self.assertEqual(response.status,200)
        human = await response.json()
        return value,{'Origin':self.broker.human_origin,'Authorization':'Bearer '+human['lease']}

    async def test_real_unix_control_same_native_rpc_once_and_secret_exclusion(self):
        task,identifier = await self.begin()
        launch,headers = await self.launch(identifier)
        self.assertEqual((await self.web.post('/api/requests/'+identifier+'/signin',json={},headers=self.headers)).status,409)
        canary = 'synthetic-only-password-and-otp-canary'
        await self.human.post('/api/event',json={'request_id':identifier,'event':{'kind':'text','epoch':1,'text':canary}},headers=headers)
        FakeBrowser.instances[-1].authenticated = True
        await self.human.post('/api/frame',json={'request_id':identifier},headers=headers)
        await asyncio.wait_for(task,2)
        self.runtime._send.assert_awaited_once()
        sent = self.runtime._send.await_args.args[0]
        self.assertEqual(sent['id'],42)
        result = json.loads(sent['result']['contentItems'][0]['text'])
        self.assertEqual(result,{'outcome':'authenticated','provider':'oak_synthetic','authenticated':True,
                                 'reason':'synthetic_login_verified','simulated':True})
        self.runtime.request.assert_not_awaited()
        self.assertEqual(self.c.active[11],'original-turn')
        for forbidden in (canary,launch['ticket'],headers['Authorization'][7:]):
            self.assertNotIn(forbidden,'\n'.join(self.c.db.iterdump()))
            self.assertNotIn(forbidden,json.dumps(self.bus.replay(11)))
            self.assertNotIn(forbidden,json.dumps(sent))
        self.assertEqual(self.c.requests.snapshot(11,11,identifier)['broker']['cleanup'],'confirmed')
        self.assertEqual(FakeBrowser.instances[-1].closed,1)

    async def test_owner_cancel_wins_and_late_broker_callback_cannot_resume_again(self):
        task,identifier = await self.begin()
        await self.launch(identifier)
        response = await self.web.post('/api/requests/'+identifier,json={'requestId':'cancel','revision':1,'decision':'cancel'},headers=self.headers)
        self.assertEqual(response.status,200)
        await task
        await self.gateway.signin.complete(11,11,identifier,'authenticated')
        self.runtime._send.assert_awaited_once()
        self.assertEqual(self.c.requests.snapshot(11,11,identifier)['outcome'],'cancelled')
        self.runtime.request.assert_not_awaited()

    async def test_ordinary_api_cannot_send_secret_or_forge_login_and_instagram_stays_blocked(self):
        task,identifier = await self.begin()
        for payload in ({'password':'synthetic-rejected-canary'}, {'authenticated':True}, {'url':'https://www.instagram.com'}):
            self.assertEqual((await self.web.post('/api/requests/'+identifier+'/signin',json=payload,headers=self.headers)).status,400)
        self.assertEqual((await self.web.post('/api/requests/'+identifier+'/signin',json={},headers={'Origin':'https://evil.test',**self.headers})).status,403)
        self.assertEqual((await self.web.post('/api/requests/'+identifier+'/signin',json={})).status,401)
        self.assertFalse(self.c.db.execute('SELECT 1 FROM signin_attempts').fetchone())
        self.assertNotIn('synthetic-rejected-canary','\n'.join(self.c.db.iterdump()))
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        self.c.active[11] = 'another-turn'
        other = asyncio.create_task(self.c.requests.template({'threadId':'original-thread','turnId':'another-turn',
            'requestId':43,'runtimeEpoch':self.runtime.runtime_epoch},'instagram_sign_in'))
        self.tasks.append(other)
        await asyncio.sleep(0)
        row = self.c.db.execute('SELECT id FROM input_requests WHERE turn_id=?',('another-turn',)).fetchone()
        self.assertFalse(self.c.requests.snapshot(11,11,row[0])['form']['capability'])
        self.assertEqual((await self.web.post('/api/requests/'+row[0]+'/signin',json={},headers=self.headers)).status,409)

    async def test_revoked_panel_session_cancels_and_confirmed_cleanup_survives_restart(self):
        task,identifier = await self.begin()
        await self.launch(identifier)
        with self.c.db:
            self.c.db.execute('DELETE FROM web_sessions')
        await asyncio.wait_for(task,2)
        for _ in range(30):
            if self.gateway.signin.snapshot(identifier)['cleanup'] == 'confirmed':
                break
            await asyncio.sleep(0.02)
        value = self.c.requests.snapshot(11,11,identifier)
        self.assertEqual(value['outcome'],'cancelled')
        self.assertEqual(value['broker'],{'phase':'cancelled','cleanup':'confirmed'})
        self.assertEqual(FakeBrowser.instances[-1].closed,1)

    async def test_unknown_dispatch_is_recorded_before_ipc_and_never_replayed(self):
        task,identifier = await self.begin()
        from oak.requests import RequestUnavailable
        self.gateway.signin.ipc = AsyncMock(side_effect=RequestUnavailable('synthetic-secret-transport-canary'))
        for _ in range(2):
            response = await self.web.post('/api/requests/'+identifier+'/signin',json={},headers=self.headers)
            self.assertEqual(response.status,409)
            self.assertNotIn('canary',await response.text())
        self.gateway.signin.ipc.assert_awaited_once()
        self.assertEqual(self.gateway.signin.snapshot(identifier),{'phase':'uncertain','cleanup':'uncertain'})
        self.assertNotIn('synthetic-secret-transport-canary','\n'.join(self.c.db.iterdump()))
        self.assertFalse(task.done())

    async def test_native_send_failure_is_uncertain_after_browser_destroyed(self):
        task,identifier = await self.begin()
        _,headers = await self.launch(identifier)
        self.runtime._send.side_effect = ConnectionError('synthetic disconnected')
        FakeBrowser.instances[-1].authenticated = True
        await self.human.post('/api/frame',json={'request_id':identifier},headers=headers)
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(task,2)
        self.assertEqual(self.c.requests.snapshot(11,11,identifier)['delivery'],'uncertain')
        self.assertEqual(FakeBrowser.instances[-1].closed,1)
        self.runtime._send.assert_awaited_once()
        self.runtime.request.assert_not_awaited()

    async def test_owner_revocation_during_status_ipc_cannot_commit_success(self):
        task,identifier = await self.begin()
        entered,release = asyncio.Event(),asyncio.Event()
        original = self.gateway.signin.ipc
        async def delayed(route,value):
            result = await original(route,value)
            if route == '/status':
                entered.set()
                await release.wait()
                return {**result,'state':'authenticated','cleanup':'confirmed'}
            return result
        self.gateway.signin.ipc = delayed
        _,headers = await self.launch(identifier)
        await asyncio.wait_for(entered.wait(),2)
        FakeBrowser.instances[-1].authenticated = True
        await self.human.post('/api/frame',json={'request_id':identifier},headers=headers)
        with self.c.db:
            self.c.db.execute('DELETE FROM web_sessions')
        release.set()
        await asyncio.wait_for(task,2)
        sent = self.runtime._send.await_args.args[0]
        result = json.loads(sent['result']['contentItems'][0]['text'])
        self.assertEqual(result['outcome'],'cancelled')
        self.assertFalse(result['authenticated'])
        self.runtime._send.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
