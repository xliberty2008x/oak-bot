"""Compile a model's bounded plan into the existing private DOM renderer."""

import json
import unittest

from oak.website_ui import CATALOG_ID, CATALOG_VERSION, compile_plan, model_context
import test_login_ui as ui_fixture


@unittest.skipUnless(ui_fixture.NODE, 'Node is required for private widget checks')
class WebsiteUIRendererTests(unittest.TestCase):
    def test_compiled_steps_use_existing_widgets_and_private_submission(self):
        observations = []
        field_sets = [[],
            [{'name': 'f1', 'type': 'email', 'label': 'Email', 'max_length': 254}],
            [{'name': 'f2', 'type': 'password', 'label': 'Password', 'max_length': 1024}],
            [{'name': 'f3', 'type': 'otp', 'label': 'Code', 'max_length': 16}],
            [{'name': 'f4', 'type': 'select', 'label': 'Method', 'max_length': 64,
              'options': [{'value': 'o1', 'label': 'Authenticator'}, {'value': 'o2', 'label': 'Backup code'}]},
             {'name': 'f5', 'type': 'checkbox', 'label': 'Remember this device', 'max_length': 1}]]
        for index, fields in enumerate(field_sets):
            observations.append({'provider': 'website', 'destination': 'https://example.test',
                'stage': 'form', 'revision': f'fixture:{index + 1}',
                'groups': [{'id': 'g1', 'fields': fields,
                            'actions': [{'id': 'a1', 'label': 'Use email' if index == 0 else 'Continue'}]}],
                'message': {'kind': 'status', 'text': ''}})
        forms = []
        for observation in observations:
            model_context(observation)  # Validated model context contains no input data.
            plan = {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
                    'revision': observation['revision'], 'group_id': 'g1',
                    'fields': [f['name'] for f in observation['groups'][0]['fields']], 'actions': ['a1']}
            forms.append({'state': 'active', 'form': compile_plan(plan, observation)})
        script = 'const forms = ' + json.dumps(forms) + ';\n' + r'''
const ui = await createUI();
await ui.attach(forms[0], 'website');
assert.equal(ui.ids.actions.querySelectorAll('button')[0].textContent, 'Use email');
assert.equal(ui.ids.fields.children.length, 0);
ui.responses.push({accepted:true}, forms[1]); await ui.submit('a1');
assert.equal(ui.input('f1').type, 'email');
ui.input('f1').value='synthetic@example.test';
ui.responses.push({accepted:true}, forms[2]); await ui.submit('a1');
assert.equal(ui.input('f2').type, 'password');
const password=ui.input('f2'); password.value='private-render-canary';
ui.responses.push({accepted:true}, forms[3]); await ui.submit('a1');
assert.equal(password.value, '', 'Submitted password is cleared from its widget');
assert.equal(ui.input('f3').inputMode, 'numeric');
ui.input('f3').value='654321';
ui.responses.push({accepted:true}, forms[4]); await ui.submit('a1');
assert.equal(ui.input('f4').tagName, 'select');
assert.equal(ui.input('f5').type, 'checkbox');
assert.equal(ui.requests.filter(r=>r.url==='/api/submit').length,4);
assert.ok(ui.requests.filter(r=>r.url==='/api/submit').every(r=>r.credentials==='omit'));
assert.ok(!JSON.stringify(ui.messages).includes('private-render-canary'));
assert.ok(!JSON.stringify(ui.messages).includes('654321'));
'''
        ui_fixture.LoginUITests.run_ui(self, script)


if __name__ == '__main__':
    unittest.main()
