"""Controlled activation plumbing, using local receipts instead of model calls."""

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from oak.harness2_activation_demo import run_demo
from oak.harness2_pipeline import BudgetStopped, PipelineStore, file_sha, schedule_next
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

    def test_terminal_monitoring_cases_are_scoped_private_unapproved_and_survive_reopen(self):
        report = run_demo(self.root, timeout=30, runner=self.runner)
        store = PipelineStore(self.root, 'harness2-demo', 1, 'media-depth')
        try:
            cases = store.cases()
            self.assertEqual(len(cases), 2)
            self.assertEqual(store.cases(approved_only=True), [])
            self.assertEqual({row['id'] for row in cases},
                             {row['monitoring_case_id'] for row in report['followups']})
            for case in cases:
                payload = case['payload']
                self.assertEqual(payload['source'], 'activation-demo-native-followup')
                self.assertEqual(payload['visibility'], 'public_fixture')
                self.assertEqual(payload['execution_mode'], 'local-fixture')
                self.assertFalse(payload['model_backed'])
                self.assertFalse(payload['quality_gain_claim'])
                self.assertNotIn('descriptor', payload)
                self.assertNotIn('reviews', payload)
                self.assertEqual(payload['result']['status'], 'unverified')
                self.assertFalse(payload['result']['delivered'])
                self.assertFalse(payload['result']['visual_passed'])
                self.assertEqual(payload['result']['mechanical_evaluation'], 'not_run_in_activation_followup')
                self.assertIn('delivery_missing', payload['failure_reason'])
                self.assertEqual(file_sha(payload['artifact_reference']), payload['receipt_reference_sha256'])
            queue = store.db.execute('SELECT state,priority FROM queue WHERE scope=?', (store.key,)).fetchone()
            self.assertEqual(tuple(queue), ('pending', 100))
            # Monitoring metadata raises priority, but does not silently grant
            # consent or create a new optimizer task from the receipt.
            self.assertIsNone(schedule_next(store))
        finally:
            store.close()
        reopened = PipelineStore(self.root, 'harness2-demo', 1, 'media-depth')
        foreign = PipelineStore(self.root, 'harness2-demo', 2, 'media-depth')
        try:
            self.assertEqual(len(reopened.cases()), 2)
            self.assertEqual(foreign.cases(), [])
        finally:
            reopened.close()
            foreign.close()

    def test_cached_terminal_followup_monitoring_is_deduplicated_after_worker_restart(self):
        attempts = []

        def interrupt_second(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 2:
                raise ConnectionError('Controlled interruption during the second follow-up.')
            return self.runner(*args, **kwargs)

        with self.assertRaises(ConnectionError):
            run_demo(self.root, timeout=30, runner=interrupt_second)
        store = PipelineStore(self.root, 'harness2-demo', 1, 'media-depth')
        first_case = store.cases()[0]['id']
        self.assertEqual(len(store.cases()), 1)
        store.close()
        report = run_demo(self.root, timeout=30, runner=interrupt_second)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(report['followups'][0]['monitoring_case_id'], first_case)
        store = PipelineStore(self.root, 'harness2-demo', 1, 'media-depth')
        try:
            self.assertEqual(len(store.cases()), 2)
            self.assertEqual(len(store.cases(approved_only=True)), 0)
        finally:
            store.close()
        self.assertEqual(run_demo(self.root, timeout=30, runner=interrupt_second), report)
        self.assertEqual(len(attempts), 3)


if __name__ == '__main__':
    unittest.main()
