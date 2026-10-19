"""Offline upload protocol, immutable evidence and split accounting; no models/codecs."""

import asyncio
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from oak import media_pilot_upload as pilot
from oak import media_pilot_upload_tasks as tasks


def verdict(**changes):
    result = {name: True for name in pilot.JUDGE_SCHEMA['required'] if name != 'issues'}
    result['issues'] = []
    result.update(changes)
    return result


def receipt(kind='private_draft'):
    return {'status': 'completed', 'final': 'Готово: приватну чернетку збережено.',
            'source_immutable': True, 'calls': [],
            'objective': {'passed': True, 'expected_outcome': kind, 'criteria': {},
                          'site_goal_observed': kind == 'private_draft',
                          'goal_completed': kind == 'private_draft'}}


class FakeRuntime:
    """Only native transport is replaced; produce still constructs actual Controller."""

    def __init__(self, *, script=None, status='completed', final='Чернетку збережено.',
                 model=pilot.MODEL, unexpected_tool=None, **kwargs):
        self.kwargs, self.script, self.status = kwargs, script, status
        self.final, self.model, self.unexpected_tool = final, model, unexpected_tool
        self.events = asyncio.Queue()
        self.requests, self.responses = [], []
        self.playing = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        if self.playing:
            await self.playing

    async def request(self, method, params):
        self.requests.append((method, copy.deepcopy(params)))
        if method == 'config/read':
            return {'config': {'mcp_servers': {'example_connector': {'enabled': True}}}}
        if method == 'thread/start':
            return {'model': self.model, 'modelProvider': 'openai', 'thread': {'id': 'fake-thread'}}
        if method == 'turn/start':
            async def play():
                await asyncio.sleep(0)
                if self.script:
                    await self.script(self)
                if self.unexpected_tool:
                    await self.events.put({'method': 'item/started', 'params': {
                        'threadId': 'fake-thread', 'turnId': 'fake-turn',
                        'item': {'type': self.unexpected_tool, 'id': 'unexpected'}}})
                await self.events.put({'method': 'item/completed', 'params': {
                    'threadId': 'fake-thread', 'turnId': 'fake-turn',
                    'item': {'type': 'agentMessage', 'text': self.final, 'phase': 'final_answer'}}})
                await self.events.put({'method': 'turn/completed', 'params': {
                    'threadId': 'fake-thread', 'turnId': 'fake-turn',
                    'turn': {'id': 'fake-turn', 'status': self.status}}})
            self.playing = asyncio.create_task(play())
            return {'turn': {'id': 'fake-turn'}}
        if method == 'turn/interrupt':
            return {}
        raise AssertionError('Unexpected native method: ' + method)

    async def tool(self, name, arguments):
        response = await self.kwargs['tool_handler']({
            'threadId': 'fake-thread', 'turnId': 'fake-turn',
            'tool': name, 'arguments': arguments})
        self.responses.append(response)
        return response


class RuntimeFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.produce_index = 0
        self.task = self.make_task(self.root / 'workspace')
        self.site = Mock()
        self.site.snapshot.return_value = {'uploads': [{'status': 'ready', 'draft_saved': True,
            'visibility': 'private', 'published': False}], 'events': []}
        self.browser = Mock()
        self.browser.run = AsyncMock(return_value={'ok': False, 'action': 'upload',
            'outcome': 'not_attempted', 'error': {'code': 'payload_too_large',
            'stage': 'upload_preflight', 'observed_bytes': 21, 'max_bytes': 20}})
        self.browser.close = AsyncMock()
        self.create = patch.object(tasks, 'create_browser', return_value=(self.browser, self.site))
        self.create.start()
        self.addCleanup(self.create.stop)
        self.objective = patch.object(tasks, 'evaluate_task', return_value={
            'passed': True, 'expected_outcome': 'private_draft', 'criteria': {'content': True}})
        self.evaluate = self.objective.start()
        self.addCleanup(self.objective.stop)

    @staticmethod
    def make_task(workspace):
        inputs = workspace / 'inputs'
        inputs.mkdir(parents=True)
        source = inputs / 'original.mp4'
        source.write_bytes(b'original synthetic fixture bytes')
        specification = inputs / 'task.json'
        specification.write_text('{"quality":"public requirements"}')
        return {'task_id': 'synthetic-task', 'workspace': str(workspace),
                'brief': 'Збережи одну приватну чернетку та оригінал.',
                'source_hashes': {str(p): pilot._file_hash(p) for p in (source, specification)},
                'original_path': str(source), 'specification_path': str(specification),
                'fixture': {'runner_secret': 'HIDDEN_SCENARIO_CANARY'}}

    async def produce(self, arm='structured', **options):
        self.produce_index += 1
        clients = []
        def factory(**kwargs):
            client = FakeRuntime(**options, **kwargs)
            clients.append(client)
            return client
        result = await pilot.produce(self.task, self.root / (arm + '-control-' + str(self.produce_index)), arm,
                                     runtime_home='/synthetic-auth-home', client_factory=factory)
        return result, clients[0]

    async def test_actual_controller_has_identical_capabilities_except_predeclared_procedure(self):
        contexts, capabilities = [], []
        for arm in pilot.ARMS:
            result, client = await self.produce(arm)
            self.assertEqual(result['status'], 'completed')
            self.assertTrue(result['objective']['passed'])
            request = next(p for method, p in client.requests if method == 'thread/start')
            contexts.append(request['developerInstructions'])
            capabilities.append(request['dynamicTools'])
            self.assertEqual(request['config'], {'mcp_servers.example_connector.enabled': False})
            self.assertEqual(request['environments'], [])
            self.assertTrue(request['ephemeral'])
            turn = next(p for method, p in client.requests if method == 'turn/start')
            self.assertEqual((turn['model'], turn['effort']), (pilot.MODEL, 'low'))
            self.assertEqual(client.kwargs['sandbox'], 'read-only')
            for feature in pilot._DISABLED_FEATURES:
                if feature != 'code_mode_host':
                    self.assertFalse(client.kwargs['config']['features.' + feature])
            self.assertNotIn('HIDDEN_SCENARIO_CANARY', json.dumps(client.requests))
        self.assertEqual(contexts[0], contexts[1])
        self.assertEqual(contexts[2], contexts[0] + '\nReusable recovery procedure:\n' + pilot.PROCEDURE)
        self.assertEqual(capabilities[0], capabilities[1])
        self.assertEqual(capabilities[1], capabilities[2])
        self.assertEqual({s['name'] for s in capabilities[0]}, pilot._DYNAMIC)

    async def test_baseline_class_only_vs_structured_is_actual_native_envelope_difference(self):
        async def script(client):
            await client.tool('oak_browser', {'action': 'upload', 'selector': '#file',
                                            'files': ['/workspace/inputs/original.mp4']})
        baseline, old = await self.produce('baseline', script=script)
        structured, new = await self.produce('structured', script=script)
        self.assertEqual(old.responses, [{'success': False, 'contentItems': [
            {'type': 'inputText', 'text': 'ValueError'}]}])
        data = json.loads(new.responses[0]['contentItems'][0]['text'])
        self.assertFalse(new.responses[0]['success'])
        self.assertEqual(data['error']['code'], 'payload_too_large')
        self.assertEqual(data['outcome'], 'not_attempted')
        self.assertEqual(baseline['calls'][0]['result'], {'ok': False, 'error': 'ValueError'})
        self.assertTrue(structured['source_immutable'])
        self.assertEqual(self.browser.run.await_args.kwargs['files'], [self.task['original_path']])

    async def test_source_change_and_missing_file_latch_failure_even_if_site_grader_passes(self):
        for mutation in ('change', 'delete'):
            with self.subTest(mutation=mutation):
                source = Path(self.task['original_path'])
                source.write_bytes(b'original synthetic fixture bytes')
                async def script(client):
                    if mutation == 'change':
                        source.write_bytes(b'mutated')
                    else:
                        source.unlink()
                    await client.tool('oak_experience', {'action': 'search', 'query': 'upload'})
                result, _ = await self.produce(mutation if mutation in pilot.ARMS else 'structured', script=script)
                self.assertFalse(result['source_immutable'])
                self.assertFalse(result['objective']['passed'])
                self.assertFalse(result['objective']['criteria']['tool_scope_compliant'])
                self.assertTrue(result['objective']['site_goal_observed'])
                self.assertFalse(result['objective']['goal_completed'])

    async def test_interrupted_native_status_and_final_reply_survive_objective_failure(self):
        result, client = await self.produce(status='interrupted', final='Спробу перервано.')
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['final'], 'Спробу перервано.')
        self.assertFalse(result['objective']['passed'])
        self.assertFalse(result['objective']['goal_completed'])
        self.assertTrue(any(method == 'turn/interrupt' for method, _ in client.requests))
        self.browser.close.assert_awaited()
        self.site.close.assert_called()

    async def test_grader_exception_preserves_completed_native_receipt_and_fails_closed(self):
        self.evaluate.side_effect = RuntimeError('sensitive scorer detail')
        result, _ = await self.produce()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['final'], 'Чернетку збережено.')
        self.assertFalse(result['objective']['passed'])
        self.assertEqual(result['objective']['error_code'], 'independent_objective_unavailable')
        self.assertNotIn('sensitive scorer detail', json.dumps(result))

    async def test_native_shell_attempt_is_a_policy_failure_even_if_service_goal_exists(self):
        result, _ = await self.produce(unexpected_tool='commandExecution')
        self.assertEqual(result['error_code'], 'producer_tool_violation')
        self.assertFalse(result['objective']['passed'])
        self.assertFalse(result['objective']['criteria']['tool_scope_compliant'])

    async def test_unexpected_producer_model_fails_before_turn_dispatch(self):
        result, client = await self.produce(model='unexpected-model')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['objective']['passed'])
        self.assertFalse(any(method == 'turn/start' for method, _ in client.requests))

    async def test_tool_binding_and_budget_fail_closed_without_browser_side_effect(self):
        tools = pilot._UploadTools(self.task, self.root, 'structured')
        tools.thread_id, tools.turn_id = 'thread', 'turn'
        wrong = await tools.handle({'threadId': 'other', 'turnId': 'turn',
                                    'tool': 'oak_browser', 'arguments': {'action': 'read'}})
        self.assertFalse(wrong['success'])
        self.assertTrue(tools.violation)
        tools.violation = False
        tools.calls = [{}] * 40
        denied = await tools.handle({'threadId': 'thread', 'turnId': 'turn',
                                     'tool': 'oak_browser', 'arguments': {'action': 'read'}})
        self.assertFalse(denied['success'])
        self.assertTrue(tools.violation)
        self.browser.run.assert_not_awaited()
        tools.calls = []
        tools.violation = False
        denied = await tools.handle({'threadId': 'thread', 'turnId': 'turn', 'tool': 'oak_browser',
                                     'arguments': {'action': 'upload', 'files': ['/private/other-task.mp4']}})
        self.assertFalse(denied['success'])
        self.assertTrue(tools.violation)
        self.browser.run.assert_not_awaited()

    async def test_judge_is_separate_fixed_schema_tool_free_and_rejects_wrong_model(self):
        clients = []
        def factory(**kwargs):
            client = FakeRuntime(final=json.dumps(verdict()), **kwargs)
            clients.append(client)
            return client
        result = await pilot.judge(self.task, receipt(), client_factory=factory)
        self.assertEqual(result, {'status': 'verified', **verdict()})
        client = clients[0]
        self.assertNotIn('tool_handler', client.kwargs)
        self.assertTrue(all(client.kwargs['config']['features.' + feature] is False
                            for feature in pilot._DISABLED_FEATURES))
        thread = next(p for method, p in client.requests if method == 'thread/start')
        self.assertEqual(thread['developerInstructions'], pilot.JUDGE_POLICY)
        self.assertEqual(thread['config'], {'mcp_servers.example_connector.enabled': False})
        turn = next(p for method, p in client.requests if method == 'turn/start')
        self.assertEqual(turn['outputSchema'], pilot.JUDGE_SCHEMA)
        self.assertNotIn('HIDDEN_SCENARIO_CANARY', json.dumps(turn))
        with self.assertRaises(ValueError):
            await pilot.judge(self.task, receipt(), client_factory=lambda **k: FakeRuntime(model='unexpected', **k))

    async def test_judge_invalid_boolean_tool_use_and_incomplete_producer_do_not_pass(self):
        for kwargs in ({'final': json.dumps(verdict(evidence_based=1))},
                       {'unexpected_tool': 'mcpToolCall', 'final': json.dumps(verdict())}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await pilot.judge(self.task, receipt(), client_factory=lambda **k: FakeRuntime(**kwargs, **k))
        incomplete = receipt()
        incomplete['status'] = 'interrupted'
        factory = Mock(side_effect=AssertionError('No judge request for incomplete producer'))
        self.assertEqual((await pilot.judge(self.task, incomplete, client_factory=factory))['status'], 'unverified')
        factory.assert_not_called()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'experiment'
        patcher = patch.object(pilot, '_implementation', return_value='sealed-source')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = pilot.initialize(self.root)
        self.materializer = patch.object(tasks, 'materialize_task', side_effect=self.materialize)
        self.materialize_mock = self.materializer.start()
        self.addCleanup(self.materializer.stop)
        self.producer = patch.object(pilot, 'produce', new_callable=AsyncMock)
        self.produce_mock = self.producer.start()
        self.produce_mock.side_effect = lambda task, *_args, **_kwargs: receipt(task['goal_kind'])
        self.addCleanup(self.producer.stop)
        judge = patch.object(pilot, 'judge', new_callable=AsyncMock,
                             return_value={'status': 'verified', **verdict()})
        self.judge_mock = judge.start()
        self.addCleanup(judge.stop)

    def materialize(self, task_id, workspace):
        task = RuntimeFixture.make_task(Path(workspace))
        task.update(task_id=task_id, goal_kind=tasks.goal_kind(task_id))
        return task

    async def run_batch(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return await pilot.run(self.root, **kwargs)

    async def test_full_plan_has_28_jobs_and_batch_schedules_no_unscheduled_work(self):
        self.assertEqual(len(pilot.jobs('dev')), 12)
        self.assertEqual(len(pilot.jobs('holdout')), 16)
        self.assertEqual({a for _, a, _ in pilot.jobs('holdout')}, {'baseline', 'procedure'})
        first = await self.run_batch(batch_size=1)
        self.assertFalse(first['complete'])
        self.assertEqual(self.produce_mock.await_count, 1)
        saved = pilot.load(self.root)['runs']
        self.assertEqual(len(saved), 1)
        row = next(iter(saved.values()))
        self.assertEqual(len(Path(row['directory']).parent.name), 24)
        self.assertNotIn(row['task_id'], row['directory'])
        self.assertNotIn(row['arm'], str(Path(row['directory']).relative_to(self.root)))
        await self.run_batch(batch_size=12)
        self.assertEqual(self.produce_mock.await_count, 12)
        pilot.freeze(self.root)
        await self.run_batch(split='holdout', batch_size=12)
        final = await self.run_batch(split='holdout', batch_size=12)
        self.assertEqual(self.produce_mock.await_count, 28)
        self.assertTrue(final['complete'])
        summary = final['summaries']['holdout']['procedure']
        self.assertEqual(summary['expected'], 8)
        self.assertEqual(summary['eligible_goal_expected'], 6)
        self.assertEqual(summary['joint_goal_passed'], 6)
        self.assertEqual(summary['correctly_blocked'], 2)
        self.assertEqual(summary['blocked_communication_passed'], 2)

    async def test_holdout_and_freeze_require_all_dev_and_do_not_materialize_heldout(self):
        with self.assertRaises(ValueError):
            await self.run_batch(split='holdout')
        with self.assertRaises(ValueError):
            pilot.freeze(self.root)
        self.materialize_mock.assert_not_called()
        self.state['frozen'] = True
        self.state['frozen_procedure_sha256'] = pilot._sha(pilot.PROCEDURE.encode())
        pilot._write(self.root / 'state.json', self.state)
        with self.assertRaises(ValueError):
            await self.run_batch(split='holdout')
        self.materialize_mock.assert_not_called()

    async def test_produced_checkpoint_resumes_judge_without_rerunning_native_producer(self):
        self.judge_mock.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.run_batch()
        self.assertEqual(self.produce_mock.await_count, 1)
        state = pilot.load(self.root)
        row = next(iter(state['runs'].values()))
        self.assertEqual(row['state'], 'produced')
        self.judge_mock.side_effect = None
        await self.run_batch()
        self.assertEqual(self.produce_mock.await_count, 1)
        self.assertEqual(next(iter(pilot.load(self.root)['runs'].values()))['state'], 'finished')

    async def test_tampered_cached_producer_or_runner_task_is_rejected(self):
        self.judge_mock.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.run_batch()
        state = pilot.load(self.root)
        row = next(iter(state['runs'].values()))
        task_path = Path(row['directory']) / 'task-runner.json'
        task_path.write_text('{}')
        with self.assertRaises(ValueError):
            await self.run_batch()
        row['receipt']['final'] = 'Tampered final'
        pilot._write(self.root / 'state.json', state)
        with self.assertRaises(ValueError):
            pilot.load(self.root)
        self.assertEqual(self.produce_mock.await_count, 1)

    async def test_judge_transport_exception_finishes_unverified_preserving_native_receipt(self):
        self.judge_mock.side_effect = RuntimeError('sensitive transport detail')
        await self.run_batch()
        row = next(iter(pilot.load(self.root)['runs'].values()))
        self.assertEqual(row['state'], 'finished')
        self.assertEqual(row['receipt']['status'], 'completed')
        self.assertEqual(row['communication']['status'], 'unverified')
        self.assertNotIn('sensitive transport detail', json.dumps(row))

    async def test_invalid_batch_split_and_changed_procedure_are_rejected(self):
        for size in (0, -1, 13, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                await self.run_batch(batch_size=size)
        with self.assertRaises(ValueError):
            pilot.jobs('unknown')
        with patch.object(pilot, 'PROCEDURE', 'Changed procedure'), self.assertRaises(ValueError):
            pilot.load(self.root)
        self.produce_mock.assert_not_awaited()


class FingerprintTests(unittest.TestCase):
    def test_disabled_feature_dependency_is_part_of_source_seal(self):
        names = []
        def digest(path):
            names.append(Path(path).name)
            return 'source-digest'
        with patch.object(pilot, '_file_hash', side_effect=digest):
            pilot._implementation()
        self.assertIn('quality.py', names)
        self.assertIn('browser.py', names)
        self.assertIn('media_pilot_upload_tasks.py', names)


if __name__ == '__main__':
    unittest.main()
