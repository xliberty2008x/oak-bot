"""Complete observed surfaces and per-action private value bindings."""

from copy import deepcopy
import json
import os
import unittest

from oak.website_scene import CATALOG_ID, CATALOG_VERSION, compile_plan, model_context, validate_scene
from oak.website_scene_dom import SCENE_SCRIPT
from test_website_scene import scene, plan


COMPONENTS = {'text': 'TextInput', 'email': 'EmailInput', 'password': 'PasswordInput',
              'otp': 'CodeInput', 'checkbox': 'Checkbox', 'select': 'Select'}


def surface_scene():
    value = scene()
    for item, dispatch in zip(value['actions'], ('submit', 'button', 'submit')):
        item['dispatch'] = dispatch
    value['actions'].append({'ref': 'a4', 'label': 'External identity', 'form': None,
        'order': 8, 'kind': 'link', 'dispatch': 'link', 'enabled': True, 'permitted': False})
    value['surfaces'] = [{'ref': 's1', 'kind': 'dialog',
        **{key: [item['ref'] for item in value[key]] for key in ('controls', 'actions', 'texts')}}]
    return value


def surface_plan(value, *, surface=None, anchor=None):
    surface = surface or value['surfaces'][0]
    actions = [a for a in value['actions'] if a['ref'] in surface['actions']]
    controls = [f for f in value['controls'] if f['ref'] in surface['controls']]
    anchor = anchor or actions[0]['ref']
    return {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
        'revision': value['revision'], 'surface_ref': surface['ref'], 'anchor': anchor,
        'fields': [{'ref': f['ref'], 'component': COMPONENTS[f['type']]} for f in controls],
        'actions': [{'ref': a['ref'], 'presentation': 'primary' if a['ref'] == anchor else 'secondary',
                     'field_refs': [f['ref'] for f in controls if f['form'] == a['form']]
                     if a['dispatch'] == 'submit' and a['permitted'] else []} for a in actions],
        'message_ref': None}


class WebsiteSurfaceTests(unittest.TestCase):
    def test_semantic_text_email_choices_cannot_rewrite_native_field_constraints(self):
        for native, selected, maximum in (('text', 'EmailInput', 1024),
                                           ('email', 'TextInput', 254),
                                           ('text', 'EmailInput', 120),
                                           ('email', 'TextInput', 80)):
            with self.subTest(native=native, selected=selected, maximum=maximum):
                value = surface_scene()
                value['controls'][0].update(type=native, max_length=maximum)
                chosen = surface_plan(value); chosen['fields'][0]['component'] = selected
                field = compile_plan(chosen, value)['fields'][0]
                self.assertEqual((field['type'], field['max_length']), (native, maximum))

    def test_complete_surface_compiles_cross_owner_alternatives_without_cross_binding(self):
        value = surface_scene()
        result = compile_plan(surface_plan(value), value)
        self.assertEqual([f['name'] for f in result['fields']], ['f1', 'f2', 'f3', 'f4'])
        self.assertEqual([a['field_refs'] for a in result['actions']], [['f1', 'f2', 'f3'], [], ['f4'], []])
        self.assertEqual([a['available'] for a in result['actions']], [True, True, True, False])
        self.assertEqual(model_context(value)['surfaces'], value['surfaces'])
        self.assertNotIn('private-scene.test', json.dumps(model_context(value)))

    def test_every_control_and_alternative_including_denied_must_be_covered(self):
        for missing in ('field', 'action', 'denied'):
            value = surface_scene(); chosen = surface_plan(value)
            if missing == 'field': chosen['fields'].pop(2)
            else: chosen['actions'].pop(-1 if missing == 'denied' else 1)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                compile_plan(chosen, value)

    def test_submit_cannot_omit_required_fields_but_button_and_link_send_none(self):
        value = surface_scene(); chosen = surface_plan(value)
        chosen['actions'][0]['field_refs'] = []
        with self.assertRaises(ValueError): compile_plan(chosen, value)
        chosen = surface_plan(value)
        for index in (1, 3): self.assertEqual(chosen['actions'][index]['field_refs'], [])
        compile_plan(chosen, value)
        for index in (2, 3):
            changed = deepcopy(chosen); changed['actions'][index]['field_refs'] = ['f1']
            with self.subTest(index=index), self.assertRaises(ValueError): compile_plan(changed, value)

    def test_denied_submit_is_visible_without_private_bindings_and_cannot_be_enabled_by_model(self):
        value = surface_scene(); value['actions'][0]['permitted'] = False
        chosen = surface_plan(value)
        action = compile_plan(chosen, value)['actions'][0]
        self.assertEqual(action['field_refs'], [])
        self.assertFalse(action['available'])
        chosen['actions'][0]['field_refs'] = ['f1', 'f2']
        with self.assertRaises(ValueError): compile_plan(chosen, value)

    def test_submit_includes_optional_fields_and_value_bearing_button_keeps_required_controls(self):
        value = surface_scene(); chosen = surface_plan(value)
        chosen['actions'][0]['field_refs'].remove('f3')
        with self.assertRaises(ValueError): compile_plan(chosen, value)
        chosen = surface_plan(value)
        chosen['actions'][1]['field_refs'] = ['f1']
        with self.assertRaises(ValueError): compile_plan(chosen, value)
        chosen['actions'][1]['field_refs'] = ['f1', 'f2']
        self.assertEqual(compile_plan(chosen, value)['actions'][1]['field_refs'], ['f1', 'f2'])

    def test_surface_membership_and_dispatch_are_strict_native_metadata(self):
        for mutate in (
                lambda s: s['surfaces'][0]['controls'].append('f99'),
                lambda s: s['surfaces'][0]['actions'].append('a1'),
                lambda s: s['surfaces'][0].update(kind='script'),
                lambda s: s['surfaces'][0].update(selector='PRIVATE_CANARY'),
                lambda s: s['actions'][0].update(dispatch='link'),
                lambda s: s['actions'][0].update(dispatch='eval')):
            value = surface_scene(); mutate(value)
            with self.assertRaises(ValueError) as error: validate_scene(value)
            self.assertNotIn('PRIVATE_CANARY', str(error.exception))
        value = surface_scene(); value['actions'][0].pop('dispatch')
        chosen = surface_plan(surface_scene())
        with self.assertRaises(ValueError): compile_plan(chosen, value)

    def test_scope_cannot_hide_required_native_fields_outside_it(self):
        for ref in ('f2', 'f3'):
            value = surface_scene(); value['surfaces'][0]['controls'].remove(ref)
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                compile_plan(surface_plan(value), value)

    def test_legacy_versions_retain_subset_and_owner_rules(self):
        for version in ('2.0.0', '2.1.0'):
            chosen = plan(); chosen['catalog_version'] = version
            self.assertEqual(len(compile_plan(chosen, scene())['fields']), 2)
            chosen['actions'].append({'ref': 'a3', 'presentation': 'secondary'})
            with self.assertRaises(ValueError): compile_plan(chosen, scene())
        value = surface_scene(); chosen = surface_plan(value); chosen['catalog_version'] = '2.0.0'
        with self.assertRaises(ValueError): compile_plan(chosen, value)

    def test_surface_caps_are_independent_of_legacy_and_overflow_has_fixed_notice(self):
        value = surface_scene()
        value['controls'] = [{'ref': 'f'+str(i), 'type': 'text', 'label': 'Field', 'required': False,
            'form': None, 'order': i, 'max_length': 100} for i in range(32)]
        value['actions'] = [{'ref': 'a'+str(i), 'kind': 'button', 'dispatch': 'button', 'label': 'Method',
            'form': None, 'order': i, 'enabled': True, 'permitted': True} for i in range(64)]
        value['surfaces'][0].update(controls=[f['ref'] for f in value['controls']],
            actions=[a['ref'] for a in value['actions']])
        value['overflow']['actions'] = 1
        result = compile_plan(surface_plan(value), value)
        self.assertEqual((len(result['fields']), len(result['actions'])), (32, 64))
        self.assertIn('Частину елементів', result['message'])
        value['controls'].append(dict(value['controls'][0], ref='f99'))
        with self.assertRaises(ValueError): validate_scene(value)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in intercepted Chromium fixtures')
class WebsiteSurfaceDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start(); self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(service_workers='block'); self.addAsyncCleanup(self.context.close)
        self.requests = []
        async def route(request):
            self.requests.append(request.request.method)
            await request.fulfill(status=200, content_type='text/html', body='<body>Local fixture</body>')
        await self.context.route('**/*', route)
        self.page = await self.context.new_page(); await self.page.goto('https://surface.example.test')
        self.requests.clear()
        self.arguments = {'stateKey': '_surface_fixture', 'expectedOrigin': 'https://surface.example.test',
            'operation': 'snapshot', 'revision': None, 'action': None, 'values': {}, 'mayConfirm': False,
            'redactions': [], 'textRedactions': [], 'credentialValues': [], 'afterInput': False, 'plan': None}

    async def read(self):
        value = await self.page.evaluate(SCENE_SCRIPT, self.arguments)
        validate_scene(value['scene'])
        return value['scene']

    async def fixture(self):
        await self.page.set_content('<main><input aria-label="Unrelated search"><button>Search</button></main>'
            '<div role="dialog" aria-modal="true"><h1>Account</h1><section>'
            '<form method="post" id="first"><label>Email<input type="email" required></label>'
            '<label>Password<input type="password" required></label><button>Continue</button>'
            '<button type="button" id="social">Social method</button></form>'
            '<form method="post" id="second"><label>Code<input autocomplete="one-time-code" required></label>'
            '<button>Verify</button></form><a href="#back" id="back">Back</a>'
            '<a href="https://denied.example.test" id="denied">Another identity</a></section></div>')
        await self.page.evaluate('''() => {window.clicks=[];window.changes=[];
            document.addEventListener('input', e=>window.changes.push(e.target.type));
            for(const form of document.forms)form.onsubmit=e=>{e.preventDefault();window.clicks.push(form.id);};
            for(const id of ['social','back','denied'])document.getElementById(id).onclick=e=>{
                e.preventDefault();window.clicks.push(id);};}''')
        return await self.read()

    async def dispatch(self, value, chosen, label, values):
        action = next(a['ref'] for a in value['actions'] if a['label'] == label)
        return await self.page.evaluate(SCENE_SCRIPT, {**self.arguments, 'operation': 'submit',
            'revision': value['revision'], 'plan': chosen, 'action': action, 'values': values})

    async def test_active_modal_surface_is_complete_across_native_owners_and_excludes_background(self):
        value = await self.fixture()
        self.assertEqual(len(value['surfaces']), 1)
        scope = value['surfaces'][0]
        self.assertEqual(scope['kind'], 'dialog')
        self.assertEqual(scope['controls'], [f['ref'] for f in value['controls']])
        self.assertEqual(scope['actions'], [a['ref'] for a in value['actions']])
        self.assertEqual([a['dispatch'] for a in value['actions']], ['submit', 'button', 'submit', 'link', 'link'])
        self.assertNotIn('Search', json.dumps(value))
        compiled = compile_plan(surface_plan(value), value)
        self.assertEqual(len(compiled['actions']), 5)
        self.assertFalse(compiled['actions'][-1]['available'])

    async def test_alternate_button_and_link_do_not_fill_or_emit_unrelated_values(self):
        for label in ('Social method', 'Back'):
            with self.subTest(label=label):
                value = await self.fixture(); chosen = surface_plan(value)
                self.assertEqual(await self.dispatch(value, chosen, label, {}), {'accepted': True})
                self.assertEqual(await self.page.locator('input').evaluate_all('ns=>ns.map(n=>n.value)'), ['', '', '', ''])
                self.assertEqual(await self.page.evaluate('window.changes'), [])
                self.assertEqual(len(await self.page.evaluate('window.clicks')), 1)
        self.assertEqual(self.requests, [])

    async def test_only_selected_native_form_fields_fill_and_duplicate_is_stale(self):
        value = await self.fixture(); chosen = surface_plan(value)
        code = next(f for f in value['controls'] if f['type'] == 'otp')
        self.assertEqual(await self.dispatch(value, chosen, 'Verify', {code['ref']: '123456'}), {'accepted': True})
        self.assertEqual(await self.page.locator('#first input').evaluate_all('ns=>ns.map(n=>n.value)'), ['', ''])
        self.assertEqual(await self.page.evaluate('window.clicks'), ['second'])
        self.assertEqual(await self.dispatch(value, chosen, 'Verify', {code['ref']: '123456'}),
            {'accepted': False, 'code': 'stale_form'})
        self.assertEqual(await self.page.evaluate('window.clicks'), ['second'])

    async def test_forged_binding_required_omission_and_denied_action_reject_before_fill(self):
        for mode in ('unrelated', 'required', 'denied'):
            with self.subTest(mode=mode):
                value = await self.fixture(); chosen = surface_plan(value)
                email = next(f for f in value['controls'] if f['type'] == 'email')
                label, values = 'Continue', {}
                if mode == 'required': chosen['actions'][0]['field_refs'] = []
                elif mode == 'unrelated':
                    label = 'Back'; values = {email['ref']: 'PRIVATE_CANARY'}
                    next(a for a in chosen['actions'] if a['ref'] == value['actions'][3]['ref'])['field_refs'] = [email['ref']]
                else: label = 'Another identity'
                self.assertEqual(await self.dispatch(value, chosen, label, values), {'accepted': False, 'code': 'stale_form'})
                self.assertEqual(await self.page.evaluate('window.changes'), [])
                self.assertEqual(await self.page.evaluate('window.clicks'), [])
        self.assertEqual(self.requests, [])

    async def test_surface_membership_change_rejects_pending_plan_without_click(self):
        value = await self.fixture(); chosen = surface_plan(value)
        await self.page.evaluate('document.querySelector("[role=dialog]").append(Object.assign(document.createElement("button"),{textContent:"New method"}))')
        self.assertEqual(await self.dispatch(value, chosen, 'Back', {}), {'accepted': False, 'code': 'stale_form'})
        self.assertEqual(await self.page.evaluate('window.clicks'), [])

    async def test_optional_external_native_field_cannot_be_omitted_from_submit(self):
        await self.page.set_content('<section><form id="account" method="post">'
            '<label>Email<input type="email" required></label><button>Continue</button>'
            '</form></section><label>Optional note<input form="account"></label>')
        await self.page.evaluate('() => {window.clicks=0;document.forms[0].onsubmit=e=>{e.preventDefault();window.clicks++}}')
        value = await self.read()
        narrow = next(surface for surface in value['surfaces'] if surface['kind'] == 'form')
        chosen = surface_plan(value, surface=narrow)
        email = next(field for field in value['controls'] if field['type'] == 'email')
        self.assertEqual(await self.dispatch(value, chosen, 'Continue', {email['ref']: 'reserved@example.invalid'}),
            {'accepted': False, 'code': 'stale_form'})
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)
        self.assertEqual(await self.page.locator('input').evaluate_all('nodes=>nodes.map(node=>node.value)'), ['', ''])

    async def test_non_modal_ancestors_include_full_region_and_document_with_bounded_candidates(self):
        await self.page.set_content('<main><section><form method="post"><label>Email<input type="email" required></label>'
            '<button>Continue</button></form><button type="button">Social sibling</button></section></main>')
        value = await self.read()
        regions = [s for s in value['surfaces'] if s['kind'] == 'region' and len(s['actions']) == 2]
        self.assertTrue(regions)
        self.assertEqual(value['surfaces'][-1]['kind'], 'document')
        self.assertLessEqual(len(value['surfaces']), 16)
        result = compile_plan(surface_plan(value, surface=regions[0]), value)
        self.assertEqual([a['label'] for a in result['actions']], ['Continue', 'Social sibling'])

    async def test_uneditable_and_unsupported_visible_controls_cannot_claim_complete_surface(self):
        await self.page.set_content('<main><form method="post">'
            '<input aria-label="Editable"><input aria-label="Disabled" disabled>'
            '<input aria-label="Read only" readonly><input type="date" aria-label="Date">'
            '<div aria-hidden="true"><input type="password" aria-label="Hidden password"></div>'
            '<button>Continue</button></form></main>')
        value = await self.read()
        self.assertEqual([field['label'] for field in value['controls']], ['Editable'])
        self.assertEqual(value['overflow']['controls'], 3)
        result = compile_plan(surface_plan(value, surface=value['surfaces'][-1]), value)
        self.assertIn('Частину елементів', result['message'])
