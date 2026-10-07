import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlencode

from aiohttp import WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from oak.bus import EventBus
from oak.controller import Controller
from oak.panel import https_url
from oak.runtime import RpcError
from oak.tools import Tools
from oak.tunnel import PreviewTunnel, TunnelDisconnected
from oak.telegram import TelegramError
from oak.web import WebGateway, telegram_owner


class WebTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.client = AsyncMock()
        self.controller = Controller(self.client, ':memory:', self.root, AsyncMock(),
                                     config={'state_dir': str(self.root)})
        self.addCleanup(self.controller.db.close)
        self.controller.tools = Tools(self.controller, {})
        self.bus = EventBus(self.controller)

    async def test_tunnel_recovers_lost_mapping_even_when_ssh_remains_alive(self):
        tunnel = PreviewTunnel('localhost', self.root)
        tunnel.url = 'https://synthetic.lhr.life'
        tunnel._exit = asyncio.get_running_loop().create_future()
        self.addCleanup(tunnel._exit.cancel)
        response = MagicMock(status=502)
        response.content.read = AsyncMock(side_effect=[b'no ', b'tunnel', b'', b'other error', b'', b'no tunnel', b'', b'no tunnel', b''])
        session = MagicMock()
        session.__aenter__.return_value = session
        session.get.return_value.__aenter__.return_value = response
        with patch('oak.tunnel.ClientSession', return_value=session), \
                patch('oak.tunnel.asyncio.wait', AsyncMock(return_value=(set(), set()))):
            with self.assertRaisesRegex(RuntimeError, 'public mapping'):
                await asyncio.wait_for(tunnel.wait(), 0.1)
        self.assertEqual(session.get.call_count, 4)
        self.assertFalse(tunnel._exit.done())

    async def test_tunnel_reconnect_does_not_end_application_task(self):
        tunnel = PreviewTunnel('localhost', self.root)
        tunnel.process = object()

        async def close():
            tunnel.process = None

        async def start(port):
            self.assertEqual(port, 8765)
            tunnel.process = object()
            tunnel.url = 'https://replacement.lhr.life'

        tunnel.close = AsyncMock(side_effect=close)
        tunnel.start = AsyncMock(side_effect=start)
        tunnel.wait = AsyncMock(side_effect=[TunnelDisconnected('lost mapping'),
                                            TunnelDisconnected('replacement lost'), asyncio.CancelledError()])
        cancelled = []

        async def pending_publication(url):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(url)

        publish = AsyncMock(side_effect=pending_publication)
        with patch('oak.tunnel.asyncio.sleep', AsyncMock()):
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(tunnel.maintain(8765, publish), 0.2)
        self.assertEqual(cancelled, ['https://replacement.lhr.life'] * 2)
        self.assertEqual(tunnel.wait.await_count, 3)
        self.assertEqual(tunnel.close.await_count, 2)

    def test_telegram_auth_signature_freshness_duplicates_and_owner(self):
        token, now = '123:synthetic-test-token', 1800000000

        def signed(owner=11, date=now):
            fields = {'auth_date': str(date), 'user': json.dumps({'id': owner}),
                      'query_id': 'synthetic-query'}
            secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
            payload = '\n'.join(k + '=' + v for k, v in sorted(fields.items()))
            fields['hash'] = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
            return urlencode(fields)

        valid = signed()
        self.assertEqual(telegram_owner(valid, token, {11}, now), 11)
        for value, key in ((signed(22), token), (signed(date=now - 3601), token),
                           (signed(date=now + 61), token), (valid + '&auth_date=1', token),
                           (valid, 'wrong-token'), (signed(owner=True), token)):
            with self.subTest(value=value[-20:], key=key):
                with self.assertRaises(ValueError):
                    telegram_owner(value, key, {11}, now)

    async def test_bus_scopes_live_and_durable_events_without_replacing_sinks(self):
        one, two = AsyncMock(), AsyncMock()
        self.bus.add_sink(one)
        self.bus.add_sink(two)
        async with self.bus.subscribe(11, 'a') as a, self.bus.subscribe(11, 'b') as b, \
                self.bus.subscribe(22, 'a') as other, self.bus.subscribe(11) as all_runs:
            for chat, run in ((11, 'a'), (22, 'a'), (11, 'b')):
                await self.bus.emit(chat, {'type': 'RUN_STARTED', 'runId': run})
            self.assertEqual((a.qsize(), b.qsize(), other.qsize(), all_runs.qsize()), (1, 1, 1, 2))
            first = a.get_nowait()
            self.assertEqual(first['event']['runId'], 'a')
            self.assertEqual([r['event']['runId'] for r in self.bus.replay(11)], ['a', 'b'])
            self.assertEqual([r['event']['runId'] for r in self.bus.replay(11, first['sequence'])], ['b'])
            self.assertEqual(len(self.bus.replay(22, run_id='a')), 1)
            self.assertEqual(self.bus.replay(22, run_id='b'), [])
            self.assertEqual(one.await_args_list, two.await_args_list)
            self.assertEqual(one.await_count, 3)
        self.assertFalse(self.bus.subscribers)
        self.client.request.assert_not_awaited()

    async def test_web_sessions_conversations_events_and_artifacts_are_owner_scoped(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)

        async def login(owner):
            response = await client.post('/api/session', json={'key': gateway.keys[str(owner)]})
            self.assertEqual(response.status, 200)
            cookie = response.cookies['oak_session'].value
            client.session.cookie_jar.clear()
            return {'Cookie': 'oak_session=' + cookie}

        first, second = await login(11), await login(22)
        response = await client.get('/api/bootstrap')
        self.assertEqual(response.status, 401)
        bearer = {'Authorization': 'Bearer ' + first['Cookie'].split('=', 1)[1]}
        response = await client.get('/api/bootstrap', headers=bearer)
        self.assertEqual(response.status, 200)
        response = await client.post('/api/conversations', headers=first, json={'title': 'First owner'})
        conversation = await response.json()
        chat = self.controller.db.execute('SELECT chat_id FROM web_conversations WHERE id=?',
                                          (conversation['id'],)).fetchone()[0]
        await self.bus.emit(chat, {'type': 'RUN_STARTED', 'runId': 'web-run'})
        await self.bus.emit(11, {'type': 'RUN_STARTED', 'runId': 'telegram-first'})
        await self.bus.emit(22, {'type': 'RUN_STARTED', 'runId': 'telegram-second'})
        response = await client.get('/api/events?conversation=' + conversation['id'], headers=first)
        records = await response.json()
        self.assertEqual([r['event']['runId'] for r in records], ['web-run'])
        response = await client.get('/api/events?conversation=' + conversation['id'], headers=second)
        self.assertEqual(response.status, 404)
        response = await client.get('/api/events', headers=second)
        self.assertEqual([r['event']['runId'] for r in await response.json()], ['telegram-second'])
        response = await client.post('/api/input', headers=second,
                                     json={'conversation': conversation['id'], 'text': 'denied', 'requestId': 'one'})
        self.assertEqual(response.status, 404)

        artifact = self.root / 'report.txt'
        artifact.write_text('Synthetic report')
        await self.bus.emit(chat, {'type': 'CUSTOM', 'name': 'artifact', 'value': {'path': str(artifact)}})
        value = self.bus.replay(chat)[-1]['event']['value']
        self.assertNotIn('path', value)
        response = await client.get(value['url'], headers=second)
        self.assertEqual(response.status, 404)
        response = await client.get(value['url'], headers=first)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.text(), 'Synthetic report')
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
        response = await client.get(value['url'], headers=bearer)
        self.assertEqual(response.status, 200)
        self.client.request.assert_not_awaited()

    async def test_panel_redacts_content_and_scopes_task_controls_to_owner(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        with self.controller.db:
            self.controller.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                (hashlib.sha256(b'unit-session').hexdigest(), 11, 4102444800))
        request = make_mocked_request('GET', '/api/panel', headers={'Authorization': 'Bearer unit-session'})
        mine = self.controller.scheduler.add(11, 'PRIVATE_TASK', delay_seconds=60)
        other = self.controller.scheduler.add(22, 'OTHER_TASK', delay_seconds=60)
        self.controller.memory.remember(11, 'PRIVATE_MEMORY')
        self.controller.memory.remember(22, 'OTHER_MEMORY')
        self.controller.instructions = 'PRIVATE_INSTRUCTIONS'
        panel = json.loads((await gateway.panel(request)).body)
        self.assertEqual(panel['memory']['count'], 1)
        self.assertFalse(panel['session']['initialized'])
        self.assertEqual(panel['settings']['model'], 'gpt-6.1-sol')
        tasks = json.loads((await gateway.tasks(request)).body)['tasks']
        self.assertEqual([task['id'] for task in tasks], [mine])
        with self.controller.db:
            self.controller.db.execute('INSERT INTO turns VALUES (?,?,?,?)', ('scheduled-run', 'unit-thread', 11, 'failed'))
            self.controller.db.execute('UPDATE jobs SET turn_id=? WHERE id=?', ('scheduled-run', mine))
        await self.bus.emit(11, {'type': 'RUN_STARTED', 'runId': 'scheduled-run'})
        await self.bus.emit(11, {'type': 'RUN_ERROR', 'runId': 'scheduled-run', 'message': 'PRIVATE_PROVIDER_ERROR'})
        request._match_info['id'] = mine
        detail = json.loads((await gateway.task(request)).body)
        self.assertEqual(detail['task']['turn_status'], 'failed')
        self.assertEqual([e['type'] for e in detail['timeline']], ['RUN_STARTED', 'RUN_ERROR'])
        self.assertNotIn('PRIVATE_PROVIDER_ERROR', json.dumps(detail))
        for secret in ('PRIVATE_TASK', 'PRIVATE_MEMORY', 'PRIVATE_INSTRUCTIONS', 'OTHER_TASK', 'OTHER_MEMORY'):
            self.assertNotIn(secret, json.dumps([panel, tasks]))
        request._match_info['id'] = other
        request.json = AsyncMock(return_value={'confirmed': True})
        with self.assertRaises(web.HTTPNotFound):
            await gateway.cancel_task(request)
        request._match_info['id'] = mine
        request.json.return_value = {'confirmed': False}
        with self.assertRaises(web.HTTPBadRequest):
            await gateway.cancel_task(request)
        self.assertEqual(self.controller.scheduler.list(11)[0]['status'], 'pending')
        request.json.return_value = {'confirmed': True}
        result = json.loads((await gateway.cancel_task(request)).body)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.controller.scheduler.list(22)[0]['id'], other)
        with self.assertRaises(web.HTTPUnauthorized):
            await gateway.panel(make_mocked_request('GET', '/api/panel'))

    async def test_computer_switch_requires_owner_origin_confirmation_and_boolean(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        with self.controller.db:
            self.controller.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                (hashlib.sha256(b'unit-session').hexdigest(), 11, 4102444800))
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer unit-session'}
        response = await client.post('/api/computer', json={'enabled': False, 'confirmed': True})
        self.assertEqual(response.status, 401)
        response = await client.get('/api/panel', headers=headers)
        self.assertEqual((await response.json())['computer'], {'configured': False, 'enabled': False, 'manual': False, 'busy': False})
        response = await client.post('/api/computer', headers=headers, json={'enabled': True, 'confirmed': True})
        self.assertEqual(response.status, 400)
        self.controller.tools.computer = object()  # Configure the contract without operating a desktop.
        for value in (None, 'false', 0, 1):
            response = await client.post('/api/computer', headers=headers, json={'enabled': value, 'confirmed': True})
            self.assertEqual(response.status, 400)
        response = await client.post('/api/computer', headers=headers, json={'enabled': False})
        self.assertEqual(response.status, 400)
        response = await client.post('/api/computer', headers={**headers, 'Origin': 'https://other.example'},
                                     json={'enabled': False, 'confirmed': True})
        self.assertEqual(response.status, 403)
        self.assertTrue(self.controller.tools.computer_status(11)['enabled'])
        for enabled in (False, True):
            response = await client.post('/api/computer', headers=headers,
                                         json={'enabled': enabled, 'confirmed': True, 'owner': 22})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['enabled'], enabled)
            response = await client.get('/api/panel', headers=headers)
            self.assertEqual((await response.json())['computer']['enabled'], enabled)
            self.assertTrue(self.controller.tools.computer_status(22)['enabled'])
        self.client.request.assert_not_awaited()

    async def test_remote_requires_confirmed_owner_short_lived_ticket_and_origin(self):
        self.controller.tools = Tools(self.controller, {'computer': {'enabled': True, 'display': ':99'}})
        await self.controller.tools.set_computer_enabled(11, False)
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        for owner, token in ((11, 'remote-owner'), (22, 'remote-other'), (11, 'remote-same-owner')):
            self.controller.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), owner, 4102444800))
        self.controller.db.commit()
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer remote-owner'}
        origin = str(client.make_url('/')).rstrip('/')
        response = await client.get('/api/remote', headers=headers)
        self.assertIn(origin.replace('http://', 'ws://'), response.headers['Content-Security-Policy'])
        self.assertIn(origin.replace('http://', 'wss://'), response.headers['Content-Security-Policy'])
        rejected_host = make_mocked_request('GET', '/', headers={'Host': 'bad; script-src https://other.example'})
        guarded = await gateway.guard(rejected_host, AsyncMock(return_value=web.Response()))
        self.assertNotIn('other.example', guarded.headers['Content-Security-Policy'])
        self.assertEqual((await client.get('/api/remote')).status, 401)
        self.assertEqual((await client.post('/api/remote/start', headers=headers, json={})).status, 400)
        with patch('oak.remote.shutil.which', return_value='/usr/bin/x11vnc'):
            cookie = {'Cookie': 'oak_session=remote-owner'}
            self.assertEqual((await client.post('/api/remote/start', headers=cookie, json={'confirmed': True})).status, 200)
            self.assertEqual((await client.post('/api/remote/stop', headers=cookie, json={'confirmed': True})).status, 200)
            response = await client.post('/api/remote/start', headers=headers, json={'confirmed': True})
            self.assertEqual(response.status, 200)
            ticket = await response.json()
            lease = {'lease': ticket['lease']}
            self.assertEqual(ticket['protocol'], 'oak-remote.' + ticket['ticket'])
            self.assertEqual(ticket['expires_in'], 30)
            self.assertTrue(self.controller.tools.computer_status(11)['manual'])
            self.assertEqual((await client.post('/api/remote/type', headers=headers, json={**lease, 'text': 'Привіт'})).status, 409)
            gateway.remote.current['socket'] = MagicMock(prepared=True, closed=False)
            with patch.object(self.controller.tools.computer, '_type', new_callable=AsyncMock) as typing, \
                    patch.object(self.controller.tools.computer, '_key', new_callable=AsyncMock) as keypress:
                for wrong in ('remote-other', 'remote-same-owner'):
                    self.assertEqual((await client.post('/api/remote/type', headers={'Authorization': 'Bearer ' + wrong},
                                                       json={**lease, 'text': 'Привіт'})).status, 403)
                self.assertEqual((await client.post('/api/remote/type', headers=headers, json={**lease, 'text': 'a' * 1001})).status, 400)
                self.assertEqual((await client.post('/api/remote/type', headers=cookie, json={**lease, 'text': 'Привіт'})).status, 200)
                self.assertEqual((await client.post('/api/remote/type', headers=cookie, json={**lease, 'key': 'BackSpace'})).status, 200)
                self.assertEqual((await client.post('/api/remote/type', headers=cookie, json={**lease, 'key': 'Escape;echo no'})).status, 400)
                self.assertEqual((await client.post('/api/remote/type', headers=cookie, json={'lease': 'old-lease', 'text': 'stale'})).status, 409)
                typing.assert_awaited_once_with('Привіт')
                keypress.assert_awaited_once_with('BackSpace')
                self.assertEqual((await client.post('/api/remote/type', headers=cookie, json={**lease, 'key': 'ctrl+shift+Left'})).status, 200)
                keypress.assert_awaited_with('ctrl+shift+Left')
                self.assertEqual((await client.post('/api/remote/type', headers=cookie, json={**lease, 'key': 'ctrl+ctrl+a'})).status, 400)
            gateway.remote.current['socket'] = None
            self.assertFalse(await self.controller.submit(11, 'wait', 901, idle_only=True))
            with self.assertRaisesRegex(ValueError, 'вручну'):
                await self.controller.tools.execute(11, 'oak_computer', {'action': 'screenshot'}, {'turnId': 'x'})
            self.assertEqual((await client.post('/api/remote/start', headers=headers, json={'confirmed': True})).status, 409)
            self.assertEqual((await client.post('/api/remote/stop', headers={'Authorization': 'Bearer remote-other'},
                                               json={'confirmed': True})).status, 403)
            for bad_origin in ('https://other.example', None):
                with self.assertRaises(WSServerHandshakeError) as denied:
                    await client.ws_connect('/api/remote/socket', protocols=[ticket['protocol']], origin=bad_origin)
                self.assertEqual(denied.exception.status, 403)
            # Missing/unrelated tickets cannot start the subprocess.
            with patch('oak.remote.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
                with self.assertRaises(WSServerHandshakeError) as denied:
                    await client.ws_connect('/api/remote/socket', protocols=['oak-remote.' + 'x' * 43], origin=origin)
                self.assertEqual(denied.exception.status, 401)
                spawn.assert_not_awaited()
                spawn.side_effect = OSError('unavailable')
                with self.assertRaises(WSServerHandshakeError) as failed:
                    await client.ws_connect('/api/remote/socket', protocols=[ticket['protocol']], origin=origin)
                self.assertEqual(failed.exception.status, 503)
                self.assertIsNone(self.controller.manual_owner)
                with self.assertRaises(WSServerHandshakeError) as replay:
                    await client.ws_connect('/api/remote/socket', protocols=[ticket['protocol']], origin=origin)
                self.assertEqual(replay.exception.status, 401)
            response = await client.post('/api/remote/start', headers=headers, json={'confirmed': True})
            ticket = await response.json()
            gateway.remote.current['expires'] = 0
            with self.assertRaises(WSServerHandshakeError) as expired:
                await client.ws_connect('/api/remote/socket', protocols=[ticket['protocol']], origin=origin)
            self.assertEqual(expired.exception.status, 401)
            await asyncio.sleep(1.1)
            self.assertIsNone(self.controller.manual_owner)
            self.assertEqual((await client.post('/api/remote/stop', headers=headers, json={'confirmed': True})).status, 200)
            self.assertFalse(self.controller.tools.computer_status(11)['enabled'])
            ticket = await (await client.post('/api/remote/start', headers=headers, json={'confirmed': True})).json()
            self.controller.db.execute('DELETE FROM web_sessions WHERE owner=11')
            self.controller.db.commit()
            with self.assertRaises(WSServerHandshakeError) as revoked:
                await client.ws_connect('/api/remote/socket', protocols=[ticket['protocol']], origin=origin)
            self.assertEqual(revoked.exception.status, 401)
            await gateway.remote.close()
            self.assertIsNone(self.controller.manual_owner)
        self.client.request.assert_not_awaited()

    async def test_manual_takeover_waits_for_active_turn_and_preserves_permissions(self):
        self.controller.tools = Tools(self.controller, {'computer': {'enabled': True, 'display': ':99'}})
        self.controller.active[11] = 'working'
        dispatching, release = asyncio.Event(), asyncio.Event()
        async def dispatch(*args):
            dispatching.set()
            await release.wait()
            return True
        self.controller._submit = AsyncMock(side_effect=dispatch)
        async def poller():
            await self.controller.submit(11, 'dispatch', 321)
            await asyncio.Event().wait()
        outer = asyncio.create_task(poller())
        self.addCleanup(outer.cancel)
        await dispatching.wait()
        interrupted = asyncio.Event()
        async def stop(chat):
            interrupted.set()
        self.controller.stop = AsyncMock(side_effect=stop)
        takeover = asyncio.create_task(self.controller.pause_for_manual(11))
        await asyncio.sleep(0)
        release.set()
        await interrupted.wait()
        self.assertFalse(outer.done())
        self.assertFalse(takeover.done())
        self.assertFalse(await self.controller.submit(22, 'must not run', 123))
        self.client.request.assert_not_awaited()
        self.controller.active.clear()  # The runtime completion event releases ownership.
        await takeover
        self.assertTrue(self.controller.tools.computer_status(11)['enabled'])
        self.assertTrue(self.controller.tools.computer_status(11)['manual'])

    async def test_task_summary_covers_jobs_outside_the_bounded_list(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11], {})
        for i in range(101):
            self.controller.db.execute('INSERT INTO jobs(id,chat_id,prompt,due,interval,mode) VALUES (?,?,?,?,?,?)',
                                       (str(i), 11, 'PRIVATE_TASK', i + 1, 60, 'run'))
        self.controller.scheduler.add(22, 'OTHER_TASK', delay_seconds=0)
        result = gateway.controls.tasks(11)
        self.assertEqual(len(result['tasks']), 100)
        self.assertEqual(result['summary']['active_count'], 101)
        self.assertEqual(result['summary']['next_task']['id'], '0')
        self.assertNotIn('PRIVATE_TASK', json.dumps(result))
        self.controller.db.execute("UPDATE jobs SET status='running' WHERE chat_id=11")
        summary = gateway.controls.tasks(11)['summary']
        self.assertEqual(summary['running_count'], 101)
        self.assertIsNone(summary['next_task'])

    async def test_model_choice_requires_confirmation_catalogue_and_owned_idle_session(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        with self.controller.db:
            self.controller.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                (hashlib.sha256(b'unit-session').hexdigest(), 11, 4102444800))
        scope = self.controller.sessions.resolve(11, 42)
        self.controller.sessions.resolve(22, 85)
        self.client.request.return_value = {'data': [
            {'model': 'gpt-6.1-sol', 'displayName': 'Default', 'hidden': False, 'defaultReasoningEffort': 'low',
             'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'medium'}]},
            {'model': 'synthetic-model', 'displayName': 'Synthetic model', 'hidden': False, 'defaultReasoningEffort': 'medium',
             'serviceTiers': [{'id': 'priority', 'name': 'Fast', 'description': 'Synthetic tier'}],
             'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}, {'reasoningEffort': 'ultra'}]}],
            'nextCursor': None}
        models = self.client.request.return_value
        self.client.request.side_effect = lambda method, params: ({'data': [{'name': 'fast_mode', 'enabled': True}]}
            if method == 'experimentalFeature/list' else models)
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer unit-session'}
        url = '/api/models?conversation=topic:42'
        change = {'model': 'synthetic-model', 'confirmed': True, 'owner': 22}
        self.assertEqual((await client.get(url)).status, 401)
        self.assertEqual((await client.post(url, headers=headers, json={**change, 'confirmed': False})).status, 400)
        self.assertEqual((await client.post(url, headers={**headers, 'Origin': 'https://other.example'}, json=change)).status, 403)
        self.assertEqual((await client.post('/api/models?conversation=topic:85', headers=headers, json=change)).status, 404)
        self.assertEqual((await client.post(url, headers=headers, json={**change, 'model': 'invented'})).status, 400)
        response = await client.get(url, headers=headers)
        self.assertEqual(response.status, 200)
        catalogue = await response.json()
        self.assertEqual(catalogue['selected'], 'gpt-6.1-sol')
        self.assertEqual(catalogue['effort'], 'low')
        self.assertIsNone(catalogue['turbo'])
        self.assertEqual([row['turbo_available'] for row in catalogue['models']], [False, True])
        self.assertEqual(catalogue['models'][1]['efforts'], ['medium', 'ultra'])
        self.assertEqual(catalogue['models'][1]['default_effort'], 'medium')
        self.controller.active[scope] = 'synthetic-turn'
        self.assertTrue((await (await client.get(url, headers=headers)).json())['busy'])
        self.assertEqual((await client.post(url, headers=headers, json=change)).status, 409)
        self.controller.active.clear()
        response = await client.post(url, headers=headers, json=change)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())['selected'], 'synthetic-model')
        self.assertEqual(self.controller.model_for(11), 'gpt-6.1-sol')
        panel = await (await client.get('/api/panel?conversation=topic:42', headers=headers)).json()
        self.assertEqual(panel['settings']['model'], 'synthetic-model')
        response = await client.post(url, headers=headers, json={**change, 'effort': 'ultra'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['effort'], 'ultra')
        response = await client.post(url, headers=headers, json={**change, 'turbo': True})
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())['turbo'])
        for turbo in (None, 1, 'true'):
            self.assertEqual((await client.post(url, headers=headers, json={**change, 'turbo': turbo})).status, 400)
        self.assertEqual((await client.post(url, headers=headers,
            json={**change, 'model': 'gpt-6.1-sol', 'effort': 'low', 'turbo': True})).status, 400)
        self.assertTrue(self.controller.turbo_for(scope))
        self.assertIsNone(self.controller.turbo_for(11))
        response = await client.post(url, headers=headers, json={**change, 'turbo': False})
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())['turbo'])
        for effort in (None, 1, '', 'low', 'invented'):
            response = await client.post(url, headers=headers, json={**change, 'effort': effort})
            self.assertEqual(response.status, 400)
        self.assertEqual(self.controller.effort_for(scope), 'ultra')
        self.assertEqual(self.controller.effort_for(11), 'low')
        panel = await (await client.get('/api/panel?conversation=topic:42', headers=headers)).json()
        self.assertEqual(panel['settings']['effort'], 'ultra')
        self.client.request.side_effect = RpcError(-32000, 'PRIVATE_PROVIDER_ERROR')
        response = await client.get(url, headers=headers)
        self.assertEqual(response.status, 503)
        self.assertNotIn('PRIVATE_PROVIDER_ERROR', await response.text())

    async def test_topic_sessions_scope_controls_and_do_not_replay_native_creation(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11, 22], {})
        with self.controller.db:
            self.controller.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                (hashlib.sha256(b'unit-session').hexdigest(), 11, 4102444800))
        self.controller.telegram = MagicMock()
        self.controller.telegram._api = AsyncMock(side_effect=[
            {'is_bot': True, 'has_topics_enabled': True, 'allows_users_to_create_topics': False},
            {'message_thread_id': 42, 'name': 'Проєкт'}, True, TelegramError('createForumTopic')])
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {'Authorization': 'Bearer unit-session'}
        action = {'name': 'Проєкт', 'operation_id': 'synthetic-create-001', 'confirmed': True}
        self.assertEqual((await client.get('/api/sessions')).status, 401)
        self.assertEqual((await client.post('/api/sessions', headers=headers, json={**action, 'confirmed': False})).status, 400)
        response = await client.post('/api/sessions', headers=headers, json=action)
        self.assertEqual(response.status, 200, await response.text())
        created = await response.json()
        self.assertEqual(created['session']['id'], 'topic:42')
        self.assertNotIn('scope_id', created['session'])
        self.assertEqual((await client.post('/api/sessions', headers=headers, json=action)).status, 200)
        self.assertEqual(self.controller.telegram._api.await_count, 2)
        scope = self.controller.sessions.lookup(11, 'topic:42')
        foreign = self.controller.sessions.resolve(22, 85, name='Інший власник')
        self.controller.memory.remember(scope, 'PRIVATE_TOPIC_MEMORY')
        self.controller.memory.remember(11, 'PRIVATE_GENERAL_MEMORY')
        own_job = self.controller.scheduler.add(scope, 'PRIVATE_TOPIC_TASK', delay_seconds=60)
        general_job = self.controller.scheduler.add(11, 'PRIVATE_GENERAL_TASK', delay_seconds=60)
        foreign_job = self.controller.scheduler.add(foreign, 'PRIVATE_OTHER_TASK', delay_seconds=60)
        response = await client.get('/api/sessions', headers=headers)
        sessions = await response.json()
        self.assertTrue(sessions['topics_enabled'])
        self.assertFalse(sessions['users_can_create_topics'])  # Bot creation does not need native user-management permission.
        self.assertEqual([s['id'] for s in sessions['sessions']], ['telegram', 'topic:42'])
        self.assertEqual(sessions['sessions'][1]['task_count'], 1)
        self.assertNotIn('PRIVATE_', json.dumps(sessions))
        response = await client.get('/api/tasks?conversation=topic:42', headers=headers)
        self.assertEqual([t['id'] for t in (await response.json())['tasks']], [own_job])
        self.assertEqual((await client.get('/api/panel?conversation=topic:85', headers=headers)).status, 404)
        self.assertEqual((await client.post('/api/tasks/' + foreign_job + '/cancel?conversation=topic:42', headers=headers, json={'confirmed': True})).status, 404)
        response = await client.post('/api/tasks/' + own_job + '/cancel?conversation=topic:42', headers=headers, json={'confirmed': True})
        self.assertTrue((await response.json())['cancelled'])
        self.assertEqual(self.controller.scheduler.list(11)[0]['id'], general_job)
        self.assertEqual(self.controller.scheduler.list(foreign)[0]['status'], 'pending')
        response = await client.post('/api/sessions/topic:42/rename', headers=headers,
            json={'confirmed': True, 'name': 'Дослідження', 'operation_id': 'synthetic-rename-001'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['session']['name'], 'Дослідження')
        self.assertEqual(self.controller.telegram._api.await_args.args, ('editForumTopic', {'chat_id': 11, 'message_thread_id': 42, 'name': 'Дослідження'}))
        action = {**action, 'name': 'Невідомий результат', 'operation_id': 'synthetic-create-002'}
        self.assertEqual((await client.post('/api/sessions', headers=headers, json=action)).status, 503)
        self.assertEqual((await client.post('/api/sessions', headers=headers, json=action)).status, 409)
        self.assertEqual((await client.post('/api/sessions', headers=headers, json={**action, 'operation_id': 'synthetic-create-003'})).status, 409)
        self.assertEqual(self.controller.telegram._api.await_count, 4)
        self.client.request.assert_not_awaited()  # Topic metadata does not start native model sessions.

        # Known rejections are retryable; only a confirmed missing topic retires it.
        self.controller.telegram.discard_topic = AsyncMock()
        self.controller.telegram._api.side_effect = TelegramError('createForumTopic', 400)
        rejected = {**action, 'name': 'Інша', 'operation_id': 'synthetic-create-004'}
        self.assertEqual((await client.post('/api/sessions', headers=headers, json=rejected)).status, 422)
        self.controller.telegram._api.side_effect = None
        self.controller.telegram._api.return_value = {'message_thread_id': 43}
        self.assertEqual((await client.post('/api/sessions', headers=headers,
            json={**rejected, 'operation_id': 'synthetic-create-005'})).status, 200)
        pending_job = self.controller.scheduler.add(scope, 'Cancelled on deletion', delay_seconds=60)
        self.controller.telegram._api.return_value = True
        for identifier in ('telegram', 'topic:85'):
            self.assertEqual((await client.post('/api/sessions/' + identifier + '/delete', headers=headers,
                json={'confirmed': True})).status, 404)
        self.assertEqual((await client.post('/api/sessions/topic:42/delete', headers=headers,
            json={'confirmed': False})).status, 400)
        self.controller.telegram._api.side_effect = TelegramError('deleteForumTopic')
        self.assertEqual((await client.post('/api/sessions/topic:42/delete', headers=headers,
            json={'confirmed': True})).status, 503)
        self.assertEqual(self.controller.sessions.lookup(11, 'topic:42'), scope)
        self.controller.telegram._api.side_effect = TelegramError('deleteForumTopic', 400, reason='topic_missing')
        response = await client.post('/api/sessions/topic:42/delete', headers=headers, json={'confirmed': True})
        self.assertEqual(await response.json(), {'id': 'topic:42', 'state': 'deleted'})
        self.controller.telegram._api.assert_awaited_with('deleteForumTopic',
            {'chat_id': 11, 'message_thread_id': 42}, timeout=10, rate_limit_attempts=1)
        self.assertEqual(self.controller.db.execute('SELECT status FROM jobs WHERE id=?', (pending_job,)).fetchone()[0], 'cancelled')
        self.assertIsNone(self.controller.sessions.lookup(11, 'topic:42'))
        self.assertEqual(self.controller.sessions.resolve(11, 42, name='Old replay'), scope)
        self.assertIsNone(self.controller.sessions.destination(scope))
        self.assertEqual(self.controller.sessions.owner(scope), 11)
        self.assertFalse(await self.controller.submit(scope, 'late input', 1234))
        self.assertEqual(self.controller.scheduler.list(11)[0]['id'], general_job)
        self.controller.telegram._api.side_effect = None
        self.controller.telegram._api.return_value = True
        self.assertEqual((await client.post('/api/sessions/topic:43/delete', headers=headers,
            json={'confirmed': True})).status, 200)
        self.assertEqual([s['id'] for s in self.controller.sessions.list(11)], ['telegram'])

    async def test_runtime_inventory_and_oauth_are_real_scoped_and_conservative(self):
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11], {})
        request = make_mocked_request('POST', '/api/integrations?conversation=topic:42', headers={'Authorization': 'Bearer unit-session'})
        with self.controller.db:
            self.controller.db.execute('INSERT INTO web_sessions(hash,owner,expires) VALUES (?,?,?)',
                (hashlib.sha256(b'unit-session').hexdigest(), 11, 4102444800))
        scope = self.controller.sessions.resolve(11, 42)
        self.controller.threads[11] = 'general-thread'
        self.controller.threads[scope] = 'loaded-topic-thread'
        self.controller.loaded.update({'general-thread', 'loaded-topic-thread'})
        calls = []

        async def rpc(method, params):
            calls.append((method, params))
            if method == 'app/installed':
                return {'apps': [{'id': 'app-one', 'runtimeName': 'Calendar', 'enabled': True, 'callable': False}]}
            if method == 'app/read':
                return {'apps': [{'id': 'app-one', 'name': 'Calendar', 'installUrl': 'https://chatgpt.com/apps/calendar',
                    'description': 'PRIVATE_DESCRIPTION', 'pluginDisplayNames': [], 'toolSummaries': []}], 'missingAppIds': []}
            if method == 'plugin/installed':
                return {'marketplaces': [{'name': 'catalog', 'plugins': [{'id': 'p-one', 'name': 'Calendar plugin',
                    'installed': True, 'enabled': True, 'authPolicy': 'ON_USE', 'availability': 'DISABLED_BY_ADMIN',
                    'disabledReason': None}]}], 'marketplaceLoadErrors': []}
            if method == 'mcpServerStatus/list':
                return {'data': [{'name': 'calendar', 'authStatus': 'notLoggedIn', 'runtimeStatus': 'authenticationRequired',
                    'tools': {}, 'resources': [], 'resourceTemplates': [], 'toolsError': 'PRIVATE_ERROR'}], 'nextCursor': None}
            if method == 'mcpServer/oauth/login':
                return {'authorizationUrl': 'https://provider.example/authorize?state=synthetic&redirect_uri=https%3A%2F%2Fpanel.example%2Fcallback'}
            raise AssertionError(method)

        self.client.request.side_effect = rpc
        inventory = json.loads((await gateway.integrations(request)).body)
        self.assertFalse(inventory['apps']['items'][0]['callable'])
        self.assertEqual(inventory['plugins']['items'][0]['status'], 'disabled_by_admin')
        self.assertNotIn('PRIVATE_', json.dumps(inventory))
        server = inventory['servers']['items'][0]
        self.assertEqual(server['runtime_status'], 'authenticationRequired')
        self.assertIsNone(server['tool_count'])
        self.assertFalse(server['oauth_available'])  # Telegram/mobile callback has not been configured.
        request._match_info['id'] = server['id']
        request.json = AsyncMock(return_value={'confirmed': True})
        with self.assertRaises(web.HTTPConflict):
            await gateway.oauth(request)
        self.assertFalse(any(method == 'mcpServer/oauth/login' for method, _ in calls))
        self.controller.config['runtime_config'] = {'mcp_oauth_callback_url': 'https://panel.example/callback'}
        with self.assertRaises(web.HTTPConflict):
            await gateway.oauth(request)  # A URL alone cannot establish a working callback listener.
        self.controller.config['runtime_config']['mcp_oauth_callback_port'] = 5555
        gateway.config['oauth_callback_ready'] = True
        started = json.loads((await gateway.oauth(request)).body)
        self.assertEqual(started['state'], 'pending')
        self.assertTrue(started['url'].startswith('https://provider.example/'))
        with self.assertRaises(web.HTTPConflict):
            await gateway.oauth(request)
        self.assertEqual(sum(method == 'mcpServer/oauth/login' for method, _ in calls), 1)
        self.assertTrue(all(params.get('threadId') == 'loaded-topic-thread' for method, params in calls if method != 'plugin/installed'))
        saved = [dict(row) for row in self.controller.db.execute('SELECT * FROM panel_oauth')]
        self.assertEqual(saved[0]['owner'], 11)
        self.assertNotIn('provider.example', json.dumps(saved))
        self.assertNotIn('synthetic', json.dumps(saved))

    def test_connection_urls_reject_credentials_and_unreachable_redirects(self):
        self.assertEqual(https_url('https://chatgpt.com/apps/calendar', native=True), 'https://chatgpt.com/apps/calendar')
        for url in ('javascript:alert(1)', 'https://chatgpt.com.evil.example/apps',
                    'https://user:secret@chatgpt.com/apps', 'https://chatgpt.com/apps?access_token=secret'):
            self.assertIsNone(https_url(url, native=True))
        gateway = WebGateway(self.controller, self.bus, 'synthetic-token', [11], {'oauth_callback_ready': True})
        self.controller.config['runtime_config'] = {'mcp_oauth_callback_url': 'https://panel.example/callback',
                                                    'mcp_oauth_callback_port': 5555}
        request = make_mocked_request('POST', '/api/integrations')
        for redirect in ('http://127.0.0.1:5555/callback', 'https://other.example/callback', 'https://panel.example/wrong'):
            self.assertFalse(gateway.controls.valid_redirect(request, 11, 'https://provider.example/auth?' + urlencode({'redirect_uri': redirect})))
        self.assertTrue(gateway.controls.valid_redirect(request, 11, 'https://provider.example/auth?' +
                         urlencode({'redirect_uri': 'https://panel.example/callback/server-id'})))


if __name__ == '__main__':
    unittest.main()
