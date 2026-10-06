import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from oak.bus import EventBus
from oak.controller import Controller
from oak.panel import https_url
from oak.runtime import RpcError
from oak.tools import Tools
from oak.tunnel import PreviewTunnel
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
        self.assertEqual((await response.json())['computer'], {'configured': False, 'enabled': False, 'busy': False})
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
             'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}, {'reasoningEffort': 'ultra'}]}],
            'nextCursor': None}
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
