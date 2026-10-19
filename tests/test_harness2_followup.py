"""Learned follow-up lifecycle with local Controller/receipt fixtures only."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.controller import Controller
from oak.harness2_followup import FollowupInterrupted, run_followup
from oak.harness2_media import make_descriptor
from oak.harness2_pipeline import BudgetStopped, MODEL, PipelineStore, UPSTREAM_COMMIT, digest, file_sha
from oak.harness_versions import HarnessVersions


class LearnedFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'private-pipeline'
        self.scope = ('learned-test', 1, 'media-depth')
        self.store = PipelineStore(self.root, *self.scope)
        self.addCleanup(self.store.close)
        self.versions = HarnessVersions(self.root / 'versions.sqlite3')
        self.addCleanup(self.versions.close)
        self.parent = self.versions.bootstrap_current(*self.scope, '')
        self.store.record_case({'visibility': 'public_fixture', 'source': 'local-admission-fixture'},
                               signature='source-preserving-depth', approved=True)
        self.manifest = {split: [{'descriptor': make_descriptor('followup-admission-fixture', i, split=split),
                         'commitment': {'inputs': {'source.png': digest(split)}}}]
                         for i, split in enumerate(('dev', 'regression', 'holdout'))}
        self.config = {'model': MODEL, 'effort': 'low', 'upstream_commit': UPSTREAM_COMMIT,
                      'max_producers': 16, 'max_roles': 8, 'max_role_tools': 512,
                      'producer_timeout': 600, 'role_timeout': 300}
        self.cycle = self.store.create_cycle('source-preserving-depth', self.manifest, self.config)
        self.version = self.versions.register(*self.scope, 'Inspect source boundaries before composing.',
            parent_id=self.parent['id'], evidence={'cycle_id': self.cycle['id'], 'fixture_only': True})
        self.frozen = {'candidate_id': self.version['id'], 'candidate_sha256': self.version['procedure_sha256']}
        self.decision_evidence = {'cycle_id': self.cycle['id'], 'freeze_sha256': digest(self.frozen),
                                  'evaluation_sha256': 'a' * 64, 'fixture_only': True}
        self.decision = self.versions.record_decision(self.version['id'], accepted=True,
            evidence=self.decision_evidence, reason='Local admission protocol fixture, not real model quality evidence.')
        self.store.update(self.cycle['id'], frozen=self.frozen,
            decision={'accepted': True, 'candidate_id': self.version['id'], 'version_decision_id': self.decision['id']},
            phase='completed')
        self.versions.activate(self.version['id'])
        self.calls, self.grades = [], []
        self.pass_quality = True
        self.unverified = False

    def materializer(self, descriptor, workspace):
        source = workspace / 'source.png'
        source.write_bytes(('Local synthetic source ' + descriptor['id']).encode())
        return {'task_id': descriptor['id'], 'brief': 'Local protocol task.',
                'source_hashes': {str(source): file_sha(source)}}

    def runner(self, task, workspace, activation, *, runtime_home, timeout):
        self.calls.append((task, workspace, activation))
        c = Controller(None, workspace.parent / 'fixture-controller.sqlite', workspace,
                       lambda *_: None, config={'harness_activation': activation})
        try:
            c.prepare_harness_input(1, 'local-' + workspace.parent.name,
                                    [{'type': 'text', 'text': task['brief']}])
            bound = c.bind_harness_turn(1, 'local-' + workspace.parent.name, 'fixture-native-turn-' + str(len(self.calls)))
        finally:
            c.close()
        snapshot = workspace.parent / 'frozen-artifact.png'
        snapshot.write_bytes(b'Exact local artifact bytes; not a decoded media fixture.')
        sha = file_sha(snapshot)
        return {'status': 'completed', 'delivered': True, 'source_immutable': True,
                'model': MODEL, 'effort': 'low', 'tool_calls': 4, 'elapsed_seconds': 0.1,
                'artifact_path': str(snapshot), 'artifact_sha256': sha,
                'candidate_path': str(snapshot), 'candidate_sha256': sha,
                'reviews': [{'verdict': 'pass', 'sha256': sha}],
                'harness_application': {key: bound[key] for key in ('id', 'project_id', 'chat_id', 'domain',
                    'request_id', 'version_id', 'procedure_sha256', 'context_sha256', 'input_sha256', 'turn_id')}}

    def evaluator(self, descriptor, directory, *, freeze_sha256):
        self.grades.append(directory)
        record = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(record['state'], 'finished')
        self.assertEqual(record['descriptor_sha256'], digest(descriptor))
        self.assertEqual(record['source_commitment']['inputs'], {'source.png':
            file_sha(directory / 'workspace' / 'source.png')})
        return {'freeze_sha256': freeze_sha256, 'procedure_sha256': record['procedure_sha256'],
            'task_id': descriptor['id'], 'status': 'completed', 'completed': True, 'delivered': True,
            'terminal_integrity': record['terminal_integrity'], 'model_valid': True, 'budget_valid': True,
            'source_immutable': True, 'unverified': self.unverified,
            'artifact_sha256': record['receipt']['artifact_sha256'], 'visual_passed': True,
            'mechanical_passed': self.pass_quality, 'joint_passed': self.pass_quality,
            'mechanical': {'status': 'pass' if self.pass_quality else 'fail'},
            'elapsed_seconds': 0.1, 'tool_calls': 4}

    def follow(self, **changes):
        return run_followup(self.root, project=self.scope[0], chat=self.scope[1], domain=self.scope[2],
            cycle_id=self.cycle['id'], timeout=30, runner=self.runner, evaluator=self.evaluator,
            materializer=self.materializer, **changes)

    def test_real_controller_hook_binds_learned_version_and_same_sha_independent_grade(self):
        report = self.follow()
        self.assertEqual(report['outcome'], 'joint_pass')
        self.assertTrue(report['application_provenance_verified'])
        self.assertEqual(report['harness_application']['version_id'], self.version['id'])
        self.assertEqual(report['harness_application']['turn_id'], 'fixture-native-turn-1')
        self.assertEqual(report['learned_admission_decision_id'], self.decision['id'])
        self.assertEqual(report['evaluation']['artifact_sha256'], report['artifact_sha256'])
        self.assertFalse(report['quality_gain_claim'])
        self.assertEqual(report['mode'], 'local-fixture')
        self.assertEqual(report['producer_attempts'], 1)
        self.assertIsNone(report['rollback'])
        self.assertEqual(self.store.usage(self.cycle['id']), [])

    def test_completed_cache_survives_reopen_without_extra_producer_or_grader(self):
        first = self.follow()
        second = self.follow()
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.grades), 1)

    def test_unaccepted_incomplete_or_inactive_cycle_cannot_launch(self):
        with self.store.db:
            self.store.db.execute('UPDATE cycles SET phase=? WHERE id=?', ('evaluating', self.cycle['id']))
        with self.assertRaises(ValueError):
            self.follow()
        with self.store.db:
            self.store.db.execute('UPDATE cycles SET phase=? WHERE id=?', ('completed', self.cycle['id']))
        self.versions.rollback(*self.scope, reason='Local inactive version fixture.')
        with self.assertRaises(ValueError):
            self.follow()
        self.assertEqual(self.calls, [])

    def test_administrative_fixture_cannot_masquerade_as_independently_learned(self):
        with self.versions.db:
            self.versions.db.execute('UPDATE harness_decisions SET kind=? WHERE version_id=?',
                                     ('controlled-fixture', self.version['id']))
        with self.assertRaises(ValueError):
            self.follow()
        self.assertEqual(self.calls, [])

    def test_verified_terminal_quality_failure_monitors_and_rolls_back_once(self):
        self.pass_quality = False
        report = self.follow()
        self.assertEqual(report['outcome'], 'verified_quality_negative')
        self.assertEqual(report['rollback']['to_version_id'], self.parent['id'])
        self.assertEqual(self.versions.resolve(*self.scope)['id'], self.parent['id'])
        self.assertEqual(self.follow(), report)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            self.versions.activate(self.version['id'])
        case = next(row for row in self.store.cases() if row['id'] == report['monitoring_case_id'])
        self.assertFalse(case['approved'])
        self.assertNotIn('descriptor', case['payload'])
        self.assertEqual(case['payload']['source'], 'learned-candidate-native-followup')

    def test_unverified_terminal_is_not_retried_and_does_not_roll_back(self):
        self.pass_quality, self.unverified = False, True
        report = self.follow()
        self.assertEqual(report['outcome'], 'terminal_unverified_or_incomplete')
        self.assertIsNone(report['rollback'])
        self.assertEqual(self.versions.resolve(*self.scope)['id'], self.version['id'])
        self.assertEqual(self.follow(), report)
        self.assertEqual(len(self.calls), 1)

    def test_unknown_only_retries_within_two_attempt_cap(self):
        original = self.runner
        attempts = []

        def interrupted(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise ConnectionError('Local unknown intent.')
            return original(*args, **kwargs)

        with patch.object(self, 'runner', interrupted):
            with self.assertRaises(FollowupInterrupted):
                self.follow()
            report = self.follow()
        self.assertEqual(report['producer_attempts'], 2)
        self.assertEqual(report['interrupted_unknown_attempts'], 1)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.store.usage(self.cycle['id']), [])

    def test_crash_before_intent_archives_preparation_without_an_extra_producer_attempt(self):
        from oak import harness2_followup as helper
        original_write, original_materializer = helper.write_once, self.materializer

        def before_intent(path, value):
            if Path(path).name == 'intent.json':
                raise RuntimeError('Local crash after materialization, before producer intent.')
            return original_write(path, value)

        def fresh_only(descriptor, workspace):
            if (workspace / 'source.png').exists():
                raise ValueError('Fixture materializer requires fresh input storage.')
            return original_materializer(descriptor, workspace)

        with patch.object(self, 'materializer', fresh_only):
            with patch.object(helper, 'write_once', side_effect=before_intent):
                with self.assertRaises(RuntimeError):
                    self.follow()
            self.assertEqual(self.calls, [])
            report = self.follow()
        directory = self.root / 'scopes' / digest(self.scope) / 'learned-followups' / self.cycle['id']
        archive = directory / 'interrupted-preparations/preparation-0001'
        metadata = json.loads((archive / 'preparation.json').read_text())
        self.assertFalse(metadata['producer_started'])
        self.assertFalse(metadata['quality_failure_claim'])
        self.assertEqual(metadata['classification'], 'interrupted_preparation_before_intent')
        self.assertEqual(metadata['files']['workspace/source.png'], file_sha(archive/'workspace/source.png'))
        self.assertTrue((archive / 'task.json').is_file())
        self.assertFalse((archive / 'intent.json').exists())
        self.assertEqual(report['producer_attempts'], 1)
        self.assertEqual(report['interrupted_unknown_attempts'], 0)
        self.assertEqual(report['interrupted_preparation_count'], 1)
        self.assertEqual((len(self.calls), len(self.grades)), (1, 1))
        self.assertEqual(self.follow(), report)

    def test_preparation_archive_is_bounded_and_never_dispatches_or_deletes_evidence(self):
        from oak import harness2_followup as helper
        original_write = helper.write_once

        def before_intent(path, value):
            if Path(path).name == 'intent.json':
                raise RuntimeError('Local pre-intent crash.')
            return original_write(path, value)

        with patch.object(helper, '_MAX_INTERRUPTED_PREPARATIONS', 2):
            with patch.object(helper, 'write_once', side_effect=before_intent):
                for _ in range(3):
                    with self.assertRaises(RuntimeError):
                        self.follow()
            with self.assertRaises(BudgetStopped):
                self.follow()
        directory = self.root / 'scopes' / digest(self.scope) / 'learned-followups' / self.cycle['id']
        self.assertEqual(len(list((directory/'interrupted-preparations').iterdir())), 2)
        self.assertTrue((directory/'attempts/1/workspace/source.png').is_file())
        self.assertEqual(self.calls, [])

    def test_native_path_rejects_explicit_fixture_admission_without_launching(self):
        with patch('oak.harness2_followup._native') as native:
            with self.assertRaisesRegex(ValueError, 'fixture-only admission'):
                run_followup(self.root, project=self.scope[0], chat=self.scope[1], domain=self.scope[2],
                             cycle_id=self.cycle['id'], timeout=30)
        native.assert_not_called()

    def test_exhausted_unknown_cap_cannot_dispatch_third_attempt_or_rollback(self):
        with patch.object(self, 'runner', side_effect=ConnectionError('Local unknown intent.')) as runner:
            for _ in range(2):
                with self.assertRaises(FollowupInterrupted):
                    self.follow()
            with self.assertRaises(BudgetStopped):
                self.follow()
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(self.versions.resolve(*self.scope)['id'], self.version['id'])

    def test_stop_and_root_worker_lock_prevent_native_dispatch(self):
        self.store.setting('stopped', True)
        with self.assertRaises(BudgetStopped):
            self.follow()
        self.store.setting('stopped', False)
        with self.store.worker():
            with self.assertRaises(RuntimeError):
                self.follow()
        self.assertEqual(self.calls, [])

    def test_disclosed_followup_source_cannot_be_renamed_as_future_fresh_holdout(self):
        report = self.follow()
        exposure = self.store.db.execute('SELECT * FROM exposure WHERE scope=? AND identity=?',
                                          (self.store.key, report['exposure_identity'])).fetchone()
        self.assertEqual(exposure['split'], 'followup')
        self.assertEqual(exposure['cycle_id'], self.cycle['id'])
        self.assertTrue(exposure['opened'])
        tasks = copy.deepcopy(self.manifest)
        tasks['holdout'] = [{'descriptor': {**report['descriptor'], 'id': 'renamed-followup', 'split': 'holdout'},
                            'commitment': report['source_commitment']}]
        with self.assertRaises(ValueError):
            self.store.create_cycle('source-preserving-depth', tasks, self.config)

    def test_missing_or_forged_application_provenance_never_claims_activation_or_rolls_back(self):
        original = self.runner

        def missing(*args, **kwargs):
            receipt = original(*args, **kwargs)
            receipt['harness_application']['turn_id'] = 'unbound-claim'
            return receipt

        self.pass_quality = False
        with patch.object(self, 'runner', missing):
            report = self.follow()
        self.assertEqual(report['outcome'], 'activation_unverified')
        self.assertFalse(report['application_provenance_verified'])
        self.assertIsNone(report['rollback'])

    def test_grader_interruption_resumes_from_terminal_native_without_reexecution(self):
        with patch.object(self, 'evaluator', side_effect=RuntimeError('Local grade interruption.')):
            with self.assertRaises(RuntimeError):
                self.follow()
        report = self.follow()
        self.assertEqual(report['outcome'], 'joint_pass')
        self.assertEqual(len(self.calls), 1)

    def test_changed_completed_artifact_fails_closed_without_replaying(self):
        report = self.follow()
        directory = self.root / 'scopes' / digest(self.scope) / 'learned-followups' / self.cycle['id']
        artifact = directory / 'attempts' / str(report['terminal_attempt']) / 'frozen-artifact.png'
        artifact.write_bytes(b'Changed after completion.')
        with self.assertRaises(ValueError):
            self.follow()
        self.assertEqual(len(self.calls), 1)

    def test_changed_terminal_metadata_cannot_hide_behind_valid_artifacts_and_native_cache(self):
        report = self.follow()
        directory = self.root / 'scopes' / digest(self.scope) / 'learned-followups' / self.cycle['id']
        path = directory / 'attempts' / str(report['terminal_attempt']) / 'receipt.json'
        wrapper = json.loads(path.read_text())
        wrapper['receipt']['tool_calls'] += 1
        path.write_text(json.dumps(wrapper))
        with self.assertRaises(ValueError):
            self.follow()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.grades), 1)

    def test_cached_report_labels_identity_outcome_and_embedded_grade_are_bound(self):
        original = self.follow()
        directory = self.root / 'scopes' / digest(self.scope) / 'learned-followups' / self.cycle['id']
        changes = ({'mode': 'native-subscription'}, {'learned_version_id': 'other-version'},
                   {'learned_admission_decision_id': 'other-decision'}, {'outcome': 'verified_quality_negative'},
                   {'native_conditions_verified': False}, {'quality_gain_claim': True},
                   {'evaluation': {**original['evaluation'], 'joint_passed': False}})
        for change in changes:
            altered = {**original, **change}
            (directory/'report.json').write_text(json.dumps(altered))
            with self.subTest(fields=tuple(change)), self.assertRaises(ValueError):
                self.follow()
        (directory/'report.json').write_text(json.dumps(original))
        self.assertEqual(self.follow(), original)
        self.assertEqual((len(self.calls), len(self.grades)), (1, 1))

    def test_cached_report_cannot_claim_rollback_without_verified_negative_and_event(self):
        report = self.follow()
        directory = self.root / 'scopes' / digest(self.scope) / 'learned-followups' / self.cycle['id']
        report['rollback'] = {'from_version_id': self.version['id'], 'to_version_id': self.parent['id']}
        (directory/'report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'non-negative follow-up'):
            self.follow()
        self.assertEqual(self.versions.resolve(*self.scope)['id'], self.version['id'])
        self.assertEqual((len(self.calls), len(self.grades)), (1, 1))

    def test_cached_active_version_describes_completion_before_a_later_owner_rollback(self):
        report = self.follow()
        self.assertIn('recorded_at', report)
        self.versions.rollback(*self.scope, reason='Later local owner rollback fixture.')
        self.assertEqual(self.versions.resolve(*self.scope)['id'], self.parent['id'])
        self.assertEqual(self.follow(), report)
        self.assertEqual(report['active_version_id'], self.version['id'])
        self.assertEqual((len(self.calls), len(self.grades)), (1, 1))

    def test_conclusive_negative_grade_for_other_artifact_never_rolls_back(self):
        self.pass_quality = False
        original_evaluator = self.evaluator

        def wrong_sha(*args, **kwargs):
            return {**original_evaluator(*args, **kwargs), 'artifact_sha256': 'f' * 64}

        with patch.object(self, 'evaluator', wrong_sha):
            with self.assertRaisesRegex(ValueError, 'conclusive follow-up verdict'):
                self.follow()
        self.assertEqual(self.versions.resolve(*self.scope)['id'], self.version['id'])
        self.assertFalse(any(row['action'] == 'rollback' for row in self.versions.activation_history(*self.scope)))

    def test_non_one_chat_scope_is_explicitly_unsupported_without_substitution(self):
        with self.assertRaisesRegex(ValueError, 'scope 1'):
            run_followup(self.root, project=self.scope[0], chat=7, domain=self.scope[2], cycle_id=self.cycle['id'],
                         runner=self.runner, evaluator=self.evaluator, materializer=self.materializer)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
