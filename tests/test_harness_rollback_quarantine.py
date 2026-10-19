"""Trusted rollback cannot resurrect a regressed version or another branch."""

import unittest

from oak.harness_versions import HarnessVersions


class RollbackQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.store = HarnessVersions(':memory:')
        self.addCleanup(self.store.close)
        self.scope = ('rollback-fixture', 7, 'media-depth')
        self.first = self.store.bootstrap_current(*self.scope, 'Known current procedure.')

    def accepted(self, parent, procedure):
        version = self.store.register(*self.scope, procedure, parent_id=parent['id'])
        self.store.record_decision(version['id'], accepted=True,
                                   evidence={'kind': 'local-test-decision'})
        self.store.activate(version['id'], expected_active=parent['id'])
        return version

    def test_explicit_rollback_cannot_bypass_regression_quarantine(self):
        second = self.accepted(self.first, 'Second procedure.')
        self.store.rollback(*self.scope, reason='Controlled regression.')
        history = self.store.activation_history(*self.scope)
        with self.assertRaisesRegex(ValueError, 'quarantined'):
            self.store.activate(second['id'])
        with self.assertRaisesRegex(ValueError, 'quarantined'):
            self.store.rollback(*self.scope, target_id=second['id'])
        self.assertEqual(self.store.resolve(*self.scope)['id'], self.first['id'])
        self.assertEqual(self.store.activation_history(*self.scope), history)

    def test_previously_active_nonquarantined_descendant_is_not_a_rollback_target(self):
        second = self.accepted(self.first, 'Second procedure.')
        third = self.accepted(second, 'Third procedure.')
        self.store.rollback(*self.scope, target_id=self.first['id'], reason='Skip to known baseline.')
        # Third was quarantined; second was simply skipped. Prior activity
        # alone must not let rollback move forward to that descendant.
        with self.assertRaisesRegex(ValueError, 'ancestor'):
            self.store.rollback(*self.scope, target_id=second['id'])
        self.assertEqual(self.store.resolve(*self.scope)['id'], self.first['id'])

    def test_known_ancestor_and_next_default_rollback_remain_available(self):
        second = self.accepted(self.first, 'Second procedure.')
        third = self.accepted(second, 'Third procedure.')
        fourth = self.accepted(third, 'Fourth procedure.')
        restored = self.store.rollback(*self.scope, target_id=second['id'], expected_active=fourth['id'])
        self.assertEqual(restored['id'], second['id'])
        self.assertEqual(restored['previous_id'], self.first['id'])
        restored = self.store.rollback(*self.scope)
        self.assertEqual(restored['id'], self.first['id'])


if __name__ == '__main__':
    unittest.main()
