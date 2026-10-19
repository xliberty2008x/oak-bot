"""Pilot split discipline, baseline selection and honest failure accounting."""

import json
import asyncio
from contextlib import redirect_stdout
import io
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


if __name__ == '__main__':
    unittest.main()
