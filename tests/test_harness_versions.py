"""Trusted activation persistence; no model, provider or production state calls."""

import hashlib
import sqlite3
import tempfile
from pathlib import Path
import unittest

from oak.harness_versions import HarnessVersions


class HarnessVersionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'private' / 'versions.sqlite'
        self.store = HarnessVersions(self.path)
        self.addCleanup(lambda: self.store.close())
        self.scope = ('project-one', -71, 'media-depth')

    def bootstrap(self, procedure='Inspect the actual source.'):
        return self.store.bootstrap_current(*self.scope, procedure)

    def candidate(self, parent, procedure='Inspect complete outlined contours.', *, accepted=True):
        version = self.store.register(*self.scope, procedure, parent_id=parent['id'],
                                      evidence={'cycle_id': 'cycle-two'})
        self.store.record_decision(version['id'], accepted=accepted,
            evidence={'receipt_sha256': 'a' * 64, 'fresh_task_ids': ['new-case']}, reason='Test acceptance fixture.')
        return version

    def test_bootstrap_is_explicit_and_reopen_preserves_active_version(self):
        first = self.bootstrap()
        self.assertEqual(first['decision']['kind'], 'bootstrap-current')
        self.assertIn('no comparative', first['decision']['reason'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.store.close()
        self.store = HarnessVersions(self.path)
        self.assertEqual(self.store.resolve(*self.scope)['id'], first['id'])
        self.assertEqual(self.bootstrap()['id'], first['id'])
        self.assertEqual(len(self.store.activation_history(*self.scope)), 1)
        with self.assertRaises(ValueError):
            self.bootstrap('Silently replace the existing baseline.')

    def test_unaccepted_and_rejected_versions_never_activate(self):
        first = self.bootstrap()
        pending = self.store.register(*self.scope, 'Pending.', parent_id=first['id'])
        rejected = self.candidate(first, 'Bad candidate.', accepted=False)
        for version in (pending, rejected):
            with self.subTest(version=version['id']), self.assertRaises(ValueError):
                self.store.activate(version['id'])
        self.assertEqual(self.store.resolve(*self.scope)['id'], first['id'])

    def test_versions_and_decisions_are_idempotent_and_immutable(self):
        first = self.bootstrap()
        candidate = self.candidate(first)
        same = self.store.register(*self.scope, candidate['procedure'], parent_id=first['id'],
                                   evidence={'cycle_id': 'cycle-two'})
        self.assertEqual(candidate['id'], same['id'])
        decision = self.store.decision(candidate['id'])
        again = self.store.record_decision(candidate['id'], accepted=True, evidence=decision['evidence'],
                                           reason=decision['reason'])
        self.assertEqual(decision, again)
        with self.assertRaises(ValueError):
            self.store.record_decision(candidate['id'], accepted=False, evidence={'replacement': True})

    def test_activation_compare_parent_and_rollback_restore_previous_verified(self):
        first = self.bootstrap()
        second = self.candidate(first)
        third = self.candidate(first, 'Another candidate from the same parent.')
        active = self.store.activate(second['id'], expected_active=first['id'])
        self.assertEqual(active['previous_id'], first['id'])
        with self.assertRaises(ValueError):
            self.store.activate(third['id'])
        with self.assertRaises(ValueError):
            self.store.rollback(*self.scope, expected_active=first['id'])
        rolled = self.store.rollback(*self.scope, expected_active=second['id'], reason='Controlled regression.')
        self.assertEqual(rolled['id'], first['id'])
        self.assertIsNone(rolled['previous_id'])
        history = self.store.activation_history(*self.scope)
        self.assertEqual([row['action'] for row in history], ['activate', 'activate', 'rollback'])
        self.assertEqual(history[-1]['previous_id'], second['id'])
        with self.assertRaises(ValueError):
            self.store.activate(second['id'])

    def test_exact_conversation_project_domain_scope(self):
        first = self.bootstrap()
        for scope in (('project-two', -71, 'media-depth'), ('project-one', 71, 'media-depth'),
                      ('project-one', -71, 'other-domain')):
            with self.subTest(scope=scope):
                self.assertIsNone(self.store.resolve(*scope))
                self.assertEqual(self.store.applications(*scope), [])
                with self.assertRaises(ValueError):
                    self.store.register(*scope, 'Cross scope parent.', parent_id=first['id'])
        other = self.store.bootstrap_current('project-two', -71, 'media-depth', 'Other private procedure.')
        with self.assertRaises(ValueError):
            self.store.rollback(*self.scope, target_id=other['id'])

    def test_application_is_frozen_before_dispatch_and_bound_once_after_ack(self):
        first = self.bootstrap()
        input_hash = hashlib.sha256(b'input').hexdigest()
        application = self.store.prepare_application(*self.scope, 'request-1', input_sha256=input_hash)
        second = self.candidate(first)
        self.store.activate(second['id'])
        repeated = self.store.prepare_application(*self.scope, 'request-1', input_sha256=input_hash)
        self.assertEqual(application['id'], repeated['id'])
        self.assertEqual(repeated['version_id'], first['id'])
        next_application = self.store.prepare_application(*self.scope, 'request-2', input_sha256=input_hash)
        self.assertEqual(next_application['version_id'], second['id'])
        self.assertIsNone(repeated['turn_id'])
        bound = self.store.bind_turn(application['id'], 'native-turn')
        self.assertEqual(bound['turn_id'], 'native-turn')
        self.assertEqual(self.store.bind_turn(application['id'], 'native-turn')['turn_id'], 'native-turn')
        with self.assertRaises(ValueError):
            self.store.bind_turn(application['id'], 'different-native-turn')
        with self.assertRaises(ValueError):
            self.store.prepare_application(*self.scope, 'request-1', input_sha256='b' * 64)

    def test_rollback_changes_next_application_and_not_historical_provenance(self):
        first = self.bootstrap()
        second = self.candidate(first)
        self.store.activate(second['id'])
        during = self.store.prepare_application(*self.scope, 'during')
        self.store.rollback(*self.scope, reason='Controlled rollback test.')
        after = self.store.prepare_application(*self.scope, 'after')
        self.assertEqual(during['version_id'], second['id'])
        self.assertEqual(after['version_id'], first['id'])
        self.assertEqual(self.store.applications(*self.scope)[0]['version_id'], second['id'])

    def test_procedure_or_evidence_tampering_fails_integrity_checks(self):
        first = self.bootstrap()
        with self.store.db:
            self.store.db.execute('UPDATE harness_versions SET evidence=? WHERE id=?', ('{}', first['id']))
        with self.assertRaises(ValueError):
            self.store.resolve(*self.scope)

    def test_no_active_version_has_no_application(self):
        self.assertIsNone(self.store.prepare_application(*self.scope, 'before-bootstrap'))
        self.assertEqual(self.store.applications(*self.scope), [])


if __name__ == '__main__':
    unittest.main()
