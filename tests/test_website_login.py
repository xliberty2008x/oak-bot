"""Offline gateway continuation checks; the broker and all website access are mocked."""

import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.bus import EventBus
from oak.controller import Controller
from oak.login import WebsiteLogin
from oak.requests import RequestUnavailable
from oak.runtime import RuntimeClient
from oak.tools import Tools
from oak.web import WebGateway


class StatusBroker:
    """Model only the status-only IPC contract, never a provider or human form."""

    def __init__(self, origin):
        self.origin = origin
        self.bindings = {}
        self.queues = {}
        self.calls = []

    def status(self, identifier, **changes):
        binding = self.bindings[identifier]
        return {'request_id': identifier, 'session_id': 'a' * 32,
                'runtime_epoch': binding['runtime_epoch'], 'provider': binding['provider'],
                'state': 'waiting', 'cleanup': 'waiting', 'session_expires': 0,
                'simulated': False, **changes}

    async def call(self, route, data):
        self.calls.append((route, dict(data)))
        if route == '/health':
            return {'ready': True, 'protocol': 1, 'providers': ['website', 'google', 'instagram']}
        identifier = data['request_id']
        if route == '/create':
            self.bindings[identifier] = dict(data)
            self.queues[identifier] = asyncio.Queue()
            return self.status(identifier, origin=self.origin, ticket='T' * 43,
                               expires=min(time.time() + 30, data['expires']))
        if route == '/status':
            return await self.queues[identifier].get()
        if route == '/cancel':
            return self.status(identifier, state='cancelled', cleanup='confirmed')
        if route == '/use':
            return {'result': {'action': 'read', 'text': 'Synthetic public page'}}
        raise AssertionError('Unexpected offline IPC route')


class WebsiteLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.runtime = RuntimeClient()
        self.runtime.request = AsyncMock()
        self.runtime._send = AsyncMock()
        self.c = Controller(self.runtime, self.root / 'state.sqlite', self.root / 'workspace', None,
                            config={'state_dir': str(self.root),
                                    'execution': {'profile': '/nonexistent/offline-profile.json'}})
        self.addCleanup(self.c.close)
        self.c.tools = Tools(self.c, {})
        self.runtime.tool_handler = self.c.tools.handle
        self.bus = EventBus(self.c)
        self.c.emit = self.bus.emit
        self.c.threads[11] = 'original-thread'
        self.c.active[11] = 'original-turn'
        self.config = {'socket': '/run/oak-website-offline/control.sock',
                       'origin': 'https://human.synthetic.invalid'}
        self.gateway = WebGateway(self.c, self.bus, 'synthetic-token', [11, 22],
                                  {'public_url': 'https://oak.synthetic.invalid',
                                   'website_login': self.config})
        self.c.web = self.gateway
        self.bridge = self.gateway.website_login
        self.broker = StatusBroker(self.bridge.origin)
        self.bridge.ipc = AsyncMock(side_effect=self.broker.call)
        self.panel = 'synthetic-owner-panel'
        self.panel_hash = hashlib.sha256(self.panel.encode()).hexdigest()
        self.request = SimpleNamespace(headers={'Authorization': 'Bearer ' + self.panel}, cookies={})
        with self.c.db:
            self.c.db.execute('INSERT INTO web_sessions VALUES (?,?,?,?)',
                              (self.panel_hash, 11, time.time() + 600, 'telegram'))
        self.native = {'threadId': 'original-thread', 'turnId': 'original-turn',
                       'runtimeEpoch': self.runtime.runtime_epoch, 'requestId': 42}
        self.next_id = 42
        self.tasks = []
        self.addAsyncCleanup(self.finish)

    async def finish(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.bridge.close()

    async def until(self, predicate):
        for _ in range(100):
            if predicate():
                return
            await asyncio.sleep(0)
        self.fail('Offline continuation did not reach the expected state')

    async def begin(self, provider='website', *, url='https://shop.synthetic.invalid/account'):
        native_id = self.next_id
        self.next_id += 1
        tool = 'oak_website_form' if provider == 'website' else 'oak_request_input'
        arguments = ({'url': url} if provider == 'website'
                     else {'template': provider + '_sign_in'})
        task = asyncio.create_task(self.runtime._handle_server_request({
            'id': native_id, 'method': 'item/tool/call', 'params': {
                'threadId': 'original-thread', 'turnId': 'original-turn',
                'tool': tool, 'arguments': arguments}}))
        self.tasks.append(task)
        await self.until(lambda: any(row[0] == json.dumps(native_id) for row in self.c.db.execute(
            'SELECT native_id FROM input_requests')) or task.done())
        self.assertFalse(task.done(), 'Native request must remain paused for the human form')
        row = self.c.db.execute('SELECT * FROM input_requests WHERE native_id=?', (json.dumps(native_id),)).fetchone()
        self.assertIn(row['id'], self.c.requests.waiters)
        return task, row['id']

    async def launch(self, identifier):
        return await self.bridge.start(11, 11, identifier, self.request)

    async def complete(self, task, identifier, state='owner_confirmed', **changes):
        await self.broker.queues[identifier].put(self.broker.status(
            identifier, **{'state': state, 'cleanup': 'retained',
                           'session_expires': time.time() + 300, **changes}))
        await asyncio.wait_for(asyncio.shield(task), 1)
        sent = self.runtime._send.await_args.args[0]
        self.assertTrue(sent['result']['success'])
        return json.loads(sent['result']['contentItems'][0]['text'])

    def row(self, identifier):
        return self.c.requests.row(11, 11, identifier)

    async def test_owner_confirmation_resumes_same_native_rpc_once_without_claiming_authentication(self):
        task, identifier = await self.begin()
        self.runtime._send.assert_not_awaited()
        launch = await self.launch(identifier)
        self.assertEqual(set(launch), {'request_id', 'provider', 'origin', 'ticket', 'expires', 'simulated'})
        self.assertFalse(launch['simulated'])
        result = await self.complete(task, identifier)
        self.assertEqual(result, {'outcome': 'owner_confirmed', 'provider': 'website',
                                 'authenticated': False, 'reason': 'website_login_owner_confirmed',
                                 'request_id': identifier, 'destination': 'https://shop.synthetic.invalid',
                                 'session_available': True, 'confirmation': 'owner'})
        self.runtime._send.assert_awaited_once()
        self.assertEqual(self.runtime._send.await_args.args[0]['id'], 42)
        self.assertEqual(self.row(identifier)['delivery'], 'sent')
        self.assertEqual(self.c.active[11], 'original-turn')
        self.runtime.request.assert_not_awaited()
        self.assertEqual(self.bridge.attempt(identifier)['cleanup'], 'retained')
        self.assertFalse(any(route == '/cancel' for route, _ in self.broker.calls))
        with self.assertRaises(RequestUnavailable):
            await self.launch(identifier)
        self.runtime._send.assert_awaited_once()

    async def test_positive_provider_evidence_is_distinct_for_real_and_generic_forms(self):
        for provider in ('google', 'instagram', 'website'):
            with self.subTest(provider=provider):
                task, identifier = await self.begin(provider)
                await self.launch(identifier)
                result = await self.complete(task, identifier, 'authenticated')
                self.assertTrue(result['authenticated'])
                self.assertTrue(result['session_available'])
                self.assertEqual(result['confirmation'], 'provider')
                self.assertEqual(result['provider'], provider)
                self.assertNotIn('simulated', result)
        self.assertEqual(self.runtime._send.await_count, 3)
        self.runtime.request.assert_not_awaited()

    async def test_db_events_and_native_result_never_contain_human_values_or_tickets(self):
        task, identifier = await self.begin()
        await self.launch(identifier)
        canary = 'synthetic-password-otp-cookie-canary'
        for payload in ({'requestId': 'forged-values', 'revision': 1, 'decision': 'submit',
                         'values': {'password': canary, 'otp': canary}},
                        {'requestId': 'forged-success', 'revision': 1, 'decision': 'authenticated'}):
            with self.assertRaises(ValueError):
                await self.c.requests.decide(11, 11, identifier, payload)
        # Even unexpected broker properties cannot become persisted form values.
        await self.complete(task, identifier, fields={'password': canary}, ticket='T' * 43)
        recorded = '\n'.join(self.c.db.iterdump()) + json.dumps(self.bus.replay(11))
        recorded += json.dumps(self.runtime._send.await_args.args[0])
        for forbidden in (canary, 'T' * 43, self.panel):
            self.assertNotIn(forbidden, recorded)
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM input_request_receipts').fetchone()[0], 0)
        self.assertEqual(set(self.bus.replay(11)[0]['event']['value']), {'id', 'kind', 'summary'})
        for route, data in self.broker.calls:
            self.assertNotIn(canary, json.dumps(data))
            self.assertNotIn('values', data)

    async def test_wrong_owner_or_chat_cannot_launch_or_replay_a_form(self):
        task, identifier = await self.begin()
        for owner, chat in ((22, 11), (11, 22)):
            with self.subTest(owner=owner, chat=chat), self.assertRaises(RequestUnavailable):
                await self.bridge.start(owner, chat, identifier, self.request)
        self.assertFalse(any(route == '/create' for route, _ in self.broker.calls))
        await self.launch(identifier)
        with self.assertRaises(RequestUnavailable):
            await self.launch(identifier)
        self.assertEqual(sum(route == '/create' for route, _ in self.broker.calls), 1)
        self.assertFalse(task.done())

    async def test_live_binding_rejects_changed_turn_thread_epoch_owner_or_panel(self):
        _, identifier = await self.begin()
        await self.launch(identifier)
        row, attempt = dict(self.row(identifier)), dict(self.bridge.attempt(identifier))
        self.assertTrue(self.bridge.live(row, attempt))
        for key, value in (('owner', 22), ('owner', 33), ('chat_id', 22),
                           ('thread_id', 'other-thread'), ('turn_id', 'other-turn'),
                           ('runtime_epoch', 'old-epoch')):
            with self.subTest(key=key, value=value):
                self.assertFalse(self.bridge.live({**row, key: value}, attempt))
        self.assertFalse(self.bridge.live(row, {**attempt, 'panel_hash': 'missing-panel'}))
        with patch.object(self.c.sessions, 'deleted', return_value=True):
            self.assertFalse(self.bridge.live(row, attempt))
        with self.c.db:
            self.c.db.execute('UPDATE web_sessions SET expires=?', (time.time() - 1,))
        self.assertFalse(self.bridge.live(row, attempt))

    async def test_cancel_and_end_turn_release_the_exact_broker_session(self):
        task, identifier = await self.begin()
        await self.launch(identifier)
        await self.c.requests.decide(11, 11, identifier,
                                    {'requestId': 'cancel', 'revision': 1, 'decision': 'cancel'})
        await task
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')
        self.assertEqual(json.loads(self.row(identifier)['response'])['outcome'], 'cancelled')
        task, authenticated = await self.begin()
        await self.launch(authenticated)
        await self.complete(task, authenticated)
        self.c.requests.invalidate_turn('original-thread', 'original-turn')
        await self.until(lambda: self.bridge.attempt(authenticated)['cleanup'] == 'confirmed')
        for route, data in self.broker.calls:
            if route == '/cancel':
                self.assertEqual(set(data), {'request_id', 'session_id', 'runtime_epoch'})
                self.assertIn(data['request_id'], {identifier, authenticated})
                self.assertEqual(data['session_id'], 'a' * 32)
                self.assertEqual(data['runtime_epoch'], self.runtime.runtime_epoch)
        self.assertEqual(self.runtime._send.await_count, 2)
        self.runtime.request.assert_not_awaited()

    async def test_runtime_cancellation_abandons_waiter_and_cleans_browser(self):
        task, identifier = await self.begin()
        await self.launch(identifier)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')
        self.assertNotIn(identifier, self.c.requests.waiters)
        self.assertEqual((self.row(identifier)['outcome'], self.row(identifier)['delivery']), ('cancelled', 'abandoned'))
        self.runtime._send.assert_not_awaited()

    async def test_changed_turn_during_create_never_returns_launch_ticket(self):
        task, identifier = await self.begin()
        async def create_after_turn_ends(route, data):
            value = await self.broker.call(route, data)
            if route == '/create':
                self.c.active[11] = 'new-turn'
            return value
        self.bridge.ipc.side_effect = create_after_turn_ends
        with self.assertRaises(RequestUnavailable):
            await self.launch(identifier)
        await self.until(lambda: self.bridge.attempt(identifier)['cleanup'] == 'confirmed')
        self.assertFalse(task.done())
        self.runtime._send.assert_not_awaited()

    async def test_blocked_or_unknown_broker_state_never_produces_false_success(self):
        for changes in ({'state': 'provider_blocked'}, {'state': 'unsupported_challenge'},
                        {'request_id': 'b' * 32}, {'runtime_epoch': 'other-epoch'},
                        {'session_id': 'b' * 32}, {'provider': 'instagram'}, {'simulated': True}):
            with self.subTest(changes=changes):
                task, identifier = await self.begin()
                await self.launch(identifier)
                await self.broker.queues[identifier].put(self.broker.status(
                    identifier, **{'state': 'authenticated', 'cleanup': 'retained',
                                   'session_expires': time.time() + 300, **changes}))
                await self.until(lambda: self.bridge.attempt(identifier)['phase'] == 'uncertain')
                self.assertFalse(task.done())
                self.assertEqual(self.row(identifier)['outcome'], 'pending')
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.runtime._send.assert_not_awaited()

    async def test_nonfinite_deadline_rejects_status_before_persistence_or_native_reply(self):
        for expiry in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(expiry=expiry):
                task, identifier = await self.begin()
                await self.launch(identifier)
                value = self.broker.status(identifier, state='authenticated', cleanup='retained',
                                           session_expires=expiry)
                with self.assertRaises(RequestUnavailable):
                    self.bridge.validate(value, self.row(identifier), self.bridge.attempt(identifier))
                await self.broker.queues[identifier].put(value)
                await self.until(lambda: self.bridge.attempt(identifier)['phase'] == 'uncertain')
                self.assertEqual(self.bridge.attempt(identifier)['session_expires'], 0)
                self.assertEqual(self.row(identifier)['outcome'], 'pending')
                self.assertIsNone(self.row(identifier)['response'])
                self.assertFalse(task.done())
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.runtime._send.assert_not_awaited()

    async def test_active_challenge_keeps_original_request_waiting(self):
        task, identifier = await self.begin('google')
        await self.launch(identifier)
        # Device approval and blocked provider forms retain active broker state;
        # their human-only stage is deliberately absent from the control API.
        await self.broker.queues[identifier].put(self.broker.status(identifier, state='active'))
        await self.until(lambda: self.bridge.attempt(identifier)['phase'] == 'active')
        self.assertFalse(task.done())
        self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.runtime._send.assert_not_awaited()

    async def test_failed_provider_result_resumes_without_authentication_or_browser_access(self):
        task, identifier = await self.begin('instagram')
        await self.launch(identifier)
        result = await self.complete(task, identifier, 'failed', cleanup='confirmed', session_expires=0)
        self.assertEqual(result['outcome'], 'failed')
        self.assertFalse(result['authenticated'])
        self.assertFalse(result['session_available'])
        self.assertIsNone(result['confirmation'])
        with self.assertRaises(RequestUnavailable):
            await self.bridge.use(11, self.native, 'instagram', {'action': 'read'})

    async def test_provider_forms_reject_owner_claimed_success(self):
        for provider in ('google', 'instagram'):
            with self.subTest(provider=provider):
                task, identifier = await self.begin(provider)
                await self.launch(identifier)
                await self.broker.queues[identifier].put(self.broker.status(
                    identifier, state='owner_confirmed', cleanup='retained', session_expires=time.time() + 300))
                await self.until(lambda: self.bridge.attempt(identifier)['phase'] == 'uncertain')
                self.assertFalse(task.done())
                self.assertEqual(self.row(identifier)['outcome'], 'pending')
        self.runtime._send.assert_not_awaited()

    async def test_success_requires_retained_browser_and_bounded_future_deadline(self):
        for changes in ({'cleanup': 'confirmed'}, {'cleanup': 'uncertain'},
                        {'session_expires': time.time() - 1}, {'session_expires': time.time() + 2000}):
            with self.subTest(changes=changes):
                task, identifier = await self.begin()
                await self.launch(identifier)
                result = await self.complete(task, identifier, **changes)
                self.assertEqual(result['outcome'], 'failed')
                self.assertFalse(result['authenticated'])
                self.assertFalse(result['session_available'])
                self.assertIsNone(result['confirmation'])

    async def test_use_requires_original_context_and_exact_generic_request_id(self):
        task, identifier = await self.begin()
        await self.launch(identifier)
        await self.complete(task, identifier)
        for metadata, request_id, chat in (
                ({**self.native, 'threadId': 'other-thread'}, identifier, 11),
                ({**self.native, 'turnId': 'other-turn'}, identifier, 11),
                ({**self.native, 'runtimeEpoch': 'other-runtime'}, identifier, 11),
                (self.native, 'b' * 32, 11), (self.native, None, 11), (self.native, identifier, 22)):
            with self.subTest(metadata=metadata, request_id=request_id, chat=chat), self.assertRaises(RequestUnavailable):
                await self.bridge.use(chat, metadata, 'website', {'action': 'read'}, request_id)
        self.assertFalse(any(route == '/use' for route, _ in self.broker.calls))
        result = await self.c.tools.execute(11, 'oak_account_browser',
                                           {'provider': 'website', 'request_id': identifier, 'action': 'read'}, self.native)
        self.assertEqual(result['text'], 'Synthetic public page')
        self.assertEqual(self.broker.calls[-1], ('/use', {
            'request_id': identifier, 'session_id': 'a' * 32, 'runtime_epoch': self.runtime.runtime_epoch,
            'owner': 11, 'chat_id': 11, 'turn_id': 'original-turn', 'operation': {'action': 'read'}}))
        with self.c.db:
            self.c.db.execute('DELETE FROM web_sessions')
        with self.assertRaises(RequestUnavailable):
            await self.bridge.use(11, self.native, 'website', {'action': 'read'}, identifier)

    async def test_missing_boundary_does_not_advertise_capability_or_open_transport(self):
        self.bridge.ipc = WebsiteLogin.ipc.__get__(self.bridge)
        with patch.object(self.bridge, 'boundary', return_value=False), patch('oak.login.UnixConnector') as connector:
            for provider in ('google', 'instagram', 'website'):
                task, identifier = await self.begin(provider)
                self.assertFalse(json.loads(self.row(identifier)['form'])['capability'])
                with self.assertRaises(RequestUnavailable):
                    await self.launch(identifier)
                self.assertEqual(self.bridge.attempt(identifier)['phase'], 'uncertain')
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            connector.assert_not_called()
        self.bridge.ipc = AsyncMock(side_effect=self.broker.call)

    async def test_no_execution_configuration_or_same_origin_cannot_enable_channel(self):
        with patch.dict(self.c.config, {'execution': None}), self.assertRaises(ValueError):
            WebsiteLogin(self.gateway, self.config)
        with self.assertRaises(ValueError):
            WebsiteLogin(self.gateway, {**self.config, 'origin': self.gateway.public_url})
        self.c.requests.website_login = None
        task, identifier = await self.begin()
        self.assertFalse(json.loads(self.row(identifier)['form'])['capability'])
        await self.c.requests.decide(11, 11, identifier,
                                    {'requestId': 'unavailable-cancel', 'revision': 1, 'decision': 'cancel'})
        await task
        result = json.loads(self.runtime._send.await_args.args[0]['result']['contentItems'][0]['text'])
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['reason'], 'trusted_channel_unavailable')

    async def test_uncertain_create_is_not_replayed_and_cleanup_is_not_claimed(self):
        task, identifier = await self.begin()
        async def unavailable(route, data):
            if route == '/health':
                return await self.broker.call(route, data)
            raise RequestUnavailable('Synthetic unavailable transport')
        self.bridge.ipc.side_effect = unavailable
        with self.assertRaises(RequestUnavailable):
            await self.launch(identifier)
        self.assertEqual(self.bridge.snapshot(identifier)['phase'], 'uncertain')
        with self.assertRaises(RequestUnavailable):
            await self.launch(identifier)
        self.assertEqual(sum(call.args[0] == '/create' for call in self.bridge.ipc.await_args_list), 1)
        await self.bridge.cleanup(identifier)
        self.assertEqual(self.bridge.snapshot(identifier)['cleanup'], 'uncertain')
        self.assertFalse(task.done())
        self.runtime._send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
