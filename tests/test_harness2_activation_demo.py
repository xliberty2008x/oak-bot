"""Controlled activation plumbing, using local receipts instead of model calls."""

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from oak.harness2_activation_demo import run_demo
from oak.harness2_pipeline import BudgetStopped, PipelineStore
from oak.harness_versions import HarnessVersions


class ControlledActivationDemoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / 'private-pipeline'
        self.calls = []
        materialize = patch('oak.harness2_media.materialize_descriptor',
            side_effect=lambda descriptor, workspace: {'id': descriptor['id'], 'brief': 'Local protocol fixture.'})
        materialize.start()
        self.addCleanup(materialize.stop)

    def runner(self, task, workspace, activation, *, runtime_home, timeout):
        self.calls.append({'task': task, 'workspace': workspace, 'activation': activation,
                           'runtime_home': runtime_home, 'timeout': timeout})
        versions = HarnessVersions(activation['store_path'])
        try:
            app = versions.prepare_application(activation['project_id'], 1, activation['domain'],
                                               'fixture-' + str(workspace), input_sha256='a' * 64)
            bound = versions.bind_turn(app['id'], 'fixture-turn-' + str(len(self.calls)))
            return {'status': 'unverified', 'delivered': False, 'artifact_path': None,
                    'artifact_sha256': None, 'source_immutable': True, 'model': 'gpt-6.1-sol', 'effort': 'low',
                    'tool_calls': 0, 'elapsed_seconds': 0.01,
                    'harness_application': {key: bound[key] for key in ('id', 'project_id', 'chat_id', 'domain',
                        'request_id', 'version_id', 'procedure_sha256', 'context_sha256', 'input_sha256', 'turn_id')}}
        finally:
            versions.close()

    def test_controlled_versions_rejection_restart_and_rollback_use_actual_pipeline_pointer(self):
        versions = HarnessVersions(self.root / 'versions.sqlite3')
        original = versions.bootstrap_current('harness2-demo', 1, 'media-depth', 'Existing tested pipeline procedure.')
        versions.close()
        report = run_demo(self.root, timeout=30, runner=self.runner)
        self.assertEqual(report['original_version_id'], original['id'])
        self.assertEqual(report['restored_version_id'], original['id'])
        self.assertEqual(report['mode'], 'local-fixture')
        self.assertFalse(report['quality_gain_claim'])
        self.assertFalse(report['controlled_decisions_model_backed'])
        self.assertFalse(report['live_provider_verification'])
        self.assertTrue(report['activation_provenance_verified'])
        self.assertTrue(report['restored_provenance_verified'])
        self.assertTrue(report['bad_candidate_gate_rejected'])
        self.assertTrue(report['rejected_candidate_activation_blocked'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(report['initiated_producer_attempts'], 2)
        self.assertEqual(report['followups'][0]['expected_version_id'], report['fixture_version_id'])
        self.assertEqual(report['followups'][1]['expected_version_id'], original['id'])
        versions = HarnessVersions(self.root / 'versions.sqlite3')
        try:
            self.assertEqual(versions.resolve('harness2-demo', 1, 'media-depth')['id'], original['id'])
            self.assertEqual(versions.decision(report['fixture_version_id'])['kind'], 'controlled-fixture')
            self.assertFalse(versions.decision(report['rejected_version_id'])['accepted'])
            with self.assertRaises(ValueError):
                versions.activate(report['rejected_version_id'])
            with self.assertRaises(ValueError):
                versions.activate(report['fixture_version_id'])
        finally:
            versions.close()

    def test_completed_native_receipts_and_final_report_are_reused_without_new_calls(self):
        first = run_demo(self.root, timeout=30, runner=self.runner)
        second = run_demo(self.root, timeout=30, runner=self.runner)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 2)

    def test_terminal_unverified_results_are_preserved_not_retried(self):
        report = run_demo(self.root, timeout=30, runner=self.runner)
        self.assertEqual([row['status'] for row in report['followups']], ['unverified', 'unverified'])
        self.assertEqual(report['initiated_producer_attempts'], 2)
        self.assertEqual(report['interrupted_unknown_attempts'], 0)
        self.assertFalse(any(row['delivered'] for row in report['followups']))

    def test_unknown_interruption_is_retained_and_resumed_inside_original_budget(self):
        attempts = []

        def interrupted_once(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise ConnectionError('Controlled interruption before terminal receipt.')
            return self.runner(*args, **kwargs)

        with self.assertRaises(ConnectionError):
            run_demo(self.root, timeout=30, runner=interrupted_once)
        report = run_demo(self.root, timeout=30, runner=interrupted_once)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(report['initiated_producer_attempts'], 3)
        self.assertEqual(report['interrupted_unknown_attempts'], 1)
        self.assertTrue(report['restored_provenance_verified'])

    def test_pipeline_stop_blocks_new_followups(self):
        store = PipelineStore(self.root, 'harness2-demo', 1, 'media-depth')
        store.setting('stopped', True)
        store.close()
        with self.assertRaises(BudgetStopped):
            run_demo(self.root, timeout=30, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_invalid_budgets_fail_before_native_calls(self):
        for changed in ({'timeout': 0}, {'timeout': 601}, {'max_producers': 1}, {'max_producers': 5}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                run_demo(self.root, runner=self.runner, **changed)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
