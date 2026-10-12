"""Private option captions remain usable; model options and input stay private."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import unittest

import test_website_scene_browser as browser_fixture
from test_website_scene import scene
from oak.website_scene import (CATALOG_ID, CATALOG_VERSION, MAX_OBSERVATION_BYTES,
    MAX_SELECT_OPTIONS, compile_plan, model_context, validate_scene)
from oak.website_scene_sanitize import MASK, sanitize_scene


class PrivateOptionSanitizeTests(unittest.TestCase):
    def observed(self):
        value = scene()
        value['controls'].append({'ref': 'f5', 'type': 'select', 'label': 'Membership',
            'required': False, 'form': 'g1', 'order': 8, 'max_length': 64,
            'options': [{'value': 'o1', 'label': 'Reading circle'},
                        {'value': 'o2', 'label': 'Personal reader'}]})
        value['texts'][0]['text'] = 'Selected Reading circle'
        return value

    def test_option_override_only_changes_option_redactions_and_model_omits_options(self):
        observed = self.observed()
        original = deepcopy(observed)
        strict = sanitize_scene(observed, ['Reading circle'], [])
        self.assertEqual(strict['controls'][-1]['options'][0]['label'], MASK)
        private = sanitize_scene(observed, ['Reading circle'], [], option_redactions=[])
        self.assertEqual(private['controls'][-1]['options'][0]['label'], 'Reading circle')
        self.assertEqual(private['texts'][0]['text'], 'Selected ' + MASK)
        self.assertEqual(observed, original)
        validate_scene(private)
        projected = model_context(private)
        for forbidden in ('Reading circle', 'Personal reader', '"o1"', '"o2"'):
            self.assertNotIn(forbidden, json.dumps(projected))
        for control in projected['controls']:
            self.assertTrue({'options', 'selected', 'value'}.isdisjoint(control))

    def test_free_text_credentials_and_generic_filters_always_mask_options(self):
        private = self.observed()
        private['controls'][-1]['options'] = [{'value': 'o' + str(i + 1), 'label': label}
            for i, label in enumerate(('CardAlpha', 'qv7-Sun', 'member@example.test',
                'https://example.test/route', 'opaqueToken1234567890123456789'))]
        cleaned = sanitize_scene(private, ['Reading circle'], ['qv7-Sun'], option_redactions=['CardAlpha'])
        self.assertEqual([o['label'] for o in cleaned['controls'][-1]['options']], [MASK] * 5)
        for bad in ('SECRET', [None], ['x'] * 129, ['x' * 4097]):
            with self.assertRaises(ValueError):
                sanitize_scene(private, [], [], option_redactions=bad)

    def large_scene(self, count):
        value = self.observed()
        value['controls'] = [value['controls'][-1]]
        value['controls'][0]['options'] = [{'value': 'o' + str(i), 'label': 'Private option caption ' + str(i)}
                                          for i in range(count)]
        value['actions'] = [dict(value['actions'][0], dispatch='submit')]
        value['surfaces'] = [{'ref': 's1', 'kind': 'form', 'controls': ['f5'],
                              'actions': [value['actions'][0]['ref']], 'texts': []}]
        return value

    def test_large_surface_select_preserves_every_option_but_model_sees_only_count(self):
        from test_website_surface import surface_plan
        for count in (83, MAX_SELECT_OPTIONS):
            with self.subTest(count=count):
                value = self.large_scene(count)
                compiled = compile_plan(surface_plan(value), value)
                self.assertEqual(len(compiled['fields'][0]['options']), count)
                self.assertEqual(compiled['fields'][0]['options'], value['controls'][0]['options'])
                context = model_context(value)
                self.assertEqual(context['controls'][0]['option_count'], count)
                self.assertNotIn('Private option caption', json.dumps(context))
                self.assertNotIn('options', context['controls'][0])
                self.assertNotIn('value', context['controls'][0])

    def test_select_limit_and_legacy_widget_limit_remain_distinct(self):
        from test_website_surface import surface_plan
        with self.assertRaises(ValueError): validate_scene(self.large_scene(MAX_SELECT_OPTIONS + 1))
        value = self.large_scene(33)
        validate_scene(value)
        legacy = deepcopy(value); legacy.pop('surfaces')
        with self.assertRaises(ValueError): validate_scene(legacy)
        plan = surface_plan(value); plan.pop('surface_ref')
        for action in plan['actions']: action.pop('field_refs')
        with self.assertRaises(ValueError): compile_plan(plan, value)

    def test_larger_select_does_not_expand_total_scene_byte_budget(self):
        value = self.large_scene(MAX_SELECT_OPTIONS)
        for option in value['controls'][0]['options']: option['label'] = 'L' * 120
        second = deepcopy(value['controls'][0]); second['ref'] = 'f6'
        for index, option in enumerate(second['options']): option['value'] = 'o' + str(index + MAX_SELECT_OPTIONS)
        value['controls'].append(second); value['surfaces'][0]['controls'].append('f6')
        self.assertGreater(len(json.dumps(value).encode()), MAX_OBSERVATION_BYTES)
        with self.assertRaises(ValueError): validate_scene(value)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class PrivateOptionBrowserTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = browser_fixture.WebsiteSceneBrowserTests.asyncSetUp
    fixture = browser_fixture.WebsiteSceneBrowserTests.fixture
    values = browser_fixture.WebsiteSceneBrowserTests.values
    submit = browser_fixture.WebsiteSceneBrowserTests.submit

    def option_field(self, form):
        return next(field for field in form['fields'] if field['type'] == 'select')

    async def render_options(self, form):
        """Use the shipped private native widget, including its empty placeholder."""
        if not hasattr(self, 'private_page'):
            code = (Path(__file__).resolve().parents[1] / 'oak/web/form-controls.js').read_text()
            async def module(route):
                await route.fulfill(status=200, content_type='text/javascript', body=code)
            await self.context.route('**/form-controls.js', module)
            self.private_page = await self.context.new_page()
            await self.private_page.goto('https://scene-fixture.example/private-preview')
        field = self.option_field(form)
        await self.private_page.evaluate("""async field => {
            const {createFormControl}=await import('/form-controls.js');
            const widget=createFormControl({...field,id:'private-choice',privateInput:true});
            document.body.replaceChildren(widget.label,widget.control);
        }""", field)
        return self.private_page.get_by_label(field['label'], exact=True)

    async def test_default_and_reselected_caption_works_after_dom_replacement_and_navigation(self):
        markup = ('<form method="post"><label>Membership class<select name="kind">'
            '<option value="individual">Individual reader</option>'
            '<option value="circle">Reading circle</option></select></label>'
            '<button>Continue</button></form>')
        self.documents['/next'] = markup.replace('<option value="circle">', '<option value="circle" selected>')
        form = await self.fixture(markup, """() => {
            window.count=0;
            const bind=()=>document.querySelector('form').onsubmit=event=>{
                event.preventDefault();window.count++;
                if(window.count===2){location.href='/next';return;}
                const form=event.target.cloneNode(true);
                form.elements.kind.querySelector('[value="circle"]').setAttribute('selected','');
                const error=document.createElement('p');error.setAttribute('role','alert');
                error.textContent='Check Reading circle before continuing';
                document.body.replaceChildren(error,form);bind();
            };bind();
        }""")
        expected = ['Individual reader', 'Reading circle']
        self.assertEqual([o['label'] for o in self.option_field(form)['options']], expected)
        choice = await self.render_options(form)
        await choice.select_option(label='Reading circle')
        await self.submit(form, {'Membership class': await choice.input_value()})
        self.assertEqual(await self.page.evaluate('window.count'), 1)
        after = await self.browser.snapshot()
        self.assertIn('Check', after['message'])
        self.assertNotIn('Reading circle', after['message'])
        self.assertEqual([o['label'] for o in self.option_field(after)['options']], expected)
        self.assertEqual(self.browser._text_redactions, [])
        self.assertIn('Reading circle', self.browser._redactions)
        choice = await self.render_options(after)
        await choice.select_option(label='Reading circle')
        await self.submit(after, {'Membership class': await choice.input_value()})
        await self.page.wait_for_url('https://scene-fixture.example/next')
        following = await self.browser.snapshot()
        self.assertEqual([o['label'] for o in self.option_field(following)['options']], expected)
        for observed in self.composer.scenes:
            projected = model_context(observed)
            serialized = json.dumps(projected)
            for caption in expected:
                self.assertNotIn(caption, serialized)
            for control in projected['controls']:
                self.assertNotIn('options', control)
                self.assertNotIn('value', control)
                self.assertNotIn('selected', control)
        self.assertTrue(all(re.fullmatch(r'o\d+', o['value']) for o in self.option_field(following)['options']))
        self.assertEqual(self.requests.count(('GET', 'document', '/next')), 1)
        await self.browser.close()
        self.assertEqual(self.browser._text_redactions, [])

    async def test_native_text_defaults_are_masked_in_private_option_captions(self):
        default = 'local-default-481'
        form = await self.fixture('<form method="post"><input type="hidden" value="' + default + '">'
            '<label>Member<input value="native-card-492"></label>'
            '<label>Method<select><option value="safe">Reader ' + default + '</option>'
            '<option value="other">Reader native-card-492</option></select></label>'
            '<button>Continue</button></form>')
        labels = [o['label'] for o in self.option_field(form)['options']]
        self.assertEqual(labels, ['Reader ' + MASK, 'Reader ' + MASK])
        self.assertNotIn(default, json.dumps(form))
        self.assertNotIn('native-card-492', json.dumps(form))

    async def test_full_surface_select_values_and_captions_survive_private_redaction_bound(self):
        from oak.website_agent import WebsiteAgentForms
        from test_website_surface import surface_plan

        self.browser = WebsiteAgentForms('https://scene-fixture.example/login')
        self.browser.context, self.browser.page = self.context, self.page
        self.addAsyncCleanup(self.browser.close)
        native_values = ['native-value-' + str(i) for i in range(32)]
        captions = ['Private caption ' + str(i) for i in range(32)]
        self.documents['/many-reflected'] = '<h1>' + ' | '.join(native_values + captions) + '</h1>'
        await self.page.set_content('<form method="post">' + ''.join(
            '<label>Choice ' + str(i) + '<select name="choice' + str(i) + '">'
            '<option value="' + value + '">' + captions[i] + '</option></select></label>'
            for i, value in enumerate(native_values)) + '<button>Continue</button></form>')
        await self.page.evaluate("""() => document.querySelector('form').onsubmit=event=>{
                event.preventDefault();location.href='/many-reflected';
            }""")
        scene = self.browser._group_observation(await self.browser._observe())
        surface = next(item for item in scene['surfaces'] if item['kind'] == 'form')
        await self.browser.agent_present(scene['revision'], surface_plan(scene, surface=surface))
        form = await self.browser.snapshot()
        self.assertEqual(form['form_schema'], 'website-v2')
        self.assertEqual(len(form['fields']), 32)
        self.assertEqual(len(form['actions'][0]['field_refs']), 32)
        await self.submit(form)
        await self.page.wait_for_url('https://scene-fixture.example/many-reflected')
        self.assertTrue(set(native_values + captions) <= set(self.browser._redactions))
        self.assertEqual(self.browser._text_redactions, [])
        projected = json.dumps(model_context(self.browser._group_observation(await self.browser._observe())))
        for private in native_values + captions:
            self.assertNotIn(private, projected)
        self.assertEqual(self.requests.count(('GET', 'document', '/many-reflected')), 1)

    async def large_surface_roundtrip(self, count):
        from oak.website_agent import WebsiteAgentForms
        from test_website_surface import surface_plan

        self.browser = WebsiteAgentForms('https://scene-fixture.example/login')
        self.browser.context, self.browser.page = self.context, self.page
        self.addAsyncCleanup(self.browser.close)
        captions = ['Private choice ' + str(i) for i in range(count)]
        await self.page.set_content('<form method="post"><label>Choice<select>' + ''.join(
            '<option value="native-choice-' + str(i) + '">' + caption + '</option>'
            for i, caption in enumerate(captions)) + '</select></label><button>Continue</button></form>')
        await self.page.evaluate("""() => {window.count=0; document.querySelector('form').onsubmit=event=>{
            event.preventDefault();window.count++;window.selected=event.target.querySelector('select').value;
        }}""")
        scene = self.browser._group_observation(await self.browser._observe())
        self.assertEqual(scene['overflow']['controls'], 0)
        self.assertEqual(len(scene['controls'][0]['options']), count)
        surface = next(s for s in scene['surfaces'] if s['kind'] == 'form')
        await self.browser.agent_present(scene['revision'], surface_plan(scene, surface=surface))
        form = await self.browser.snapshot()
        field = self.option_field(form)
        self.assertEqual([o['label'] for o in field['options']], captions)
        choice = await self.render_options(form)
        await choice.select_option(label=captions[-1])
        opaque = await choice.input_value()
        self.assertTrue(re.fullmatch(r'o\d+', opaque))
        await self.browser.submit(form['revision'], form['actions'][0]['id'], {field['name']: opaque})
        self.assertEqual(await self.page.evaluate('window.count'), 1)
        self.assertEqual(await self.page.evaluate('window.selected'), 'native-choice-' + str(count-1))
        after = self.browser._group_observation(await self.browser._observe())
        for observed in (scene, after):
            context = model_context(observed)
            self.assertEqual(context['controls'][0]['option_count'], count)
            self.assertNotIn('Private choice ', json.dumps(context))
            self.assertNotIn('native-choice-', json.dumps(context))
            self.assertNotIn('options', context['controls'][0])

    async def test_83_options_roundtrip_through_private_widget_and_native_control(self):
        await self.large_surface_roundtrip(83)

    async def test_256_options_roundtrip_through_private_widget_and_native_control(self):
        await self.large_surface_roundtrip(MAX_SELECT_OPTIONS)

    async def test_257_options_are_explicit_overflow_without_partial_option_projection(self):
        from oak.website_scene_dom import SCENE_SCRIPT
        await self.page.set_content('<form method="post"><label>Choice<select>' + ''.join(
            '<option value="n' + str(i) + '">Private choice ' + str(i) + '</option>'
            for i in range(MAX_SELECT_OPTIONS + 1)) + '</select></label><button>Continue</button></form>')
        observed = await self.page.evaluate(SCENE_SCRIPT, {'stateKey': 'boundedOptions',
            'expectedOrigin': 'https://scene-fixture.example', 'operation': 'snapshot',
            'maxSelectOptions': MAX_SELECT_OPTIONS})
        self.assertEqual(observed['scene']['controls'], [])
        self.assertEqual(observed['scene']['overflow']['controls'], 1)
        self.assertNotIn('Private choice ', json.dumps(model_context(observed['scene'])))

    async def test_legacy_scene_collector_keeps_32_option_limit(self):
        from oak.website_scene_dom import SCENE_SCRIPT
        for count in (32, 33):
            await self.page.set_content('<form method="post"><label>Choice<select>' + ''.join(
                '<option value="n' + str(i) + '">Choice ' + str(i) + '</option>'
                for i in range(count)) + '</select></label><button>Continue</button></form>')
            observed = await self.page.evaluate(SCENE_SCRIPT, {'stateKey': 'legacyOptions',
                'expectedOrigin': 'https://scene-fixture.example', 'operation': 'snapshot'})
            self.assertEqual(observed['scene']['overflow']['controls'], int(count > 32))
            self.assertEqual(len(observed['scene']['controls']), int(count <= 32))

    async def test_typed_identifier_password_otp_survive_navigation_as_option_redactions(self):
        secrets = {'Member': 'typed-card-951', 'Phrase': 'qv7-Sun', 'Digits': '624739'}
        form = await self.fixture('<form method="post"><label>Member<input></label>'
            '<label>Phrase<input type="password"></label><label>Digits<input autocomplete="one-time-code"></label>'
            '<button>Continue</button></form>', """() => document.querySelector('form').onsubmit=event=>{
                event.preventDefault();location.href='/reflected';
            }""")
        self.documents['/reflected'] = ('<form method="post"><label>Method<select>' +
            ''.join('<option value="choice' + str(i) + '">Reader ' + secret + '</option>'
                    for i, secret in enumerate(secrets.values())) +
            '<option value="ordinary">Reading circle</option></select></label><button>Continue</button></form>')
        await self.submit(form, secrets)
        await self.page.wait_for_url('https://scene-fixture.example/reflected')
        after = await self.browser.snapshot()
        self.assertEqual([o['label'] for o in self.option_field(after)['options']],
                         ['Reader ' + MASK] * 3 + ['Reading circle'])
        self.assertEqual(set(self.browser._text_redactions), set(secrets.values()))
        for secret in secrets.values():
            self.assertNotIn(secret, json.dumps(after))
            self.assertNotIn(secret, json.dumps(model_context(self.composer.scenes[-1])))
        await self.browser.close()
        self.assertEqual(self.browser._text_redactions, [])

    async def test_python_masks_option_echo_when_page_overrides_javascript_redactor(self):
        secrets = {'Member': 'shortCard951', 'Phrase': 'qv7-Sun'}
        form = await self.fixture('<form method="post"><label>Member<input></label>'
            '<label>Phrase<input type="password"></label><button>Continue</button></form>', """() => {
            const originalSplit=String.prototype.split;
            document.querySelector('form').onsubmit=event=>{
                event.preventDefault();const values=[...event.target.querySelectorAll('input')].map(e=>e.value);
                String.prototype.split=function(separator,...rest){
                    if(values.includes(separator))return [String(this)];
                    return originalSplit.call(this,separator,...rest);
                };
                document.body.innerHTML='<form method="post"><label>Method<select>'+values.map((v,i)=>
                    '<option value="choice'+i+'">Reader '+v+'</option>').join('')+
                    '<option value="ordinary">Reading circle</option></select></label><button>Continue</button></form>';
            };
        }""")
        await self.submit(form, secrets)
        self.assertEqual(await self.page.evaluate('(s)=>s.split(s).join("masked")', secrets['Phrase']), secrets['Phrase'])
        after = await self.browser.snapshot()
        self.assertEqual([o['label'] for o in self.option_field(after)['options']],
                         ['Reader ' + MASK, 'Reader ' + MASK, 'Reading circle'])
        for secret in secrets.values():
            self.assertNotIn(secret, json.dumps(after))
            self.assertNotIn(secret, json.dumps(model_context(self.composer.scenes[-1])))


if __name__ == '__main__':
    unittest.main()
