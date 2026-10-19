"""Prior DEV role evidence is real, scoped and separate from upstream Library.

Set OAK_H2_HISTORY_PIPELINE to a prepared source copy to test a prospective
between-cycle protocol change without editing a running cycle's implementation.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

if os.environ.get('OAK_H2_HISTORY_PIPELINE'):
    spec = importlib.util.spec_from_file_location('oak._history_pipeline_draft',
                                                 os.environ['OAK_H2_HISTORY_PIPELINE'])
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
else:
    from oak import harness2_pipeline as pipeline


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PriorDevelopmentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = pipeline.PipelineStore(self.root, 'selected-project', 1)
        self.addCleanup(self.store.close)
        self.config = {'model': pipeline.MODEL, 'effort': 'low',
            'upstream_commit': pipeline.UPSTREAM_COMMIT, 'max_producers': 12,
            'max_roles': 8, 'max_role_tools': 64, 'producer_timeout': 600, 'role_timeout': 300}

    def cycle(self, label, *, store=None, phase=None):
        store = store or self.store
        dev = {'id': label + '-dev', 'split': 'dev'}
        store.record_case({'visibility': 'public_fixture', 'descriptor': dev}, signature='layers', approved=True)
        manifest = {split: [{'descriptor': dev if split == 'dev' else {'id': label + '-' + split},
                             'commitment': {'inputs': {'source.png': pipeline.digest([label, split])}}}]
                    for split in ('dev', 'regression', 'holdout')}
        cycle = store.create_cycle('layers', manifest, self.config)
        if phase:
            store.update(cycle['id'], phase=phase)
        return store.cycle(cycle['id'])

    def attempt(self, cycle, *, role='experience.seq1', response='NO_ENTRY', status='completed',
                store=None, ordinal=1):
        store = store or self.store
        root = pipeline._scope_root(store) / 'cycles' / cycle['id'] / 'roles' / ('a' * 64)
        folder = root / f'attempt-{ordinal:04d}'
        folder.mkdir(parents=True, exist_ok=True)
        record = {'role': role, 'model': pipeline.MODEL, 'effort': 'low',
                  'channel': 'oak_chatgpt_subscription', 'attempt': ordinal, 'status': status}
        if response is not None:
            (folder / 'response.txt').write_text(response)
            record['response_sha256'] = sha(response.encode())
        (folder / 'receipt.json').write_text(json.dumps(record))
        return folder

    def test_verbatim_no_entry_reaches_next_cycle_without_fabricating_library(self):
        first = self.cycle('first', phase='completed')
        folder = self.attempt(first, response='<edits>Manual contours remained confounded.</edits>\nNO_ENTRY')
        second = self.cycle('second')
        result = pipeline.approved_development_evidence(self.store, second)
        history = result['attempt_history']
        self.assertEqual(len(history['attempts']), 1)
        entry = history['attempts'][0]
        self.assertEqual(entry['response'], (folder / 'response.txt').read_text())
        self.assertEqual(entry['response_sha256'], sha((folder / 'response.txt').read_bytes()))
        self.assertEqual(entry['receipt_sha256'], sha((folder / 'receipt.json').read_bytes()))
        self.assertIn('NO_ENTRY', json.dumps(history))
        self.assertNotIn('lesson', entry)
        self.assertFalse((pipeline._scope_root(self.store) / 'upstream-library').exists())

    def test_native_failure_status_is_retained_without_an_invented_response(self):
        first = self.cycle('first', phase='completed')
        self.attempt(first, role='inv1.a1', status='failed', response=None)
        second = self.cycle('second')
        attempts = pipeline.approved_development_evidence(self.store, second)['attempt_history']['attempts']
        self.assertEqual(attempts[0]['status'], 'failed')
        self.assertFalse(attempts[0]['response_available'])
        self.assertNotIn('response', attempts[0])

    def test_hash_change_fails_before_copying_response(self):
        first = self.cycle('first', phase='completed')
        folder = self.attempt(first)
        (folder / 'response.txt').write_text('Changed after native completion.')
        second = self.cycle('second')
        with self.assertRaisesRegex(ValueError, 'differs from its native receipt'):
            pipeline.approved_development_evidence(self.store, second)

    def test_foreign_scope_and_incomplete_or_future_cycles_are_excluded(self):
        foreign = pipeline.PipelineStore(self.root, 'foreign-project', 2)
        self.addCleanup(foreign.close)
        foreign_cycle = self.cycle('foreign', store=foreign, phase='completed')
        self.attempt(foreign_cycle, store=foreign, response='FOREIGN_PRIVATE_CANARY')
        first = self.cycle('first', phase='searching')
        self.attempt(first, response='UNFINISHED_CANARY')
        current = self.cycle('current')
        future = self.cycle('future', phase='completed')
        self.attempt(future, response='FUTURE_CANARY')
        history = pipeline.approved_development_evidence(self.store, current)['attempt_history']
        self.assertEqual(history['attempts'], [])
        self.assertNotIn('CANARY', json.dumps(history))

    def test_no_holdout_acceptance_or_independent_grade_read_boundary(self):
        first = self.cycle('first', phase='completed')
        self.attempt(first, response='NO_ENTRY')
        base = pipeline._scope_root(self.store) / 'cycles' / first['id']
        for filename in ('evaluation.json', 'decision.json', 'freeze.json', 'upstream-result.json'):
            (base / filename).write_text('INDEPENDENT_OR_HOLDOUT_SECRET_CANARY')
        second = self.cycle('second')
        result = pipeline.approved_development_evidence(self.store, second)
        self.assertNotIn('SECRET_CANARY', json.dumps(result))

    def test_symlink_and_hardlink_cannot_import_other_files(self):
        first = self.cycle('first', phase='completed')
        folder = self.attempt(first)
        second = self.cycle('second')
        secret = self.root / 'secret.txt'
        secret.write_text('OUTSIDE_PRIVATE_CANARY')
        response = folder / 'response.txt'
        response.unlink()
        response.symlink_to(secret)
        with self.assertRaisesRegex(ValueError, 'links'):
            pipeline.approved_development_evidence(self.store, second)
        response.unlink()
        os.link(secret, response)
        with self.assertRaisesRegex(ValueError, 'fixed boundary'):
            pipeline.approved_development_evidence(self.store, second)

    def test_real_model_and_role_identity_are_required(self):
        first = self.cycle('first', phase='completed')
        folder = self.attempt(first)
        receipt = json.loads((folder / 'receipt.json').read_text())
        receipt['model'] = 'other-model'
        (folder / 'receipt.json').write_text(json.dumps(receipt))
        second = self.cycle('second')
        with self.assertRaisesRegex(ValueError, 'unexpected native identity'):
            pipeline.approved_development_evidence(self.store, second)

    def test_only_four_recent_completed_cycles_and_bounded_verbatim_messages(self):
        prior = []
        for n in range(5):
            cycle = self.cycle('prior' + str(n), phase='completed')
            self.attempt(cycle, response='NO_ENTRY ' + str(n))
            self.attempt(cycle, role='inv1.a1', ordinal=2, response='x' * 15000)
            prior.append(cycle)
        current = self.cycle('current')
        history = pipeline.approved_development_evidence(self.store, current)['attempt_history']
        self.assertLessEqual(len(pipeline.canonical(history).encode()), 16384)
        self.assertGreater(history['omitted_attempts'], 0)
        self.assertNotIn(prior[0]['id'], json.dumps(history))
        self.assertEqual(history['attempts'][0]['role'], 'experience.seq1')
        self.assertEqual(history['attempts'][0]['response'], 'NO_ENTRY 4')
        self.assertEqual(sum(row['role'] == 'experience.seq1' for row in history['attempts']), 4)


if __name__ == '__main__':
    unittest.main()
