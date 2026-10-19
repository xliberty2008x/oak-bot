"""Pilot split discipline, baseline selection and honest failure accounting."""

import json
import asyncio
from contextlib import redirect_stdout
import io
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from oak import media_pilot as pilot


def receipt(passed=True):
    return {'status': 'completed' if passed else 'timeout', 'delivered': passed,
            'artifact_sha256': 'a' * 64,
            'reviews': [{'verdict': 'pass', 'sha256': 'a' * 64, 'issues': [], 'checks': []}] if passed else [],
            'tool_calls': 3, 'elapsed_seconds': 4.0}


class PilotFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'experiment'
        self.fingerprint = patch.object(pilot, '_implementation', return_value='implementation')
        self.fingerprint.start()
        self.addCleanup(self.fingerprint.stop)
        self.state = pilot.initialize(self.root)

    def save(self):
        pilot._write(self.root / 'pilot.json', self.state)

    def populate_development(self, outcomes=None):
        artifact = self.root / 'test-artifact.png'
        artifact.write_bytes(b'immutable-test-delivery')
        digest = pilot._file_hash(artifact)
        self.state['procedures'].update({
            'candidate-1': {'procedure': 'Measure before laying out.', 'hypothesis': 'Fewer clipped titles.'},
            'candidate-2': {'procedure': 'Inspect local motion.', 'hypothesis': 'Fewer bad transitions.'}})
        for procedure_id in self.state['procedures']:
            for index, key in enumerate(pilot._keys(self.state, 'dev', procedure_id)):
                passed = (outcomes or {}).get(procedure_id, [True] * 6)[index]
                value = receipt(passed)
                if passed:
                    value.update(artifact_path=str(artifact), artifact_sha256=digest)
                    value['reviews'][0]['sha256'] = digest
                self.state['runs'][key] = {'state': 'finished', 'split': 'dev',
                    'task_id': key.split('/')[1], 'procedure_id': procedure_id,
                    'repeat': 1, 'receipt': value, 'structural': {'status': 'pass', 'checks': []},
                    'metrics': pilot._metrics(value, {'status': 'pass'}, True)}
        self.save()


class PilotContracts(PilotFixture, unittest.TestCase):
    def test_initialization_never_overwrites_existing_experiment(self):
        with self.assertRaises(ValueError):
            pilot.initialize(self.root)
        self.assertEqual(len(self.state['tasks']), 12)
        self.assertEqual(sum(row['split'] == 'holdout' for row in self.state['tasks']), 6)

    def test_proposal_input_does_not_include_heldout_or_workspace_data(self):
        self.populate_development()
        heldout = next(row for row in self.state['tasks'] if row['split'] == 'holdout')
        heldout['brief'] = 'HOLDOUT_CANARY'
        self.state['runs']['holdout/x/baseline/1'] = {
            'state': 'finished', 'split': 'holdout', 'receipt': {'summary': 'RESULT_CANARY'}}
        self.state['runs'][next(iter(self.state['runs']))]['receipt']['artifact_path'] = '/private/PATH_CANARY'
        serialized = json.dumps(pilot.proposal_evidence(self.state))
        for canary in ('HOLDOUT_CANARY', 'RESULT_CANARY', 'PATH_CANARY'):
            self.assertNotIn(canary, serialized)

    def test_baseline_wins_when_candidates_do_not_improve(self):
        self.populate_development()
        frozen = pilot.freeze(self.root)
        self.assertEqual(frozen['procedure_id'], 'baseline')
        self.assertEqual(pilot.freeze(self.root), frozen)

    def test_regression_is_not_selected_even_with_better_first_review(self):
        self.populate_development({'baseline': [True] * 5 + [False],
                                   'candidate-1': [True] * 6,
                                   'candidate-2': [True] * 6})
        # Both candidates pass more tasks, but one changed an input and the
        # other introduces an independent material defect on a development run.
        for key in pilot._keys(self.state, 'dev', 'candidate-1')[:1]:
            self.state['runs'][key]['metrics']['structural_passed'] = False
        for key in pilot._keys(self.state, 'dev', 'candidate-2')[:1]:
            self.state['runs'][key]['metrics']['material_defect'] = True
        self.save()
        self.assertEqual(pilot.freeze(self.root)['procedure_id'], 'baseline')

    def test_higher_success_candidate_can_be_frozen_before_holdout(self):
        self.populate_development({'baseline': [True] * 4 + [False] * 2})
        self.assertEqual(pilot.freeze(self.root)['procedure_id'], 'candidate-1')
        state = pilot.load(self.root)
        self.assertFalse(state['holdout_started'])
        with self.assertRaises(ValueError):
            pilot.proposal_evidence(state)

    def test_missing_development_run_prevents_selection(self):
        self.populate_development()
        self.state['runs'].pop(next(iter(self.state['runs'])))
        self.save()
        with self.assertRaises(ValueError):
            pilot.freeze(self.root)

    def test_selected_candidate_is_also_the_heldout_challenger(self):
        self.populate_development({'baseline': [True] * 4 + [False] * 2,
                                   'candidate-1': [True] * 6,
                                   'candidate-2': [True] * 5 + [False]})
        key = pilot._keys(self.state, 'dev', 'candidate-1')[0]
        self.state['runs'][key]['metrics']['structural_passed'] = False
        self.save()
        frozen = pilot.freeze(self.root)
        self.assertEqual(frozen['procedure_id'], 'candidate-2')
        self.assertEqual(frozen['challenger_id'], 'candidate-2')

    def test_report_keeps_timeouts_in_denominator(self):
        self.populate_development({'baseline': [True] * 4 + [False] * 2})
        report = pilot.report(self.root)
        base = report['summaries']['dev']['baseline']
        self.assertEqual((base['expected'], base['finished'], base['passed'], base['unverified']), (6, 6, 4, 2))
        self.assertFalse(report['complete'])
        self.assertTrue((self.root / 'report.md').is_file())

    def test_source_mutation_or_missing_visual_review_cannot_pass(self):
        self.assertFalse(pilot._metrics(receipt(), {'status': 'pass'}, False)['passed'])
        value = receipt()
        value['reviews'] = []
        self.assertFalse(pilot._metrics(value, {'status': 'pass'}, True)['passed'])
        value = receipt()
        value['status'] = 'failed'
        self.assertFalse(pilot._metrics(value, {'status': 'pass'}, True)['passed'])
        value = receipt()
        value['artifact_sha256'] = 'b' * 64
        self.assertFalse(pilot._metrics(value, {'status': 'pass'}, True)['passed'])

    def test_changed_implementation_is_not_silently_resumed(self):
        with patch.object(pilot, '_implementation', return_value='other'):
            with self.assertRaises(ValueError):
                pilot.load(self.root)

    def test_changed_snapshot_invalidates_cached_success(self):
        self.populate_development()
        (self.root / 'test-artifact.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            pilot.report(self.root)

    def test_settings_and_manifest_cannot_change_during_experiment(self):
        self.state['config']['repeats'] = 2
        self.save()
        with self.assertRaises(ValueError):
            pilot.load(self.root)


class PilotAsyncContracts(PilotFixture, unittest.IsolatedAsyncioTestCase):
    async def test_parallel_attempts_are_bounded_and_finished_failures_are_retained(self):
        active, maximum, calls = 0, 0, 0

        async def run(task, workspace, procedure):
            nonlocal active, maximum, calls
            active += 1
            calls += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return receipt(False)

        def materialize(task_id, workspace):
            return {'task_id': task_id, 'source_hashes': {}}

        runtime = type('Runtime', (), {'run': staticmethod(run)})()
        with patch('oak.media_pilot_tasks.materialize_task', side_effect=materialize), redirect_stdout(io.StringIO()):
            await pilot._run_split(self.root, self.state, 'dev', ['baseline'], runtime)
            await pilot._run_split(self.root, self.state, 'dev', ['baseline'], runtime)
        self.assertEqual(maximum, 2)
        self.assertEqual(calls, 6)
        summary = pilot._summarize(pilot.load(self.root), 'dev', 'baseline')
        self.assertEqual((summary['expected'], summary['finished'], summary['unverified']), (6, 6, 6))

    async def test_frozen_candidate_tampering_stops_before_model_calls(self):
        self.populate_development({'baseline': [False] * 6})
        frozen = pilot.freeze(self.root)
        self.state = pilot.load(self.root)
        self.state['procedures'][frozen['procedure_id']]['procedure'] += ' changed'
        self.save()
        runtime = type('Runtime', (), {'run': AsyncMock()})()
        with self.assertRaises(ValueError):
            await pilot.evaluate(self.root, runtime)
        runtime.run.assert_not_called()

    async def test_holdout_requires_freeze(self):
        runtime = type('Runtime', (), {'run': AsyncMock()})()
        with self.assertRaises(ValueError):
            await pilot.evaluate(self.root, runtime)
        runtime.run.assert_not_called()


class HardPilotFixture(PilotFixture):
    def setUp(self):
        super().setUp()
        self.root = Path(self.temporary.name) / 'hard-experiment'
        self.state = pilot.initialize(self.root, suite='hard', repeats=2)

    def content_result(self, passed=True, score=.9):
        return {'status': 'pass' if passed else 'fail', 'structural_status': 'pass',
                'quality_score': score, 'checks': [{'criterion': 'content:eye-1',
                    'status': 'pass' if passed else 'fail', 'evidence': 'All frames inspected.'}]}

    def populate_hard_development(self, grades):
        artifact = self.root / 'test-artifact.png'
        artifact.write_bytes(b'immutable hard lifecycle fixture')
        digest = pilot._file_hash(artifact)
        self.state['procedures'].update({
            'candidate-1': {'procedure': 'Inspect local preservation.', 'hypothesis': 'Fewer altered regions.'},
            'candidate-2': {'procedure': 'Inspect every motion frame.', 'hypothesis': 'Fewer frozen intervals.'}})
        for procedure_id in self.state['procedures']:
            delivered_count, quality_count, score = grades[procedure_id]
            for index, key in enumerate(pilot._keys(self.state, 'dev', procedure_id)):
                value = receipt()
                value.update(artifact_path=str(artifact), artifact_sha256=digest,
                             candidate_path=str(artifact), candidate_sha256=digest, candidate_complete=True)
                value['reviews'][0]['sha256'] = digest
                metrics = pilot._metrics(value, self.content_result(), True, self.content_result())
                metrics.update(passed=index < delivered_count, quality_passed=index < quality_count,
                               quality_score=score)
                self.state['runs'][key] = {'state': 'finished', 'split': 'dev',
                    'task_id': key.split('/')[1], 'procedure_id': procedure_id,
                    'repeat': int(key.rsplit('/', 1)[1]), 'receipt': value,
                    'source_immutable': True, 'structural': self.content_result(),
                    'candidate_quality': self.content_result(), 'metrics': metrics}
        self.save()


class HardPilotContracts(HardPilotFixture, unittest.TestCase):
    def test_hard_suite_uses_new_tasks_and_repeats_are_fixed_before_development(self):
        from oak.media_pilot_tasks import TASKS as basic_tasks
        self.assertTrue({row['id'] for row in basic_tasks}.isdisjoint(
            {row['id'] for row in self.state['tasks']}))
        self.assertEqual(self.state['config']['repeats'], 2)
        self.assertEqual(len(pilot._keys(self.state, 'dev', 'baseline')), 12)
        self.assertEqual(len(pilot._keys(self.state, 'holdout', 'baseline')), 12)

    def test_candidate_quality_and_delivered_success_measure_different_bytes(self):
        value = {**receipt(), 'candidate_complete': True, 'candidate_sha256': 'b' * 64}
        metrics = pilot._metrics(value, self.content_result(), True, self.content_result(False, .2))
        self.assertTrue(metrics['passed'])
        self.assertFalse(metrics['quality_passed'])
        self.assertEqual(metrics['quality_score'], .2)
        self.assertFalse(metrics['reviewer_false_pass'])

        metrics = pilot._metrics(value, self.content_result(False, .2), True, self.content_result())
        self.assertFalse(metrics['passed'])
        self.assertTrue(metrics['quality_passed'])
        self.assertEqual(metrics['quality_score'], .9)
        self.assertTrue(metrics['reviewer_false_pass'])

    def test_passing_blocked_candidate_is_quality_evidence_without_delivery_success(self):
        value = {**receipt(False), 'status': 'unverified', 'candidate_complete': True}
        metrics = pilot._metrics(value, {'status': 'unverified'}, True, self.content_result())
        self.assertTrue(metrics['quality_passed'])
        self.assertFalse(metrics['passed'])
        self.assertFalse(metrics['reviewer_false_pass'])
        self.assertTrue(metrics['unverified'])

    def test_partial_candidate_or_changed_sources_cannot_gain_quality_credit(self):
        for complete, immutable in ((False, True), (True, False)):
            value = {**receipt(False), 'candidate_complete': complete}
            metrics = pilot._metrics(value, self.content_result(), immutable, self.content_result())
            self.assertFalse(metrics['quality_passed'])
            self.assertEqual(metrics['quality_score'], 0.)
            self.assertFalse(metrics['passed'])

    def test_quality_gain_can_win_with_equal_delivery_success(self):
        self.populate_hard_development({'baseline': (6, 6, .6),
            'candidate-1': (6, 10, .85), 'candidate-2': (8, 8, .75)})
        frozen = pilot.freeze(self.root)
        self.assertEqual(frozen['procedure_id'], 'candidate-1')
        self.assertEqual(frozen['challenger_id'], 'candidate-1')

    def test_higher_quality_cannot_win_if_delivery_success_regresses(self):
        self.populate_hard_development({'baseline': (6, 6, .6),
            'candidate-1': (5, 12, .95), 'candidate-2': (6, 6, .6)})
        frozen = pilot.freeze(self.root)
        self.assertEqual(frozen['procedure_id'], 'baseline')
        # It can still be evaluated without becoming the selected procedure.
        self.assertEqual(frozen['challenger_id'], 'candidate-1')

    def test_hard_baseline_wins_exact_quality_and_delivery_ties(self):
        self.populate_hard_development({key: (6, 8, .8)
            for key in ('baseline', 'candidate-1', 'candidate-2')})
        self.assertEqual(pilot.freeze(self.root)['procedure_id'], 'baseline')

    def test_changed_undelivered_candidate_invalidates_cached_evidence(self):
        artifact = self.root / 'candidate.png'
        artifact.write_bytes(b'candidate before mutation')
        key = pilot._keys(self.state, 'dev', 'baseline')[0]
        self.state['runs'][key] = {'state': 'finished', 'split': 'dev', 'procedure_id': 'baseline',
            'receipt': {'status': 'unverified', 'delivered': False, 'candidate_complete': True,
                        'candidate_path': str(artifact), 'candidate_sha256': pilot._file_hash(artifact)}}
        self.save()
        pilot.load(self.root)
        artifact.write_bytes(b'candidate after mutation')
        with self.assertRaises(ValueError):
            pilot.report(self.root)


class HardPilotAsyncContracts(HardPilotFixture, unittest.IsolatedAsyncioTestCase):
    async def test_content_gate_rejects_unverified_execution_format_and_source_failures(self):
        key = pilot._keys(self.state, 'dev', 'baseline')[0]
        valid = {'state': 'finished', 'split': 'dev', 'procedure_id': 'baseline',
            'task_id': key.split('/')[1], 'receipt': {'status': 'unverified', 'candidate_complete': True},
            'source_immutable': True, 'candidate_quality': self.content_result(False, .2)}
        invalid = []
        for field, value in (('source_immutable', False), ('state', 'running')):
            row = copy.deepcopy(valid)
            row[field] = value
            invalid.append(row)
        for field, value in (('candidate_complete', False), ('status', 'timeout'), ('status', 'failed')):
            row = copy.deepcopy(valid)
            row['receipt'][field] = value
            invalid.append(row)
        for quality in ({'status': 'unverified', 'structural_status': 'unverified', 'checks': []},
                self.content_result(),
                {**self.content_result(False), 'structural_status': 'fail'},
                {'status': 'fail', 'structural_status': 'pass',
                 'checks': [{'criterion': 'frame_rate', 'status': 'fail'}]}):
            row = copy.deepcopy(valid)
            row['candidate_quality'] = quality
            invalid.append(row)
        for row in invalid:
            with self.subTest(row=row):
                self.state['runs'] = {key: row}
                self.save()
                with patch.object(pilot, '_run_split', new_callable=AsyncMock), patch.object(
                        pilot, 'propose', new_callable=AsyncMock) as propose:
                    with self.assertRaises(ValueError):
                        await pilot.develop(self.root, object())
                    propose.assert_not_called()

    async def test_completed_content_failure_opens_development_without_holdout(self):
        key = pilot._keys(self.state, 'dev', 'baseline')[0]
        self.state['runs'][key] = {'state': 'finished', 'split': 'dev', 'procedure_id': 'baseline',
            'task_id': key.split('/')[1], 'receipt': {'status': 'unverified', 'candidate_complete': True},
            'source_immutable': True, 'structural': {'status': 'unverified'},
            'candidate_quality': self.content_result(False, .2),
            'metrics': pilot._metrics({'candidate_complete': True}, {'status': 'unverified'},
                                      True, self.content_result(False, .2))}
        self.save()
        proposals = [{'procedure': 'Inspect each frame.', 'hypothesis': 'Fewer temporal defects.'},
                     {'procedure': 'Preserve source layers.', 'hypothesis': 'Fewer fidelity defects.'}]
        with patch.object(pilot, '_run_split', new_callable=AsyncMock) as run_split, patch.object(
                pilot, 'propose', new_callable=AsyncMock, return_value=proposals) as propose:
            state = await pilot.develop(self.root, object())
        propose.assert_awaited_once()
        self.assertFalse(state['holdout_started'])
        self.assertEqual([call.args[2] for call in run_split.await_args_list], ['dev', 'dev'])
        self.assertEqual([call.args[3] for call in run_split.await_args_list],
                         [['baseline'], ['candidate-1', 'candidate-2']])
        evidence = propose.await_args.args[0]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['candidate_quality_checks'][0]['criterion'], 'content:eye-1')

    async def test_attempt_scores_different_delivered_and_final_candidate_snapshots(self):
        delivered = self.root / 'delivered.png'
        candidate = self.root / 'candidate.png'
        delivered.write_bytes(b'delivered artwork')
        candidate.write_bytes(b'final modified artwork')
        value = receipt()
        value.update(artifact_path=str(delivered), artifact_sha256=pilot._file_hash(delivered),
                     candidate_path=str(candidate), candidate_sha256=pilot._file_hash(candidate),
                     candidate_complete=True)
        value['reviews'][0]['sha256'] = value['artifact_sha256']
        runtime = type('Runtime', (), {'run': AsyncMock(return_value=value)})()

        def score(task_id, path, workspace, **kwargs):
            return self.content_result(path == delivered, .9 if path == delivered else .2)

        row = next(row for row in self.state['tasks'] if row['split'] == 'dev')
        with patch('oak.media_pilot_hard_tasks.materialize_task', return_value={'source_hashes': {}}), \
                patch('oak.media_pilot_hard_quality.evaluate_artifact', side_effect=score) as evaluate, \
                redirect_stdout(io.StringIO()):
            await pilot._run_attempt(self.root, self.state, 'dev', row, 'baseline', 1, runtime)
        record = self.state['runs'][pilot._keys(self.state, 'dev', 'baseline')[0]]
        self.assertEqual(evaluate.call_count, 2)
        self.assertTrue(record['metrics']['passed'])
        self.assertFalse(record['metrics']['quality_passed'])
        self.assertEqual(record['metrics']['quality_score'], .2)
        self.assertFalse(record['metrics']['reviewer_false_pass'])


if __name__ == '__main__':
    unittest.main()
