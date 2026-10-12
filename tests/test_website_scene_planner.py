"""Mocked Decisions protocol, quota and grounded atomic composition tests."""

import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch

from oak.website_planner import REQUEST_MODEL, RESPONSE_MODEL, WebsitePlannerError
from oak.website_scene_planner import MAX_REQUEST_BYTES, ScenePlanner, anchor_payload, composition_payload, surface_payload, _answers
from oak.website_scene import MAX_OBSERVATION_BYTES, validate_scene
from test_website_scene import scene


def surface_scene():
    observed = scene()
    observed['controls'].append({'ref': 'f5', 'type': 'select', 'label': 'Member category',
        'required': True, 'form': 'g3', 'order': 8, 'max_length': 64,
        'options': [{'value': 'o1', 'label': 'PRIVATE_OPTION_CAPTION'}]})
    observed['actions'][0]['dispatch'] = 'submit'
    observed['actions'][1].update(form=None, kind='link', dispatch='link')
    observed['actions'][2]['dispatch'] = 'submit'
    observed['actions'].extend([
        {'ref': 'a4', 'label': 'Use this member category', 'form': 'g3', 'order': 9,
         'kind': 'button', 'dispatch': 'submit', 'enabled': True, 'permitted': True},
        {'ref': 'a5', 'label': 'Another identity service', 'form': None, 'order': 10,
         'kind': 'button', 'dispatch': 'button', 'enabled': True, 'permitted': False},
        {'ref': 'a6', 'label': 'Choose another method', 'form': 'g1', 'order': 11,
         'kind': 'button', 'dispatch': 'button', 'enabled': True, 'permitted': True},
        {'ref': 'a7', 'label': 'Unavailable submission', 'form': 'g1', 'order': 12,
         'kind': 'button', 'dispatch': 'submit', 'enabled': True, 'permitted': False},
    ])
    observed['surfaces'] = [
        {'ref': 's1', 'kind': 'form', 'controls': ['f1', 'f2', 'f3'],
         'actions': ['a1', 'a6', 'a7'], 'texts': ['t2']},
        {'ref': 's2', 'kind': 'dialog', 'controls': ['f1', 'f2', 'f3', 'f5'],
         'actions': ['a1', 'a2', 'a4', 'a5', 'a6', 'a7'], 'texts': ['t1', 't2']},
        {'ref': 's3', 'kind': 'document', 'controls': [item['ref'] for item in observed['controls']],
         'actions': [item['ref'] for item in observed['actions']], 'texts': ['t1', 't2']},
    ]
    return observed


def response(payload, overrides=None, *, usage=True):
    choices = {'anchor': 'a1', 'surface': 's2', 'field_f1': 'EmailInput',
               'field_f2': 'PasswordInput', 'field_f3': 'omit', 'action_a2': 'include', 'message': 't2'}
    choices.update(overrides or {})
    answers = {}
    for key, question in payload['questions'].items():
        choice = choices.get(key, 'omit')
        answers[key] = {'type': 'choice', 'choice': choice, 'confidence': 0,
                        'probabilities': {option: float(option == choice) for option in question['criteria']}}
    result = {'id': 'PRIVATE_RESPONSE_CANARY', 'model': RESPONSE_MODEL, 'provider': 'TypeSafe', 'answers': answers}
    if usage:
        result['usage'] = {'input_tokens': 100, 'output_tokens': 30, 'cost': 0.00001}
    return result


class WebsiteScenePlannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = ScenePlanner('SYNTHETIC_KEY_CANARY')
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload))

    def test_118_choice_rounded_distribution_preserves_inclusive_one_percent_bounds(self):
        # Mirrors the live failure's count and decimal sum, without its page data.
        criteria = {f'option_{index}': None for index in range(118)}
        questions = {'anchor': {'type': 'choice', 'criteria': criteria}}
        for first, second, accepted in ((.98, .01, True), (1, .01, True),
                                         (.979, .01, False), (1, .011, False)):
            probabilities = dict.fromkeys(criteria, 0)
            probabilities.update(option_0=first, option_1=second)
            wire = {'model': RESPONSE_MODEL, 'answers': {'anchor': {'type': 'choice',
                'choice': 'option_0', 'confidence': .8, 'probabilities': probabilities}}}
            with self.subTest(first=first, second=second):
                if accepted:
                    normalized, _ = _answers(wire, questions)
                    self.assertEqual(normalized['anchor']['probabilities'], probabilities)
                else:
                    with self.assertRaises(WebsitePlannerError) as error:
                        _answers(wire, questions)
                    self.assertEqual(error.exception.code, 'invalid_response')
        for mutation in ('nonfinite', 'boolean', 'wrong_max', 'missing', 'tiny_excess'):
            probabilities = dict.fromkeys(criteria, 0)
            probabilities.update(option_0=1, option_1=.01)
            wire['answers']['anchor'].update(choice='option_0', probabilities=probabilities)
            if mutation == 'nonfinite': probabilities['option_2'] = float('nan')
            elif mutation == 'boolean': probabilities['option_2'] = False
            elif mutation == 'wrong_max': wire['answers']['anchor']['choice'] = 'option_1'
            elif mutation == 'missing': del probabilities['option_117']
            else: probabilities['option_2'] = 1e-100
            with self.subTest(mutation=mutation), self.assertRaises(WebsitePlannerError):
                _answers(wire, questions)

    async def test_two_conditioned_batches_compose_individual_fields_and_actions(self):
        result = await self.client.plan(scene())
        first, second = [call.args[0] for call in self.client._post.await_args_list]
        self.assertEqual(self.client._post.await_count, 2)
        for payload in (first, second):
            self.assertEqual(payload['model'], REQUEST_MODEL)
            self.assertEqual(payload['provider'], {'only': ['typesafe'], 'allow_fallbacks': False})
            self.assertTrue(all(question['type'] == 'choice' and len(question['criteria']) >= 2
                                for question in payload['questions'].values()))
        self.assertEqual(set(first['questions']), {'anchor'})
        self.assertEqual(second['state']['selected_anchor'], 'a1')
        self.assertEqual(set(second['questions']), {'field_f1', 'field_f2', 'field_f3', 'action_a2', 'message'})
        self.assertEqual([field['name'] for field in result['form']['fields']], ['f1', 'f2'])
        self.assertEqual(result['form']['message'], 'Check your account identifier')
        self.assertEqual(result['usage'], {'cost': 0.00002, 'unknown_cost_calls': 0})
        self.assertEqual(result['decision']['components']['field_f3']['choice'], 'omit')
        self.assertEqual(result['decision']['anchor']['anchor']['confidence'], 0.0)

    async def test_reserve_and_record_order_and_safe_metadata(self):
        events, records = [], []
        def reserve():
            events.append('reserve')
        async def post(payload):
            events.append('post')
            return response(payload)
        async def record(metadata):
            events.append('record')
            records.append(deepcopy(metadata))
        self.client._post = AsyncMock(side_effect=post)
        await self.client.plan(scene(), reserve=reserve, record=record)
        self.assertEqual(events, ['reserve', 'post', 'record'] * 2)
        for item in records:
            self.assertEqual(set(item), {'phase', 'status', 'model', 'cost', 'elapsed_ms', 'error'})
            self.assertEqual(item['status'], 'selected')
            self.assertEqual(item['model'], RESPONSE_MODEL)
            self.assertIsNone(item['error'])
        self.assertNotIn('CANARY', json.dumps(records))
        self.assertNotIn('Account email', json.dumps(records))

    async def test_second_reservation_failure_never_posts_again(self):
        reserve = AsyncMock(side_effect=[None, RuntimeError('SECRET_QUOTA_CANARY')])
        records = []
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(scene(), reserve=reserve, record=records.append)
        self.assertEqual(error.exception.code, 'unavailable')
        self.assertNotIn('CANARY', str(error.exception))
        self.assertEqual(self.client._post.await_count, 1)
        self.assertEqual(len(records), 1)

    async def test_anchor_can_abstain_and_paid_attempt_is_recorded(self):
        records = []
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, {'anchor': 'none'}))
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(scene(), record=records.append)
        self.assertEqual(error.exception.code, 'no_candidate')
        self.assertEqual(self.client._post.await_count, 1)
        self.assertEqual(records[-1]['status'], 'abstained')
        self.assertEqual(records[-1]['cost'], 0.00001)

    async def test_missing_malformed_and_extra_second_answers_fail_without_retry(self):
        for mutation in ('missing', 'extra', 'downgrade', 'probability', 'confidence', 'model', 'provider', 'cost'):
            def post(payload):
                result = response(payload)
                if 'anchor' in payload['questions']:
                    return result
                if mutation == 'missing':
                    del result['answers']['action_a2']
                elif mutation == 'extra':
                    result['answers']['PRIVATE_EXTRA_CANARY'] = {}
                elif mutation == 'downgrade':
                    result['answers']['field_f2']['choice'] = 'TextInput'
                elif mutation == 'probability':
                    result['answers']['field_f3']['probabilities']['omit'] = float('nan')
                elif mutation == 'confidence':
                    result['answers']['action_a2']['confidence'] = True
                elif mutation == 'model':
                    result['model'] = 'unapproved-model'
                elif mutation == 'provider':
                    result['provider'] = 'unapproved-provider'
                else:
                    result['usage']['cost'] = -1
                return result
            with self.subTest(mutation=mutation):
                records = []
                self.client._post = AsyncMock(side_effect=post)
                with self.assertRaises(WebsitePlannerError) as error:
                    await self.client.plan(scene(), record=records.append)
                self.assertEqual(error.exception.code, 'invalid_response')
                self.assertEqual(self.client._post.await_count, 2)
                self.assertEqual(records[-1]['status'], 'failed')
                self.assertNotIn('CANARY', str(error.exception) + json.dumps(records))

    async def test_network_failure_and_cancellation_are_recorded_safely(self):
        for failure in (RuntimeError('PRIVATE_ERROR_CANARY https://secret.test/?token=CANARY'), asyncio.CancelledError()):
            self.client._post = AsyncMock(side_effect=failure)
            records = []
            expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else WebsitePlannerError
            with self.assertRaises(expected):
                await self.client.plan(scene(), record=records.append)
            self.assertEqual(self.client._post.await_count, 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['error'], 'unavailable')
            self.assertNotIn('CANARY', json.dumps(records))

    async def test_required_omission_is_invalid_plan_not_silent_insertion(self):
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, {'field_f2': 'omit'}))
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(scene())
        self.assertEqual(error.exception.code, 'invalid_plan')
        self.assertEqual(self.client._post.await_count, 2)

    async def test_record_failure_preserves_original_protocol_failure(self):
        self.client._post = AsyncMock(return_value={'model': 'WRONG_PRIVATE_CANARY'})
        record = AsyncMock(side_effect=RuntimeError('PRIVATE_RECORD_CANARY'))
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(scene(), record=record)
        self.assertEqual(error.exception.code, 'invalid_response')
        self.assertNotIn('CANARY', str(error.exception))
        self.client._post.assert_awaited_once()

    async def test_observation_frozen_before_callbacks_and_no_private_routing_export(self):
        observed = scene()
        async def post(payload):
            observed['revision'] = 'changed:2'
            observed['controls'][0]['label'] = 'MUTATION_CANARY'
            return response(payload)
        self.client._post = AsyncMock(side_effect=post)
        result = await self.client.plan(observed)
        self.assertEqual(result['plan']['revision'], 'private_revision:1')
        self.assertEqual(result['form']['fields'][0]['label'], 'Account email')
        encoded = json.dumps([call.args[0] for call in self.client._post.await_args_list])
        for forbidden in ('MUTATION_CANARY', 'SYNTHETIC_KEY_CANARY', 'private_revision', 'private-scene.test'):
            self.assertNotIn(forbidden, encoded)
        metadata = {key: value for key, value in result.items() if key not in {'form', 'plan'}}
        self.assertNotIn('PRIVATE_RESPONSE_CANARY', json.dumps(metadata))
        self.assertNotIn('Account email', json.dumps(metadata))

    async def test_unknown_cost_stays_explicit(self):
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, usage=False))
        records = []
        result = await self.client.plan(scene(), record=records.append)
        self.assertEqual(result['usage'], {'cost': 0, 'unknown_cost_calls': 2})
        self.assertTrue(all(item['cost'] is None for item in records))

    async def test_zero_field_chooser_compiles_with_one_call_when_no_decisions_remain(self):
        observed = scene()
        observed['controls'], observed['texts'] = [], []
        observed['actions'] = [observed['actions'][0]]
        observed['actions'][0].update(enabled=True, label='Use a code')
        reserve, records = AsyncMock(), []
        result = await self.client.plan(observed, reserve=reserve, record=records.append)
        self.assertFalse(result['form']['fields'])
        self.assertEqual(result['form']['message'], '')
        self.assertEqual(result['decision']['components'], {})
        self.assertEqual(result['usage'], {'cost': 0.00001, 'unknown_cost_calls': 0})
        self.assertEqual(self.client._post.await_count, 1)
        reserve.assert_awaited_once()
        self.assertEqual(len(records), 1)
        self.assertFalse(composition_payload(observed, 'a1')['questions'])

    async def test_chooser_with_observed_message_only_asks_message_not_approval(self):
        observed = scene()
        observed['controls'] = []
        observed['actions'] = [observed['actions'][0]]
        observed['actions'][0].update(enabled=True, label='Use a code')
        result = await self.client.plan(observed)
        self.assertEqual(set(self.client._post.await_args_list[1].args[0]['questions']), {'message'})
        self.assertEqual(result['form']['message'], 'Check your account identifier')
        self.assertFalse(result['form']['fields'])

    async def test_chooser_without_usage_counts_only_the_single_actual_request(self):
        observed = scene()
        observed['controls'], observed['texts'] = [], []
        observed['actions'] = [observed['actions'][0]]
        observed['actions'][0]['enabled'] = True
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, usage=False))
        result = await self.client.plan(observed)
        self.assertEqual(result['usage'], {'cost': 0, 'unknown_cost_calls': 1})
        self.client._post.assert_awaited_once()

    async def test_no_candidate_and_structural_rejection_consume_no_quota(self):
        for mode in ('empty', 'blocked', 'wait', 'value'):
            observed, reserve, record = scene(), AsyncMock(), AsyncMock()
            if mode == 'empty':
                observed['controls'], observed['actions'] = [], []
            elif mode == 'blocked':
                for action in observed['actions']:
                    action['permitted'] = False
            elif mode == 'wait':
                observed['stage'] = 'wait'
            else:
                observed['controls'][0]['value'] = 'PRIVATE_VALUE_CANARY'
            with self.subTest(mode=mode), self.assertRaises(WebsitePlannerError):
                await self.client.plan(observed, reserve=reserve, record=record)
            reserve.assert_not_awaited()
            record.assert_not_awaited()
        self.client._post.assert_not_awaited()

    def test_disabled_permitted_anchor_and_native_constraints(self):
        observed = scene()
        self.assertIn('a1', anchor_payload(observed)['questions']['anchor']['criteria'])
        observed['actions'][0]['permitted'] = False
        self.assertNotIn('a1', anchor_payload(observed)['questions']['anchor']['criteria'])
        with self.assertRaises(WebsitePlannerError):
            composition_payload(observed, 'a1')
        observed = scene()
        observed['controls'] = []
        self.assertNotIn('a1', anchor_payload(observed)['questions']['anchor']['criteria'])

    def test_link_anchor_has_no_field_questions_or_field_bearing_alternatives(self):
        observed = scene()
        for item in observed['controls'] + observed['actions']:
            item['form'] = None
        observed['actions'][1]['kind'] = 'link'
        button_questions = composition_payload(observed, 'a1')['questions']
        self.assertNotIn('action_a2', button_questions)
        link_questions = composition_payload(observed, 'a2')['questions']
        self.assertFalse(any(key.startswith('field_') for key in link_questions))

    async def test_surface_keeps_other_owner_fields_alternatives_and_denied_actions(self):
        observed = surface_scene()
        self.client._post = AsyncMock(side_effect=lambda payload:
            response(payload, {'binding_a6': 'none'}))
        records, reserve = [], AsyncMock()
        result = await self.client.plan(observed, reserve=reserve, record=records.append)
        plan = result['plan']
        self.assertEqual(plan['surface_ref'], 's2')
        self.assertEqual({field['ref'] for field in plan['fields']}, {'f1', 'f2', 'f3', 'f5'})
        actions = {action['ref']: action for action in plan['actions']}
        self.assertEqual(set(actions), {'a1', 'a2', 'a4', 'a5', 'a6', 'a7'})
        self.assertEqual(actions['a1']['field_refs'], ['f1', 'f2', 'f3'])
        self.assertEqual(actions['a4']['field_refs'], ['f5'])
        for ref in ('a2', 'a5', 'a6', 'a7'):
            self.assertEqual(actions[ref]['field_refs'], [])
        rendered = {action['id']: action for action in result['form']['actions']}
        self.assertFalse(rendered['a5']['available'])
        self.assertFalse(rendered['a7']['available'])
        self.assertTrue(rendered['a1']['available'])  # Filling may enable submit.
        questions = self.client._post.await_args_list[1].args[0]['questions']
        self.assertEqual(set(questions), {'binding_a6', 'message'})
        self.assertTrue(all('omit' not in q['criteria'] for q in questions.values()))
        payloads = json.dumps([call.args[0] for call in self.client._post.await_args_list])
        self.assertNotIn('PRIVATE_OPTION_CAPTION', payloads)
        self.assertNotIn('private-scene.test', payloads)
        self.assertEqual(reserve.await_count, 2)
        self.assertEqual([item['phase'] for item in records], ['anchor', 'components'])

    async def test_surface_button_binding_does_not_follow_native_submit_automatically(self):
        for binding, expected in [('none', []), ('fields', ['f1', 'f2', 'f3'])]:
            self.client._post = AsyncMock(side_effect=lambda payload:
                response(payload, {'binding_a6': binding}))
            result = await self.client.plan(surface_scene())
            chosen = next(a for a in result['plan']['actions'] if a['ref'] == 'a6')
            self.assertEqual(chosen['field_refs'], expected)
            self.assertEqual(len(result['plan']['fields']), 4)

    async def test_nonmodal_surface_is_a_semantic_choice_not_a_smallest_ancestor_rule(self):
        observed = surface_scene()
        observed['surfaces'][1]['kind'] = 'region'
        observed['surfaces'].insert(1, {'ref': 's4', 'kind': 'container',
            'controls': ['f1', 'f2', 'f3', 'f5', 'f4'],
            'actions': ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7'], 'texts': ['t1', 't2']})
        payload = surface_payload(observed, 'a1')
        self.assertEqual(set(payload['questions']['surface']['criteria']), {'s1', 's2', 's3', 's4', 'none'})
        with self.assertRaises(WebsitePlannerError):
            composition_payload(observed, 'a1')  # No silent smallest-scope fallback.
        self.client._post = AsyncMock(side_effect=lambda request:
            response(request, {'binding_a6': 'none'}))
        records, reserve = [], AsyncMock()
        result = await self.client.plan(observed, reserve=reserve, record=records.append)
        self.assertEqual(result['plan']['surface_ref'], 's2')
        self.assertEqual(reserve.await_count, 3)
        self.assertEqual([r['phase'] for r in records], ['anchor', 'surface', 'components'])
        self.assertAlmostEqual(result['usage']['cost'], .00003)
        self.assertEqual(self.client._post.await_args_list[2].args[0]['state']['selected_surface'], 's2')
        self.assertNotIn('field_f4', self.client._post.await_args_list[2].args[0]['questions'])

    async def test_surface_none_stops_before_components_without_fallback(self):
        observed = surface_scene()
        observed['surfaces'][1]['kind'] = 'region'
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, {'surface': 'none'}))
        records = []
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(observed, record=records.append)
        self.assertEqual(error.exception.code, 'no_candidate')
        self.assertEqual(self.client._post.await_count, 2)
        self.assertEqual(records[-1]['phase'], 'surface')
        self.assertEqual(records[-1]['status'], 'abstained')

    def test_surface_options_carry_their_own_sanitized_semantics_and_preserve_goal_context(self):
        observed = surface_scene()
        observed['surfaces'][1]['kind'] = 'region'
        observed['after_input'] = True
        observed['history'] = [{'action': 'Continue to the next step', 'field_types': ['email']}]
        observed['controls'][0]['role'] = 'email'
        original = deepcopy(observed)
        payload = surface_payload(observed, 'a1', goal='Review the current account settings')
        criteria, context = payload['questions']['surface']['criteria'], payload['state']
        complete = criteria['s2']
        for category in ('controls', 'actions', 'texts'):
            self.assertEqual({item['ref'] for item in complete[category]}, set(observed['surfaces'][1][category]))
        self.assertEqual(complete['controls'][0]['label'], observed['controls'][0]['label'])
        self.assertEqual(complete['controls'][0]['role'], 'email')
        self.assertEqual(complete['actions'][-2]['dispatch'], 'button')
        self.assertFalse(next(item for item in complete['actions'] if item['ref'] == 'a5')['permitted'])
        self.assertEqual(complete['texts'], observed['texts'])
        self.assertEqual(context['task_goal'], 'Review the current account settings')
        self.assertEqual(context['history'], observed['history'])
        self.assertTrue(context['after_input'])
        self.assertEqual(context['selected_anchor'], 'a1')
        self.assertEqual(context['texts'], observed['texts'])  # Global context is still visible.
        self.assertNotIn('controls', context)
        self.assertNotIn('actions', context)
        self.assertNotIn('surfaces', context)
        encoded = json.dumps(payload)
        for private in ('PRIVATE_OPTION_CAPTION', 'private-scene.test', '"options"', '"destination"'):
            self.assertNotIn(private, encoded)
        self.assertEqual(observed, original)

    def test_surface_alias_dedup_preserves_broad_scope_different_kinds_and_text_evidence(self):
        observed = surface_scene(); observed['surfaces'][1]['kind'] = 'region'
        duplicate = deepcopy(observed['surfaces'][1]); duplicate['ref'] = 's8'
        for key in ('controls', 'actions', 'texts'):
            duplicate[key].reverse()
        changed_kind = dict(duplicate, ref='s9', kind='container')
        changed_text = dict(duplicate, ref='s10', texts=['t1'])
        observed['surfaces'].extend([duplicate, changed_kind, changed_text])
        payload = surface_payload(observed, 'a1')
        self.assertEqual(set(payload['questions']['surface']['criteria']), {'s1', 's2', 's3', 's9', 's10', 'none'})
        # Broad document remains a model option; no smallest-scope decision.
        self.assertGreater(len(payload['questions']['surface']['criteria']['s3']['controls']),
                           len(payload['questions']['surface']['criteria']['s1']['controls']))
        # Direct structural callers can retain an equivalent observed alias.
        self.assertEqual(composition_payload(observed, 'a1', surface_ref='s8')['state']['selected_surface'], 's8')
        with self.assertRaises(WebsitePlannerError):
            composition_payload(observed, 'a1')

    def test_equal_labels_or_counts_do_not_merge_different_observed_members(self):
        observed = surface_scene(); observed['surfaces'][1]['kind'] = 'region'
        for action in observed['actions']:
            action['label'] = 'Observed alternative'
        observed['surfaces'].extend([
            {'ref': 's8', 'kind': 'region', 'controls': ['f1', 'f2', 'f3'],
             'actions': ['a1', 'a2'], 'texts': ['t1']},
            {'ref': 's9', 'kind': 'region', 'controls': ['f1', 'f2', 'f3'],
             'actions': ['a1', 'a5'], 'texts': ['t1']},
        ])
        criteria = surface_payload(observed, 'a1')['questions']['surface']['criteria']
        self.assertIn('s8', criteria)
        self.assertIn('s9', criteria)

    async def test_exact_aliases_avoid_only_a_redundant_surface_request(self):
        observed = surface_scene()
        observed['surfaces'] = [dict(observed['surfaces'][1], kind='region')]
        observed['surfaces'].append(dict(observed['surfaces'][0], ref='s8'))
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, {'binding_a6': 'none'}))
        reserve, records = AsyncMock(), []
        result = await self.client.plan(observed, reserve=reserve, record=records.append)
        self.assertEqual(result['plan']['surface_ref'], 's2')
        self.assertEqual({field['ref'] for field in result['plan']['fields']}, set(observed['surfaces'][0]['controls']))
        self.assertEqual({action['ref'] for action in result['plan']['actions']}, set(observed['surfaces'][0]['actions']))
        self.assertEqual([record['phase'] for record in records], ['anchor', 'components'])
        self.assertEqual(reserve.await_count, 2)

    async def test_full_serialized_request_bound_precedes_reservation_and_counts_json_escaping(self):
        payload = anchor_payload(scene())
        payload['state']['additional_context'] = '\u00e9' * 100
        wire_bytes = len(json.dumps(payload, allow_nan=False).encode('utf-8'))
        self.assertGreater(wire_bytes, len(json.dumps(payload, ensure_ascii=False).encode('utf-8')))
        reserve, record = AsyncMock(), AsyncMock()
        with patch('oak.website_scene_planner.MAX_REQUEST_BYTES', wire_bytes - 1):
            with self.assertRaises(WebsitePlannerError) as error:
                await self.client._request(payload, 'anchor', reserve, record)
        self.assertEqual(error.exception.code, 'invalid_plan')
        reserve.assert_not_awaited(); record.assert_not_awaited(); self.client._post.assert_not_awaited()
        with patch('oak.website_scene_planner.MAX_REQUEST_BYTES', wire_bytes):
            await self.client._request(payload, 'anchor', reserve, record)
        reserve.assert_awaited_once(); record.assert_awaited_once(); self.client._post.assert_awaited_once()

    async def test_valid_maximum_scene_members_fit_request_bound_without_truncation(self):
        observed = scene()
        observed['controls'] = [{'ref': f'f{i}', 'type': 'checkbox', 'label': '\u754c' * 80,
            'required': False, 'form': 'g1', 'order': i, 'max_length': 1} for i in range(1, 33)]
        observed['actions'] = [{'ref': f'a{i}', 'label': '\u754c' * 80, 'form': 'g1',
            'order': i, 'kind': 'button', 'dispatch': 'submit',
            'enabled': True, 'permitted': i == 1} for i in range(1, 65)]
        observed['texts'] = [{'ref': f't{i}', 'kind': 'help', 'text': '\u754c' * 80} for i in range(1, 25)]
        observed['surfaces'] = [{'ref': f's{i}', 'kind': 'region',
            'controls': [item['ref'] for item in observed['controls']],
            'actions': [item['ref'] for item in observed['actions']],
            'texts': [item['ref'] for item in observed['texts'] if item['ref'] != f't{i}']}
            for i in range(1, 17)]
        validate_scene(observed)
        self.assertLessEqual(len(json.dumps(observed, ensure_ascii=False).encode('utf-8')), MAX_OBSERVATION_BYTES)
        payload = surface_payload(observed, 'a1')
        self.assertEqual(len(payload['questions']['surface']['criteria']), 17)
        for index in range(1, 17):
            option = payload['questions']['surface']['criteria'][f's{index}']
            self.assertEqual((len(option['controls']), len(option['actions']), len(option['texts'])), (32, 64, 23))
        self.assertLess(len(json.dumps(payload).encode('utf-8')), MAX_REQUEST_BYTES)
        reserve = AsyncMock()
        await self.client._request(payload, 'surface', reserve, None)
        reserve.assert_awaited_once(); self.client._post.assert_awaited_once()

    async def test_surface_membership_selection_is_closed_set_and_quota_bounded(self):
        observed = surface_scene(); observed['surfaces'][1]['kind'] = 'region'
        self.client._post = AsyncMock(side_effect=lambda payload: response(payload, {'surface': 's999'}))
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(observed)
        self.assertEqual(error.exception.code, 'invalid_response')
        self.assertEqual(self.client._post.await_count, 2)
        attempts = []
        def reserve():
            attempts.append(1)
            if len(attempts) == 3:
                raise ValueError('PRIVATE_BUDGET')
        self.client._post = AsyncMock(side_effect=response)
        with self.assertRaises(WebsitePlannerError) as error:
            await self.client.plan(observed, reserve=reserve)
        self.assertEqual(error.exception.code, 'unavailable')
        self.assertEqual(self.client._post.await_count, 2)

    async def test_generic_goal_is_context_in_all_three_requests_without_login_constraint(self):
        observed = surface_scene(); observed['surfaces'][1]['kind'] = 'region'
        goal = 'Update notification preferences.'
        client = ScenePlanner('KEY', goal=goal)
        client._post = AsyncMock(side_effect=lambda payload: response(payload, {'binding_a6': 'none'}))
        await client.plan(observed)
        for call in client._post.await_args_list:
            self.assertEqual(call.args[0]['state']['task_goal'], goal)
            self.assertNotIn('existing-account sign-in', json.dumps(call.args[0]))
        legacy = composition_payload(scene(), 'a1', goal=goal)
        self.assertNotIn('sign-in', json.dumps(legacy))

    def test_surface_does_not_omit_external_native_controls_even_when_optional(self):
        # Native submit includes external form-associated fields too. Optional
        # does not mean an observed field may disappear from its private UI.
        for required in (True, False):
            with self.subTest(required=required):
                observed = surface_scene()
                observed['controls'][3].update(form='g1', required=required)
                self.assertEqual(composition_payload(observed, 'a1')['state']['selected_surface'], 's3')
                observed['surfaces'].pop()
                with self.assertRaises(WebsitePlannerError) as error:
                    composition_payload(observed, 'a1')
                self.assertEqual(error.exception.code, 'invalid_plan')

    async def test_surface_fixed_components_do_not_create_a_second_paid_question(self):
        observed = scene()
        observed['controls'] = [observed['controls'][1]]
        observed['texts'] = []
        observed['actions'] = [observed['actions'][0]]
        observed['actions'][0]['dispatch'] = 'submit'
        observed['surfaces'] = [{'ref': 's1', 'kind': 'form', 'controls': ['f2'],
                                  'actions': ['a1'], 'texts': []}]
        result = await self.client.plan(observed)
        self.assertEqual(result['plan']['fields'], [{'ref': 'f2', 'component': 'PasswordInput'}])
        self.assertEqual(result['plan']['actions'][0]['field_refs'], ['f2'])
        self.client._post.assert_awaited_once()

    async def test_surface_model_cannot_omit_a_control_or_invent_a_binding(self):
        for bad_answer in ({'field_f1': 'omit'}, {'binding_a6': 'all_drafts'}):
            client = ScenePlanner('KEY')
            client._post = AsyncMock(side_effect=lambda payload:
                response(payload, {'binding_a6': 'none', **bad_answer}))
            observed = surface_scene()
            observed['controls'][0]['type'] = 'text'  # Genuinely ambiguous widget.
            with self.assertRaises(WebsitePlannerError) as error:
                await client.plan(observed)
            self.assertEqual(error.exception.code, 'invalid_response')
            self.assertEqual(client._post.await_count, 2)

    async def test_surface_standard_types_and_roles_skip_redundant_widget_judgments(self):
        for kind, role, component in [('email', None, 'EmailInput'), ('text', 'email', 'EmailInput'),
                                      ('text', 'tel', 'TextInput'), ('otp', 'one-time-code', 'CodeInput')]:
            observed = scene()
            control = observed['controls'][0]
            control.update(type=kind, max_length=16 if kind == 'otp' else 254)
            if role is not None:
                control['role'] = role
            observed['controls'], observed['texts'] = [control], []
            observed['actions'] = [observed['actions'][0]]
            observed['actions'][0]['dispatch'] = 'submit'
            observed['surfaces'] = [{'ref': 's1', 'kind': 'form', 'controls': ['f1'],
                                      'actions': ['a1'], 'texts': []}]
            client = ScenePlanner('KEY'); client._post = AsyncMock(side_effect=response)
            result = await client.plan(observed)
            self.assertEqual(result['plan']['fields'][0]['component'], component)
            client._post.assert_awaited_once()

    async def test_surface_preserves_full_catalog_bounds_instead_of_legacy_limits(self):
        observed = scene()
        observed['controls'] = [{'ref': 'f' + str(i), 'type': 'checkbox', 'label': 'Option',
            'required': False, 'form': 'g1', 'order': i, 'max_length': 1} for i in range(1, 33)]
        observed['actions'] = [{'ref': 'a' + str(i), 'label': 'Observed action', 'form': 'g1',
            'order': i, 'kind': 'button', 'dispatch': 'submit',
            'enabled': True, 'permitted': i == 1} for i in range(1, 65)]
        observed['texts'] = []
        observed['surfaces'] = [{'ref': 's1', 'kind': 'dialog',
            'controls': [f['ref'] for f in observed['controls']],
            'actions': [a['ref'] for a in observed['actions']], 'texts': []}]
        result = await self.client.plan(observed)
        self.assertEqual((len(result['plan']['fields']), len(result['plan']['actions'])), (32, 64))
        self.client._post.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
