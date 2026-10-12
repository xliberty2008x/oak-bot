"""Pure scene/catalog boundary checks; no browser or model calls."""

from copy import deepcopy
import json
import unittest

from oak.website_scene import (
    CATALOG_ID, CATALOG_VERSION, compile_plan, model_context, validate_scene,
)


def scene():
    return {'provider': 'website', 'destination': 'https://private-scene.test',
            'revision': 'private_revision:1', 'stage': 'form', 'after_input': False,
            'overflow': {'controls': 0, 'actions': 0, 'texts': 0},
            'controls': [
                {'ref': 'f1', 'type': 'email', 'label': 'Account email', 'required': True,
                 'form': 'g1', 'order': 0, 'max_length': 254},
                {'ref': 'f2', 'type': 'password', 'label': 'Password', 'required': True,
                 'form': 'g1', 'order': 1, 'max_length': 1024},
                {'ref': 'f3', 'type': 'checkbox', 'label': 'Remember this browser', 'required': False,
                 'form': 'g1', 'order': 2, 'max_length': 1},
                {'ref': 'f4', 'type': 'text', 'label': 'Search', 'required': False,
                 'form': 'g2', 'order': 3, 'max_length': 1024}],
            'actions': [
                {'ref': 'a1', 'label': 'Enter workspace', 'form': 'g1', 'order': 4,
                 'kind': 'button', 'enabled': False, 'permitted': True},
                {'ref': 'a2', 'label': 'Recover account', 'form': 'g1', 'order': 5,
                 'kind': 'button', 'enabled': True, 'permitted': True},
                {'ref': 'a3', 'label': 'Search', 'form': 'g2', 'order': 6,
                 'kind': 'button', 'enabled': True, 'permitted': True}],
            'texts': [{'ref': 't1', 'kind': 'heading', 'text': 'Return to your workspace'},
                      {'ref': 't2', 'kind': 'error', 'text': 'Check your account identifier'}]}


def plan():
    return {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
            'revision': 'private_revision:1', 'anchor': 'a1',
            'fields': [{'ref': 'f1', 'component': 'EmailInput'},
                       {'ref': 'f2', 'component': 'PasswordInput'}],
            'actions': [{'ref': 'a1', 'presentation': 'primary'},
                        {'ref': 'a2', 'presentation': 'secondary'}], 'message_ref': 't2'}


class WebsiteSceneTests(unittest.TestCase):
    def test_subset_compiles_grounded_components_and_copied_message(self):
        observed, chosen = scene(), plan()
        form = compile_plan(chosen, observed)
        self.assertEqual([item['name'] for item in form['fields']], ['f1', 'f2'])
        self.assertEqual([item['type'] for item in form['fields']], ['email', 'password'])
        self.assertEqual(form['message'], 'Check your account identifier')
        self.assertEqual(form['stage'], 'form')
        self.assertEqual(set(form), {'provider', 'destination', 'revision', 'stage', 'fields', 'actions', 'message'})
        observed['controls'][0]['label'] = 'Changed after compilation'
        self.assertEqual(form['fields'][0]['label'], 'Account email')

    def test_native_required_and_ownership_constraints(self):
        for change in ('omit_required', 'foreign_field', 'foreign_action', 'missing_anchor', 'second_primary'):
            with self.subTest(change=change):
                chosen = plan()
                if change == 'omit_required':
                    chosen['fields'].pop()
                elif change == 'foreign_field':
                    chosen['fields'].append({'ref': 'f4', 'component': 'TextInput'})
                elif change == 'foreign_action':
                    chosen['actions'].append({'ref': 'a3', 'presentation': 'secondary'})
                elif change == 'missing_anchor':
                    chosen['actions'].pop(0)
                else:
                    chosen['actions'][1]['presentation'] = 'primary'
                with self.assertRaises(ValueError):
                    compile_plan(chosen, scene())

    def test_loose_controls_are_individually_composed_not_one_required_group(self):
        observed, chosen = scene(), plan()
        for item in observed['controls'] + observed['actions']:
            item['form'] = None
        chosen['fields'] = [{'ref': 'f1', 'component': 'EmailInput'}]
        self.assertEqual(len(compile_plan(chosen, observed)['fields']), 1)

    def test_component_compatibility_cannot_downgrade_secrets(self):
        for component in ('TextInput', 'EmailInput', 'CodeInput', 'Select', 'Checkbox'):
            chosen = plan()
            chosen['fields'][1]['component'] = component
            with self.assertRaises(ValueError):
                compile_plan(chosen, scene())
        observed, chosen = scene(), plan()
        observed['controls'][0]['type'] = 'text'
        observed['controls'][0]['max_length'] = 1024
        chosen['fields'][0]['component'] = 'CodeInput'
        field = compile_plan(chosen, observed)['fields'][0]
        self.assertEqual((field['type'], field['max_length']), ('otp', 16))
        observed['controls'][0]['type'], observed['controls'][0]['max_length'] = 'otp', 16
        chosen['fields'][0]['component'] = 'TextInput'
        with self.assertRaises(ValueError):
            compile_plan(chosen, observed)

    def test_text_email_component_choices_preserve_native_validation_and_length(self):
        for native, selected, maximum in (('text', 'EmailInput', 1024),
                                           ('email', 'TextInput', 254),
                                           ('text', 'EmailInput', 120),
                                           ('email', 'TextInput', 80)):
            with self.subTest(native=native, selected=selected, maximum=maximum):
                observed, chosen = scene(), plan()
                observed['controls'][0].update(type=native, max_length=maximum)
                chosen['fields'][0]['component'] = selected
                field = compile_plan(chosen, observed)['fields'][0]
                self.assertEqual((field['type'], field['max_length']), (native, maximum))

    def test_disabled_action_needs_fields_and_permission_is_independent(self):
        observed, chosen = scene(), plan()
        self.assertEqual(compile_plan(chosen, observed)['actions'][0]['id'], 'a1')
        observed['actions'][0]['permitted'] = False
        with self.assertRaises(ValueError):
            compile_plan(chosen, observed)
        observed = scene()
        observed['controls'] = []
        chosen['fields'] = []
        with self.assertRaises(ValueError):
            compile_plan(chosen, observed)
        observed['actions'][0]['enabled'] = True
        self.assertFalse(compile_plan(chosen, observed)['fields'])

    def test_links_never_fill_loose_or_native_controls(self):
        for primary in (True, False):
            observed, chosen = scene(), plan()
            for item in observed['controls'] + observed['actions']:
                item['form'] = None
            observed['actions'][0 if primary else 1].update(kind='link', enabled=True)
            with self.subTest(primary=primary), self.assertRaises(ValueError):
                compile_plan(chosen, observed)

    def test_unknown_stale_duplicate_and_auth_properties_rejected(self):
        for change in ('stale', 'unknown', 'duplicate', 'message', 'html', 'auth', 'version'):
            chosen = plan()
            if change == 'stale':
                chosen['revision'] = 'different:2'
            elif change == 'unknown':
                chosen['fields'][0]['ref'] = 'f99'
            elif change == 'duplicate':
                chosen['fields'].append(deepcopy(chosen['fields'][0]))
            elif change == 'message':
                chosen['message_ref'] = 't99'
            elif change == 'version':
                chosen['catalog_version'] = '1.0.0'
            else:
                chosen[change] = 'MODEL_CANARY'
            with self.subTest(change=change), self.assertRaises(ValueError):
                compile_plan(chosen, scene())

    def test_value_default_selector_and_url_properties_rejected_without_echo(self):
        for field in ('value', 'values', 'checked', 'selected', 'default', 'selector', 'href'):
            observed = scene()
            observed['controls'][0][field] = 'SECRET_CANARY'
            with self.subTest(field=field), self.assertRaises(ValueError) as error:
                validate_scene(observed)
            self.assertNotIn('SECRET_CANARY', str(error.exception))

    def test_post_input_sanitized_semantics_retained_options_and_routing_not_projected(self):
        observed = scene()
        observed['after_input'] = True
        observed['controls'].append({'ref': 'f5', 'type': 'select', 'label': 'Authentication method',
            'required': False, 'form': 'g1', 'order': 7, 'max_length': 64,
            'options': [{'value': 'o1', 'label': 'PRIVATE_OPTION_CANARY'}]})
        context = model_context(observed)
        self.assertTrue(context['after_input'])
        self.assertEqual(context['texts'], observed['texts'])
        self.assertEqual(context['controls'][0]['label'], 'Account email')
        self.assertEqual(context['controls'][-1]['option_count'], 1)
        serialized = json.dumps(context)
        for hidden in ('PRIVATE_OPTION_CANARY', 'private-scene.test', 'private_revision', '"options"', '"value"', '"o1"'):
            self.assertNotIn(hidden, serialized)

    def test_scene_bounds_and_exact_types(self):
        for mutate in (
                lambda item: item.update(after_input=1),
                lambda item: item['overflow'].update(controls=True),
                lambda item: item['actions'][0].update(enabled=1),
                lambda item: item['controls'][0].update(order=-1),
                lambda item: item['controls'][0].update(label='x' * 161),
                lambda item: item['texts'][0].update(text='x' * 241),
                lambda item: item.update(controls=item['controls'] * 9),
                lambda item: item.update(actions=item['actions'] * 22),
                lambda item: item.update(texts=item['texts'] * 13),
                lambda item: item.update(stage='authenticated')):
            observed = scene()
            mutate(observed)
            with self.assertRaises(ValueError):
                validate_scene(observed)

    def test_label_prefix_is_valid_utf16_and_plan_output_limits_enforced(self):
        observed = scene()
        observed['controls'][0]['label'] = 'x' * 119 + '\U0001f600' + 'y' * 20
        self.assertEqual(compile_plan(plan(), observed)['fields'][0]['label'], 'x' * 119)
        for index in range(4, 11):
            observed['actions'].append({'ref': 'a' + str(index), 'label': 'Alternative', 'form': 'g1',
                'order': index + 7, 'kind': 'button', 'enabled': True, 'permitted': True})
        chosen = plan()
        chosen['actions'] += [{'ref': 'a' + str(index), 'presentation': 'secondary'} for index in range(4, 11)]
        with self.assertRaises(ValueError):
            compile_plan(chosen, observed)

    def test_byte_budget_and_sixteen_field_limit_are_independent_of_item_bounds(self):
        observed = scene()
        observed['controls'] = [{'ref': 'f' + str(index), 'type': 'select', 'label': 'Choices',
            'required': False, 'form': 'g1', 'order': index, 'max_length': 64,
            'options': [{'value': 'o' + str(index * 32 + option), 'label': 'x' * 120}
                        for option in range(32)]} for index in range(32)]
        with self.assertRaises(ValueError):
            validate_scene(observed)
        observed = scene()
        observed['controls'] = [{'ref': 'f' + str(index), 'type': 'text', 'label': 'Identifier',
            'required': False, 'form': 'g1', 'order': index, 'max_length': 1024} for index in range(17)]
        chosen = plan()
        chosen['fields'] = [{'ref': item['ref'], 'component': 'TextInput'} for item in observed['controls']]
        validate_scene(observed)
        with self.assertRaises(ValueError):
            compile_plan(chosen, observed)

    def test_optional_roles_and_human_history_are_evidence_not_widget_properties(self):
        observed = scene()
        observed['controls'][0]['role'] = 'username'
        observed['controls'][1]['role'] = 'current-password'
        observed['history'] = [{'action': 'Use another method', 'field_types': []},
                               {'action': 'Continue', 'field_types': ['email']}]
        context = model_context(observed)
        self.assertEqual(context['controls'][0]['role'], 'username')
        self.assertEqual(context['history'], observed['history'])
        self.assertNotIn('role', compile_plan(plan(), observed)['fields'][0])
        observed['history'][0]['action'] = 'Later mutation'
        self.assertEqual(context['history'][0]['action'], 'Use another method')

    def test_roles_and_history_reject_arbitrary_data_wrong_types_and_overflow(self):
        for mutate in (
                lambda item: item['controls'][0].update(role='current-password'),
                lambda item: item['controls'][0].update(role='ARBITRARY_ROLE_CANARY'),
                lambda item: item['controls'][0].update(role=['username']),
                lambda item: item.update(history=[{'action': 'Continue', 'field_types': [], 'values': 'SECRET_CANARY'}]),
                lambda item: item.update(history=[{'action': 'Continue', 'field_types': ['SECRET_CANARY']}]),
                lambda item: item.update(history=[{'action': 'Continue', 'field_types': ['text'] * 33}]),
                lambda item: item.update(history=[{'action': 'Continue', 'field_types': []}] * 4),
                lambda item: item.update(history=[{'action': 'x' * 161, 'field_types': []}])):
            observed = scene()
            mutate(observed)
            with self.assertRaises(ValueError) as error:
                validate_scene(observed)
            self.assertNotIn('CANARY', str(error.exception))


if __name__ == '__main__':
    unittest.main()
