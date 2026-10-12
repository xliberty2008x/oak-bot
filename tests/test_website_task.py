"""Delegated lifecycle and authority checks without model or browser networking."""

import asyncio
from copy import deepcopy
import json
import unittest

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_planner import RESPONSE_MODEL, WebsitePlannerError
from oak.website_scene import model_context
from oak.website_task import WebsiteTask, validate_task_snapshot


def observation(revision='page:1', *, stage='agent_working', sequence=0, after=False):
    scene = {'provider': 'website', 'destination': 'https://fixture.example',
        'revision': revision, 'stage': 'form', 'controls': [],
        'actions': [{'ref': 'a1', 'label': 'PRIVATE_LABEL_CANARY', 'form': None,
            'order': 0, 'kind': 'button', 'enabled': True, 'permitted': True}],
        'texts': [], 'after_input': after, 'overflow': {'controls': 0, 'actions': 0, 'texts': 0}}
    return {'revision': revision, 'stage': stage, 'scene': model_context(scene),
        'progress': {'event_seq': sequence, 'outcome': 'submitted' if sequence else 'idle'}}


def proposal(obs, operation='act'):
    return {'operation': operation, 'revision': obs['revision'],
        'action_ref': 'a1' if operation in {'act', 'present'} else None,
        'plan': {'revision': obs['revision']} if operation == 'present' else None}


class Planner:
    def __init__(self, operation='act'):
        self.operation, self.calls = operation, 0

    async def plan(self, obs, *, reserve, record):
        await reserve()
        self.calls += 1
        return proposal(obs, self.operation)


class WebsiteTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.current = observation()
        self.reads, self.actions, self.forms, self.tasks = 0, [], [], []

    async def asyncTearDown(self):
        for task in self.tasks:
            await task.close()

    async def observe(self):
        self.reads += 1
        return deepcopy(self.current)

    async def act(self, revision, ref):
        self.actions.append((revision, ref))
        return deepcopy(self.current)

    async def present(self, revision, plan):
        self.forms.append((revision, plan))
        self.current['stage'] = 'form'
        return deepcopy(self.current)

    def task(self, planner=None, **kwargs):
        callbacks = dict(observe=self.observe, act=self.act, present=self.present, poll_seconds=.005)
        callbacks.update(kwargs)
        task = WebsiteTask(planner or Planner(), **callbacks)
        self.tasks.append(task)
        task.start()
        return task

    async def until(self, predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(.002)

    async def test_unchanged_navigation_is_never_inferred_or_dispatched_twice(self):
        planner = Planner()
        task = self.task(planner)
        await self.until(lambda: self.reads >= 5)
        self.assertEqual(self.actions, [('page:1', 'a1')])
        self.assertEqual((planner.calls, task.snapshot()['calls'], task.snapshot()['steps']), (1, 1, 1))
        self.assertNotIn('PRIVATE_LABEL_CANARY', json.dumps(task.snapshot()))

    async def test_hydration_new_scene_advances_without_human_sequence(self):
        planner = Planner('wait')
        task = self.task(planner)
        await self.until(lambda: self.reads >= 3)
        self.assertEqual(planner.calls, 1)
        self.current = observation('page:2')
        planner.operation = 'act'
        task.wake()
        await self.until(lambda: bool(self.actions))
        self.assertEqual(self.actions, [('page:2', 'a1')])
        self.assertEqual(planner.calls, 2)

    async def test_unchanged_no_human_plan_times_out_without_paid_or_action_retries(self):
        for operation in ('wait', 'act'):
            self.current = observation()
            self.actions.clear()
            planner = Planner(operation)
            task = self.task(planner, readiness_seconds=.03)
            await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
            self.assertEqual(task.snapshot()['error'], 'readiness_timeout')
            self.assertEqual((planner.calls, task.snapshot()['steps']), (1, 1))
            self.assertEqual(len(self.actions), int(operation == 'act'))
            before = self.reads
            task.wake(); await asyncio.sleep(.01)
            self.assertEqual(self.reads, before)

    async def test_scene_and_human_progress_changes_reset_readiness_deadline(self):
        planner = Planner('wait')
        task = self.task(planner, readiness_seconds=.2)
        await self.until(lambda: planner.calls == 1)
        first_deadline = task._readiness_deadline
        self.current = observation('hydrated:1')
        task.wake()
        await self.until(lambda: planner.calls == 2)
        self.assertGreater(task._readiness_deadline, first_deadline)
        second_deadline = task._readiness_deadline
        self.current['progress'] = {'event_seq': 1, 'outcome': 'submitted'}
        self.current['scene']['after_input'] = True
        task.wake()
        await self.until(lambda: planner.calls == 3)
        self.assertGreater(task._readiness_deadline, second_deadline)
        self.assertNotEqual(task.snapshot()['state'], 'requires_agent')

    async def test_private_human_waits_ignore_readiness_timeout(self):
        for stage in ('form', 'device_approval', 'authenticated'):
            self.current = observation(stage=stage)
            planner = Planner('wait')
            task = self.task(planner, readiness_seconds=.01)
            await self.until(lambda: task.snapshot()['state'] == 'waiting_for_owner')
            await asyncio.sleep(.035)
            self.assertEqual(task.snapshot()['state'], 'waiting_for_owner')
            self.assertIsNone(task._readiness_deadline)
            self.assertEqual(planner.calls, 0)
            await task.close()

    async def test_actionable_review_is_planned_and_cannot_wait_indefinitely(self):
        self.current = observation(stage='review', sequence=1, after=True)
        planner = Planner('wait')
        task = self.task(planner, readiness_seconds=.03)
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'readiness_timeout')
        self.assertEqual(planner.calls, 1)
        self.assertEqual(self.actions, [])

    async def test_empty_postinput_waits_without_model_then_composes_revealed_chooser(self):
        self.current = observation(stage='review', sequence=1, after=True)
        self.current['scene'].update(stage='unsupported_layout', actions=[])
        planner = Planner('present')
        task = self.task(planner, readiness_seconds=.2)
        await self.until(lambda: self.reads >= 4)
        self.assertEqual((planner.calls, len(self.forms)), (0, 0))
        self.current = observation('method:2', stage='review', sequence=1, after=True)
        await self.until(lambda: len(self.forms) == 1)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(task.snapshot()['progress'], {'event_seq': 1, 'outcome': 'submitted'})
        self.assertEqual(self.actions, [])

    async def test_empty_postinput_deadline_escalates_without_completion_or_paid_calls(self):
        self.current = observation(sequence=1, after=True)
        self.current['scene'].update(stage='unsupported_layout', actions=[])
        planner = Planner('present')
        task = self.task(planner, readiness_seconds=.03)
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'readiness_timeout')
        self.assertEqual((planner.calls, task.snapshot()['steps']), (0, 0))
        self.assertEqual(self.forms, [])

    async def test_empty_revision_changes_do_not_extend_readiness_deadline(self):
        self.current = observation(sequence=1, after=True)
        self.current['scene'].update(stage='unsupported_layout', actions=[])
        async def changing_empty():
            self.reads += 1
            result = deepcopy(self.current)
            result['revision'] = 'loading:' + str(self.reads)
            return result
        planner = Planner('present')
        task = self.task(planner, observe=changing_empty, readiness_seconds=.03)
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'readiness_timeout')
        self.assertGreater(self.reads, 2)
        self.assertEqual(planner.calls, 0)

    async def test_present_waits_privately_then_resumes_after_human_step(self):
        planner = Planner('present')
        task = self.task(planner)
        await self.until(lambda: self.reads >= 5)
        self.assertEqual((len(self.forms), planner.calls), (1, 1))
        self.assertEqual(task.snapshot()['state'], 'waiting_for_owner')
        self.current = observation('next:1', sequence=1, after=True)
        task.wake()
        await self.until(lambda: len(self.forms) == 2)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(task.snapshot()['progress'], {'event_seq': 1, 'outcome': 'submitted'})
        self.assertEqual(self.actions, [])

    async def test_existing_pending_human_plan_has_zero_model_calls(self):
        self.current['stage'] = 'form'
        planner = Planner()
        self.task(planner)
        await self.until(lambda: self.reads >= 4)
        self.assertEqual(planner.calls, 0)

    async def test_surface_visual_anchor_is_not_action_execution_permission(self):
        self.current['scene']['actions'][0]['permitted'] = False
        task = self.task(Planner('present'))
        await self.until(lambda: bool(self.forms))
        self.assertEqual(task.snapshot()['state'], 'waiting_for_owner')
        await task.close()
        self.current['stage'] = 'agent_working'
        task = self.task(Planner('act'))
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'invalid_proposal')
        self.assertEqual(self.actions, [])

    async def test_stale_dispatch_reobserves_but_never_replays_old_revision(self):
        async def stale(revision, ref):
            self.actions.append((revision, ref))
            raise RecoverableFormError('stale_form')
        planner = Planner()
        task = self.task(planner, act=stale)
        await self.until(lambda: self.reads >= 5)
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(planner.calls, 1)
        self.current = observation('changed:1')
        task.wake()
        await self.until(lambda: len(self.actions) == 2)
        self.assertEqual(self.actions[-1], ('changed:1', 'a1'))

    async def test_uncertain_stops_without_replay_and_preserves_fixed_reason(self):
        async def uncertain(revision, ref):
            self.actions.append((revision, ref))
            raise SubmitUncertainError()
        task = self.task(act=uncertain)
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        before = self.reads
        task.wake(); await asyncio.sleep(.02)
        self.assertEqual((self.reads, len(self.actions)), (before, 1))
        self.assertEqual(task.snapshot()['error'], 'action_uncertain')

    async def test_model_abstention_and_invalid_response_escalate_without_fallback(self):
        task = self.task(Planner('unsupported'))
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'no_candidate')
        class Broken(Planner):
            async def plan(self, obs, *, reserve, record):
                await reserve()
                raise WebsitePlannerError('invalid_response')
        task = self.task(Broken())
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'invalid_response')
        self.assertFalse(self.actions or self.forms)

    async def test_browser_block_is_distinct_from_model_abstention(self):
        for stage in ('provider_blocked', 'unsupported_challenge'):
            self.current = observation()
            self.current['stage'] = stage
            planner = Planner()
            task = self.task(planner)
            await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
            self.assertEqual(task.snapshot()['error'], 'unsupported')
            self.assertEqual(task.snapshot()['calls'], 0)
            self.assertEqual(planner.calls, 0)
        self.assertFalse(self.actions or self.forms)

    async def test_actual_call_cap_stops_second_batch_before_post(self):
        class TwoCalls(Planner):
            async def plan(self, obs, *, reserve, record):
                await reserve(); self.calls += 1
                await reserve(); self.calls += 1
                return proposal(obs)
        planner = TwoCalls()
        task = self.task(planner, max_calls=1)
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual((planner.calls, task.snapshot()['calls']), (1, 1))
        self.assertEqual(task.snapshot()['error'], 'call_limit')

    async def test_changed_scenes_are_bounded_by_step_limit(self):
        task = self.task(Planner('wait'), max_steps=1)
        await self.until(lambda: task.snapshot()['steps'] == 1)
        self.current = observation('other:1')
        task.wake()
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'step_limit')

    async def test_close_during_inference_prevents_browser_dispatch(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Pending(Planner):
            async def plan(self, obs, *, reserve, record):
                await reserve(); entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass  # Even a badly behaved planner cannot dispatch after close.
                return proposal(obs)
        task = self.task(Pending())
        await entered.wait()
        await task.close()
        self.assertEqual(task.snapshot()['state'], 'cancelled')
        self.assertFalse(self.actions or self.forms)
        self.assertTrue(task._task.done())

    async def test_close_from_own_callback_never_self_awaits(self):
        holder = {}
        async def finish(revision, ref):
            await holder['task'].close()
            return deepcopy(self.current)
        task = self.task(act=finish); holder['task'] = task
        await self.until(lambda: task._task.done())
        self.assertEqual(task.snapshot()['state'], 'cancelled')

    async def test_invalid_observations_private_errors_and_postinput_act_are_rejected(self):
        self.current['secret'] = 'PRIVATE_ERROR_CANARY'
        task = self.task()
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'invalid_observation')
        self.current = observation(after=True)
        task = self.task()
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'invalid_proposal')
        async def broken():
            raise RuntimeError('PRIVATE_ERROR_CANARY')
        task = self.task(observe=broken)
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'callback_failed')
        self.assertNotIn('CANARY', json.dumps(task.snapshot()))
        self.assertFalse(self.actions or self.forms)

    async def test_private_diagnostics_validate_copy_bound_and_omit_free_text(self):
        metadata = {'phase': 'anchor', 'status': 'selected', 'model': RESPONSE_MODEL,
                    'cost': .00002, 'elapsed_ms': 241, 'error': None}
        class Recorded(Planner):
            async def plan(self, obs, *, reserve, record):
                await reserve()
                record(metadata)
                metadata['cost'] = 9
                return proposal(obs, 'wait')
        task = self.task(Recorded())
        await self.until(lambda: task.snapshot()['state'] == 'waiting_for_owner')
        self.assertEqual(task.diagnostics(), {'requests': [{**metadata, 'cost': .00002}],
            'known_cost': .00002, 'unknown_cost_calls': 0, 'elapsed_ms': 241})
        self.assertNotIn('requests', task.snapshot())
        copy = task.diagnostics(); copy['requests'][0]['cost'] = 7
        self.assertEqual(task.diagnostics()['known_cost'], .00002)
        for bad in ({**metadata, 'label': 'PRIVATE_CANARY'},
                    {**metadata, 'error': 'PRIVATE_CANARY'},
                    {**metadata, 'cost': float('nan')}, {**metadata, 'elapsed_ms': True}):
            with self.assertRaises(WebsitePlannerError):
                task._record(bad)
        with self.assertRaises(WebsitePlannerError):
            task._record(metadata)  # Cannot record more attempts than reserved calls.
        self.assertNotIn('CANARY', json.dumps(task.diagnostics()))

    async def test_regressed_human_progress_stops_before_new_model_call(self):
        self.current = observation(sequence=2, stage='form')
        planner = Planner()
        task = self.task(planner)
        await self.until(lambda: self.reads >= 2)
        self.current = observation('other:1', sequence=1)
        task.wake()
        await self.until(lambda: task.snapshot()['state'] == 'requires_agent')
        self.assertEqual(task.snapshot()['error'], 'progress_regressed')
        self.assertEqual(planner.calls, 0)

    async def test_snapshot_strict_types_unknown_keys_and_copy(self):
        task = WebsiteTask(Planner(), observe=self.observe, act=self.act, present=self.present)
        original = task.snapshot()
        copied = validate_task_snapshot(original)
        copied['progress']['event_seq'] = 9
        self.assertEqual(original['progress']['event_seq'], 0)
        for mutate in (lambda v: v.update(label='PRIVATE_CANARY'),
                       lambda v: v.update(calls=True), lambda v: v.update(calls=13),
                       lambda v: v.update(error='PRIVATE_CANARY'),
                       lambda v: v['progress'].update(secret='PRIVATE_CANARY'),
                       lambda v: v['progress'].update(outcome='authenticated')):
            value = deepcopy(original); mutate(value)
            with self.assertRaisesRegex(ValueError, '^Invalid delegated website task status.$'):
                validate_task_snapshot(value)


if __name__ == '__main__':
    unittest.main()
