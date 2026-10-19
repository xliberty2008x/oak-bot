"""Portable synthetic evidence export; no models, codecs or provider calls."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from oak.harness2_pipeline import PipelineStore, digest
if os.environ.get('OAK_H2_HISTORY_REPORT'):
    spec = importlib.util.spec_from_file_location('oak._history_report_draft', os.environ['OAK_H2_HISTORY_REPORT'])
    report_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report_module)
    export_report, main = report_module.export_report, report_module.main
else:
    from oak.harness2_report import export_report, main
from oak.harness_versions import HarnessVersions


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return path


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'private-pipeline'
        self.scope = ('public-project', 1, 'media-depth')
        self.store = PipelineStore(self.root, *self.scope)
        self.addCleanup(self.store.close)
        self.store.record_case({'visibility': 'public_fixture', 'case': 'source detail'},
                               signature='layer', approved=True)
        self.manifest = {split: [{'descriptor': {'id': split, 'split': split},
                                  'commitment': {'inputs': {'source.png': digest(split)}}}]
                         for split in ('dev', 'regression', 'holdout')}
        self.config = {'model': 'gpt-6.1-sol', 'effort': 'low',
            'upstream_commit': '1cfe254e8d25b15f37048c3730134686abe60335',
            'max_producers': 12, 'max_roles': 4, 'max_role_tools': 64,
            'producer_timeout': 600, 'role_timeout': 300}
        self.cycle = self.store.create_cycle('layer', self.manifest, self.config)
        self.scope_root = self.root / 'scopes' / digest(self.scope)
        self.directory = self.scope_root / 'cycles' / self.cycle['id']
        self.directory.mkdir(parents=True)

    def finish(self):
        self.search = {'upstream': {'status': 'ok', 'modes': {'seq': {'ledger': {'R': 4, 'P': 3}}}},
                       'provenance': {'chosen_harness': str(self.directory / 'upstream/final'),
                                      'library_entries_before': [], 'library_entries_after': []}}
        versions = HarnessVersions(self.root / 'versions.sqlite3')
        original = versions.bootstrap_current(*self.scope, '')
        candidate = versions.register(*self.scope, 'Inspect source layers.', parent_id=original['id'],
                                      evidence={'kind': 'public synthetic'})
        versions.record_decision(candidate['id'], accepted=False, evidence={'kind': 'tie'}, reason='No gain.')
        versions.close()
        self.frozen = {'candidate_id': candidate['id'], 'candidate_sha256': candidate['procedure_sha256'],
                       'parent_id': original['id'], 'parent_sha256': original['procedure_sha256'],
                       'manifest_sha256': digest(self.manifest), 'config_sha256': digest(self.config),
                       'search_sha256': digest(self.search), 'frozen_at': 'fixed'}
        baseline = {'status': 'completed', 'delivered': True, 'joint_passed': True,
                    'visual_passed': True, 'mechanical_passed': True, 'source_immutable': True,
                    'material_defect': False, 'unverified': False, 'elapsed_seconds': 2,
                    'tool_calls': 4, 'review_calls': 1, 'mechanical': {'quality_score': .95},
                    'artifact_sha256': sha(b'artifact')}
        self.evaluation = {'freeze_sha256': digest(self.frozen), 'pairs': [
            {'task_id': split, 'split': split, 'baseline': baseline,
             'candidate': {**baseline, 'tool_calls': 6, 'elapsed_seconds': 3}}
            for split in ('regression', 'holdout')]}
        self.decision = {'accepted': False, 'reasons': ['no_strict_fresh_holdout_gain'],
                         'candidate_id': candidate['id'], 'active_id': original['id']}
        for name, value in (('parent.json', {'id': original['id']}), ('upstream-result.json', self.search),
                            ('freeze.json', self.frozen), ('evaluation.json', self.evaluation),
                            ('decision.json', self.decision)):
            write(self.directory / name, value)
        self.store.update(self.cycle['id'], search=self.search, frozen=self.frozen,
                          decision=self.decision, phase='completed')
        source = self.directory / 'development/executions/native/attempt-1/workspace/inputs/source.png'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'source')
        self.artifact = source.parent.parent.parent / 'snapshots/final.png'
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b'artifact')
        receipt = {'status': 'completed', 'delivered': True, 'artifact_path': str(self.artifact),
                   'artifact_sha256': sha(b'artifact'), 'candidate_path': str(self.artifact),
                   'candidate_sha256': sha(b'artifact'), 'model': 'gpt-6.1-sol', 'effort': 'low',
                   'reviews': [{'verdict': 'pass', 'sha256': sha(b'artifact')}],
                   'tool_calls': 4, 'elapsed_seconds': 2}
        self.producer = write(self.directory / 'development/executions/native/receipt.json',
            {'state': 'finished', 'run_id': 'namespace/task/base1', 'task': {'source_hashes': {str(source): sha(b'source')}},
             'receipt': receipt, 'terminal_integrity': 'verified'})
        write(self.producer.parent / 'interrupted-0.json',
              {'state': 'interrupted', 'classification': 'interrupted_without_terminal_receipt', 'attempt': 0})
        attempt = self.directory / 'roles/cache/attempt-0001'
        harness = attempt / 'context/harness/AGENTS.md'
        harness.parent.mkdir(parents=True)
        harness.write_text('Inspect source layers.')
        (attempt / 'response.txt').write_text('<notes>Preserve local layers.</notes>')
        self.role = write(attempt / 'receipt.json', {'status': 'completed', 'attempt': 1, 'cache_key': 'cache',
            'model': 'gpt-6.1-sol', 'channel': 'oak_chatgpt_subscription',
            'response_sha256': sha((attempt / 'response.txt').read_bytes()),
            'harness_sha256': digest({'AGENTS.md': sha(harness.read_bytes())}),
            'usd': None, 'elapsed_seconds': 2, 'tool_calls': [{'tool': 'oak_h2_read', 'path': 'task.md'}]})
        write(self.directory / 'roles/cache/attempt-0000/receipt.json',
              {'status': 'interrupted_unknown', 'attempt': 0, 'quality_failure_claim': False})
        write(self.directory / 'roles/failed/attempt-0001/receipt.json',
              {'status': 'failed', 'attempt': 1, 'error_code': 'role_timeout'})
        return candidate

    def test_export_rejects_pending_cycles_before_disclosing_holdout(self):
        with self.assertRaisesRegex(ValueError, 'completed cycles'):
            export_report(self.root, *self.scope[:2], domain=self.scope[2])

    def test_private_case_fails_closed_even_if_unapproved(self):
        self.finish()
        self.store.record_case({'visibility': 'explicit_private', 'private': 'SECRET_CANARY'},
                               signature='private', approved=False)
        with self.assertRaisesRegex(ValueError, 'non-public cases') as caught:
            export_report(self.root, *self.scope[:2])
        self.assertNotIn('SECRET_CANARY', str(caught.exception))

    def test_portable_report_retains_all_attempts_and_measured_denominators(self):
        self.finish()
        report = export_report(self.root, *self.scope[:2])
        text = json.dumps(report)
        self.assertNotIn(str(self.root), text)
        self.assertIn('/pipeline/scopes/', text)
        self.assertIsNone(report['cost_usd'])
        self.assertFalse(report['broad_quality_improvement_claim'])
        cycle = report['cycles'][0]
        self.assertEqual(cycle['attempt_counts']['producer_records'], 2)
        self.assertEqual(cycle['attempt_counts']['producer_interrupted'], 1)
        self.assertEqual(cycle['attempt_counts']['role_statuses'],
                         {'completed': 1, 'failed': 1, 'interrupted_unknown': 1})
        self.assertEqual(cycle['producer_records'][0]['artifact_bindings']['artifact']['sha256'], sha(b'artifact'))
        candidate = next(group for group in cycle['metrics']['groups']
                         if group['split'] == 'holdout' and group['arm'] == 'candidate')
        self.assertEqual(candidate['planned_distinct_tasks'], 1)
        self.assertEqual(candidate['tool_calls']['mean'], 6)
        self.assertEqual(candidate['elapsed_seconds']['median'], 3)
        self.assertEqual(cycle['metrics']['ties'], 1)
        self.assertTrue(report['original_file_sha256'])
        digest_field = report.pop('portable_content_sha256')
        self.assertEqual(digest(report), digest_field)

    def test_original_receipt_hash_preserved_before_path_substitution(self):
        self.finish()
        original = sha(self.producer.read_bytes())
        report = export_report(self.root, *self.scope[:2])
        self.assertEqual(report['original_file_sha256'][self.producer.relative_to(self.root).as_posix()], original)
        self.assertEqual(report['cycles'][0]['producer_records'][0]['raw_receipt_sha256'], original)

    def test_changed_artifact_or_role_harness_is_not_rescored_or_silently_accepted(self):
        self.finish()
        self.artifact.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'recorded SHA'):
            export_report(self.root, *self.scope[:2])
        self.artifact.write_bytes(b'artifact')
        (self.role.parent / 'context/harness/AGENTS.md').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'harness no longer'):
            export_report(self.root, *self.scope[:2])

    def test_recorded_terminal_integrity_failure_remains_a_failure(self):
        self.finish()
        record = json.loads(self.producer.read_text())
        record['terminal_integrity'] = 'failed'
        write(self.producer, record)
        self.artifact.write_bytes(b'known-invalid-terminal-bytes')
        report = export_report(self.root, *self.scope[:2])
        binding = report['cycles'][0]['producer_records'][0]['artifact_bindings']['artifact']
        self.assertFalse(binding['verified'])
        self.assertEqual(binding['expected_sha256'], sha(b'artifact'))

    def test_evidence_cannot_read_foreign_paths_or_links(self):
        self.finish()
        record = json.loads(self.producer.read_text())
        record['receipt']['artifact_path'] = '/etc/passwd'
        write(self.producer, record)
        with self.assertRaisesRegex(ValueError, 'outside'):
            export_report(self.root, *self.scope[:2])
        record['receipt']['artifact_path'] = str(self.scope_root / '..' / '..' / 'versions.sqlite3')
        write(self.producer, record)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            export_report(self.root, *self.scope[:2])
        record['receipt']['artifact_path'] = str(self.artifact)
        write(self.producer, record)
        self.artifact.unlink()
        self.artifact.symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'links'):
            export_report(self.root, *self.scope[:2])

    def test_other_private_scope_is_never_exported(self):
        self.finish()
        foreign = PipelineStore(self.root, 'foreign-project', 2)
        try:
            foreign.record_case({'visibility': 'explicit_private', 'private': 'FOREIGN_CANARY'},
                                 signature='foreign')
        finally:
            foreign.close()
        self.assertNotIn('FOREIGN_CANARY', json.dumps(export_report(self.root, *self.scope[:2])))

    def test_credential_like_content_is_rejected_without_printing_it(self):
        self.finish()
        self.store.record_case({'visibility': 'public_fixture', 'bad': 'Bearer SECRETKEY01234567890'},
                               signature='bad-label', approved=False)
        with self.assertRaisesRegex(ValueError, 'Credential-like') as caught:
            export_report(self.root, *self.scope[:2])
        self.assertNotIn('SECRETKEY', str(caught.exception))

    def test_cli_writes_portable_json_without_mutating_database(self):
        self.finish()
        output = Path(self.tmp.name) / 'public/report.json'
        original_rows = self.store.db.execute('SELECT COUNT(*) FROM cycles').fetchone()[0]
        main(['--root', str(self.root), '--project', self.scope[0], '--chat', str(self.scope[1]),
              '--output', str(output)])
        self.assertTrue(output.is_file())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(str(self.root), output.read_text())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM cycles').fetchone()[0], original_rows)
        with self.assertRaisesRegex(ValueError, 'outside the private pipeline'):
            main(['--root', str(self.root), '--project', self.scope[0], '--chat', str(self.scope[1]),
                  '--output', str(self.directory / 'decision.json')])

    def test_controlled_activation_report_keeps_terminal_receipts_and_no_quality_claim(self):
        from unittest.mock import patch
        from oak.harness2_activation_demo import run_demo
        self.finish()
        def runner(task, workspace, activation, **kwargs):
            versions = HarnessVersions(activation['store_path'])
            try:
                app = versions.prepare_application(activation['project_id'], 1, activation['domain'],
                    'fixture-' + str(workspace), input_sha256='a' * 64)
                app = versions.bind_turn(app['id'], 'fixture-turn')
                artifact = Path(workspace) / 'final.png'
                artifact.write_bytes(b'controlled-followup')
                return {'status': 'completed', 'delivered': True, 'source_immutable': True,
                    'model': 'gpt-6.1-sol', 'effort': 'low', 'tool_calls': 1, 'elapsed_seconds': .1,
                    'artifact_path': str(artifact), 'artifact_sha256': sha(artifact.read_bytes()),
                    'reviews': [{'verdict': 'pass', 'sha256': sha(artifact.read_bytes())}],
                    'harness_application': {key: app[key] for key in ('id', 'project_id', 'chat_id', 'domain',
                        'request_id', 'version_id', 'procedure_sha256', 'context_sha256', 'input_sha256', 'turn_id')}}
            finally:
                versions.close()
        with patch('oak.harness2_media.materialize_descriptor', return_value={'brief': 'local fixture'}):
            controlled = run_demo(self.root, project=self.scope[0], timeout=30, runner=runner)
        report = export_report(self.root, *self.scope[:2])
        activation = report['controlled_activation']
        self.assertEqual(activation['report']['mode'], 'local-fixture')
        self.assertFalse(activation['report']['quality_gain_claim'])
        self.assertEqual(len(activation['followup_receipts']), 2)
        self.assertTrue(all(item['artifact_bindings']['artifact']['verified']
                            for item in activation['followup_receipts']))
        self.assertEqual(report['versions']['active'][0]['version_id'], controlled['restored_version_id'])
        self.assertEqual(len(report['versions']['applications']), 2)

    def test_existing_private_bootstrap_procedure_is_not_exported(self):
        self.finish()
        other = HarnessVersions(self.root / 'versions.sqlite3')
        try:
            # A second domain's private procedure must be excluded by scope.
            other.bootstrap_current(self.scope[0], 1, 'private-domain', 'PRIVATE_PERSONA_CANARY')
        finally:
            other.close()
        self.assertNotIn('PRIVATE_PERSONA_CANARY', json.dumps(export_report(self.root, *self.scope[:2])))
        other = HarnessVersions(self.root / 'versions.sqlite3')
        try:
            other.register(*self.scope, 'PRIVATE_PERSONA_CANARY', source='bootstrap-current')
        finally:
            other.close()
        with self.assertRaisesRegex(ValueError, 'non-public existing procedure'):
            export_report(self.root, *self.scope[:2])

    def test_prior_no_entry_context_copy_and_actual_read_request_preserve_empty_library(self):
        self.finish()
        context = {'scope': list(self.scope), 'cases': [], 'attempt_history': {
            'source': 'Oak adapter attempt_history', 'attempts': [
                {'role': 'experience.seq1', 'response': 'NO_ENTRY', 'response_sha256': sha(b'NO_ENTRY')}]}}
        path = write(self.directory / 'approved-development-evidence.json', context)
        copied = self.role.parent / 'context/source/OAK_CASE_EVIDENCE.json'
        copied.parent.mkdir(parents=True)
        copied.write_bytes(path.read_bytes())
        receipt = json.loads(self.role.read_text())
        receipt['role'] = 'inv1.a1'
        receipt['tool_calls'].append({'tool': 'oak_h2_read', 'path': 'source/OAK_CASE_EVIDENCE.json'})
        write(self.role, receipt)
        report = export_report(self.root, *self.scope[:2])
        usage = report['cycles'][0]['attempt_history_usage']
        self.assertTrue(usage['adapter_extension'])
        self.assertEqual(usage['prior_attempts_in_context'], 1)
        self.assertEqual(usage['upstream_library_entries_before'], [])
        self.assertEqual(usage['upstream_library_entries_after'], [])
        self.assertEqual(len(usage['verified_context_copies']), 1)
        self.assertEqual(len(usage['recorded_file_read_requests']), 1)
        self.assertIn('NO_ENTRY', json.dumps(report['cycles'][0]['files']['approved-development-evidence.json']))
        copied.write_text('DIFFERENT_CONTEXT')
        with self.assertRaisesRegex(ValueError, 'different approved case/history context'):
            export_report(self.root, *self.scope[:2])

    def construction(self, folder='some-generic-uuid'):
        """Tiny byte/hash fixtures; no images, FFmpeg, scores or models run."""
        root = self.scope_root / 'construction-controls' / folder
        task = self.manifest['dev'][0]
        source = root / task['descriptor']['id'] / 'workspace/inputs/source.png'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'original-construction-source')
        task['commitment']['inputs']['source.png'] = sha(source.read_bytes())
        controls = {}
        for label in ('positive', 'negative'):
            file = root / task['descriptor']['id'] / label / 'frames/000.png'
            file.parent.mkdir(parents=True)
            file.write_bytes((label + '-authored-bytes').encode())
            controls[label] = {'artifact_sha256': sha(file.read_bytes()),
                'files': {file.relative_to(root).as_posix(): sha(file.read_bytes())},
                'mechanical': {'status': 'pass' if label == 'positive' else 'fail',
                               'structural_status': 'pass', 'checks': [{'observed': label}]}}
        report = {'status': 'pass', 'cases': [{'descriptor_sha256': digest(task['descriptor']),
                    'source_commitment': task['commitment'], 'status': 'pass', 'controls': controls}]}
        path = write(root / 'preflight.json', report)
        self.config['construction_controls_sha256'] = digest(report)
        # This helper runs before finish(): update the test's initial unstarted
        # fixture row, then freeze those precise commitments in finish().
        with self.store.db:
            self.store.db.execute('UPDATE cycles SET config=?,manifest=? WHERE id=?',
                                 (json.dumps(self.config), json.dumps(self.manifest), self.cycle['id']))
        return path, report

    def test_exact_authored_control_receipt_and_all_bound_files_are_exported(self):
        path, record = self.construction()
        self.finish()
        preliminary = write(self.scope_root / 'construction-controls/superseded/preflight.json',
                            {'status': 'fail', 'PRELIMINARY_CANARY': True})
        report = export_report(self.root, *self.scope[:2])
        selected = report['cycles'][0]['authored_construction_controls']
        self.assertEqual(selected['record'], record)
        self.assertEqual(selected['raw_receipt_sha256'], sha(path.read_bytes()))
        self.assertEqual(selected['selected_receipt_sha256'], self.config['construction_controls_sha256'])
        self.assertEqual(len(selected['verified_bindings'][0]['files']), 2)
        self.assertEqual(len(selected['verified_bindings'][0]['source_files']), 1)
        self.assertNotIn('PRELIMINARY_CANARY', json.dumps(report))
        self.assertNotIn(preliminary.relative_to(self.root).as_posix(), report['original_file_sha256'])

    def test_selected_control_file_tampering_or_directory_escape_fails_closed(self):
        path, record = self.construction('1')
        self.finish()
        relative = next(iter(record['cases'][0]['controls']['negative']['files']))
        control = path.parent / relative
        control.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'recorded SHA'):
            export_report(self.root, *self.scope[:2])
        control.write_bytes(b'negative-authored-bytes')
        control.unlink()
        control.symlink_to(self.root / 'versions.sqlite3')
        with self.assertRaisesRegex(ValueError, 'links'):
            export_report(self.root, *self.scope[:2])

    def test_missing_selected_construction_receipt_is_not_replaced_with_preliminary(self):
        path, _ = self.construction('2')
        self.finish()
        path.unlink()
        write(path.parent.parent / 'preliminary/preflight.json', {'status': 'pass', 'cases': []})
        with self.assertRaisesRegex(ValueError, 'exact selected authored construction receipt'):
            export_report(self.root, *self.scope[:2])

    def test_control_file_cannot_escape_its_specific_preflight_root(self):
        path, record = self.construction()
        negative = record['cases'][0]['controls']['negative']
        original_sha = next(iter(negative['files'].values()))
        negative['files'] = {'../other-control/secret.bin': original_sha}
        write(path, record)
        self.config['construction_controls_sha256'] = digest(record)
        with self.store.db:
            self.store.db.execute('UPDATE cycles SET config=? WHERE id=?',
                                 (json.dumps(self.config), self.cycle['id']))
        self.finish()
        with self.assertRaisesRegex(ValueError, 'escaped its bound preflight directory'):
            export_report(self.root, *self.scope[:2])


if __name__ == '__main__':
    unittest.main()
