"""Mocked Decisions contract and proposal safety; no real model quality claims."""

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock

from oak.website_planner import REQUEST_MODEL, RESPONSE_MODEL, WebsitePlannerError
from oak.website_scene import model_context
from oak.website_step_planner import StepPlanner, step_payload
from oak.website_goal import DEFAULT_GOAL
from test_website_scene_planner import surface_scene


def scene(*, fields=False, after=False, revision='fixture:1'):
    return {'provider': 'website', 'destination': 'https://private-fixture.example',
        'revision': revision, 'stage': 'form', 'controls': ([
            {'ref': 'f1', 'type': 'email', 'label': 'Member identifier', 'required': True,
             'form': 'g1', 'order': 0, 'max_length': 254},
            {'ref': 'f2', 'type': 'password', 'label': 'Membership phrase', 'required': True,
             'form': 'g1', 'order': 1, 'max_length': 1024}] if fields else []),
        'actions': [{'ref': 'a1', 'label': 'Enter membership area', 'form': 'g1' if fields else None,
                     'order': 0, 'kind': 'button', 'enabled': True, 'permitted': True}],
        'texts': [], 'after_input': after, 'overflow': {'controls': 0, 'actions': 0, 'texts': 0}}


def observation(value, stage='agent_working'):
    return {'revision': value['revision'], 'stage': stage, 'scene': model_context(value),
            'progress': {'event_seq': 0, 'outcome': 'idle'}}


def response(payload, choice='act_a1', overrides=None, *, cost=True):
    selections = {'anchor': choice, 'surface': 's2', 'field_f1': 'EmailInput', 'field_f2': 'PasswordInput', 'message': 'none'}
    selections.update(overrides or {})
    answers = {}
    for key, question in payload['questions'].items():
        chosen = selections.get(key, 'omit')
        answers[key] = {'type': 'choice', 'choice': chosen, 'confidence': 0,
            'probabilities': {option: float(option == chosen) for option in question['criteria']}}
    result = {'model': RESPONSE_MODEL, 'provider': 'TypeSafe', 'answers': answers,
              'id': 'PRIVATE_RESPONSE_CANARY'}
    if cost:
        result['usage'] = {'cost': 0.00001, 'input_tokens': 100, 'output_tokens': 20}
    return result


class WebsiteStepPlannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = StepPlanner('SYNTHETIC_KEY_CANARY')
        self.client._post = AsyncMock(side_effect=response)

    async def test_navigation_is_one_grounded_proposal_without_private_form_or_auth(self):
        result = await self.client.plan(observation(scene()))
        self.assertEqual(result['operation'], 'act')
        self.assertEqual(result['action_ref'], 'a1')
        self.assertEqual(result['consumed_refs'], ['a1'])
        self.assertIsNone(result['plan'])
        self.assertEqual(result['revision'], 'fixture:1')
        self.assertEqual(self.client._post.await_count, 1)
        self.assertNotIn('authenticated', result)
        self.assertEqual(result['usage'], {'cost': .00001, 'unknown_cost_calls': 0})
        payload = self.client._post.await_args.args[0]
        self.assertEqual(payload['model'], REQUEST_MODEL)
        self.assertEqual(payload['provider'], {'only': ['typesafe'], 'allow_fallbacks': False})
        self.assertEqual(set(payload['questions']['anchor']['criteria']), {'act_a1', 'present_a1', 'wait', 'none'})

    async def test_generic_crosspage_navigation_then_individual_private_components(self):
        steps = [scene(revision='page_one:1'), scene(revision='page_two:1'), scene(fields=True, revision='page_three:1')]
        steps[0]['actions'][0]['label'] = 'Create membership'
        steps[1]['actions'][0]['label'] = 'I already belong'
        steps[1]['actions'][0]['kind'] = 'link'
        choices = iter(['act_a1', 'act_a1', 'present_a1', 'present_a1'])
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, next(choices)))
        results = [await self.client.plan(observation(step)) for step in steps]
        self.assertEqual([r['operation'] for r in results], ['act', 'act', 'present'])
        self.assertEqual([r['revision'] for r in results], ['page_one:1', 'page_two:1', 'page_three:1'])
        plan = results[-1]['plan']
        self.assertEqual(plan['fields'], [{'ref': 'f1', 'component': 'EmailInput'}, {'ref': 'f2', 'component': 'PasswordInput'}])
        self.assertEqual(self.client._post.await_count, 4)
        self.assertEqual(len(results[-1]['requests']), 2)
        self.assertNotIn('Create membership', json.dumps(results))

    async def test_null_owners_do_not_group_loose_controls_but_native_form_does(self):
        native = scene(fields=True)
        self.assertNotIn('act_a1', step_payload(native)['questions']['anchor']['criteria'])
        for field in native['controls']:
            field['form'] = None
        native['actions'][0]['form'] = None
        self.assertIn('act_a1', step_payload(native)['questions']['anchor']['criteria'])
        native['after_input'] = True
        self.assertNotIn('act_a1', step_payload(native)['questions']['anchor']['criteria'])

    async def test_current_input_priority_is_semantic_not_an_alternative_action_filter(self):
        # This checks the request contract only; real Jev quality is evaluated
        # separately. The alternate method remains grounded and selectable.
        value = scene(fields=True)
        value['controls'] = [value['controls'][0]]
        value['controls'][0].update(type='text', label='Member reference')
        value['actions'].append({'ref': 'a2', 'label': 'Use another identity service',
            'form': None, 'order': 1, 'kind': 'button', 'enabled': True, 'permitted': True})
        payload = step_payload(observation(value))
        question = payload['questions']['anchor']
        self.assertEqual(set(question['criteria']),
            {'present_a1', 'act_a2', 'present_a2', 'wait', 'none'})
        self.assertEqual(payload['state']['controls'], model_context(value)['controls'])
        self.assertIn('prefer present for that step', question['instructions'])
        self.assertIn('when navigation is needed', question['instructions'])
        self.assertIn('identity-provider', question['instructions'])
        self.client._post = AsyncMock(side_effect=lambda request: response(request, 'act_a2'))
        selected = await self.client.plan(observation(value))
        self.assertEqual((selected['operation'], selected['action_ref']), ('act', 'a2'))

    async def test_after_input_choices_cannot_propose_act_even_with_forged_model_answer(self):
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(scene(after=True))
        self.assertEqual(error.exception.code, 'invalid_response')
        self.assertEqual(self.client._post.await_count, 1)

    async def test_pending_human_plan_and_trusted_terminal_stage_do_not_call_model(self):
        for stage, operation in [('form', 'wait'), ('device_approval', 'wait'),
                                 ('provider_blocked', 'unsupported'), ('authenticated', 'unsupported')]:
            result = await self.client.plan(observation(scene(fields=True), stage))
            self.assertEqual(result['operation'], operation)
            self.assertEqual(result['requests'], [])
            self.assertIsNone(result['plan'])
            self.assertNotIn('authenticated', result)
        self.client._post.assert_not_awaited()

    async def test_review_is_not_forced_completion_and_action_only_plan_uses_one_request(self):
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, 'present_a1'))
        result = await self.client.plan(observation(scene(after=True), 'review'))
        self.assertEqual(result['operation'], 'present')
        self.assertEqual(result['plan']['fields'], [])
        self.assertEqual(self.client._post.await_count, 1)

    async def test_wait_and_abstention_are_explicit_without_fallback_or_automatic_action(self):
        empty = scene(); empty.update(stage='unsupported_layout', actions=[])
        for answer, operation in [('wait', 'wait'), ('none', 'unsupported')]:
            self.client._post = AsyncMock(side_effect=lambda payload: response(payload, answer))
            result = await self.client.plan(empty)
            self.assertEqual(result['operation'], operation)
            self.assertEqual(result['consumed_refs'], [])
            self.assertEqual(self.client._post.await_count, 1)
            self.assertEqual(result['requests'][0]['status'], 'abstained' if answer == 'none' else 'selected')

    async def test_projection_options_are_validation_only_and_never_exported(self):
        value = scene()
        value['controls'] = [{'ref': 'f' + str(i), 'type': 'select', 'label': 'Choice '+str(i),
            'required': False, 'form': None, 'order': i, 'max_length': 64,
            'options': [{'value': 'o' + str(i), 'label': 'PRIVATE_OPTION_CANARY_'+str(i)}]} for i in (1, 2)]
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, 'present_a1',
            {'field_f1': 'Select', 'field_f2': 'Select'}))
        result = await self.client.plan(observation(value))
        self.assertEqual(len(result['plan']['fields']), 2)
        output = json.dumps([call.args[0] for call in self.client._post.await_args_list]) + json.dumps(result)
        for private in ('PRIVATE_OPTION_CANARY', 'SYNTHETIC_KEY_CANARY', 'PRIVATE_RESPONSE_CANARY',
                        'scene.invalid', 'private-fixture.example'):
            self.assertNotIn(private, output)
        self.assertNotIn('options', self.client._post.await_args.args[0]['state']['controls'][0])

    async def test_unknown_fields_malformed_plans_and_provider_responses_fail_closed(self):
        value = observation(scene()); value['scene']['actions'][0]['selector'] = 'PRIVATE_SELECTOR_CANARY'
        with self.assertRaises(WebsitePlannerError):
            await self.client.plan(value)
        self.client._post.assert_not_awaited()
        for mutation in ('unknown_ref', 'provider', 'model', 'probability', 'required_omission'):
            def post(payload):
                result = response(payload, 'present_a1')
                if mutation == 'unknown_ref':
                    result['answers']['anchor']['choice'] = 'act_a999'
                elif mutation in {'provider', 'model'}:
                    result[mutation] = 'UNAPPROVED_CANARY'
                elif mutation == 'probability':
                    result['answers'][next(iter(result['answers']))]['confidence'] = float('nan')
                elif 'field_f2' in result['answers']:
                    answer = result['answers']['field_f2']; answer['choice'] = 'omit'
                    answer['probabilities'] = {k: float(k == 'omit') for k in answer['probabilities']}
                return result
            with self.subTest(mutation=mutation):
                client = StepPlanner('KEY'); client._post = AsyncMock(side_effect=post)
                with self.assertRaises(WebsitePlannerError) as error:
                    await client.plan(scene(fields=True))
                self.assertEqual(error.exception.code, 'invalid_plan' if mutation == 'required_omission' else 'invalid_response')
                self.assertLessEqual(client._post.await_count, 2)
                self.assertNotIn('CANARY', str(error.exception))

    async def test_twelve_actual_request_cap_survives_concurrent_plans(self):
        results = await asyncio.gather(*(self.client.plan(scene()) for _ in range(13)), return_exceptions=True)
        self.assertEqual(self.client._post.await_count, 12)
        self.assertEqual(self.client.calls, 12)
        self.assertEqual(sum(isinstance(r, WebsitePlannerError) for r in results), 1)

    async def test_second_request_reservation_denial_never_posts_or_retries(self):
        events, records = [], []
        def reserve():
            events.append('reserve')
            if len(events) == 2:
                raise RuntimeError('PRIVATE_QUOTA_CANARY')
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, 'present_a1'))
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(scene(fields=True), reserve=reserve, record=records.append)
        self.assertEqual(error.exception.code, 'unavailable')
        self.assertEqual(self.client._post.await_count, 1)
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(len(records), 1)

    async def test_latency_cost_and_failures_record_only_fixed_private_metadata(self):
        records = []
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, cost=False))
        result = await self.client.plan(scene(), record=records.append)
        self.assertEqual(result['usage'], {'cost': 0, 'unknown_cost_calls': 1})
        self.assertEqual(set(records[0]), {'phase', 'status', 'model', 'cost', 'elapsed_ms', 'error'})
        self.assertIs(type(records[0]['elapsed_ms']), int)
        for failure in (RuntimeError('PRIVATE_FAILURE_CANARY'), asyncio.CancelledError()):
            self.client._post = AsyncMock(side_effect=failure)
            expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else WebsitePlannerError
            with self.assertRaises(expected):
                await self.client.plan(scene(), record=records.append)
            self.client._post.assert_awaited_once()
            self.assertEqual(records[-1]['error'], 'unavailable')
        self.assertNotIn('CANARY', json.dumps(records))
        self.assertNotIn('Enter membership', json.dumps(records))

    async def test_observation_is_frozen_before_callbacks_and_provider_limit_is_pinned(self):
        value = scene(fields=True)
        async def reserve():
            value['actions'][0]['label'] = 'MUTATION_CANARY'
            value['revision'] = 'mutated:2'
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, 'present_a1'))
        result = await self.client.plan(value, reserve=reserve)
        self.assertEqual(result['plan']['revision'], 'fixture:1')
        self.assertNotIn('MUTATION_CANARY', json.dumps([c.args[0] for c in self.client._post.await_args_list]))
        for limit in (0, 13, True):
            with self.assertRaises(ValueError):
                StepPlanner('KEY', max_calls=limit)
        with self.assertRaises(ValueError):
            StepPlanner('KEY', model='another-model')

    async def test_surface_private_projection_composes_every_method_without_option_values(self):
        value = surface_scene()
        self.client._post = AsyncMock(side_effect=lambda payload:
            response(payload, 'present_a1', {'binding_a6': 'none', 'message': 't2'}))
        result = await self.client.plan(observation(value))
        self.assertEqual(result['operation'], 'present')
        plan = result['plan']
        self.assertEqual(plan['surface_ref'], 's2')
        self.assertEqual({f['ref'] for f in plan['fields']}, {'f1', 'f2', 'f3', 'f5'})
        self.assertEqual({a['ref'] for a in plan['actions']}, {'a1', 'a2', 'a4', 'a5', 'a6', 'a7'})
        self.assertEqual(next(a for a in plan['actions'] if a['ref'] == 'a2')['field_refs'], [])
        self.assertIn('s2', result['consumed_refs'])
        self.assertEqual(self.client.calls, 2)
        output = json.dumps(result) + json.dumps([c.args[0] for c in self.client._post.await_args_list])
        for forbidden in ('PRIVATE_OPTION_CAPTION', 'o1', 'private-scene.test', 'SYNTHETIC_KEY_CANARY'):
            self.assertNotIn(forbidden, output)

    async def test_surface_navigation_does_not_need_to_publish_or_execute_other_methods(self):
        value = surface_scene()
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, 'act_a2'))
        result = await self.client.plan(observation(value))
        self.assertEqual((result['operation'], result['action_ref']), ('act', 'a2'))
        self.assertIsNone(result['plan'])
        self.assertEqual(result['consumed_refs'], ['a2'])
        self.client._post.assert_awaited_once()

    async def test_surface_pending_human_form_does_not_replan_or_bill(self):
        result = await self.client.plan(observation(surface_scene(), 'form'))
        self.assertEqual(result['operation'], 'wait')
        self.client._post.assert_not_awaited()

    async def test_surface_second_call_uses_actual_quota_and_cannot_fall_back_to_partial_form(self):
        self.client = StepPlanner('KEY', max_calls=1)
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, 'present_a1'))
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(observation(surface_scene()))
        self.assertEqual(error.exception.code, 'unavailable')
        self.client._post.assert_awaited_once()
        self.assertEqual(self.client.calls, 1)

    async def test_generic_goal_reaches_all_three_batches_and_never_becomes_execution_authority(self):
        value = surface_scene(); value['surfaces'][1]['kind'] = 'region'
        goal = 'Update contact preferences.'
        client = StepPlanner('KEY', goal=goal)
        client._post = AsyncMock(side_effect=lambda payload:
            response(payload, 'present_a1', {'binding_a6': 'none', 'message': 't2'}))
        result = await client.plan(observation(value))
        self.assertEqual(result['plan']['surface_ref'], 's2')
        self.assertEqual(client.calls, 3)
        self.assertEqual([r['phase'] for r in result['requests']], ['anchor', 'surface', 'components'])
        for call in client._post.await_args_list:
            self.assertEqual(call.args[0]['state']['task_goal'], goal)
            self.assertNotIn('existing-account sign-in', json.dumps(call.args[0]))
        self.assertNotIn(goal, json.dumps(result))
        value['after_input'] = True
        self.assertFalse(any(k.startswith('act_') for k in step_payload(observation(value), goal=goal)['questions']['anchor']['criteria']))

    async def test_surface_third_request_obeys_actual_call_limit(self):
        value = surface_scene(); value['surfaces'][1]['kind'] = 'region'
        client = StepPlanner('KEY', max_calls=2)
        client._post = AsyncMock(side_effect=lambda payload: response(payload, 'present_a1'))
        with self.assertRaises(WebsitePlannerError) as error:
            await client.plan(observation(value))
        self.assertEqual(error.exception.code, 'unavailable')
        self.assertEqual((client.calls, client._post.await_count), (2, 2))

    def test_goal_default_compatibility_and_private_value_rejection(self):
        self.assertEqual(StepPlanner('KEY').goal, DEFAULT_GOAL)
        self.assertEqual(step_payload(scene())['state']['task_goal'], DEFAULT_GOAL)
        for invalid in ('', 'someone@example.invalid', 'https://private.example', 'secret\nvalue',
                        'x' * 401, 'sk_' + 'a' * 40, {'goal': 'Sign in'}):
            with self.subTest(invalid=type(invalid).__name__), self.assertRaises(ValueError):
                StepPlanner('KEY', goal=invalid)


if __name__ == '__main__':
    unittest.main()
