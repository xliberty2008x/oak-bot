"""Durable scheduling and independent admission; no model or provider calls."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.harness2_pipeline import (BudgetStopped, MODEL, PipelineStore, UPSTREAM_COMMIT,
                                  acceptance, approved_development_evidence, canonical, digest, schedule_next)


def descriptor(label, *, content=None):
    return {'id': label, 'commitment': {'inputs': {'source.png': digest(content or label)}}}


def manifest(label='cycle-one'):
    return {split: [descriptor(label + '-' + split)] for split in ('dev', 'regression', 'holdout')}


def config(**changes):
    return {'model': MODEL, 'effort': 'low', 'upstream_commit': UPSTREAM_COMMIT,
            'max_producers': 2, 'max_roles': 2, 'max_role_tools': 2,
            'producer_timeout': 30, 'role_timeout': 30, **changes}


class PipelineStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'private-state'
        self.store = self.open_store()
        self.case = self.record('first')

    def open_store(self, project='one-project', chat=-71, domain='media-depth'):
        store = PipelineStore(self.root, project, chat, domain)
        self.addCleanup(store.close)
        return store

    def record(self, label, *, approved=True, signature='source-over-occlusion', store=None, **extra):
        return (store or self.store).record_case(
            {'case': label, 'visibility': 'public_fixture', 'artifact_reference': 'sha256:' + digest(label), **extra},
            signature=signature, approved=approved)

    def cycle(self, *, label='cycle-one', task_manifest=None, settings=None, store=None):
        return (store or self.store).create_cycle('source-over-occlusion', task_manifest or manifest(label),
                                                 settings or config())

    def test_state_is_private_and_unapproved_material_cannot_schedule(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        private = self.store.record_case({'case': 'PRIVATE_CANARY', 'artifact_reference': 'private-receipt'},
                                         signature='unapproved', approved=False)
        self.assertIn(private, [row['id'] for row in self.store.cases()])
        self.assertNotIn(private, [row['id'] for row in self.store.cases(approved_only=True)])
        with self.assertRaises(ValueError):
            self.store.create_cycle('unapproved', manifest(), config())
        with self.assertRaises(ValueError):
            self.store.record_case({'case': 'not-permitted'}, signature='no-consent', approved=True)
        explicit = self.store.record_case({'visibility': 'explicit_private', 'case': 'selected-private'},
                                          signature='selected-private', approved=True)
        self.assertIn(explicit, [row['id'] for row in self.store.cases(approved_only=True)])

    def test_case_retry_and_failure_grouping_are_idempotent_and_priority_is_maximum(self):
        self.assertEqual(self.record('first'), self.case)
        self.record('another')
        self.store.record_case({'visibility': 'public_fixture', 'case': 'priority'},
                                signature='source-over-occlusion', approved=True, priority=30)
        self.store.record_case({'visibility': 'public_fixture', 'case': 'lower-priority'},
                                signature='source-over-occlusion', approved=True, priority=2)
        rows = self.store.db.execute('SELECT * FROM queue WHERE scope=?', (self.store.key,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['priority'], 30)
        self.assertEqual(len(self.store.cases()), 4)

    def test_queries_feedback_and_cycles_keep_full_project_conversation_domain_scope(self):
        cycle = self.cycle()
        for scope in (('another-project', -71, 'media-depth'), ('one-project', 71, 'media-depth'),
                      ('one-project', -71, 'another-domain')):
            with self.subTest(scope=scope):
                other = self.open_store(*scope)
                self.assertEqual(other.cases(), [])
                self.assertEqual(other.cycles(), [])
                self.assertIsNone(other.setting('stopped'))
                with self.assertRaises(ValueError):
                    other.cycle(cycle['id'])
                with self.assertRaises(ValueError):
                    other.add_feedback(self.case, {'correction': 'Foreign case.'})
                foreign_case = self.record('first', store=other)
                self.assertNotEqual(foreign_case, self.case)
                # Independent scopes may reserve the same public fixture.
                self.assertNotEqual(self.cycle(store=other)['id'], cycle['id'])
        self.store.setting('stopped', True)
        self.assertIsNone(self.open_store('another-project').setting('stopped'))

    def test_feedback_is_durable_reference_and_deduplicates(self):
        correction = {'kind': 'owner-correction', 'reason': 'Foreground removes source details.'}
        ident = self.store.add_feedback(self.case, correction)
        self.assertEqual(self.store.add_feedback(self.case, correction), ident)
        reopened = self.open_store()
        row = reopened.db.execute('SELECT * FROM feedback WHERE id=? AND scope=?',
                                   (ident, reopened.key)).fetchone()
        self.assertEqual(row['value'], canonical(correction))
        self.assertEqual(reopened.db.execute('SELECT COUNT(*) FROM feedback WHERE scope=?',
                                             (reopened.key,)).fetchone()[0], 1)

    def test_cycle_requires_all_sets_and_fixed_subscription_model_and_finite_budgets(self):
        for field in ('dev', 'regression', 'holdout'):
            broken = manifest()
            broken[field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.cycle(task_manifest=broken)
        bad = [{'model': 'other-model'}, {'effort': 'high'}, {'upstream_commit': 'unfixed'}]
        for field in ('max_producers', 'max_roles', 'max_role_tools', 'producer_timeout', 'role_timeout'):
            bad.extend({field: value} for value in (0, -1, True, float('inf'), float('nan')))
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.cycle(settings=config(**change))
        self.assertEqual(self.store.cycles(), [])

    def test_sealed_config_manifest_freeze_and_decision_cannot_be_replaced(self):
        cycle = self.cycle()
        for field in ('manifest', 'config', 'ordinal'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.update(cycle['id'], **{field: 'changed'})
        frozen = {'candidate_sha256': 'a' * 64, 'policy_sha256': digest(config())}
        self.store.update(cycle['id'], frozen=frozen, phase='frozen')
        self.store.update(cycle['id'], frozen=frozen)
        self.store.open_holdout(cycle['id'])
        with self.assertRaises(ValueError):
            self.store.update(cycle['id'], frozen={'candidate_sha256': 'b' * 64})
        decision = {'accepted': False, 'reasons': ['no_strict_fresh_holdout_gain']}
        self.store.update(cycle['id'], decision=decision, phase='complete')
        self.store.update(cycle['id'], decision=decision)
        with self.assertRaises(ValueError):
            self.store.update(cycle['id'], decision={'accepted': True})

    def test_holdout_opens_only_after_freeze_and_cannot_be_reused_next_cycle(self):
        first = self.cycle()
        with self.assertRaises(ValueError):
            self.store.open_holdout(first['id'])
        self.store.update(first['id'], frozen={'candidate_sha256': 'a' * 64})
        self.store.open_holdout(first['id'])
        row = self.store.db.execute('SELECT opened FROM exposure WHERE cycle_id=? AND split=?',
                                     (first['id'], 'holdout')).fetchone()
        self.assertTrue(row['opened'])
        second = manifest('cycle-two')
        second['holdout'] = manifest()['holdout']
        with self.assertRaises(ValueError):
            self.cycle(task_manifest=second)
        self.assertEqual(len(self.store.cycles()), 1)

    def test_holdout_commitments_reject_duplicate_bytes_even_under_new_task_names(self):
        tasks = manifest()
        repeated = copy.deepcopy(tasks['holdout'][0])
        repeated['id'] = 'different-name-identical-input'
        repeated['commitment']['inputs'] = {'renamed.png': next(iter(repeated['commitment']['inputs'].values()))}
        tasks['holdout'].append(repeated)
        with self.assertRaises(ValueError):
            self.cycle(task_manifest=tasks)

    def test_fresh_holdout_excludes_previous_dev_or_regression_and_current_exposed_sets(self):
        first = self.cycle()
        for split in ('dev', 'regression'):
            tasks = manifest('new-' + split)
            tasks['holdout'] = copy.deepcopy(first['manifest'][split])
            with self.subTest(previous_split=split), self.assertRaises(ValueError):
                self.cycle(task_manifest=tasks)
        for split in ('dev', 'regression'):
            tasks = manifest('same-cycle-' + split)
            tasks['holdout'] = copy.deepcopy(tasks[split])
            with self.subTest(current_split=split), self.assertRaises(ValueError):
                self.cycle(task_manifest=tasks)

    def test_disclosed_holdout_may_be_regression_but_new_holdout_must_remain_fresh(self):
        first = self.cycle()
        self.store.update(first['id'], frozen={'candidate_sha256': 'a' * 64})
        self.store.open_holdout(first['id'])
        tasks = manifest('cycle-two')
        tasks['regression'] = copy.deepcopy(first['manifest']['holdout'])
        second = self.cycle(task_manifest=tasks)
        self.assertEqual(second['ordinal'], 2)
        self.assertNotEqual(first['id'], second['id'])

    def test_budgets_stop_persist_resume_and_retain_interrupted_intents(self):
        cycle = self.cycle()
        reserve = self.store.budget_hook(cycle['id'])
        self.assertEqual(reserve('producer', {'run_id': 'unknown-1'}), 1)
        self.store.setting('stopped', True)
        with self.assertRaises(BudgetStopped):
            reserve('producer', {'run_id': 'not-started'})
        reopened = self.open_store()
        self.assertTrue(reopened.setting('stopped'))
        with reopened.worker():
            usage = reopened.usage(cycle['id'])
        self.assertEqual(usage, [{'kind': 'producer', 'state': 'interrupted_unknown', 'count': 1}])
        reopened.setting('stopped', False)
        resumed = reopened.budget_hook(cycle['id'])
        self.assertEqual(resumed('producer', {'run_id': 'new-attempt'}), 2)
        with self.assertRaises(BudgetStopped):
            resumed('producer', {'run_id': 'beyond-budget'})
        with self.assertRaises(ValueError):
            resumed('unknown-category', {})

    def test_each_budget_category_counts_its_own_real_native_intents(self):
        cycle = self.cycle()
        reserve = self.store.budget_hook(cycle['id'])
        for kind in ('producer', 'role', 'role_tool'):
            self.assertEqual(reserve(kind, {'id': kind + '-one'}), 1)
            self.assertEqual(reserve(kind, {'id': kind + '-two'}), 2)
            with self.assertRaises(BudgetStopped):
                reserve(kind, {'id': kind + '-three'})

    def test_worker_lock_prevents_concurrent_recovery_and_unknown_intents_are_counted_once(self):
        cycle = self.cycle()
        other = self.open_store()
        with self.store.worker():
            self.store.budget_hook(cycle['id'])('role', {'role': 'running'})
            with self.assertRaises(RuntimeError):
                with other.worker():
                    self.fail('A second worker must not enter.')
            self.assertEqual(self.store.usage(cycle['id'])[0]['state'], 'started')
        with other.worker():
            self.assertEqual(other.usage(cycle['id'])[0]['state'], 'interrupted_unknown')
        with other.worker():
            pass
        events = other.db.execute('SELECT COUNT(*) FROM events WHERE scope=? AND kind=?',
                                   (other.key, 'worker_restart')).fetchone()[0]
        self.assertEqual(events, 1)

    def test_finished_receipts_survive_reopen_without_becoming_unknown(self):
        cycle = self.cycle()
        self.store.budget_hook(cycle['id'])('producer', {'id': 'finished-native'})
        self.store.reconcile(cycle['id'], 'producer', {'receipt_sha256': 'a' * 64})
        reopened = self.open_store()
        with reopened.worker():
            pass
        self.assertEqual(reopened.usage(cycle['id']),
                         [{'kind': 'producer', 'state': 'phase_recorded', 'count': 1}])
        self.assertEqual(reopened.cycle(cycle['id'])['manifest'], cycle['manifest'])


POLICY = {'regression_tasks': 2, 'holdout_tasks': 2, 'max_elapsed_seconds': 300, 'max_tool_calls': 64}


def outcome(task_id, *, passed=True, **changes):
    return {'task_id': task_id, 'status': 'completed', 'source_immutable': True, 'delivered': True,
            'unverified': False, 'material_defect': False, 'elapsed_seconds': 10.0, 'tool_calls': 8,
            'artifact_sha256': 'a' * 64, 'candidate_sha256': 'a' * 64,
            'visual_passed': True, 'mechanical_passed': passed, 'joint_passed': passed, **changes}


def comparisons():
    regression = [{'task_id': 'reg-' + str(i), 'split': 'regression',
                   'baseline': outcome('reg-' + str(i)), 'candidate': outcome('reg-' + str(i))} for i in (1, 2)]
    holdout = [{'task_id': 'hold-' + str(i), 'split': 'holdout',
                'baseline': outcome('hold-' + str(i), passed=i == 2),
                'candidate': outcome('hold-' + str(i))} for i in (1, 2)]
    return regression, holdout


class IndependentAcceptanceTests(unittest.TestCase):
    def test_strict_fresh_holdout_gain_without_regression_is_admitted_narrowly(self):
        result = acceptance(*comparisons(), policy=POLICY)
        self.assertTrue(result['accepted'], result)
        self.assertEqual(result['baseline_holdout_joint'], 1)
        self.assertEqual(result['candidate_holdout_joint'], 2)
        self.assertFalse(result['broad_quality_improvement_claim'])
        self.assertEqual(result['policy_sha256'], digest(POLICY))

    def test_tie_is_rejected_and_no_broad_quality_claim_is_made(self):
        regression, holdout = comparisons()
        holdout[0]['baseline'] = outcome('hold-1')
        result = acceptance(regression, holdout, policy=POLICY)
        self.assertFalse(result['accepted'])
        self.assertIn('no_strict_fresh_holdout_gain', result['reasons'])

    def test_regression_loss_rejects_holdout_gain(self):
        regression, holdout = comparisons()
        regression[0]['candidate'] = outcome('reg-1', passed=False)
        result = acceptance(regression, holdout, policy=POLICY)
        self.assertFalse(result['accepted'])
        self.assertIn('regression_loss', result['reasons'])

    def test_missing_arms_incomplete_unverified_changed_source_and_missing_delivery_remain_failures(self):
        # A claimed joint pass cannot coexist with these failed conditions.
        mutations = [{'status': 'timeout'}, {'status': 'interrupted'}, {'unverified': True},
                     {'source_immutable': False}, {'delivered': False}, {'material_defect': True},
                     {'elapsed_seconds': 301}, {'tool_calls': 65}]
        for arm in ('baseline', 'candidate'):
            for change in mutations:
                regression, holdout = comparisons()
                regression[0][arm].update(change)
                with self.subTest(arm=arm, change=change):
                    self.assertFalse(acceptance(regression, holdout, policy=POLICY)['accepted'])
        regression, holdout = comparisons()
        del holdout[0]['candidate']
        result = acceptance(regression, holdout, policy=POLICY)
        self.assertFalse(result['accepted'])
        self.assertIn('missing_arm', result['reasons'])

    def test_terminal_baseline_failure_is_counted_without_vetoing_real_candidate_gain(self):
        regression, holdout = comparisons()
        holdout[0]['baseline'].update(status='timeout', delivered=False, visual_passed=False,
            mechanical_passed=False, joint_passed=False, artifact_sha256=None, candidate_sha256=None)
        result = acceptance(regression, holdout, policy=POLICY)
        self.assertTrue(result['accepted'], result)
        self.assertEqual(result['baseline_holdout_joint'], 1)

    def test_each_planned_split_must_be_complete_not_only_combined_count(self):
        regression, holdout = comparisons()
        extra = copy.deepcopy(holdout[0])
        extra['task_id'] = 'extra-heldout'
        extra['baseline']['task_id'] = extra['candidate']['task_id'] = extra['task_id']
        result = acceptance(regression[:1], [*holdout, extra], policy=POLICY)
        self.assertFalse(result['accepted'])

    def test_repeated_task_is_not_independent_holdout_evidence(self):
        regression, holdout = comparisons()
        result = acceptance(regression, [holdout[0], copy.deepcopy(holdout[0])], policy=POLICY)
        self.assertFalse(result['accepted'])

    def test_nonfinite_negative_or_noninteger_costs_are_rejected(self):
        for arm in ('baseline', 'candidate'):
            for field, values in (('elapsed_seconds', (float('nan'), float('inf'), -1)),
                                  ('tool_calls', (float('nan'), -1, 1.5, True))):
                for value in values:
                    regression, holdout = comparisons()
                    regression[0][arm][field] = value
                    with self.subTest(arm=arm, field=field, value=value):
                        self.assertFalse(acceptance(regression, holdout, policy=POLICY)['accepted'])


class GenericQueueSchedulingTests(unittest.TestCase):
    """Exercise reusable scheduling with construction and rendering mocked out."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = PipelineStore(Path(self.directory.name) / 'state', 'queue-project', 7, 'media-depth')
        self.addCleanup(self.store.close)
        self.sealed = []
        self.seal_patch = patch('oak.harness2_pipeline._seal_descriptor', side_effect=self.seal)
        self.seal_patch.start()
        self.addCleanup(self.seal_patch.stop)
        self.controls_patch = patch('oak.harness2_media.preflight_descriptors',
                                    return_value={'status': 'pass', 'kind': 'mocked-authored-controls'})
        self.controls = self.controls_patch.start()
        self.addCleanup(self.controls_patch.stop)
        config_patch = patch('oak.harness2_pipeline.default_config', side_effect=lambda: config())
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def seal(self, store, task_descriptor):
        self.assertEqual(store.scope, self.store.scope)
        self.sealed.append(copy.deepcopy(task_descriptor))
        # Moving an exposed holdout to regression keeps identical input bytes.
        content_identity = {key: value for key, value in task_descriptor.items() if key != 'split'}
        commitment = {'inputs': {'source.png': digest(content_identity)},
                      'descriptor_sha256': digest(task_descriptor)}
        return {'descriptor': copy.deepcopy(task_descriptor),
                'commitment': {**commitment, 'sha256': digest(commitment)}}

    @staticmethod
    def dev(index, kind='creation'):
        from oak.harness2_media import make_descriptor
        return make_descriptor('generic-queue-local-tests', index, kind=kind)

    def enqueue(self, index, *, signature='source-preserving-depth', priority=10,
                approved=True, task_descriptor=None):
        return self.store.record_case({'visibility': 'public_fixture', 'case': index,
            'descriptor': task_descriptor or self.dev(index)}, signature=signature,
            approved=approved, priority=priority)

    def complete(self, cycle):
        # Administrative local scheduling fixture; no optimizer/admission claim.
        self.store.update(cycle['id'], phase='completed')
        with self.store.db:
            self.store.db.execute('UPDATE queue SET state="completed" WHERE id=? AND scope=?',
                                  (cycle['queue_id'], self.store.key))

    def test_highest_priority_group_with_approved_dev_is_selected(self):
        self.enqueue(1, signature='lower-priority', priority=20)
        self.enqueue(2, signature='higher-priority', priority=90)
        cycle = schedule_next(self.store)
        self.assertEqual(cycle['manifest']['dev'][0]['descriptor']['id'], self.dev(2)['id'])
        group = self.store.db.execute('SELECT signature,state FROM queue WHERE id=?',
                                      (cycle['queue_id'],)).fetchone()
        self.assertEqual(tuple(group), ('higher-priority', 'scheduled'))
        self.assertEqual(self.controls.call_count, 1)
        self.assertEqual(cycle['config']['construction_controls_sha256'],
                         digest({'status': 'pass', 'kind': 'mocked-authored-controls'}))

    def test_unapproved_descriptors_and_approved_metadata_only_cases_are_not_optimized(self):
        self.enqueue(3, signature='private-unapproved', priority=1000, approved=False)
        self.store.record_case({'visibility': 'public_fixture', 'reason': 'Only a prior receipt reference'},
                                signature='metadata-only', approved=True, priority=900)
        self.enqueue(4, signature='approved-new-task', priority=1)
        cycle = schedule_next(self.store)
        self.assertEqual(cycle['manifest']['dev'][0]['descriptor']['id'], self.dev(4)['id'])
        self.assertNotIn(self.dev(3)['id'], [row['id'] for row in self.sealed])

    def test_case_may_be_explicitly_approved_later_without_duplicate_execution(self):
        case_id = self.enqueue(5, approved=False)
        self.assertIsNone(schedule_next(self.store))
        self.assertEqual(self.enqueue(5, approved=True), case_id)
        cycle = schedule_next(self.store)
        self.assertEqual(cycle['manifest']['dev'][0]['descriptor']['id'], self.dev(5)['id'])
        self.assertEqual(len(self.store.cases()), 1)

    def test_used_dev_descriptor_is_not_replayed_under_new_case_metadata(self):
        self.enqueue(6)
        first = schedule_next(self.store)
        self.complete(first)
        self.enqueue(60, task_descriptor=self.dev(6), priority=100)
        self.assertIsNone(schedule_next(self.store))
        self.assertEqual(len(self.store.cycles()), 1)
        self.assertEqual(self.controls.call_count, 1)
        self.enqueue(7, priority=100)
        second = schedule_next(self.store)
        self.assertEqual(second['manifest']['dev'][0]['descriptor']['id'], self.dev(7)['id'])

    def test_next_cycle_has_fresh_holdout_and_moves_prior_holdout_to_regression(self):
        self.enqueue(8)
        first = schedule_next(self.store)
        self.complete(first)
        self.enqueue(9, task_descriptor=self.dev(9, 'repair'))
        second = schedule_next(self.store)
        old_holds = first['manifest']['holdout']
        new_regs = second['manifest']['regression']
        self.assertEqual([row['descriptor']['id'] for row in new_regs],
                         [row['descriptor']['id'] for row in old_holds])
        self.assertTrue(all(row['descriptor']['split'] == 'regression' for row in new_regs))
        self.assertEqual([row['commitment']['inputs'] for row in new_regs],
                         [row['commitment']['inputs'] for row in old_holds])
        exposed_inputs = {digest(sorted(row['commitment']['inputs'].values()))
                          for rows in first['manifest'].values() for row in rows}
        next_holds = {digest(sorted(row['commitment']['inputs'].values()))
                      for row in second['manifest']['holdout']}
        self.assertEqual(len(next_holds), 2)
        self.assertTrue(exposed_inputs.isdisjoint(next_holds))
        self.assertEqual({row['descriptor']['kind'] for row in second['manifest']['holdout']},
                         {'creation', 'repair'})

    def test_generic_queue_continues_past_two_demo_cycles(self):
        expected = []
        holdout_ids = set()
        for index in range(10, 14):
            dev = self.dev(index, 'repair' if index % 2 else 'creation')
            self.enqueue(index, task_descriptor=dev)
            cycle = schedule_next(self.store)
            self.assertEqual(cycle['ordinal'], index - 9)
            self.assertEqual(cycle['manifest']['dev'][0]['descriptor']['id'], dev['id'])
            expected.append(dev['id'])
            new_holdouts = {row['descriptor']['id'] for row in cycle['manifest']['holdout']}
            self.assertTrue(holdout_ids.isdisjoint(new_holdouts))
            holdout_ids.update(new_holdouts)
            self.complete(cycle)
        self.assertEqual(len(self.store.cycles()), 4)
        self.assertEqual(len(holdout_ids), 8)
        self.assertIsNone(schedule_next(self.store))

    def test_feedback_repends_completed_group_and_raises_its_priority(self):
        first_case = self.enqueue(14, signature='corrected-group', priority=10)
        first = schedule_next(self.store)
        self.complete(first)
        self.enqueue(15, signature='competing-group', priority=70)
        self.enqueue(16, signature='corrected-group', priority=10)
        with self.store.db:
            self.store.db.execute('UPDATE queue SET state="completed" WHERE scope=? AND signature=?',
                                  (self.store.key, 'corrected-group'))
        self.store.add_feedback(first_case, {'kind': 'correction', 'reason': 'Keep the source pattern visible.'})
        row = self.store.db.execute('SELECT state,priority FROM queue WHERE scope=? AND signature=?',
                                    (self.store.key, 'corrected-group')).fetchone()
        self.assertEqual(tuple(row), ('pending', 100))
        second = schedule_next(self.store)
        self.assertEqual(second['manifest']['dev'][0]['descriptor']['id'], self.dev(16)['id'])

    def test_unsupported_family_or_nondev_case_cannot_start_automatic_search(self):
        for index, change in enumerate(({'family': 'projective'}, {'split': 'holdout'}), start=17):
            case = {**self.dev(index), **change}
            self.enqueue(index, signature='unsupported-' + str(index), task_descriptor=case)
        with self.assertRaises(ValueError):
            schedule_next(self.store)
        self.assertEqual(self.store.cycles(), [])
        self.controls.assert_not_called()

    def test_failed_construction_control_prevents_cycle_admission(self):
        self.enqueue(19)
        self.controls.return_value = {'status': 'fail', 'reason': 'Controlled local fixture.'}
        with self.assertRaises(ValueError):
            schedule_next(self.store)
        self.assertEqual(self.store.cycles(), [])

    def test_optimizer_context_includes_approved_current_dev_feedback(self):
        case_id = self.enqueue(20)
        cycle = schedule_next(self.store)
        correction = {'kind': 'owner-correction', 'message': 'Keep cream rings intact behind foreground leaves.'}
        self.store.add_feedback(case_id, correction)
        evidence = approved_development_evidence(self.store, cycle)
        self.assertEqual(evidence['scope'], self.store.scope)
        self.assertEqual([row['case_id'] for row in evidence['cases']], [case_id])
        self.assertEqual(evidence['cases'][0]['development_case']['descriptor'], self.dev(20))
        self.assertEqual(evidence['cases'][0]['user_feedback'], [correction])
        self.assertLess(len(canonical(evidence).encode()), 64000)

    def test_optimizer_context_excludes_unapproved_other_scope_other_task_and_independent_grades(self):
        case_id = self.enqueue(21)
        cycle = schedule_next(self.store)
        signature = 'source-preserving-depth'
        hidden = self.store.record_case({'visibility': 'explicit_private', 'descriptor': self.dev(21),
            'private_note': 'UNAPPROVED_CANARY'}, signature=signature, approved=False)
        self.store.add_feedback(hidden, {'message': 'UNAPPROVED_FEEDBACK_CANARY'})
        self.enqueue(22)
        self.store.record_case({'visibility': 'public_fixture', 'descriptor': self.dev(21),
            'private_note': 'OTHER_QUEUE_CANARY'}, signature='other-queue', approved=True)
        for split in ('holdout', 'regression'):
            self.store.record_case({'visibility': 'public_fixture',
                'descriptor': cycle['manifest'][split][0]['descriptor'], 'private_note': split.upper() + '_CANARY'},
                signature=signature, approved=True)
        # Production independent-result cases have task_id/result/receipt
        # references, rather than a consented current DEV descriptor.
        for approved in (False, True):
            self.store.record_case({'visibility': 'public_fixture', 'task_id': self.dev(21)['id'],
                'result': {'hidden_measurement': 'INDEPENDENT_GRADE_CANARY', 'score': int(approved)},
                'artifact_reference': 'private-independent-receipt'}, signature=signature, approved=approved)
        for project, chat, domain in (('foreign-project', 7, 'media-depth'),
                                      ('queue-project', 8, 'media-depth'),
                                      ('queue-project', 7, 'foreign-domain')):
            other = PipelineStore(self.store.root, project, chat, domain)
            self.addCleanup(other.close)
            foreign = other.record_case({'visibility': 'public_fixture', 'descriptor': self.dev(21),
                'private_note': 'FOREIGN_SCOPE_CANARY'}, signature=signature, approved=True)
            other.add_feedback(foreign, {'message': 'FOREIGN_FEEDBACK_CANARY'})
        evidence = approved_development_evidence(self.store, cycle)
        self.assertEqual([row['case_id'] for row in evidence['cases']], [case_id])
        self.assertNotIn('CANARY', canonical(evidence))

    def test_optimizer_context_bound_counts_utf8_bytes_and_rejects_oversized_feedback(self):
        case_id = self.enqueue(23)
        cycle = schedule_next(self.store)
        self.store.add_feedback(case_id, {'message': 'Ж' * 33000})
        with self.assertRaises(ValueError):
            approved_development_evidence(self.store, cycle)


if __name__ == '__main__':
    unittest.main()
