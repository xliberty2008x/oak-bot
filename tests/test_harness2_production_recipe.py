"""Promotion scheduling with real journals; no codec or native model execution."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.harness2_media import exposed_regression, make_descriptor
from oak.harness2_pipeline import (PipelineStore, default_config, digest,
                                  input_identity, schedule_next)


class PromotedDevelopmentRecipeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = PipelineStore(Path(temporary.name) / 'private',
                                   'recipe-project', 73, 'media-depth')
        self.addCleanup(self.store.close)
        self.seals = []
        sealing = patch('oak.harness2_pipeline._seal_descriptor', side_effect=self.seal)
        sealing.start()
        self.addCleanup(sealing.stop)
        controls = patch('oak.harness2_media.preflight_descriptors', return_value={
            'status': 'pass', 'kind': 'mocked-construction-control-protocol'})
        self.controls = controls.start()
        self.addCleanup(controls.stop)

    def seal(self, store, descriptor):
        self.assertEqual(store.scope, self.store.scope)
        self.seals.append(copy.deepcopy(descriptor))
        # A rename or a split change cannot turn identical source pixels into
        # independent evidence. Keep real input_identity/create_cycle checks.
        artwork = {k: v for k, v in descriptor.items() if k not in {'id', 'split'}}
        commitment = {'inputs': {'source.png': digest(artwork)},
                      'descriptor_sha256': digest(descriptor)}
        commitment['sha256'] = digest(commitment)
        return {'descriptor': copy.deepcopy(descriptor), 'commitment': commitment}

    def descriptor(self, index, *, split='dev', kind='creation'):
        return make_descriptor('production-recipe-protocol', index, split=split, kind=kind)

    def history(self, *, offset=0, holdouts=2):
        """A completed, disclosed administrative fixture, never a quality claim."""
        signature = 'historical-' + str(offset)
        dev = self.descriptor(offset)
        self.store.record_case({'visibility': 'public_fixture', 'descriptor': dev},
                               signature=signature, approved=True)
        rows = {
            'dev': [self.seal(self.store, dev)],
            'regression': [self.seal(self.store, self.descriptor(offset + i, split='regression'))
                           for i in (1, 2)],
            'holdout': [self.seal(self.store, self.descriptor(offset + 10 + i, split='holdout',
                                                            kind='repair' if i % 2 else 'creation'))
                        for i in range(holdouts)]}
        config = default_config()
        config['policy'].update(regression_tasks=2, holdout_tasks=holdouts)
        cycle = self.store.create_cycle(signature, rows, config)
        self.store.update(cycle['id'], frozen={'source': 'administrative protocol fixture'})
        self.store.open_holdout(cycle['id'])
        self.store.update(cycle['id'], phase='completed')
        with self.store.db:
            self.store.db.execute('UPDATE queue SET state="completed" WHERE id=? AND scope=?',
                                  (cycle['queue_id'], self.store.key))
        return self.store.cycle(cycle['id'])

    def promote(self, row, *, renamed=False):
        dev = {**row['descriptor'], 'split': 'dev'}
        if renamed:
            dev['id'] = 'media-renamed-identical-public-source'
        self.store.record_case({'visibility': 'public_fixture', 'descriptor': dev,
                                'source': 'explicitly promoted exposed fixture'},
                               signature='promoted-depth', approved=True, priority=100)
        return dev

    def assert_disjoint(self, cycle):
        identities = {split: [input_identity(r) for r in rows]
                      for split, rows in cycle['manifest'].items()}
        self.assertTrue(set(identities['dev']).isdisjoint(identities['regression']))
        self.assertEqual(len(identities['regression']), len(set(identities['regression'])))
        self.assertTrue(set(identities['holdout']).isdisjoint(
            [*identities['dev'], *identities['regression']]))
        self.assertEqual(cycle['config']['policy']['regression_tasks'],
                         len(cycle['manifest']['regression']))
        self.assertEqual(cycle['config']['policy']['holdout_tasks'], 2)

    def test_promoted_holdout_is_removed_and_exposed_regressions_supply_minimum_two(self):
        previous = self.history()
        promoted = previous['manifest']['holdout'][0]
        self.promote(promoted)
        cycle = schedule_next(self.store)
        self.assert_disjoint(cycle)
        regression = cycle['manifest']['regression']
        self.assertEqual(len(regression), 2)
        self.assertIn(input_identity(previous['manifest']['holdout'][1]),
                      [input_identity(r) for r in regression])
        allowed = {input_identity(r) for split in ('holdout', 'regression')
                   for r in previous['manifest'][split]}
        for name in ('holdout-create-reeds309-depth', 'holdout-repair-reeds310-depth'):
            allowed.add(input_identity(self.seal(self.store, exposed_regression(name))))
        self.assertTrue({input_identity(r) for r in regression} <= allowed)
        self.assertEqual(cycle['config']['max_producers'], 16)
        self.controls.assert_called_once()

    def test_renamed_identical_source_is_excluded_by_input_identity(self):
        previous = self.history()
        dev = self.promote(previous['manifest']['holdout'][0], renamed=True)
        cycle = schedule_next(self.store)
        self.assertEqual(cycle['manifest']['dev'][0]['descriptor']['id'], dev['id'])
        self.assert_disjoint(cycle)
        self.assertNotIn(previous['manifest']['holdout'][0]['descriptor']['id'],
                         [r['descriptor']['id'] for r in cycle['manifest']['regression']])

    def test_supplement_draws_from_recorded_exposed_history_when_available(self):
        self.history(offset=0)
        previous = self.history(offset=100)
        self.promote(previous['manifest']['holdout'][0])
        disclosed = {row[0] for row in self.store.db.execute(
            'SELECT identity FROM exposure WHERE scope=? AND opened IS NOT NULL',
            (self.store.key,))}
        cycle = schedule_next(self.store)
        self.assert_disjoint(cycle)
        self.assertEqual(len(cycle['manifest']['regression']), 2)
        remaining = input_identity(previous['manifest']['holdout'][1])
        regressions = {input_identity(r) for r in cycle['manifest']['regression']}
        self.assertIn(remaining, regressions)
        self.assertTrue(regressions <= disclosed)

    def test_six_prior_holdouts_retain_all_five_independent_regressions_after_promotion(self):
        previous = self.history(holdouts=6)
        self.promote(previous['manifest']['holdout'][0])
        cycle = schedule_next(self.store)
        self.assert_disjoint(cycle)
        self.assertEqual({input_identity(r) for r in cycle['manifest']['regression']},
                         {input_identity(r) for r in previous['manifest']['holdout'][1:]})
        # Four DEV rollouts, both arms on five regressions and two fresh tasks,
        # plus the declared four-attempt reserve. Acceptance counts stay exact.
        self.assertEqual(cycle['config']['max_producers'], 4 + 2 * (5 + 2) + 4)

    def test_worker_envelope_rejection_does_not_reserve_fresh_holdout_or_native_work(self):
        self.history(holdouts=6)
        self.promote(self.store.cycles()[-1]['manifest']['holdout'][0])
        before = {table: self.store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                  for table in ('cycles', 'exposure', 'operations')}
        with self.assertRaisesRegex(ValueError, 'budget envelope'):
            schedule_next(self.store, budget_limits={
                'max_producers': 21, 'max_roles': 8, 'max_role_tools': 512})
        self.controls.assert_not_called()
        for table, count in before.items():
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], count)


if __name__ == '__main__':
    unittest.main()
