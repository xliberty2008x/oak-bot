"""Controller task injection and native pilot protocol tests, without models."""

import asyncio
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.harness_versions import HarnessVersions
from oak.media_pilot_runtime import PilotRuntime
from test_media_pilot_runtime import FakeFactory, completed


class ControllerHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'harness.sqlite'
        self.store = HarnessVersions(self.path)
        self.addCleanup(self.store.close)
        self.scope = ('harness-project', 7, 'media-depth')
        self.first = self.store.bootstrap_current(*self.scope, 'FIRST_VERSION_SOURCE_INSPECTION')
        self.config = {'harness_activation': {'store_path': str(self.path),
                                             'project_id': self.scope[0], 'domain': self.scope[2]}}
        self.client = SimpleNamespace(request=AsyncMock(return_value={'turn': {'id': 'native-turn-1'}}))
        self.controller = self.make_controller(self.config)

    def make_controller(self, config):
        controller = Controller(self.client, self.root / 'controller.sqlite', self.root, AsyncMock(), config=config)
        controller._thread = AsyncMock(return_value='existing-thread')
        self.addCleanup(controller.close)
        return controller

    def new_accepted(self):
        candidate = self.store.register(*self.scope, 'SECOND_VERSION_CONTOUR_INSPECTION', parent_id=self.first['id'])
        self.store.record_decision(candidate['id'], accepted=True, evidence={'kind': 'controlled-test-receipt'},
                                   kind='controlled-fixture')
        self.store.activate(candidate['id'])
        return candidate

    async def test_real_submit_dispatches_active_procedure_and_turn_provenance(self):
        async def acknowledge(method, params):
            self.assertEqual(method, 'turn/start')
            # The intent exists durably before the runtime is asked to act.
            prepared = self.store.applications(*self.scope)
            self.assertEqual(len(prepared), 1)
            self.assertIsNone(prepared[0]['turn_id'])
            self.assertEqual(prepared[0]['version_id'], self.first['id'])
            self.assertIn(self.first['procedure'], params['input'][-1]['text'])
            self.assertIn('bootstrap-current', params['input'][-1]['text'])
            return {'turn': {'id': 'native-turn-1'}}
        self.client.request.side_effect = acknowledge
        self.assertTrue(await self.controller.submit(7, 'Create the next media artifact.', 101))
        rows = self.store.applications(*self.scope)
        self.assertEqual(rows[0]['turn_id'], 'native-turn-1')
        self.assertEqual(self.controller.db.execute('SELECT turn_id FROM input_runs WHERE update_id=101').fetchone()[0],
                         'native-turn-1')
        # Existing input deduplication still prevents a second native turn.
        await self.controller.submit(7, 'Create the next media artifact.', 101)
        self.assertEqual(self.client.request.await_count, 1)

    async def test_loaded_thread_and_rollback_use_current_version_on_each_new_input(self):
        second = self.new_accepted()
        await self.controller.submit(7, 'Create one image.', 201)
        self.assertIn(second['procedure'], self.client.request.call_args.args[1]['input'][-1]['text'])
        self.store.rollback(*self.scope, reason='Controlled regression.')
        self.controller.active.clear()
        self.client.request.return_value = {'turn': {'id': 'native-turn-2'}}
        await self.controller.submit(7, 'Create another image.', 202)
        payload = self.client.request.call_args.args[1]
        self.assertIn(self.first['procedure'], payload['input'][-1]['text'])
        self.assertNotIn(second['procedure'], payload['input'][-1]['text'])
        self.assertEqual([row['version_id'] for row in self.store.applications(*self.scope)],
                         [second['id'], self.first['id']])

    async def test_other_chat_and_unconfigured_controller_do_not_receive_procedure(self):
        await self.controller.submit(8, 'Create an image.', 301)
        self.assertNotIn(self.first['procedure'], str(self.client.request.call_args))
        self.assertEqual(self.store.applications(self.scope[0], 8, self.scope[2]), [])
        off = self.make_controller({})
        self.client.request.reset_mock()
        await off.submit(7, 'Create an image.', 302)
        self.assertNotIn('harness', str(self.client.request.call_args))
        self.assertEqual(self.store.applications(*self.scope), [])

    async def test_model_mutable_experience_cannot_change_active_pointer(self):
        lesson = self.controller.memory.save_experience(7, 'media-depth', 'Unrelated lesson', 'Image creation',
                                                         'MODEL_WRITABLE_PROCEDURE')
        self.controller.memory.forget_experience(7, lesson['id'])
        await self.controller.submit(7, 'Create an image.', 401)
        self.assertEqual(self.store.resolve(*self.scope)['id'], self.first['id'])
        self.assertIn(self.first['procedure'], self.client.request.call_args.args[1]['input'][-1]['text'])

    async def test_dispatch_disconnect_retains_unbound_provenance_and_uncertain_input(self):
        self.client.request.side_effect = ConnectionError('Runtime disconnected.')
        with self.assertRaises(ConnectionError):
            await self.controller.submit(7, 'Create an image.', 501)
        self.assertEqual(self.controller.db.execute('SELECT status FROM inputs WHERE update_id=501').fetchone()[0],
                         'uncertain')
        self.assertIsNone(self.store.applications(*self.scope)[0]['turn_id'])
        await self.controller.submit(7, 'Create an image.', 501)
        self.assertEqual(self.client.request.await_count, 1)


class PilotHarnessActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_pilot_uses_controller_activation_hook_and_records_exact_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'task'
            workspace.mkdir()
            path = root / 'private-harness.sqlite'
            store = HarnessVersions(path)
            try:
                active = store.bootstrap_current('harness2-demo', 1, 'media-depth', 'ACTIVE_NATIVE_PROCEDURE_MARKER')

                async def plan(client):
                    client.events.put_nowait(completed())

                factory = FakeFactory(plan)
                runtime = PilotRuntime({'harness_activation': {'store_path': str(path),
                    'project_id': 'harness2-demo', 'domain': 'media-depth'}},
                    runtime_home='/explicit-authorized-runtime-home', timeout=2, client_factory=factory)
                result = await runtime.run({'id': 'protocol-fixture', 'brief': 'Inspect this empty fixture.'},
                                           workspace, 'EXISTING_CANDIDATE_INJECTION')
                # This protocol fixture delivers no artifact; activation does
                # not turn an incomplete media result into a passing delivery.
                self.assertEqual(result['status'], 'unverified')
                self.assertFalse(result['delivered'])
                producer = factory.clients[0]
                start = next(params for method, params in producer.calls if method == 'thread/start')
                turn = next(params for method, params in producer.calls if method == 'turn/start')
                self.assertIn('EXISTING_CANDIDATE_INJECTION', start['developerInstructions'])
                self.assertIn(active['procedure'], turn['input'][-1]['text'])
                application = result['harness_application']
                self.assertEqual(application['version_id'], active['id'])
                self.assertEqual(application['turn_id'], 'producer-turn')
                self.assertEqual(application['context_sha256'],
                    hashlib.sha256(turn['input'][-1]['text'].encode()).hexdigest())
                self.assertEqual(store.applications('harness2-demo', 1, 'media-depth')[0]['turn_id'],
                                 'producer-turn')
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
