"""Pinned-method checks and mocked subscription protocol; no model calls."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from oak.harness2_upstream import (COMMIT, NativeRoleSpec, SubscriptionRole,
                                   _ContextTools, _files_digest, _snapshot,
                                   run_development, upstream)
from oak.runtime import MODEL


class FakeRuntime:
    calls = []
    fail = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = asyncio.Queue()
        self.tool_handler = kwargs['tool_handler']
        type(self).calls.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def request(self, method, params):
        if method == 'config/read':
            return {'config': {'mcp_servers': {'must_disable': {'enabled': True}}}}
        if method == 'thread/start':
            self.thread_params = params
            return {'thread': {'id': 'thread'}, 'model': MODEL, 'modelProvider': 'openai'}
        if method == 'turn/start':
            self.turn_params = params
            if self.fail:
                raise ConnectionError('not copied to public error')
            text = params['input'][0]['text']
            if 'EXPERIENCE' in text or '<experience>' in text:
                answer = ('<experience>\ncue: source has signed entries\nlesson: retain both signs\n'
                          'evidence: INV and DOM retained the negative entry in native output\n'
                          'confidence: local observed contrast\n</experience>')
            else:
                result = await self.tool_handler({'tool': 'oak_h2_write', 'threadId': 'thread',
                    'turnId': 'turn', 'arguments': {'path': 'context/harness/AGENTS.md',
                                                   'content': 'Keep signed values.\n'}})
                if not result['success']:
                    raise ValueError('fixture rejected')
                answer = '<notes>Preserve signed values from the source.</notes>'
            for event in (
                {'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn',
                    'item': {'type': 'agentMessage', 'text': answer, 'phase': 'final_answer'}}},
                {'method': 'turn/completed', 'params': {'threadId': 'thread', 'turnId': 'turn',
                    'turn': {'id': 'turn', 'status': 'completed'}}},
            ):
                await self.events.put(event)
            return {'turn': {'id': 'turn'}}
        return {}


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.context = self.root / 'source'
        (self.context / 'harness').mkdir(parents=True)
        (self.context / 'harness/AGENTS.md').write_text('Base procedure.\n')
        (self.context / 'task.md').write_text('Reconcile signed amounts.')
        FakeRuntime.calls = []
        FakeRuntime.fail = False

    def tearDown(self):
        self.tmp.cleanup()

    def role(self, **kwargs):
        return SubscriptionRole(self.root / 'audit', client_factory=FakeRuntime, **kwargs)

    def test_unmodified_pinned_core_and_honest_channel(self):
        core = upstream()
        self.assertEqual(core.commit, COMMIT)
        self.assertIn('/third_party/harness2/', core.schedule.__file__)
        self.assertEqual(NativeRoleSpec().kind, 'oak_chatgpt_subscription')
        with self.assertRaises(ValueError):
            NativeRoleSpec(model='other')
        with self.assertRaises(ValueError):
            NativeRoleSpec(effort='high')

    def test_native_contract_auth_policy_harvest_and_restart_cache(self):
        reservations = []
        role = self.role(budget_hook=lambda kind, identity: reservations.append((kind, identity)))
        reply = role.call('Edit the harness.', self.context, 'inv1.a1', harvest='context/harness')
        self.assertTrue(reply.ok)
        self.assertEqual((reply.workspace / 'AGENTS.md').read_text(), 'Keep signed values.\n')
        self.assertEqual(len(FakeRuntime.calls), 1)
        client = FakeRuntime.calls[0]
        self.assertEqual(client.kwargs['sandbox'], 'read-only')
        self.assertEqual(client.thread_params['model'], MODEL)
        self.assertTrue(client.thread_params['ephemeral'])
        self.assertFalse(client.thread_params['config']['mcp_servers.must_disable.enabled'])
        self.assertFalse(client.kwargs['config']['features.shell_tool'])
        self.assertEqual(client.turn_params['effort'], 'low')
        self.assertEqual(set(spec['name'] for spec in client.thread_params['dynamicTools']),
                         {'oak_h2_list', 'oak_h2_read', 'oak_h2_write', 'oak_h2_view'})
        self.assertEqual([kind for kind, _ in reservations], ['role', 'role_tool'])
        another = self.role(budget_hook=lambda *_: self.fail('cached role reserved budget'))
        cached = another.call('Edit the harness.', self.context, 'inv1.a1', harvest='context/harness')
        self.assertTrue(cached.cached)
        self.assertEqual(len(FakeRuntime.calls), 1)
        self.assertEqual(cached.workspace, reply.workspace)
        receipt = json.loads(reply.audit.read_text())
        self.assertEqual(receipt['accounting'], 'chatgpt_subscription_no_usd_meter')
        self.assertIsNone(receipt['usd'])

    def test_cache_identity_changes_with_evidence_and_detects_mutation(self):
        role = self.role()
        reply = role.call('Edit.', self.context, 'inv1', harvest='context/harness')
        (self.context / 'task.md').write_text('A new task.')
        role.call('Edit.', self.context, 'inv1', harvest='context/harness')
        self.assertEqual(len(FakeRuntime.calls), 2)
        (reply.workspace / 'AGENTS.md').write_text('tampered')
        (self.context / 'task.md').write_text('Reconcile signed amounts.')
        with self.assertRaisesRegex(ValueError, 'harness changed'):
            role.call('Edit.', self.context, 'inv1', harvest='context/harness')

    def test_interrupted_attempt_retained_and_fresh_workspace(self):
        role = self.role()
        reply = role.call('Edit.', self.context, 'dom1', harvest='context/harness')
        attempt = reply.audit.parent
        (attempt.parent / 'complete.json').unlink()
        receipt = json.loads(reply.audit.read_text())
        receipt['status'] = 'running'
        reply.audit.write_text(json.dumps(receipt))
        new = self.role().call('Edit.', self.context, 'dom1', harvest='context/harness')
        self.assertNotEqual(new.workspace, reply.workspace)
        self.assertEqual(json.loads(reply.audit.read_text())['status'], 'interrupted_unknown')
        self.assertEqual(json.loads(new.audit.read_text())['attempt'], 2)

    def test_completion_index_crash_recovers_without_another_native_call(self):
        reply = self.role().call('Edit.', self.context, 'inv1', harvest='context/harness')
        (reply.audit.parent.parent / 'complete.json').unlink()
        cached = self.role(budget_hook=lambda *_: self.fail('completed role reserved again')).call(
            'Edit.', self.context, 'inv1', harvest='context/harness')
        self.assertTrue(cached.cached)
        self.assertEqual(len(FakeRuntime.calls), 1)

    def test_failed_native_attempt_reported_not_cached(self):
        FakeRuntime.fail = True
        role = self.role()
        with self.assertRaises(upstream().proposer.ProposerError):
            role.call('Edit.', self.context, 'inv1')
        receipt = next((self.root / 'audit').rglob('receipt.json'))
        self.assertEqual(json.loads(receipt.read_text())['status'], 'failed')
        self.assertNotIn('not copied', receipt.read_text())
        FakeRuntime.fail = False
        reply = self.role().call('Edit.', self.context, 'inv1')
        self.assertEqual(json.loads(reply.audit.read_text())['attempt'], 2)

    def test_budget_stops_before_native_launch(self):
        def stop(*_):
            raise ValueError('persistent budget exhausted')
        with self.assertRaisesRegex(ValueError, 'persistent budget'):
            self.role(budget_hook=stop).call('Edit.', self.context, 'inv1')
        self.assertFalse(FakeRuntime.calls)

    def test_shared_native_lock_serializes_uncached_roles(self):
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        guard = threading.Lock()
        running = maximum = 0
        async def native(role, *_):
            nonlocal running, maximum
            with guard:
                running += 1
                maximum = max(maximum, running)
            await asyncio.sleep(0.03)
            with guard:
                running -= 1
            return '<notes>bounded fixture</notes>', {}
        lock = self.root / 'shared-native.lock'
        first = self.role(native_lock_path=lock)
        second = self.role(native_lock_path=lock)
        with patch.object(SubscriptionRole, '_native', native), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(role.call, 'Read the context.', self.context, label)
                       for role, label in ((first, 'inv1'), (second, 'dom1'))]
            self.assertTrue(all(future.result().ok for future in futures))
        self.assertEqual(maximum, 1)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_path_scope_symlink_grader_and_write_invariants(self):
        files = _snapshot(self.context, 10000)
        tools = _ContextTools(files, self.context, ('AGENTS.md',), 100, 8, None, {})
        tools.thread_id = 't'
        for path in ('../secret', '/etc/passwd', 'context/../task.md', 'task.md'):
            result = asyncio.run(tools.handle({'tool': 'oak_h2_write', 'threadId': 't',
                'arguments': {'path': path, 'content': 'mutated'}}))
            self.assertFalse(result['success'])
        self.assertEqual((self.context / 'task.md').read_text(), 'Reconcile signed amounts.')
        (self.context / 'link').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'links'):
            _snapshot(self.context, 10000)
        (self.context / 'link').unlink()
        (self.context / 'task.md').write_text('deterministic_eval_result private')
        with self.assertRaisesRegex(ValueError, 'grader verdict'):
            self.role().call('Edit.', self.context, 'inv1')

    def test_view_returns_actual_image_and_tool_budget_is_fail_closed(self):
        import io
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (3, 2), 'red').save(stream, 'PNG')
        files = {'picture.png': stream.getvalue()}
        tools = _ContextTools(files, self.context, ('AGENTS.md',), 100, 1, None, {})
        tools.thread_id = 't'
        result = asyncio.run(tools.handle({'tool': 'oak_h2_view', 'threadId': 't',
                                          'arguments': {'path': 'picture.png'}}))
        self.assertTrue(result['success'])
        self.assertTrue(result['contentItems'][1]['imageUrl'].startswith('data:image/png;base64,'))
        result = asyncio.run(tools.handle({'tool': 'oak_h2_list', 'threadId': 't', 'arguments': {}}))
        self.assertFalse(result['success'])
        self.assertTrue(tools.violation)

    def test_real_upstream_two_tasks_reflection_library_and_resume(self):
        core = upstream()
        root = self.root
        surface = core.tree.SurfaceSpec(name='codex', always_on=('AGENTS.md',), always_on_max=12000,
            always_on_cap_reason='bounded procedure', skill_dir='skills', skill_name_rule='matches_dir',
            trigger_hint='', autotrigger_file='AGENTS.md', trigger_line='', subagent_requires_mode=False,
            plugin_dir=None, hooks_file=None, reject_empty_always_on=True)
        class Adapter:
            def __init__(self):
                self.substrate = type('Substrate', (), {'name': 'oak', 'surface': lambda _: surface})()
                self.spec = core.spec.BenchSpec(name='oak', runs_root=root / 'runs',
                    install_mode='workspace_config', prompts=core.spec.BenchPrompts(work='reconcile signed amounts'),
                    channels={'proposer': NativeRoleSpec(), 'selector': NativeRoleSpec()})
                self.executions = 0
            def domain_of(self, _): return 'ledger'
            def task_prompt(self, _): return 'Return the signed total for the input.'
            def task_files(self, _): return None
            def base_harness(self): return root / 'source/harness'
            def validate_tree(self, tree):
                if set(tree) != {'AGENTS.md'}: raise ValueError('Unsupported component.')
            def execute(self, task_id, harness_dir, run_id, **kwargs):
                path = root / 'executions' / run_id
                if (path / 'answer.txt').is_file(): return path
                path.mkdir(parents=True)
                self.executions += 1
                (path / 'answer.txt').write_text('Total: 80.15')
                return path
            def delivered(self, path): return (path / 'answer.txt').is_file()
            def trajectory(self, path, **kwargs): return '[ACTION] sum signed amounts\n[OBSERVATION] Total: 80.15'
            def deliverable_text(self, path): return (path / 'answer.txt').read_text()
            def deliverable_files(self, path): return {'answer.txt': self.deliverable_text(path)}
        adapter = Adapter()
        # FakeRuntime recognizes only this explicit flag; native protocol and
        # all actual upstream method functions still run for every role.
        original_native = SubscriptionRole._native
        async def native(role, runtime_dir, tools, mission, system, duration):
            if '<experience>' in mission:
                mission = 'EXPERIENCE\n' + mission
            return await original_native(role, runtime_dir, tools, mission, system, duration)
        from unittest.mock import patch
        with patch.object(SubscriptionRole, '_native', native):
            first = run_development('task-1', adapter=adapter, role=self.role(),
                                    work_dir=root / 'cycle1', library_dir=root / 'library')
            second = run_development('task-2', adapter=adapter, role=self.role(),
                                     work_dir=root / 'cycle2', library_dir=root / 'library')
            self.assertEqual(first.status, 'ok', first.to_dict())
            self.assertEqual(second.status, 'ok', second.to_dict())
            self.assertEqual(first.oak_provenance['library_entries_before'], [])
            self.assertEqual(second.oak_provenance['library_entries_before'], ['task-1'])
            self.assertEqual(second.oak_provenance['library_entries_after'], ['task-1', 'task-2'])
            self.assertEqual(adapter.executions, 8)
            self.assertEqual(first.modes['seq']['ledger']['R'], 4)
            self.assertEqual(first.modes['seq']['ledger']['P'], 3)
            self.assertTrue(Path(second.oak_provenance['chosen_harness']).is_dir())
            inv_context = root / 'cycle2/runs/oak-oak-seq1/task-2/context/inv1/library/harnesses/task-1'
            self.assertTrue((inv_context / 'EXPERIENCE.md').is_file())
            count = len(FakeRuntime.calls)
            resumed = run_development('task-2', adapter=adapter, role=self.role(),
                                      work_dir=root / 'cycle2', library_dir=root / 'library')
            self.assertEqual(resumed.status, 'skipped')
            self.assertEqual(adapter.executions, 8)
            self.assertEqual(len(FakeRuntime.calls), count)

    def test_outer_cycle_genuine_upstream_and_completed_restart_protocol(self):
        """Orchestration integration only: media/model observations are fixtures."""
        from unittest.mock import patch
        from oak.harness2_pipeline import PipelineStore, default_config, digest, run_cycle
        core = upstream()
        root = self.root
        surface = core.tree.SurfaceSpec(name='codex', always_on=('AGENTS.md',), always_on_max=12000,
            always_on_cap_reason='bounded procedure', skill_dir='skills', skill_name_rule='must_match',
            trigger_hint='', autotrigger_file='AGENTS.md', trigger_line='', subagent_requires_mode=False,
            plugin_dir=None, hooks_file=None)
        descriptors = [{'id': name, 'split': split, 'family': 'choreography'}
                       for split, names in (('dev', ['dev']), ('regression', ['reg1', 'reg2']),
                                            ('holdout', ['hold1', 'hold2'])) for name in names]
        commitments = {row['id']: {'inputs': {'source.png': digest(row['id'])},
                                  'sha256': digest(['source', row['id']])} for row in descriptors}
        manifest = {split: [{'descriptor': row, 'commitment': commitments[row['id']]}
                           for row in descriptors if row['split'] == split]
                    for split in ('dev', 'regression', 'holdout')}
        class Adapter:
            executions = 0
            def __init__(self, path, descriptors, **kwargs):
                self.root = Path(path)
                self.root.mkdir(parents=True, exist_ok=True)
                self.descriptors = {row['id']: row for row in descriptors}
                self.kwargs = kwargs
                self.substrate = type('Substrate', (), {'name': 'mocked_media', 'surface': lambda _: surface})()
                self.spec = core.spec.BenchSpec(name='oak', runs_root=self.root / 'runs',
                    install_mode='workspace_config', prompts=core.spec.BenchPrompts(work='reconcile signed amounts'),
                    channels={'proposer': NativeRoleSpec(), 'selector': NativeRoleSpec()})
                self.base_harness().mkdir(exist_ok=True)
                (self.base_harness() / 'AGENTS.md').write_text(kwargs.get('baseline_procedure', ''))
            def domain_of(self, _): return 'choreography'
            def task_prompt(self, _): return 'Return the signed total for the input.'
            def task_files(self, _):
                path = self.root / 'optimizer-source'
                path.mkdir(exist_ok=True)
                return path
            def _task(self, _): return {}
            def _procedure(self, path): return (Path(path) / 'AGENTS.md').read_text()
            def base_harness(self): return self.root / 'base'
            def validate_tree(self, tree):
                if set(tree) != {'AGENTS.md'}: raise ValueError('Unsupported component.')
            def execute(self, task_id, harness_dir, run_id, **kwargs):
                path = self.root / 'executions' / digest(run_id)
                if (path / 'answer.txt').is_file(): return path
                hook = self.kwargs.get('budget_hook')
                if hook: hook('producer', {'run_id': run_id, 'attempt': 1})
                path.mkdir(parents=True)
                type(self).executions += 1
                (path / 'answer.txt').write_text('Total: 80.15')
                return path
            def delivered(self, path): return (path / 'answer.txt').is_file()
            def trajectory(self, path, **kwargs): return '[ACTION] sum signed amounts\n[OBSERVATION] Total: 80.15'
            def deliverable_text(self, path): return (path / 'answer.txt').read_text()
            def deliverable_files(self, path): return {'answer.txt': self.deliverable_text(path)}
            def score(self, path, task_id):
                return {'status': 'completed', 'source_immutable': True, 'delivered': True,
                        'unverified': False, 'material_defect': False, 'elapsed_seconds': 1,
                        'tool_calls': 1, 'artifact_sha256': digest('exact-delivered-fixture'),
                        'joint_passed': True, 'visual_passed': True, 'mechanical_passed': True,
                        'model_valid': True, 'budget_valid': True}
        store = PipelineStore(root / 'pipeline', 'project', 1)
        store.record_case({'visibility': 'public_fixture', 'task': 'fixture'},
                          signature='signed-source', approved=True)
        cycle = store.create_cycle('signed-source', manifest, default_config())
        try:
            with patch('oak.harness2_media.MediaBenchmarkAdapter', Adapter), patch(
                    'oak.harness2_media.source_commitment', lambda descriptor, _: commitments[descriptor['id']]), patch(
                    'oak.harness2_upstream.SubscriptionRole', lambda *args, **kwargs:
                        SubscriptionRole(*args, client_factory=FakeRuntime, **kwargs)):
                update = store.update
                def crash_before_frozen_commit(cycle_id, **kwargs):
                    if kwargs.get('phase') == 'frozen':
                        raise RuntimeError('controlled crash after immutable freeze publication')
                    return update(cycle_id, **kwargs)
                with patch.object(store, 'update', crash_before_frozen_commit):
                    with self.assertRaisesRegex(RuntimeError, 'controlled crash'):
                        run_cycle(store, cycle['id'])
                self.assertEqual(Adapter.executions, 4)
                self.assertEqual(len(FakeRuntime.calls), 4)
                result = run_cycle(store, cycle['id'])
                self.assertEqual(result['phase'], 'completed')
                self.assertFalse(result['decision']['accepted'])
                self.assertIn('no_strict_fresh_holdout_gain', result['decision']['reasons'])
                self.assertEqual(Adapter.executions, 12)
                self.assertEqual(len(FakeRuntime.calls), 4)
                saved_usage = store.usage(cycle['id'])
                resumed = run_cycle(store, cycle['id'])
                self.assertEqual(resumed, result)
                self.assertEqual(Adapter.executions, 12)
                self.assertEqual(len(FakeRuntime.calls), 4)
                self.assertEqual(store.usage(cycle['id']), saved_usage)
        finally:
            store.close()

    def test_real_media_adapter_with_upstream_namespace_and_mocked_transports(self):
        """Generated source IO and actual adapter; fixture output is not a solution."""
        from oak.harness2_media import MediaBenchmarkAdapter, make_descriptor
        from tests.test_harness2_media import FakeRuntime as FixtureMediaRuntime
        descriptor = make_descriptor('upstream-adapter-contract', 1, family='projective')
        FixtureMediaRuntime.calls = []
        adapter = MediaBenchmarkAdapter(self.root / 'actual-media-adapter', [descriptor],
            runtime_factory=FixtureMediaRuntime, baseline_procedure='Observe the original sources.')
        role = self.role()
        result = run_development(descriptor['id'], adapter=adapter, role=role,
            work_dir=self.root / 'genuine-upstream', library_dir=self.root / 'actual-library')
        self.assertEqual(result.status, 'ok', result.to_dict())
        self.assertEqual(len(FixtureMediaRuntime.calls), 4)
        self.assertEqual(len(FakeRuntime.calls), 4, result.to_dict())
        self.assertEqual(result.modes['seq']['ledger']['R'], 4)
        self.assertEqual(Path(result.oak_provenance['chosen_harness']).joinpath('AGENTS.md').read_text(),
                         'Keep signed values.\n')
        for receipt in (self.root / 'actual-media-adapter/executions').glob('*/receipt.json'):
            record = json.loads(receipt.read_text())
            self.assertIn('/', record['run_id'])
            self.assertEqual(record['state'], 'finished')
        # The copied source deliberately is no solution to this restoration
        # task. No independent quality gate or model behavior is claimed here.


if __name__ == '__main__':
    unittest.main()
