"""Intercepted modal observation/dispatch; no provider, model or account calls."""

import json
import os
import unittest

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_agent import WebsiteAgentForms
from oak.website_scene import CATALOG_ID, CATALOG_VERSION


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class WebsiteSceneModalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.requests = []

        async def intercept(route):
            self.requests.append(route.request.method)
            await route.fulfill(status=200, content_type='text/html', body='<body>Fixture</body>')

        await self.context.route('**/*', intercept)
        self.page = await self.context.new_page()
        await self.page.goto('https://modal-fixture.example/'); self.requests.clear()
        self.browser = WebsiteAgentForms('https://modal-fixture.example/')
        self.browser.page, self.browser.context = self.page, self.context
        self.addAsyncCleanup(self.browser.close)

    async def fixture(self, html, script=None):
        await self.page.set_content(html)
        if script:
            await self.page.evaluate(script)
        return await self.browser.agent_observe()

    def plan(self, observation, label='Continue'):
        scene = observation['scene']
        action = next(item for item in scene['actions'] if item['label'] == label)
        surface = scene['surfaces'][-1]
        fields = [field for field in scene['controls'] if field['ref'] in surface['controls']]
        components = {'text': 'TextInput', 'email': 'EmailInput', 'password': 'PasswordInput',
            'otp': 'CodeInput', 'select': 'Select', 'checkbox': 'Checkbox'}
        return {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
            'revision': observation['revision'], 'anchor': action['ref'], 'surface_ref': surface['ref'],
            'fields': [{'ref': field['ref'], 'component': components[field['type']]} for field in fields],
            'actions': [{'ref': item['ref'], 'presentation': 'primary' if item is action else 'secondary',
                'field_refs': [field['ref'] for field in fields if field['form'] == item['form']]
                    if item['dispatch'] == 'submit' and item['permitted'] else []}
                for item in scene['actions'] if item['ref'] in surface['actions']], 'message_ref': None}

    async def test_modal_is_scoped_before_action_control_and_text_limits(self):
        observation = await self.fixture('<main>' + ''.join(
            '<h2>Background</h2><input aria-label="Background"><button>Background</button>' for _ in range(90))
            + '</main><div role="dialog" aria-modal="true"><h1>Membership</h1>'
            '<form method="post"><label>Identifier<input></label><label>Phrase<input type="password"></label>'
            '<button>Continue</button></form><a href="#existing" onclick="window.visited=true">Existing membership</a></div>')
        scene = observation['scene']
        self.assertEqual([field['type'] for field in scene['controls']], ['text', 'password'])
        self.assertEqual([action['label'] for action in scene['actions']], ['Continue', 'Existing membership'])
        self.assertEqual([text['text'] for text in scene['texts']], ['Membership'])
        self.assertEqual(scene['overflow'], {'controls': 0, 'actions': 0, 'texts': 0})
        await self.browser.agent_act(observation['revision'], scene['actions'][1]['ref'])
        self.assertTrue(await self.page.evaluate('window.visited'))
        self.assertEqual(await self.page.locator('input').evaluate_all('nodes=>nodes.some(node=>node.value)'), False)
        self.assertEqual(self.requests, [])

    async def test_inert_and_aria_hidden_ancestors_hide_open_shadow_descendants(self):
        observation = await self.fixture('<section inert><div id="inert-host"></div></section>'
            '<section aria-hidden="true"><div id="hidden-host"></div></section>'
            '<div id="visible-host"></div><button>Visible outer</button>', r'''() => {
              for(const id of ['inert-host','hidden-host','visible-host']) {
                const root=document.getElementById(id).attachShadow({mode:'open'});
                root.innerHTML='<h2>'+id+'</h2><input aria-label="'+id+'"><button>'+id+'</button>';
              }
            }''')
        scene = observation['scene']
        self.assertEqual([field['label'] for field in scene['controls']], ['visible-host'])
        self.assertEqual([action['label'] for action in scene['actions']], ['Visible outer', 'visible-host'])
        self.assertNotIn('inert-host', json.dumps(scene))
        self.assertNotIn('hidden-host', json.dumps(scene))

    async def test_shadow_modal_uses_deep_focus_otherwise_last_visible_modal(self):
        observation = await self.fixture('<div id="first" role="dialog" aria-modal="true"><button>First</button></div>'
            '<div id="host"></div>', r'''() => {
              const root=document.querySelector('#host').attachShadow({mode:'open'});
              root.innerHTML='<div role="dialog" aria-modal="true"><input aria-label="Shadow identifier"><button>Shadow</button></div>';
              root.querySelector('input').focus();
            }''')
        self.assertEqual([a['label'] for a in observation['scene']['actions']], ['Shadow'])
        await self.page.locator('#first button').focus()
        first = await self.browser.agent_observe()
        self.assertEqual([a['label'] for a in first['scene']['actions']], ['First'])
        self.assertNotEqual(observation['revision'], first['revision'])
        await self.page.evaluate('document.activeElement.blur()')
        last = await self.browser.agent_observe()
        self.assertEqual([a['label'] for a in last['scene']['actions']], ['Shadow'])

    async def test_native_modal_does_not_require_aria_attribute(self):
        observation = await self.fixture('<button>Background</button><dialog><button>Native modal</button></dialog>',
            "() => document.querySelector('dialog').showModal()")
        self.assertEqual([a['label'] for a in observation['scene']['actions']], ['Native modal'])
        await self.page.locator('dialog').evaluate('dialog=>dialog.close()')
        after = await self.browser.agent_observe()
        self.assertEqual([a['label'] for a in after['scene']['actions']], ['Background'])
        self.assertNotEqual(after['revision'], observation['revision'])

    async def test_visible_child_override_and_non_dialog_modal_attribute(self):
        observation = await self.fixture('<section style="visibility:hidden"><button style="visibility:visible">Visible override</button>'
            '<button>Hidden inherited</button></section><div aria-modal="true"><button>Ordinary container</button></div>')
        self.assertEqual([a['label'] for a in observation['scene']['actions']], ['Visible override', 'Ordinary container'])

    async def test_new_modal_makes_old_background_plan_stale_before_any_input(self):
        observation = await self.fixture('<form method="post"><label>Identifier<input></label><button>Continue</button></form>',
            '() => {window.events=0;document.addEventListener("input",()=>window.events++);document.querySelector("button").onclick=()=>window.events++;}')
        await self.browser.agent_present(observation['revision'], self.plan(observation))
        private = await self.browser.snapshot()
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=dialog aria-modal=true><button>Modal action</button></div>')")
        with self.assertRaises(RecoverableFormError):
            await self.browser.submit(private['revision'], private['actions'][0]['id'],
                {private['fields'][0]['name']: 'private-fixture-value'})
        self.assertEqual(await self.page.evaluate('window.events'), 0)
        self.assertEqual(await self.page.locator('input').input_value(), '')
        after = await self.browser.agent_observe()
        self.assertEqual([a['label'] for a in after['scene']['actions']], ['Modal action'])
        self.assertEqual(after['progress']['outcome'], 'stale')
        self.assertEqual(self.requests, [])

    async def test_midfill_modal_change_is_uncertain_and_never_clicks_background(self):
        observation = await self.fixture('<form method="post"><label>Identifier<input></label><button>Continue</button></form>',
            r'''() => {
              document.querySelector('button').onclick=()=>window.clicked=true;
              document.querySelector('input').oninput=()=>document.body.insertAdjacentHTML('beforeend',
                '<div role="dialog" aria-modal="true"><button>New modal</button></div>');
            }''')
        await self.browser.agent_present(observation['revision'], self.plan(observation))
        private = await self.browser.snapshot()
        with self.assertRaises(SubmitUncertainError):
            await self.browser.submit(private['revision'], private['actions'][0]['id'],
                {private['fields'][0]['name']: 'private-fixture-value'})
        self.assertIsNone(await self.page.evaluate('window.clicked'))
        self.assertEqual(self.browser.agent_progress()['outcome'], 'uncertain')
        self.assertEqual(self.requests, [])

    async def test_hidden_background_defaults_are_still_redacted_inside_modal(self):
        secret = 'private-default-671'
        observation = await self.fixture('<section inert aria-hidden="true"><div id="host"></div></section>'
            '<div role="dialog" aria-modal="true"><h1>Retry '+secret+'</h1>'
            '<button>Continue '+secret+'</button></div>',
            "() => document.querySelector('#host').attachShadow({mode:'open'}).innerHTML="
            + json.dumps('<input type="hidden" value="'+secret+'">'))
        self.assertNotIn(secret, json.dumps(observation))
        self.assertIn('Retry', json.dumps(observation))
        self.assertEqual(observation['scene']['controls'], [])

    async def test_required_native_control_outside_modal_still_disallows_associated_action(self):
        observation = await self.fixture('<form id="owner" method="post"><input required style="display:none"></form>'
            '<div role="dialog" aria-modal="true"><button form="owner">Continue</button></div>')
        self.assertEqual(observation['scene']['controls'], [])
        self.assertFalse(observation['scene']['actions'][0]['permitted'])
        with self.assertRaises(ValueError):
            await self.browser.agent_act(observation['revision'], observation['scene']['actions'][0]['ref'])
        self.assertEqual(self.requests, [])
