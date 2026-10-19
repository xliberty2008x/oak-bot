"""Freshness, native execution caching and independent delivered-byte gates.

These tests use generated PNG artwork and a fake native transport. They do not
claim model behavior, FFmpeg execution or access to an external media provider.
"""

import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from oak import harness2_media as media


class FakeRuntime:
    calls = []

    def __init__(self, **config):
        self.config = config

    async def run(self, task, workspace, procedure):
        self.calls.append((str(workspace), procedure, self.config))
        # The transport fixture copies original bytes to a separate immutable
        # output sink; it makes no assertion that this solves the media task.
        destination = Path(workspace).parent / 'snapshots' / 'final.png'
        destination.parent.mkdir()
        data = Path(task['reference_paths'][0]).read_bytes()
        destination.write_bytes(data)
        destination.chmod(0o400)
        sha = hashlib.sha256(data).hexdigest()
        return {'status': 'completed', 'delivered': True, 'artifact_path': str(destination),
                'artifact_sha256': sha, 'candidate_path': str(destination),
                'candidate_sha256': sha, 'candidate_complete': True,
                'source_immutable': True, 'model': media.MODEL, 'effort': 'low',
                'reviews': [{'verdict': 'pass', 'sha256': sha, 'summary': 'Fixture observation.'}],
                'tool_calls': 3, 'elapsed_seconds': 1.25, 'render_calls': [],
                'context_sha256': hashlib.sha256(procedure.encode()).hexdigest()}


class MediaDescriptors(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_fresh_cycle_namespace_changes_actual_sources_and_commitment(self):
        first = media.make_descriptor('cycle-one', 1)
        self.assertEqual(first, media.make_descriptor('cycle-one', 1))
        second = media.make_descriptor('cycle-two', 1)
        self.assertNotEqual(first['seed'], second['seed'])
        first_task = media.materialize_descriptor(first, self.root / 'first')
        second_task = media.materialize_descriptor(second, self.root / 'second')
        first_commitment = media.source_commitment(first, first_task)
        second_commitment = media.source_commitment(second, second_task)
        self.assertNotEqual(first_commitment['sha256'], second_commitment['sha256'])
        self.assertNotEqual(first_commitment['inputs']['actor-far.png'],
                            second_commitment['inputs']['actor-far.png'])
        self.assertNotIn(str(self.root), json.dumps(first_commitment))
        self.assertEqual(len(first_commitment['sha256']), 64)

    def test_depth_dimensions_stay_in_declared_native_demo_bound(self):
        descriptors = [media.make_descriptor('bounded-cycle', index) for index in range(32)]
        self.assertEqual(len({row['id'] for row in descriptors}), 32)
        self.assertGreater(len({tuple(row['size']) for row in descriptors}), 8)
        for row in descriptors:
            self.assertTrue(800 <= row['size'][0] <= 864)
            self.assertTrue(512 <= row['size'][1] <= 576)

    def test_revealed_holdout_cannot_be_relabelled_as_independent(self):
        descriptor = media.exposed_regression('holdout-create-folio307-perspective')
        self.assertEqual(descriptor['split'], 'regression')
        descriptor['split'] = 'holdout'
        with self.assertRaises(ValueError):
            media.validate_descriptor(descriptor)
        with self.assertRaises(ValueError):
            media.exposed_regression('holdout-create-linkage311-motion')

    def test_historical_projective_sources_keep_original_bytes(self):
        descriptor = media.exposed_regression('holdout-create-folio307-perspective')
        generated = media.materialize_descriptor(descriptor, self.root / 'adapted')
        original = media.source.materialize_task(descriptor['id'], self.root / 'original')
        hashes = lambda task: {Path(name).name: sha for name, sha in task['source_hashes'].items()}
        self.assertEqual(hashes(generated), hashes(original))

    def test_materialization_keeps_semantics_runner_only_and_never_overwrites(self):
        descriptor = media.make_descriptor('source-isolation', 2)
        task = media.materialize_descriptor(descriptor, self.root / 'workspace')
        contract = json.loads(Path(task['specification_path']).read_text())
        text = json.dumps(contract)
        for name in ('"properties"', '"masks"', '"rgba"', '"foregrounds"',
                     '"clean_original"', '"target_points"', '"donor_points"'):
            self.assertNotIn(name, text)
        self.assertEqual(set(task['reference_paths']), set(task['reference_roles']))
        facts = media.quality_descriptor(descriptor, self.root / 'workspace')
        self.assertIn('foregrounds', facts['properties'])
        self.assertEqual(set(path.name for path in (self.root / 'workspace').iterdir()), {'inputs'})
        with self.assertRaises(ValueError):
            media.materialize_descriptor(descriptor, self.root / 'workspace')

    def test_commitment_refuses_changed_or_mismatched_inputs(self):
        descriptor = media.make_descriptor('commitment', 2, family='projective')
        task = media.materialize_descriptor(descriptor, self.root / 'workspace')
        task['task_id'] = 'other'
        with self.assertRaises(ValueError):
            media.source_commitment(descriptor, task)
        task['task_id'] = descriptor['id']
        Path(task['reference_paths'][0]).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            media.source_commitment(descriptor, task)

    def test_regenerated_hidden_semantics_must_match_actual_source_pixels(self):
        descriptor = media.make_descriptor('semantic-reference', 1, family='projective')
        task = media.materialize_descriptor(descriptor, self.root / 'workspace')
        frame = next(media.authored_descriptor_frames(descriptor, self.root / 'workspace'))
        self.assertEqual(frame.size, tuple(descriptor['size']))
        source_path = Path(task['reference_paths'][0])
        with Image.open(source_path) as image:
            image.load()
            image.putpixel((0, 0), (1, 2, 3))
            image.save(source_path)
        with self.assertRaises(ValueError):
            media.quality_descriptor(descriptor, self.root / 'workspace')

    def test_png_preflight_has_real_positive_negative_and_verified_restart(self):
        descriptor = media.make_descriptor('authored-png-control', 1, family='projective')
        report = media.preflight_descriptors([descriptor], self.root / 'preflight')
        self.assertEqual(report['status'], 'pass')
        controls = report['cases'][0]['controls']
        self.assertEqual(controls['positive']['mechanical']['status'], 'pass')
        self.assertEqual(controls['negative']['mechanical']['status'], 'fail')
        self.assertEqual(controls['negative']['mechanical']['structural_status'], 'pass')
        self.assertNotIn(str(self.root), json.dumps(report))
        with patch.object(media, 'evaluate_artifact', side_effect=AssertionError('Completed controls reran.')):
            self.assertEqual(media.preflight_descriptors([descriptor], self.root / 'preflight'), report)


class NativeAdapter(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.descriptor = media.make_descriptor('adapter-development', 1, family='projective')
        FakeRuntime.calls = []
        self.budget = []

    def adapter(self, **kwargs):
        return media.MediaBenchmarkAdapter(self.root / 'adapter', [self.descriptor],
            runtime_factory=FakeRuntime, budget_hook=lambda kind, identity: self.budget.append((kind, identity)),
            **kwargs)

    def test_real_upstream_protocol_and_only_supported_edit_surface(self):
        from oak.harness2_upstream import upstream
        adapter = self.adapter()
        self.assertIsInstance(adapter, upstream().spec.BenchmarkAdapter)
        self.assertEqual(adapter.substrate.surface().always_on, ('AGENTS.md',))
        for tree in ({'AGENTS.md': '', 'scripts/change.py': 'pass'},
                     {'AGENTS.md': '', 'guardrails.md': 'override'}, {}):
            with self.assertRaises(ValueError):
                adapter.validate_tree(tree)

    def _copy_selector_candidate(self, adapter, directory):
        from oak.harness2_upstream import upstream
        api = upstream()  # Verify the real pinned implementation before import.
        from harness2.core.selection import Selection, _write_candidates
        context = self.root / 'selector-context'
        candidates = context / 'candidates'
        candidates.mkdir(parents=True)
        rollout = api.spec.RunView('c1', 'selector-fixture', directory, True, True)
        selection = Selection(self.descriptor['id'], 'seq', 2, id_map={'A': 'c1'})
        names = _write_candidates(selection, {'c1': adapter.deliverable_text(directory)},
                                  {'c1': rollout}, candidates, adapter)
        return context, names

    def _view_selector_image(self, context, relative):
        from oak.harness2_upstream import _ContextTools
        files = {str(p.relative_to(context)): p.read_bytes()
                 for p in context.rglob('*') if p.is_file()}
        tools = _ContextTools(files, context, ('AGENTS.md',), 12000, 4, None, {})
        result = asyncio.run(tools.handle({'tool': 'oak_h2_view', 'threadId': None,
                                          'arguments': {'path': relative}}))
        self.assertTrue(result['success'])
        self.assertTrue(any(item.get('type') == 'inputImage' for item in result['contentItems']))

    def _selector_video_fixture(self):
        class VideoRuntime(FakeRuntime):
            async def run(self, *args):
                value = await super().run(*args)
                destination = Path(value['artifact_path']).with_suffix('.mp4')
                destination.write_bytes(b'\x00\x00\x00\x18ftypmp42synthetic-video-inspector-fixture')
                destination.chmod(0o400)
                sha = hashlib.sha256(destination.read_bytes()).hexdigest()
                value.update(artifact_path=str(destination), candidate_path=str(destination),
                             artifact_sha256=sha, candidate_sha256=sha,
                             reviews=[{'verdict': 'pass', 'sha256': sha}])
                return value
        adapter = self.adapter()
        adapter.runtime_factory = VideoRuntime
        directory = adapter.execute(self.descriptor['id'], None, 'selector-video-fixture')
        sha = json.loads((directory / 'receipt.json').read_text())['receipt']['artifact_sha256']
        image = io.BytesIO()
        Image.new('RGB', (24, 16), (20, 90, 170)).save(image, format='PNG')
        observed = {'sha256': sha, 'type': 'video', 'decode_status': 'complete',
                    'coverage': 'Synthetic tiny contact sheet; mocked inspector, no codec call.',
                    'evidence': [{'image_url': 'data:image/png;base64,' +
                                 base64.b64encode(image.getvalue()).decode(),
                                 'label': 'Timestamped video samples'}]}
        return adapter, directory, observed

    def test_real_upstream_selector_copies_and_role_can_view_delivered_png(self):
        adapter = self.adapter()
        directory = adapter.execute(self.descriptor['id'], None, 'selector-png-fixture')
        with patch('oak.quality_media.inspect_media', side_effect=AssertionError('PNG decoded as video.')):
            context, names = self._copy_selector_candidate(adapter, directory)
        self.assertEqual(names, {'final.png'})
        record = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual((context / 'candidates/A/final.png').read_bytes(),
                         Path(record['receipt']['artifact_path']).read_bytes())
        self.assertIn('exact delivered artifact', (context / 'candidates/A.md').read_text())
        self._view_selector_image(context, 'candidates/A/final.png')

    def test_real_upstream_selector_video_without_trace_images_gets_viewable_fallback(self):
        adapter, directory, observed = self._selector_video_fixture()
        self.assertEqual(adapter.deliverable_images(directory), [])
        with patch('oak.quality_media.inspect_media', return_value=observed) as inspect:
            context, names = self._copy_selector_candidate(adapter, directory)
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(len(names), 1)
        self.assertTrue(next(iter(names)).endswith('.png'))
        self.assertFalse(any(path.suffix == '.mp4' for path in context.rglob('*')))
        self._view_selector_image(context, 'candidates/A/' + next(iter(names)))
        with patch('oak.quality_media.inspect_media', side_effect=AssertionError('Cached evidence decoded again.')):
            self.assertEqual([p.name for p in adapter.deliverable_assets(directory)], list(names))

    def test_selector_assets_refuse_different_valid_delivery_and_candidate_shas(self):
        adapter, directory, observed = self._selector_video_fixture()
        record_path = directory / 'receipt.json'
        record = json.loads(record_path.read_text())
        original = Path(record['receipt']['artifact_path'])
        other = original.with_name('other-delivery.mp4')
        other.write_bytes(b'\x00\x00\x00\x18ftypmp42different-frozen-delivery')
        other.chmod(0o400)
        record['receipt'].update(artifact_path=str(other),
                                  artifact_sha256=hashlib.sha256(other.read_bytes()).hexdigest())
        record_path.write_text(json.dumps(record))
        with patch('oak.quality_media.inspect_media', return_value=observed) as inspect:
            with self.assertRaises(ValueError):
                adapter.deliverable_assets(directory)
        inspect.assert_not_called()
        self.assertFalse((directory / 'selector-assets').exists())

    def test_selector_video_evidence_refuses_wrong_inspection_sha_and_cache_tamper(self):
        adapter, directory, observed = self._selector_video_fixture()
        with patch('oak.quality_media.inspect_media', return_value={**observed, 'sha256': '0' * 64}):
            with self.assertRaises(ValueError):
                adapter.deliverable_assets(directory)
        with patch('oak.quality_media.inspect_media', return_value=observed):
            assets = adapter.deliverable_assets(directory)
        assets[0].write_bytes(b'changed image evidence')
        with patch('oak.quality_media.inspect_media', side_effect=AssertionError('Tamper triggered new decode.')):
            with self.assertRaises(ValueError):
                adapter.deliverable_assets(directory)

    def test_optimizer_sources_omit_measurement_recipe_and_host_identity(self):
        adapter = self.adapter()
        files = adapter.task_files(self.descriptor['id'])
        self.assertNotIn('scene.json', {p.name for p in files.iterdir()})
        self.assertEqual(len(list(files.glob('*.png'))), 3)
        text = (files / 'work.json').read_text() + adapter.task_prompt(self.descriptor['id'])
        for name in ('acceptance', 'projective_repair_mae', 'projective_alignment_step_px',
                     'minimum_observable_support_px', str(self.root), 'properties', 'masks'):
            self.assertNotIn(name, text)
        self.assertEqual(adapter.tasks('holdout'), [])

    def test_parallel_upstream_contexts_materialize_sources_exactly_once(self):
        adapter = self.adapter()
        with patch.object(media, 'materialize_descriptor', wraps=media.materialize_descriptor) as materialize:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(adapter.task_files, self.descriptor['id']) for _ in range(2)]
                paths = [future.result() for future in futures]
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(materialize.call_count, 1)
        self.assertTrue((paths[0] / 'work.json').is_file())

    def test_nondev_adapter_requires_explicit_postfreeze_independent_mode(self):
        held = media.make_descriptor('future-unseen', 3, split='holdout')
        with self.assertRaises(ValueError):
            media.MediaBenchmarkAdapter(self.root / 'not-optimizer', [held])
        with self.assertRaises(ValueError):
            media.MediaBenchmarkAdapter(self.root / 'not-frozen', [held], optimizer=False)
        independent = media.MediaBenchmarkAdapter(self.root / 'independent', [held],
                                                  optimizer=False, freeze_sha256='f' * 64)
        self.assertEqual(independent.tasks(), [held['id']])

    def test_native_execute_is_idempotent_across_adapter_restart(self):
        adapter = self.adapter()
        directory = adapter.execute(self.descriptor['id'], None, 'probe-one')
        record = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(record['state'], 'finished')
        self.assertTrue(adapter.delivered(directory))
        self.assertEqual(self.adapter().execute(self.descriptor['id'], None, 'probe-one'), directory)
        self.assertEqual(len(FakeRuntime.calls), 1)
        self.assertEqual(len(self.budget), 1)
        self.assertEqual(record['model'], 'gpt-6.1-sol')
        self.assertEqual(record['effort'], 'low')
        self.assertIsNone(record['receipt'].get('cost_usd'))

    def test_actual_upstream_logical_run_identity_is_mapped_to_opaque_directory(self):
        adapter = self.adapter()
        run_id = 'namespace/' + self.descriptor['id'] + '/base1'
        directory = adapter.execute(self.descriptor['id'], None, run_id)
        self.assertEqual(directory.name, hashlib.sha256(run_id.encode()).hexdigest())
        self.assertEqual(json.loads((directory / 'receipt.json').read_text())['run_id'], run_id)
        self.assertEqual(adapter.execute(self.descriptor['id'], None, run_id), directory)
        for unsafe in ('namespace/../base1', '/absolute/base1', 'namespace//base1', 'task\\base1'):
            with self.assertRaises(ValueError):
                adapter.execute(self.descriptor['id'], None, unsafe)

    def test_cached_run_refuses_changed_procedure_or_timeout(self):
        adapter = self.adapter()
        adapter.execute(self.descriptor['id'], None, 'fixed-id')
        harness = self.root / 'candidate'
        harness.mkdir()
        (harness / 'AGENTS.md').write_text('Observe source contours before rendering.')
        with self.assertRaises(ValueError):
            adapter.execute(self.descriptor['id'], harness, 'fixed-id')
        with self.assertRaises(ValueError):
            adapter.execute(self.descriptor['id'], None, 'fixed-id', timeout=300)
        self.assertEqual(len(FakeRuntime.calls), 1)

    def test_finished_failed_delivery_is_cached_instead_of_retried(self):
        class FailedDelivery(FakeRuntime):
            async def run(self, *args):
                value = await super().run(*args)
                value.update(status='unverified', delivered=False,
                             artifact_path=None, artifact_sha256=None)
                return value
        adapter = self.adapter()
        adapter.runtime_factory = FailedDelivery
        directory = adapter.execute(self.descriptor['id'], None, 'failed-delivery')
        self.assertFalse(adapter.delivered(directory))
        adapter.execute(self.descriptor['id'], None, 'failed-delivery')
        self.assertEqual(len(FakeRuntime.calls), 1)

    def test_observed_terminal_source_violation_is_durable_negative_never_retried(self):
        class SourceViolation(FakeRuntime):
            async def run(self, task, workspace, procedure):
                value = await super().run(task, workspace, procedure)
                Path(task['reference_paths'][0]).write_bytes(b'fixture source violation')
                value.update(status='failed', source_immutable=False, delivered=False,
                             artifact_path=None, artifact_sha256=None)
                return value
        adapter = self.adapter()
        adapter.runtime_factory = SourceViolation
        directory = adapter.execute(self.descriptor['id'], None, 'terminal-source-violation')
        record = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(record['state'], 'finished')
        self.assertEqual(record['terminal_integrity'], 'failed')
        self.assertFalse(adapter.delivered(directory))
        self.assertEqual(adapter.execute(self.descriptor['id'], None, 'terminal-source-violation'), directory)
        self.assertEqual(len(FakeRuntime.calls), 1)
        result = media.evaluate_delivery(self.descriptor, directory, freeze_sha256='f' * 64)
        self.assertFalse(result['joint_passed'])
        self.assertFalse(result['source_immutable'])
        self.assertFalse(list(directory.glob('interrupted-*.json')))

    def test_finished_pending_integrity_recovers_negative_without_native_replay(self):
        class SourceViolation(FakeRuntime):
            async def run(self, task, workspace, procedure):
                value = await super().run(task, workspace, procedure)
                Path(task['reference_paths'][0]).write_bytes(b'fixture source violation')
                value.update(status='failed', source_immutable=False, delivered=False,
                             artifact_path=None, artifact_sha256=None)
                return value
        adapter = self.adapter()
        adapter.runtime_factory = SourceViolation
        original_write = media._write
        def interrupt_after_terminal(path, payload):
            original_write(path, payload)
            if (Path(path).name == 'receipt.json' and payload.get('state') == 'finished'
                    and payload.get('terminal_integrity') == 'pending_validation'):
                raise RuntimeError('Fixture interruption after durable terminal receipt.')
        with patch.object(media, '_write', side_effect=interrupt_after_terminal):
            with self.assertRaises(RuntimeError):
                adapter.execute(self.descriptor['id'], None, 'pending-invalid-terminal')
        directory = adapter.root / 'executions' / hashlib.sha256(b'pending-invalid-terminal').hexdigest()
        pending = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(pending['state'], 'finished')
        self.assertEqual(pending['terminal_integrity'], 'pending_validation')
        self.assertEqual(len(FakeRuntime.calls), 1)
        resumed = adapter.execute(self.descriptor['id'], None, 'pending-invalid-terminal')
        self.assertEqual(resumed, directory)
        recovered = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(recovered['state'], 'finished')
        self.assertEqual(recovered['terminal_integrity'], 'failed')
        self.assertEqual(recovered['receipt'], pending['receipt'])
        self.assertEqual(recovered['integrity_recovery'], 'validation_resumed_from_terminal_receipt')
        self.assertFalse(adapter.delivered(directory))
        result = media.evaluate_delivery(self.descriptor, directory, freeze_sha256='f' * 64)
        self.assertFalse(result['joint_passed'])
        self.assertFalse(result['source_immutable'])
        self.assertEqual(len(FakeRuntime.calls), 1)
        self.assertEqual(len(self.budget), 1)
        self.assertFalse(list(directory.glob('interrupted-*.json')))

    def test_finished_pending_integrity_recovers_verified_without_native_replay(self):
        adapter = self.adapter()
        original_write = media._write
        def interrupt_after_terminal(path, payload):
            original_write(path, payload)
            if (Path(path).name == 'receipt.json' and payload.get('state') == 'finished'
                    and payload.get('terminal_integrity') == 'pending_validation'):
                raise RuntimeError('Fixture interruption after durable terminal receipt.')
        with patch.object(media, '_write', side_effect=interrupt_after_terminal):
            with self.assertRaises(RuntimeError):
                adapter.execute(self.descriptor['id'], None, 'pending-valid-terminal')
        directory = adapter.execute(self.descriptor['id'], None, 'pending-valid-terminal')
        recovered = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(recovered['state'], 'finished')
        self.assertEqual(recovered['terminal_integrity'], 'verified')
        self.assertTrue(adapter.delivered(directory))
        self.assertEqual(len(FakeRuntime.calls), 1)
        self.assertEqual(len(self.budget), 1)
        Path(recovered['receipt']['artifact_path']).unlink()
        with self.assertRaises(ValueError):
            adapter.execute(self.descriptor['id'], None, 'pending-valid-terminal')
        self.assertEqual(json.loads((directory / 'receipt.json').read_text())['terminal_integrity'], 'verified')
        self.assertEqual(len(FakeRuntime.calls), 1)

    def test_shared_native_lock_delays_transport_until_other_role_releases(self):
        path = self.root / 'native.lock'
        event = threading.Event()
        adapter = self.adapter(native_lock_path=path)
        adapter.budget_hook = lambda *_: event.set()
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(adapter.execute, self.descriptor['id'], None, 'serial-native')
                try:
                    self.assertTrue(event.wait(5))
                    self.assertEqual(FakeRuntime.calls, [])
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                directory = future.result(timeout=15)
        self.assertTrue(adapter.delivered(directory))

    def test_budget_stop_precedes_native_execution_and_source_materialization(self):
        adapter = self.adapter()
        def stop(*_):
            raise RuntimeError('Fixture budget exhausted.')
        adapter.budget_hook = stop
        with self.assertRaises(RuntimeError):
            adapter.execute(self.descriptor['id'], None, 'budget-stop')
        self.assertEqual(FakeRuntime.calls, [])
        directory = adapter.root / 'executions' / hashlib.sha256(b'budget-stop').hexdigest()
        self.assertFalse((directory / 'attempt-1').exists())
        self.assertEqual(json.loads((directory / 'receipt.json').read_text())['state'], 'pending_budget')
        adapter.budget_hook = lambda kind, identity: self.budget.append((kind, identity))
        adapter.execute(self.descriptor['id'], None, 'budget-stop')
        original = json.loads((directory / 'interrupted-1.json').read_text())
        self.assertFalse(original['producer_started'])
        self.assertEqual(original['classification'], 'interrupted_before_producer_start')
        self.assertEqual(json.loads((directory / 'receipt.json').read_text())['attempt'], 2)

    def test_missing_snapshot_does_not_silently_trigger_new_execution(self):
        adapter = self.adapter()
        directory = adapter.execute(self.descriptor['id'], None, 'damaged-cache')
        record = json.loads((directory / 'receipt.json').read_text())
        Path(record['receipt']['artifact_path']).unlink()
        with self.assertRaises(ValueError):
            adapter.execute(self.descriptor['id'], None, 'damaged-cache')
        self.assertEqual(len(FakeRuntime.calls), 1)

    def test_interruption_preserved_and_only_unfinished_attempt_resumes(self):
        class InterruptedRuntime(FakeRuntime):
            failures = 1

            async def run(self, *args):
                if self.failures:
                    type(self).failures -= 1
                    raise RuntimeError('Simulated external boundary loss, no model call.')
                return await super().run(*args)

        adapter = self.adapter()
        adapter.runtime_factory = InterruptedRuntime
        with self.assertRaises(RuntimeError):
            adapter.execute(self.descriptor['id'], None, 'interrupted')
        directory = adapter.execute(self.descriptor['id'], None, 'interrupted')
        archived = json.loads((directory / 'interrupted-1.json').read_text())
        finished = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(archived['state'], 'interrupted')
        self.assertEqual(finished['attempt'], 2)
        self.assertNotEqual(archived['workspace'], finished['workspace'])
        self.assertTrue(Path(archived['workspace']).is_dir())
        self.assertEqual([row[1]['attempt'] for row in self.budget], [1, 2])
        adapter.execute(self.descriptor['id'], None, 'interrupted')
        self.assertEqual(len(self.budget), 2)

    def test_numeric_measurement_unavailable_to_upstream_until_outer_freeze(self):
        adapter = self.adapter()
        directory = adapter.execute(self.descriptor['id'], None, 'no-early-grade')
        with self.assertRaises(ValueError):
            adapter.score(directory, self.descriptor['id'])
        for text in (adapter.trajectory(directory, obs_trunc=10000), adapter.deliverable_text(directory)):
            for forbidden in ('quality_score', 'mechanical_passed', 'joint_passed', str(self.root)):
                self.assertNotIn(forbidden, text)
        self.assertFalse((directory / 'evaluation.json').exists())

    def test_joint_delivery_requires_visual_and_mechanical_same_immutable_sha(self):
        adapter = self.adapter()
        directory = adapter.execute(self.descriptor['id'], None, 'joint-proof')
        with patch.object(media, 'evaluate_artifact', return_value={
                'status': 'pass', 'structural_status': 'pass', 'quality_score': 1, 'checks': []}):
            passed = media.evaluate_delivery(self.descriptor, directory, freeze_sha256='f' * 64)
            self.assertTrue(passed['joint_passed'])
            path = directory / 'receipt.json'
            record = json.loads(path.read_text())
            record['receipt']['reviews'][0]['sha256'] = '0' * 64
            path.write_text(json.dumps(record))
            failed = media.evaluate_delivery(self.descriptor, directory, freeze_sha256='f' * 64)
            self.assertTrue(failed['mechanical_passed'])
            self.assertFalse(failed['visual_passed'])
            self.assertFalse(failed['joint_passed'])
        with patch.object(media, 'evaluate_artifact', return_value={
                'status': 'fail', 'structural_status': 'pass', 'quality_score': .9, 'checks': []}):
            self.assertFalse(media.evaluate_delivery(self.descriptor, directory,
                                                     freeze_sha256='f' * 64)['joint_passed'])

    def test_trace_captures_observations_without_inline_binary_images(self):
        raw = b'small synthetic tool image'
        result = {'contentItems': [{'type': 'inputText', 'text': '{"sha256":"abc"}'},
                    {'type': 'inputImage', 'imageUrl': 'data:image/png;base64,' +
                     base64.b64encode(raw).decode()}]}
        result = media.MediaBenchmarkAdapter._observation(result, self.root / 'images')
        self.assertEqual(result['observations'], [{'sha256': 'abc'}])
        self.assertEqual(len(result['image_files']), 1)
        self.assertEqual((self.root / 'images' / result['image_files'][0]).read_bytes(), raw)
        self.assertNotIn('base64', json.dumps(result))

    def test_actual_transport_wrapper_persists_partial_and_completed_tool_trace(self):
        adapter = self.adapter()
        workspace = self.root / 'execution' / 'attempt-1' / 'workspace'
        workspace.mkdir(parents=True)
        trace = []
        seen = []
        async def original(_):
            partial = json.loads((workspace.parent / 'trace.json').read_text())
            seen.append(partial)
            return {'contentItems': [{'type': 'inputText', 'text': '{"ok":true}'}]}
        class Client:
            def __init__(self, **kwargs):
                self.handler = kwargs['tool_handler']
        adapter.runtime_factory = None
        with patch('oak.runtime.RuntimeClient', Client):
            runtime = adapter._runtime(trace, workspace.parent / 'visible-evidence',
                                       600, workspace, workspace.parent.parent)
            client = runtime.client_factory(tool_handler=original)
            asyncio.run(client.handler({'tool': 'oak_pilot_render',
                'arguments': {'code': 'print(' + repr(str(workspace)) + ')'}}))
        self.assertEqual(len(seen), 1)
        self.assertNotIn('result', seen[0][0])
        completed = json.loads((workspace.parent / 'trace.json').read_text())
        self.assertEqual(completed[0]['result']['observations'], [{'ok': True}])
        self.assertNotIn(str(self.root), json.dumps(completed))

    def test_protected_factory_reaches_pilot_and_independent_reviewer_capture_seam(self):
        adapter = self.adapter()
        adapter.runtime_factory = None
        seen = []
        def protected_factory(**kwargs):
            seen.append(kwargs)
            return object()
        adapter.native_client_factory = protected_factory
        workspace = self.root / 'factory-proof' / 'workspace'
        workspace.mkdir(parents=True)
        runtime = adapter._runtime([],workspace.parent/'evidence',600,workspace,workspace.parent)
        # PilotRuntime passes this same client_factory to its independent
        # QualityReviewer; producer and reviewer callbacks remain host-side.
        callback = object()
        runtime.client_factory(cwd=str(workspace),sandbox='read-only',
                               config={'web_search':'disabled'},tool_handler=callback)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]['sandbox'],'read-only')
        self.assertIsNot(seen[0]['tool_handler'],callback)  # Trace capture wraps the callback.


if __name__ == '__main__':
    unittest.main()
