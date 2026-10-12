"""Native-agent orchestration over intercepted pages; no model/network accounts."""

import asyncio
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch

from oak.signin_providers import RecoverableFormError, SubmitUncertainError
from oak.website_agent import AgentProposalError, WebsiteAgentForms, validate_agent_observation
from oak.website_scene import CATALOG, CATALOG_ID, CATALOG_VERSION, model_context

SITE = 'https://agent-fixture.example'
COMPONENTS = {'text': 'TextInput', 'email': 'EmailInput', 'password': 'PasswordInput',
              'otp': 'CodeInput', 'checkbox': 'Checkbox', 'select': 'Select'}


class AgentObservationTests(unittest.TestCase):
    def test_anonymous_dependency_discovery_requires_explicit_native_agent_opt_in(self):
        from oak.website_forms import WebsiteForms
        from oak.website_resources import AnonymousWebsiteResources, StaticWebsiteResources
        default, legacy = WebsiteAgentForms(SITE), WebsiteForms(SITE)
        self.assertIsNone(default._resources)
        self.assertIsNone(legacy._resources)
        approved = WebsiteAgentForms(SITE, resource_hosts={'cdn.example': ['script']})
        self.assertIs(type(approved._resources), StaticWebsiteResources)
        self.assertEqual(approved._resources._hosts, {'cdn.example': frozenset({'script'})})
        agent = WebsiteAgentForms(SITE, anonymous_static_resources=True)
        self.assertIsInstance(agent._resources, AnonymousWebsiteResources)
        self.assertEqual(agent._resources.origin, SITE)
        self.assertIsNone(agent._resources._session)
        self.assertEqual(agent._resources._hosts, {})

    def test_anonymous_dependency_flag_rejects_non_boolean_configuration(self):
        for value in (None, 0, 1, 'true', {}, []):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'explicit boolean'):
                    WebsiteAgentForms(SITE, anonymous_static_resources=value)

    def fixture(self):
        scene = {'provider': 'website', 'destination': SITE, 'revision': 'fixture:1', 'stage': 'form',
            'controls': [{'ref': 'f1', 'type': 'select', 'label': 'Method', 'required': False,
                'form': None, 'order': 0, 'max_length': 64,
                'options': [{'value': 'o1', 'label': 'Private caption'}]}],
            'actions': [{'ref': 'a2', 'label': 'Continue', 'form': None, 'order': 0,
                'kind': 'button', 'enabled': True, 'permitted': True}], 'texts': [],
            'after_input': False, 'overflow': {'controls': 0, 'actions': 0, 'texts': 0}}
        return {'revision': scene['revision'], 'stage': 'agent_working', 'scene': model_context(scene),
                'progress': {'event_seq': 0, 'outcome': 'idle'}}

    def test_copied_projection_retains_components_without_native_options(self):
        source = self.fixture()
        result = validate_agent_observation(source)
        result['scene']['controls'][0]['label'] = 'Changed'
        self.assertEqual(source['scene']['controls'][0]['label'], 'Method')
        self.assertEqual(source['scene']['controls'][0]['option_count'], 1)
        self.assertNotIn('Private caption', json.dumps(source))
        self.assertNotIn(SITE, json.dumps(source))

    def test_multiple_selects_validate_without_exposing_native_options(self):
        source = self.fixture()
        source['scene']['controls'].append({**source['scene']['controls'][0],
                                           'ref': 'f3', 'label': 'Region', 'order': 1, 'option_count': 3})
        result = validate_agent_observation(source)
        self.assertEqual(result, source)
        self.assertEqual([item['option_count'] for item in result['scene']['controls']], [1, 3])
        for control in result['scene']['controls']:
            self.assertNotIn('options', control)
        self.assertNotIn('Private caption', json.dumps(result))

    def test_surface_select_counts_support_large_lists_without_private_options(self):
        for count in (83, 256):
            source = self.fixture()
            source['scene']['controls'][0]['option_count'] = count
            source['scene']['surfaces'] = [{'ref': 's1', 'kind': 'document',
                'controls': ['f1'], 'actions': ['a2'], 'texts': []}]
            result = validate_agent_observation(source)
            self.assertEqual(result['scene']['controls'][0]['option_count'], count)
            self.assertNotIn('options', result['scene']['controls'][0])
            self.assertNotIn('Private caption', json.dumps(result))
        source['scene']['controls'][0]['option_count'] = 257
        with self.assertRaises(ValueError):
            validate_agent_observation(source)
        source['scene'].pop('surfaces')
        source['scene']['controls'][0]['option_count'] = 33
        with self.assertRaises(ValueError):
            validate_agent_observation(source)

    def test_surface_memberships_and_dispatch_are_validated_in_model_projection(self):
        source = self.fixture()
        source['scene']['actions'][0]['dispatch'] = 'button'
        source['scene']['surfaces'] = [{'ref': 's1', 'kind': 'dialog',
            'controls': ['f1'], 'actions': ['a2'], 'texts': []}]
        result = validate_agent_observation(source)
        self.assertEqual(result, source)
        result['scene']['surfaces'][0]['actions'].clear()
        self.assertEqual(source['scene']['surfaces'][0]['actions'], ['a2'])
        for change in (lambda v: v['scene']['surfaces'][0]['controls'].append('f999'),
                       lambda v: v['scene']['surfaces'][0].update(html='PRIVATE_CANARY'),
                       lambda v: v['scene']['actions'][0].update(dispatch='script')):
            bad = deepcopy(source); change(bad)
            with self.assertRaisesRegex(ValueError, '^Invalid agent website observation.$'):
                validate_agent_observation(bad)

    def test_unknown_fields_or_malformed_projection_cannot_reach_agent(self):
        changes = [lambda v: v.update(token='PRIVATE_CANARY'),
            lambda v: v['scene'].update(destination=SITE),
            lambda v: v['scene']['controls'][0].update(options=[{'value': 'secret', 'label': 'secret'}]),
            lambda v: v['scene']['controls'][0].update(value='PRIVATE_CANARY'),
            lambda v: v['scene']['controls'][0].update(components=['PasswordInput']),
            lambda v: v['scene']['controls'][0].update(option_count=True),
            lambda v: v['scene']['actions'][0].update(selector='#raw'),
            lambda v: v['scene']['catalog']['limits'].update(fields=99),
            lambda v: v['progress'].update(event_seq=True),
            lambda v: v['progress'].update(outcome='authenticated'),
            lambda v: v.update(stage='whatever'), lambda v: v.update(revision='https://secret')]
        for change in changes:
            value = self.fixture(); change(value)
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '^Invalid agent website observation.$'):
                    validate_agent_observation(value)


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class WebsiteAgentBrowserTests(unittest.IsolatedAsyncioTestCase):
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
        await self.page.goto(SITE + '/'); self.requests.clear()
        self.browser = WebsiteAgentForms(SITE)
        self.browser.page, self.browser.context = self.page, self.context
        self.addAsyncCleanup(self.browser.close)

    async def fixture(self, html, script=None):
        await self.page.set_content(html)
        if script:
            await self.page.evaluate(script)
        return await self.browser.agent_observe()

    def action(self, observation, label):
        return next(item['ref'] for item in observation['scene']['actions'] if item['label'] == label)

    def plan(self, observation, label, *, omit=()):
        action = next(item for item in observation['scene']['actions'] if item['label'] == label)
        errors = [item for item in observation['scene']['texts'] if item['kind'] == 'error']
        plan = {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
            'revision': observation['revision'], 'anchor': action['ref'],
            'fields': [{'ref': item['ref'], 'component': COMPONENTS[item['type']]}
                for item in observation['scene']['controls'] if item['form'] == action['form']
                and item['label'] not in omit and action['kind'] != 'link'],
            'actions': [{'ref': action['ref'], 'presentation': 'primary'}],
            'message_ref': errors[0]['ref'] if errors else None}
        surfaces = [surface for surface in observation['scene'].get('surfaces', [])
                    if action['ref'] in surface['actions']]
        if surfaces:
            surface = min(surfaces, key=lambda s: len(s['controls']) + len(s['actions']))
            fields = [field for field in observation['scene']['controls'] if field['ref'] in surface['controls']]
            plan.update(surface_ref=surface['ref'], fields=[{'ref': field['ref'],
                'component': COMPONENTS[field['type']]} for field in fields if field['label'] not in omit],
                actions=[{'ref': item['ref'],
                    'presentation': 'primary' if item['ref'] == action['ref'] else 'secondary',
                    'field_refs': [field['ref'] for field in fields if field['form'] == item['form']]
                        if item['permitted'] and item.get('dispatch') == 'submit' else []}
                    for item in observation['scene']['actions'] if item['ref'] in surface['actions']])
            plan['message_ref'] = next((item['ref'] for item in errors if item['ref'] in surface['texts']), None)
        return plan

    async def present(self, observation, label='Continue'):
        result = await self.browser.agent_present(observation['revision'], self.plan(observation, label))
        self.assertEqual(result['stage'], 'form')
        return await self.browser.snapshot()

    async def human_submit(self, form, values=None):
        values = values or {}
        action = form['actions'][0]
        await self.browser.submit(form['revision'], action['id'],
            {item['name']: values.get(item['type'], 'private-fixture-input') for item in form['fields']
             if item['name'] in action.get('field_refs', [field['name'] for field in form['fields']])})

    async def test_accepted_navigation_waits_for_delayed_dom_without_another_click(self):
        before = await self.fixture('<button>Open account</button>', r'''() => {
          window.clicks=0;window.inputs=0;
          document.addEventListener('input',()=>window.inputs++);
          document.querySelector('button').onclick=()=>{
            window.clicks++;
            setTimeout(()=>{document.body.innerHTML='<form><label>Identifier<input type="email"></label><button>Continue</button></form>';},350);
          };
        }''')
        after = await self.browser.agent_act(before['revision'], self.action(before, 'Open account'))
        self.assertEqual([field['type'] for field in after['scene']['controls']], ['email'])
        self.assertEqual(await self.page.evaluate('({clicks:window.clicks,inputs:window.inputs})'),
                         {'clicks': 1, 'inputs': 0})
        self.assertEqual(await self.page.locator('input').input_value(), '')
        self.assertEqual(after['progress'], {'event_seq': 0, 'outcome': 'idle'})
        self.assertFalse(self.browser._human_values_entered)
        self.assertFalse(self.browser._submitted)
        self.assertEqual(self.requests, [])

    async def test_unchanged_post_action_scene_returns_at_deadline_without_replay(self):
        before = await self.fixture('<button onclick="window.clicks=(window.clicks||0)+1">Open</button>')
        start = asyncio.get_running_loop().time()
        with patch('oak.website_agent._POST_ACTION_SECONDS', .15), \
                patch('oak.website_agent._POST_ACTION_POLL_SECONDS', .02):
            after = await self.browser.agent_act(before['revision'], self.action(before, 'Open'))
        elapsed = asyncio.get_running_loop().time() - start
        self.assertGreaterEqual(elapsed, .14)
        self.assertLess(elapsed, 1)
        self.assertEqual(after['scene'], before['scene'])
        self.assertNotEqual(after['revision'], before['revision'])
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
        self.assertEqual(after['progress'], {'event_seq': 0, 'outcome': 'idle'})
        self.assertFalse(self.browser._lock.locked())

    async def test_changed_empty_dom_after_navigation_waits_for_form_in_same_call(self):
        before = await self.fixture('<button>Open account</button>', r'''() => {
          window.clicks=0;
          document.querySelector('button').onclick=()=>{
            window.clicks++;
            document.body.innerHTML='<p>Loading account interface</p>';
            setTimeout(()=>{document.body.innerHTML='<form><label>Identifier<input type="email"></label><button>Continue</button></form>';},350);
          };
        }''')
        stages = []
        original = self.browser._observe

        async def observed():
            state = await original()
            stages.append(state['stage'])
            return state

        with patch.object(self.browser, '_observe', side_effect=observed):
            after = await self.browser.agent_act(before['revision'], self.action(before, 'Open account'))
        self.assertIn('unsupported_layout', stages)
        self.assertEqual(after['stage'], 'agent_working')
        self.assertEqual([field['type'] for field in after['scene']['controls']], ['email'])
        self.assertEqual(await self.page.locator('input').input_value(), '')
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
        self.assertEqual(after['progress'], {'event_seq': 0, 'outcome': 'idle'})
        self.assertFalse(self.browser._human_values_entered)
        self.assertEqual(self.requests, [])

    async def test_changed_empty_provider_block_returns_without_readiness_wait(self):
        before = await self.fixture('<button>Open account</button>', r'''() => {
          window.clicks=0;
          document.querySelector('button').onclick=()=>{
            window.clicks++;
            document.body.innerHTML='<h1>Temporarily blocked</h1>';
          };
        }''')
        after = await asyncio.wait_for(
            self.browser.agent_act(before['revision'], self.action(before, 'Open account')), 1)
        self.assertEqual(after['stage'], 'provider_blocked')
        self.assertEqual(after['scene']['controls'], [])
        self.assertEqual(after['scene']['actions'], [])
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
        self.assertFalse(self.browser._human_values_entered)
        self.assertEqual(self.requests, [])

    async def pending_readiness(self):
        before = await self.fixture('<button onclick="window.clicks=(window.clicks||0)+1">Open</button>')
        original = self.browser._observe
        ready = asyncio.Event()
        reads = 0

        async def observed():
            nonlocal reads
            state = await original()
            reads += 1
            if reads == 2:
                ready.set()  # Pre-dispatch read, then first completed post-click read.
            return state

        observer = patch.object(self.browser, '_observe', side_effect=observed)
        observer.start()
        self.addCleanup(observer.stop)
        task = asyncio.create_task(self.browser.agent_act(before['revision'], self.action(before, 'Open')))

        async def cleanup():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(ready.wait(), 1)
        return task

    async def test_additional_read_cannot_extend_readiness_deadline(self):
        before = await self.fixture('<button onclick="window.clicks=(window.clicks||0)+1">Open</button>')
        original = self.browser._observe
        reads = 0
        cancelled_read = asyncio.Event()

        async def blocked_read():
            nonlocal reads
            reads += 1
            if reads > 2:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled_read.set()
            return await original()

        with patch.object(self.browser, '_observe', side_effect=blocked_read), \
                patch('oak.website_agent._POST_ACTION_SECONDS', .15), \
                patch('oak.website_agent._POST_ACTION_POLL_SECONDS', .01):
            after = await asyncio.wait_for(
                self.browser.agent_act(before['revision'], self.action(before, 'Open')), 1)
        self.assertTrue(cancelled_read.is_set())
        self.assertEqual(after['scene'], before['scene'])
        self.assertNotEqual(after['revision'], before['revision'])
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
        self.assertFalse(self.browser._agent_act_uncertain)
        self.assertFalse(self.browser._lock.locked())

    async def test_cancellation_during_readiness_revokes_further_autonomous_action(self):
        task = await self.pending_readiness()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.browser._lock.locked())
        observed = await self.browser.agent_observe()
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_act(observed['revision'], self.action(observed, 'Open'))
        self.assertEqual(error.exception.code, 'navigation_requires_owner')
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
        self.assertEqual(observed['progress'], {'event_seq': 0, 'outcome': 'idle'})

    async def test_close_during_readiness_stops_observation_and_releases_lock(self):
        task = await self.pending_readiness()
        await self.browser.close()
        with self.assertRaises(ValueError):
            await asyncio.wait_for(task, 1)
        self.assertFalse(self.browser._lock.locked())
        with self.assertRaises(ValueError):
            await self.browser.agent_observe()

    async def test_stale_private_input_during_readiness_cannot_return_pre_input_scene(self):
        task = await self.pending_readiness()
        with self.assertRaises(RecoverableFormError):
            await self.browser.submit('obsolete:0', 'a999', {'f999': 'PRIVATE_INPUT_CANARY'})
        with self.assertRaises(RecoverableFormError) as error:
            await asyncio.wait_for(task, 1)
        self.assertEqual(error.exception.code, 'stale_form')
        observed = await self.browser.agent_observe()
        self.assertTrue(observed['scene']['after_input'])
        self.assertNotIn('PRIVATE_INPUT_CANARY', json.dumps(observed))
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_act(observed['revision'], self.action(observed, 'Open'))
        self.assertEqual(error.exception.code, 'navigation_requires_owner')
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
        self.assertFalse(self.browser._lock.locked())

    async def test_secondary_actions_with_null_owner_cannot_join_owned_input_form(self):
        observed = await self.fixture('<form><label>Email<input type="email" required></label>'
            '<button>Continue</button></form><button>Alternate one</button><button>Alternate two</button>')
        invalid = self.plan(observed, 'Continue')
        invalid['actions'] += [{'ref': self.action(observed, label), 'presentation': 'secondary'}
                               for label in ('Alternate one', 'Alternate two')]
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_present(observed['revision'], invalid)
        self.assertEqual(error.exception.code, 'invalid_plan')
        self.assertEqual(observed, await self.browser.agent_observe())
        form = await self.present(observed)
        self.assertEqual(len(form['fields']), 1)
        self.assertEqual([action['label'] for action in form['actions']], ['Continue'])
        self.assertFalse(self.browser._human_values_entered)

    async def test_agent_navigation_private_validation_and_next_step_loop(self):
        first = await self.fixture('<h1>Home</h1><button id="join">Sign up</button>', r'''() => {
          window.clicks=0;document.addEventListener('click',()=>window.clicks++);
          document.querySelector('#join').onclick=()=>{
            document.body.innerHTML='<h1>Create membership</h1><form method="post"><label>New email<input type="email"></label></form><button id="member">Already a member</button>';
            document.querySelector('#member').onclick=()=>{
              document.body.innerHTML='<h1>Sign in</h1><p role="alert"></p><form method="post"><label>Email<input type="email" required></label><label>Phrase<input type="password" required></label><button>Continue</button></form>';
              document.querySelector('form').onsubmit=event=>{
                event.preventDefault();const mail=event.target.querySelector('input').value;
                if(mail.startsWith('bad')){document.querySelector('[role=alert]').textContent='Try again '+mail;return;}
                document.body.innerHTML='<h1>Verify membership</h1><form method="post"><label>Security digits<input autocomplete="one-time-code" required></label><button>Verify</button></form>';
                document.querySelector('form').onsubmit=e=>{e.preventDefault();document.body.innerHTML='<h1>Welcome</h1><a href="/home">Home</a>';};
              };
            };
          };
        }''')
        self.assertEqual(first['stage'], 'agent_working')
        self.assertEqual((await self.browser.snapshot())['fields'], [])
        signup = await self.browser.agent_act(first['revision'], self.action(first, 'Sign up'))
        signin = await self.browser.agent_act(signup['revision'], self.action(signup, 'Already a member'))
        self.assertFalse(self.browser._submitted)
        self.assertEqual(signin['progress'], {'event_seq': 0, 'outcome': 'idle'})
        self.assertEqual(await self.page.evaluate('window.clicks'), 2)
        form = await self.present(signin)
        self.assertEqual(await self.page.evaluate('window.clicks'), 2)
        with self.assertRaises(ValueError):
            await self.browser.agent_act(signin['revision'], self.action(signin, 'Continue'))
        await self.human_submit(form, {'email': 'bad@example.test', 'password': 'private-phrase-671'})
        rejected = await self.browser.agent_observe()
        self.assertEqual(rejected['stage'], 'agent_working')
        self.assertEqual(rejected['progress'], {'event_seq': 1, 'outcome': 'submitted'})
        self.assertFalse(await self.browser.verified())
        self.assertNotIn('bad@example.test', json.dumps(rejected))
        self.assertNotIn('private-phrase-671', json.dumps(rejected))
        self.assertIn('Try again', json.dumps(rejected))
        self.assertEqual((await self.browser.snapshot())['actions'], [])
        form = await self.present(rejected)
        await self.human_submit(form, {'email': 'good@example.test', 'password': 'second-phrase-891'})
        verification = await self.browser.agent_observe()
        self.assertEqual([item['type'] for item in verification['scene']['controls']], ['otp'])
        form = await self.present(verification, 'Verify')
        await self.human_submit(form, {'otp': '172936'})
        final = await self.browser.agent_observe()
        self.assertEqual(final['stage'], 'agent_working')
        self.assertEqual(final['progress']['event_seq'], 3)
        private = await self.browser.snapshot()
        self.assertEqual(private['actions'], [])
        self.assertFalse(await self.browser.confirmable(private['revision']))
        self.assertFalse(await self.browser.verified())
        self.assertEqual(self.requests, [])
        for secret in ['private-phrase-671', 'second-phrase-891', '172936', SITE]:
            self.assertNotIn(secret, json.dumps(final))

    async def test_private_identifier_then_action_only_step_can_present_next_input(self):
        first = await self.fixture('<form method="post"><label>Identifier<input required></label>'
            '<button>Continue</button></form>', r'''() => {
              window.steps=0;
              document.querySelector('form').onsubmit=event=>{
                event.preventDefault();window.steps++;
                document.body.innerHTML='<h1>Choose another method</h1><button id="next">Continue</button>';
                document.querySelector('#next').onclick=()=>{
                  window.steps++;
                  document.body.innerHTML='<form method="post"><label>Membership phrase<input type="password"></label><button>Verify</button></form>';
                  document.querySelector('form').onsubmit=e=>{e.preventDefault();window.steps++;};
                };
              };
            }''')
        first_form = await self.present(first)
        await self.human_submit(first_form, {'text': 'private-member-713'})
        chooser = await self.browser.agent_observe()
        self.assertEqual(chooser['stage'], 'agent_working')
        self.assertEqual(chooser['scene']['controls'], [])
        self.assertEqual(chooser['progress'], {'event_seq': 1, 'outcome': 'submitted'})
        self.assertFalse(await self.browser.verified())
        review = await self.browser.snapshot()
        self.assertEqual(review['stage'], 'agent_working')
        self.assertEqual(review['actions'], [])
        self.assertFalse(await self.browser.confirmable(review['revision']))
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_act(chooser['revision'], self.action(chooser, 'Continue'))
        self.assertEqual(error.exception.code, 'navigation_requires_owner')
        second_form = await self.present(chooser)
        self.assertEqual(second_form['fields'], [])
        self.assertEqual([a['label'] for a in second_form['actions']], ['Continue'])
        self.assertNotEqual(review['revision'], second_form['revision'])
        self.assertFalse(await self.browser.confirmable(review['revision']))
        self.assertEqual(await self.page.evaluate('window.steps'), 1)
        await self.human_submit(second_form)
        self.assertEqual(await self.page.evaluate('window.steps'), 2)
        next_step = await self.browser.agent_observe()
        self.assertEqual(next_step['stage'], 'agent_working')
        self.assertEqual(next_step['progress'], {'event_seq': 2, 'outcome': 'submitted'})
        self.assertEqual([f['type'] for f in next_step['scene']['controls']], ['password'])
        self.assertEqual((await self.browser.snapshot())['actions'], [])
        self.assertFalse(await self.browser.confirmable(second_form['revision']))
        self.assertFalse(await self.browser.verified())
        third_form = await self.present(next_step, 'Verify')
        await self.human_submit(third_form, {'password': 'private-phrase-916'})
        self.assertEqual(await self.page.evaluate('window.steps'), 3)
        self.assertFalse(await self.browser.verified())
        self.assertNotIn('private-member-713', json.dumps(next_step))
        self.assertEqual(self.requests, [])

    async def test_task_automatically_presents_method_password_and_code_after_private_steps(self):
        from oak.website_task import WebsiteTask

        await self.fixture('<form method="post"><label>Identifier<input required></label>'
            '<button>Continue</button></form>', r'''() => {
              window.steps=0;
              document.querySelector('form').onsubmit=event=>{
                event.preventDefault();window.steps++;
                document.body.innerHTML='';
                setTimeout(()=>{
                  document.body.innerHTML='<h1>Choose a method</h1><button id="method">Use password</button>';
                  document.querySelector('#method').onclick=()=>{
                    window.steps++;
                    document.body.innerHTML='<form method="post"><label>Phrase<input type="password" required></label><button>Verify</button></form>';
                    document.querySelector('form').onsubmit=e=>{
                      e.preventDefault();window.steps++;
                      document.body.innerHTML='<form method="post"><label>Code<input autocomplete="one-time-code" required></label><button>Finish</button></form>';
                      document.querySelector('form').onsubmit=last=>{
                        last.preventDefault();window.steps++;document.body.innerHTML='<h1>Next page</h1>';
                      };
                    };
                  };
                },80);
              };
            }''')
        test = self
        class FixturePlanner:
            calls = 0
            async def plan(self, observed, *, reserve, record):
                await reserve()
                self.calls += 1
                action = observed['scene']['actions'][0]
                return {'operation': 'present', 'revision': observed['revision'],
                        'action_ref': action['ref'], 'plan': test.plan(observed, action['label'])}

        async def forbidden_act(*args):
            self.fail('The task must not activate private method choices automatically.')

        planner = FixturePlanner()
        task = WebsiteTask(planner, observe=self.browser.agent_observe, act=forbidden_act,
                           present=self.browser.agent_present, poll_seconds=.01, readiness_seconds=.3)
        self.addAsyncCleanup(task.close)
        task.start()

        async def rendered(types):
            async with asyncio.timeout(2):
                while True:
                    form = await self.browser.snapshot()
                    self.assertNotIn('confirm', [action['id'] for action in form['actions']])
                    self.assertNotEqual(form['stage'], 'review')
                    if form['stage'] == 'form' and [field['type'] for field in form['fields']] == types:
                        return form
                    await asyncio.sleep(.01)

        form = await rendered(['text'])
        await self.human_submit(form, {'text': 'private-member-automatic-913'})
        form = await rendered([])
        self.assertEqual([a['label'] for a in form['actions']], ['Use password'])
        self.assertEqual(await self.page.evaluate('window.steps'), 1)
        await self.human_submit(form)
        form = await rendered(['password'])
        await self.human_submit(form, {'password': 'private-password-automatic-715'})
        form = await rendered(['otp'])
        await self.human_submit(form, {'otp': '835174'})
        async with asyncio.timeout(2):
            while task.snapshot()['state'] != 'requires_agent':
                await asyncio.sleep(.01)
        self.assertEqual(task.snapshot()['error'], 'readiness_timeout')
        self.assertEqual(task.snapshot()['progress'], {'event_seq': 4, 'outcome': 'submitted'})
        self.assertEqual((planner.calls, await self.page.evaluate('window.steps')), (4, 4))
        self.assertEqual((await self.browser.snapshot())['actions'], [])
        self.assertFalse(await self.browser.verified())
        self.assertEqual(self.requests, [])

    async def test_native_unsupported_hidden_and_prefilled_controls_block_agent_button(self):
        for markup in ('<input type="hidden" value="private-default">', '<input type="file">',
                       '<input disabled value="private-default">', '<input readonly value="private-default">'):
            observation = await self.fixture('<form method="post" onsubmit="event.preventDefault();window.sent=true">'
                + markup + '<button>Continue</button></form>')
            with self.subTest(markup=markup):
                with self.assertRaises((ValueError, RecoverableFormError)):
                    await self.browser.agent_act(observation['revision'], self.action(observation, 'Continue'))
                self.assertIsNone(await self.page.evaluate('window.sent'))
        self.assertEqual(self.requests, [])

    async def test_unassociated_navigation_does_not_group_or_fill_loose_controls(self):
        for button in ('<button type="button">Open</button>', '<button>Open</button>',
                       '<div role="button" tabindex="0">Open</div>'):
            observation = await self.fixture('<label>Search<input type="search" value="anonymous-default"></label>'
                '<input type="hidden" value="hidden-default"><input readonly value="readonly-default">'
                '<input disabled value="disabled-default">' + button, r'''() => {
                  window.inputEvents=0;window.clicked=0;window.submitted=0;
                  document.addEventListener('input',()=>window.inputEvents++);
                  document.addEventListener('change',()=>window.inputEvents++);
                  document.addEventListener('submit',event=>{event.preventDefault();window.submitted++;});
                  document.querySelector('button,[role=button]').onclick=()=>window.clicked++;
                }''')
            with self.subTest(button=button):
                action = next(item for item in observation['scene']['actions'] if item['label'] == 'Open')
                self.assertIsNone(action['form'])
                self.assertTrue(any(field['form'] is None for field in observation['scene']['controls']))
                after = await self.browser.agent_act(observation['revision'], action['ref'])
                self.assertEqual(await self.page.evaluate('({clicked:window.clicked,inputs:window.inputEvents,submitted:window.submitted})'),
                                 {'clicked': 1, 'inputs': 0, 'submitted': 0})
                self.assertEqual(await self.page.locator('input').evaluate_all('nodes=>nodes.map(node=>node.value)'),
                                 ['anonymous-default', 'hidden-default', 'readonly-default', 'disabled-default'])
                self.assertEqual(after['progress'], {'event_seq': 0, 'outcome': 'idle'})
                self.assertFalse(self.browser._submitted)
                self.assertFalse(self.browser._human_values_entered)
        self.assertEqual(self.requests, [])

    async def test_external_native_form_owner_still_blocks_unfilled_defaults(self):
        observation = await self.fixture('<form id="associated" method="post"></form>'
            '<input form="associated" type="hidden" value="private-default">'
            '<button form="associated" type="button" onclick="window.clicked=true">Open</button>')
        action = next(item for item in observation['scene']['actions'] if item['label'] == 'Open')
        self.assertIsNotNone(action['form'])
        self.assertEqual(observation['scene']['controls'], [])
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_act(observation['revision'], action['ref'])
        self.assertEqual(error.exception.code, 'navigation_requires_owner')
        self.assertIsNone(await self.page.evaluate('window.clicked'))
        self.assertEqual(self.requests, [])

    async def test_private_revision_changes_on_presentation_with_unchanged_native_scene(self):
        observation = await self.fixture('<form method="post" onsubmit="event.preventDefault();window.sent=(window.sent||0)+1">'
            '<label>Identifier<input required></label><button>Continue</button></form>')
        working = await self.browser.snapshot()
        self.assertEqual(working['stage'], 'agent_working')
        self.assertEqual(working, await self.browser.snapshot())
        form = await self.present(observation)
        self.assertNotEqual(form['revision'], working['revision'])
        self.assertNotEqual(form['revision'], observation['revision'])
        self.assertEqual((await self.browser.agent_observe())['revision'], observation['revision'])
        self.assertEqual(form, await self.browser.snapshot())
        await self.browser.agent_present(observation['revision'], self.plan(observation, 'Continue'))
        self.assertEqual(form, await self.browser.snapshot())
        for obsolete in (working['revision'], observation['revision']):
            with self.assertRaises(RecoverableFormError):
                await self.browser.submit(obsolete, form['actions'][0]['id'], {})
        self.assertEqual(form, await self.browser.snapshot())
        self.assertIsNone(await self.page.evaluate('window.sent'))
        await self.human_submit(form)
        self.assertEqual(await self.page.evaluate('window.sent'), 1)
        self.assertEqual(self.browser.agent_progress(), {'event_seq': 1, 'outcome': 'submitted'})
        after = await self.browser.snapshot()
        self.assertEqual(after['stage'], 'agent_working')
        self.assertNotEqual(after['revision'], form['revision'])
        with self.assertRaises(RecoverableFormError):
            await self.human_submit(form)
        self.assertEqual(await self.page.evaluate('window.sent'), 1)

    async def test_empty_after_submission_is_unresolved_and_never_confirmable(self):
        observation = await self.fixture('<form method="post" onsubmit="event.preventDefault();document.body.innerHTML=\'<h1>Finished</h1>\'">'
            '<label>Identifier<input></label><button>Continue</button></form>')
        form = await self.present(observation)
        await self.human_submit(form)
        review = await self.browser.snapshot()
        native = await self.browser.agent_observe()
        self.assertEqual(review['stage'], 'agent_working')
        self.assertEqual(review['form_schema'], 'website-v2')
        self.assertEqual((review['fields'], review['actions']), ([], []))
        self.assertTrue(review['can_complete'])
        self.assertTrue(await self.browser.owner_finishable(review['revision']))
        self.assertEqual(review, await self.browser.snapshot())
        self.assertFalse(await self.browser.confirmable(review['revision']))
        self.assertFalse(await self.browser.confirmable(form['revision']))
        self.assertFalse(await self.browser.confirmable(native['revision']))
        self.assertFalse(await self.browser.verified())
        await self.page.set_content('<form method="post"><input><button>Continue</button></form>')
        self.assertFalse(await self.browser.confirmable(review['revision']))

    async def test_explicit_owner_finishability_is_private_and_independent_of_next_form(self):
        initial = await self.fixture('<button>Open</button>', r'''() => {
          document.querySelector('button').onclick=()=>{
            document.body.innerHTML='<form method="post"><label>Identifier<input required></label><button>Continue</button></form>';
            document.querySelector('form').onsubmit=e=>{
              e.preventDefault();document.body.innerHTML='<h1>Choose method</h1><button>Another method</button>';
            };
          };
        }''')
        initial_form = await self.browser.snapshot()
        self.assertFalse(initial_form['can_complete'])
        self.assertFalse(await self.browser.owner_finishable(initial_form['revision']))
        observed = await self.browser.agent_act(initial['revision'], self.action(initial, 'Open'))
        form = await self.present(observed)
        self.assertFalse(form['can_complete'])
        self.assertFalse(await self.browser.owner_finishable(form['revision']))
        await self.human_submit(form)
        chooser = await self.browser.agent_observe()
        self.assertNotIn('can_complete', json.dumps(chooser))
        private = await self.browser.snapshot()
        self.assertEqual(private['stage'], 'agent_working')
        self.assertTrue(private['can_complete'])
        self.assertTrue(await self.browser.owner_finishable(private['revision']))
        self.assertFalse(await self.browser.verified())
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.owner_finishable(form['revision'])
        self.assertEqual(error.exception.code, 'stale_form')
        followup = await self.present(chooser, 'Another method')
        self.assertTrue(followup['can_complete'])
        self.assertEqual([action['label'] for action in followup['actions']], ['Another method'])
        self.assertNotIn('confirm', [action['id'] for action in followup['actions']])
        self.assertTrue(await self.browser.owner_finishable(followup['revision']))
        self.assertFalse(await self.browser.confirmable(followup['revision']))
        await self.page.locator('button').evaluate("e=>e.textContent='Changed method'")
        with self.assertRaises(RecoverableFormError) as error:
            await self.browser.owner_finishable(followup['revision'])
        self.assertEqual(error.exception.code, 'stale_form')
        changed = await self.browser.agent_observe()
        self.assertEqual(changed['progress']['outcome'], 'stale')
        latest = await self.browser.snapshot()
        self.assertTrue(latest['can_complete'])  # Earlier accepted human action survives plan churn.
        self.assertTrue(await self.browser.owner_finishable(latest['revision']))
        replacement = await self.present(changed, 'Changed method')
        self.assertTrue(replacement['can_complete'])
        self.assertTrue(await self.browser.owner_finishable(replacement['revision']))
        self.assertEqual(self.requests, [])

    async def test_link_navigation_does_not_fill_unrelated_loose_controls(self):
        observation = await self.fixture('<input value="native-private"><a href="#signin" onclick="window.visited=true">Sign in</a>')
        following = await self.browser.agent_act(observation['revision'], self.action(observation, 'Sign in'))
        self.assertTrue(await self.page.evaluate('window.visited'))
        self.assertEqual(await self.page.locator('input').input_value(), 'native-private')
        self.assertNotIn('native-private', json.dumps(following))
        self.assertFalse(self.browser._submitted)

    async def test_plan_requires_fresh_grounded_fields_and_cannot_replace_pending_form(self):
        observation = await self.fixture('<form method="post"><label>Identifier<input required></label><button>Continue</button></form>')
        self.assertTrue(observation['scene']['surfaces'])
        legacy = self.plan(observation, 'Continue')
        legacy.pop('surface_ref')
        for action in legacy['actions']:
            action.pop('field_refs')
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_present(observation['revision'], legacy)
        self.assertEqual(error.exception.code, 'invalid_plan')
        invalid = self.plan(observation, 'Continue'); invalid['fields'] = []
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_present(observation['revision'], invalid)
        self.assertEqual(error.exception.code, 'invalid_plan')
        self.assertIsInstance(error.exception, RecoverableFormError)
        invalid = self.plan(observation, 'Continue'); invalid['fields'][0]['ref'] = 'f99999'
        with self.assertRaises(ValueError):
            await self.browser.agent_present(observation['revision'], invalid)
        await self.present(observation)
        original = await self.browser.snapshot()
        self.assertEqual(await self.browser.agent_present(observation['revision'], self.plan(observation, 'Continue')),
                         await self.browser.agent_observe())
        replacement = self.plan(observation, 'Continue'); replacement['fields'][0]['component'] = 'EmailInput'
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_present(observation['revision'], replacement)
        self.assertEqual(error.exception.code, 'human_step_pending')
        self.assertEqual(original, await self.browser.snapshot())
        await self.page.locator('input').evaluate("e=>e.setAttribute('required','')")
        await self.page.locator('button').evaluate("e=>e.textContent='Changed'")
        stale = await self.browser.agent_observe()
        self.assertEqual(stale['progress'], {'event_seq': 1, 'outcome': 'stale'})
        self.assertEqual(stale['stage'], 'agent_working')

    async def test_reflected_password_is_redacted_and_agent_cannot_act_after_private_input(self):
        secret = 'short-Z17'
        observation = await self.fixture('<form method="post"><label>Phrase<input type="password"></label>'
            '<button>Continue</button></form>', r'''() => {
              const original=String.prototype.split;
              document.querySelector('form').onsubmit=e=>{
                e.preventDefault();const entered=document.querySelector('input').value;
                String.prototype.split=function(separator,...rest){
                  return separator===entered?[String(this)]:original.call(this,separator,...rest);
                };
                document.body.innerHTML='<h1>Try again '+entered+'</h1><p role="alert">Rejected '+entered+'</p>'+
                  '<button onclick="window.clicked=true">Another method</button>';
              };
            }''')
        form = await self.present(observation)
        await self.human_submit(form, {'password': secret})
        self.assertIn(secret, await self.page.locator('h1').inner_text())
        clean = await self.browser.agent_observe()
        self.assertNotIn(secret, json.dumps(clean))
        self.assertIn('Try again', json.dumps(clean))
        with self.assertRaises(AgentProposalError) as error:
            await self.browser.agent_act(clean['revision'], self.action(clean, 'Another method'))
        self.assertEqual(error.exception.code, 'navigation_requires_owner')
        self.assertIsNone(await self.page.evaluate('window.clicked'))
        self.assertFalse(await self.browser.verified())

    async def test_stale_human_submission_consumes_plan_without_input_or_click(self):
        observation = await self.fixture('<form method="post"><label>Identifier<input></label><button>Continue</button></form>',
            '() => {window.changed=0;document.addEventListener("input",()=>window.changed++);}')
        form = await self.present(observation)
        await self.page.locator('button').evaluate("e=>e.textContent='New action'")
        with self.assertRaises(RecoverableFormError):
            await self.human_submit(form)
        result = await self.browser.agent_observe()
        self.assertEqual(result['progress'], {'event_seq': 1, 'outcome': 'stale'})
        self.assertEqual(await self.page.evaluate('window.changed'), 0)
        self.assertEqual(result['stage'], 'agent_working')
        self.assertTrue(self.browser._human_values_entered)
        self.assertFalse((await self.browser.snapshot())['can_complete'])

    async def test_uncertain_human_dispatch_is_not_replayed_or_left_presented(self):
        observation = await self.fixture('<form method="post" onsubmit="event.preventDefault();window.sent=(window.sent||0)+1"><label>Identifier<input></label><button>Continue</button></form>')
        form = await self.present(observation)
        await self.human_submit(form)
        self.assertTrue((await self.browser.snapshot())['can_complete'])
        form = await self.present(await self.browser.agent_observe())
        original = self.page.evaluate

        async def failed_result(expression, argument=None):
            result = await original(expression, argument)
            if argument and argument.get('operation') == 'submit':
                raise RuntimeError('PRIVATE_TRANSPORT_CANARY')
            return result

        with patch.object(self.page, 'evaluate', side_effect=failed_result):
            with self.assertRaises(SubmitUncertainError):
                await self.human_submit(form)
        self.assertEqual(await self.page.evaluate('window.sent'), 2)
        self.assertEqual(self.browser.agent_progress(), {'event_seq': 2, 'outcome': 'uncertain'})
        self.assertEqual((await self.browser.snapshot())['stage'], 'agent_working')
        self.assertFalse((await self.browser.snapshot())['can_complete'])
        with self.assertRaises(RecoverableFormError):
            await self.human_submit(form)
        self.assertEqual(await self.page.evaluate('window.sent'), 2)
        self.assertNotIn('PRIVATE_TRANSPORT_CANARY', json.dumps(await self.browser.agent_observe()))

    async def test_agent_action_stale_or_uncertain_cannot_replay(self):
        observation = await self.fixture('<button onclick="window.clicks=(window.clicks||0)+1">Start</button>')
        reference = self.action(observation, 'Start')
        await self.browser.agent_act(observation['revision'], reference)
        with self.assertRaises(RecoverableFormError):
            await self.browser.agent_act(observation['revision'], reference)
        observation = await self.browser.agent_observe()
        original = self.page.evaluate

        async def failed_result(expression, argument=None):
            result = await original(expression, argument)
            if argument and argument.get('operation') == 'submit':
                raise RuntimeError('PRIVATE_DISPATCH_CANARY')
            return result

        with patch.object(self.page, 'evaluate', side_effect=failed_result):
            with self.assertRaises(SubmitUncertainError):
                await self.browser.agent_act(observation['revision'], reference)
        self.assertEqual(await self.page.evaluate('window.clicks'), 2)
        observation = await self.browser.agent_observe()
        with self.assertRaises(ValueError):
            await self.browser.agent_act(observation['revision'], reference)
        self.assertEqual(await self.page.evaluate('window.clicks'), 2)

    async def test_initial_blank_is_not_completion_and_close_revokes_agent_authority(self):
        observation = await self.fixture('<h1>Blank page</h1>')
        self.assertEqual(observation['stage'], 'unsupported_layout')
        self.assertFalse(await self.browser.verified())
        self.assertFalse(await self.browser.confirmable(observation['revision']))
        await self.browser.close()
        for operation in (self.browser.agent_observe(), self.browser.snapshot(),
                          self.browser.agent_act(observation['revision'], 'a1'),
                          self.browser.agent_present(observation['revision'], {})):
            with self.assertRaises(ValueError):
                await operation

    async def test_overflow_is_visible_without_model_selection_or_hidden_choices(self):
        observation = await self.fixture(''.join('<button>Action '+str(i)+'</button>' for i in range(70)))
        self.assertEqual(len(observation['scene']['actions']), 64)
        self.assertEqual(observation['scene']['overflow']['actions'], 6)
        self.assertEqual(observation['stage'], 'agent_working')
        self.assertIsNone(self.browser.diagnostics()['model'])


if __name__ == '__main__':
    unittest.main()
