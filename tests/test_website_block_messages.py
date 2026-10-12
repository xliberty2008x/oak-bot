"""Observed policy/transport causes stay distinct, without disclosing URLs or page text."""

import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from oak.signin_providers import RecoverableFormError
from oak.website_agent import WebsiteAgentForms
from oak.website_forms import BLOCK_MESSAGES, WebsiteForms
from oak.website_scene import CATALOG_ID, CATALOG_VERSION
from tests import test_login_ui as ui


SITE = 'https://block-fixture.example'
FORM = ('<form method="post" onsubmit="event.preventDefault();this.remove()">'
        '<label>Identifier<input required></label><button>Continue</button></form>')


class BlockStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_exception_is_not_a_remote_refusal_or_raw_error(self):
        adapter = WebsiteForms(SITE)
        adapter.page = SimpleNamespace(main_frame=object())
        request = SimpleNamespace(url=SITE + '/', frame=adapter.page.main_frame,
                                  is_navigation_request=lambda: True)
        route = SimpleNamespace(request=request, abort=AsyncMock(),
            continue_=AsyncMock(side_effect=ValueError('PRIVATE_ERROR_TOKEN https://private.invalid/?token=secret')))
        await adapter._route(route)
        state = adapter._document_guard()
        self.assertEqual(adapter._state_message(state), BLOCK_MESSAGES['route_error'])
        self.assertNotIn('PRIVATE_ERROR_TOKEN', json.dumps(state))
        route.abort.assert_awaited_once()

    async def test_unknown_inherited_boundary_does_not_claim_page_rejection(self):
        adapter = WebsiteForms(SITE)
        adapter._blocked = True
        self.assertEqual(adapter._state_message(adapter._document_guard()), BLOCK_MESSAGES['unknown'])


@unittest.skipUnless(ui.NODE, 'Node is required for offline private renderer checks')
class BlockMessageRendererTests(unittest.TestCase):
    def test_fixed_block_messages_render_but_arbitrary_page_text_never_does(self):
        ui.LoginUITests.run_ui(self, 'const messages = ' + json.dumps(list(BLOCK_MESSAGES.values())) + r''';
for (const message of messages) {
  const page = await createUI();
  const value = website([], [], 'provider_blocked'); value.form.message = message;
  value.form.form_schema='website-v2'; value.form.can_complete=false;
  await page.attach(value, 'website', 'https://example.test', {form_schema:'website-v2'});
  assert.equal(page.ids.status.textContent,message);
  assert.equal(page.ids.cancel.hidden,false);
  assert.equal(page.ids.complete.hidden,true);
  assert.equal(page.requests.filter(r=>r.url==='/api/cancel').length,0);
}
const page=await createUI(), value=website([], [], 'provider_blocked');
value.form.message='<img src="https://private.invalid/?token=PRIVATE_CANARY">';
await page.attach(value,'website');
assert.equal(page.ids.status.textContent,messages[messages.length-1]);
assert.ok(!JSON.stringify(page.messages).includes('PRIVATE_CANARY'));
assert.equal(page.ids.status.children.length,0);
''')


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1',
                     'opt-in intercepted Chromium fixtures')
class BlockMessageBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        self.chromium = await self.driver.chromium.launch(headless=True, chromium_sandbox=True)
        self.addAsyncCleanup(self.chromium.close)
        self.context = await self.chromium.new_context(service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.adapter = WebsiteAgentForms(SITE)
        self.adapter.context = self.context
        await self.adapter._configure_context()
        self.adapter.page = await self.context.new_page()
        self.context.on('page', lambda page: asyncio.create_task(self.adapter._close_popup(page)))
        self.responses = {'/': (200, FORM)}
        self.forwarded = []

        async def intercepted(route):
            async def allowed():
                self.forwarded.append(urlsplit(route.request.url).netloc)
                response = self.responses.get(urlsplit(route.request.url).path, (200, '<h1>Ready</h1>'))
                if response is None:
                    await route.abort('failed')
                else:
                    await route.fulfill(status=response[0], content_type='text/html', body=response[1])
            await self.adapter._route(SimpleNamespace(request=route.request,
                continue_=allowed, abort=route.abort))

        await self.context.route('**/*', intercepted)
        await self.adapter.page.goto(SITE + '/')

    async def accepted_input(self):
        observation = await self.adapter.agent_observe()
        scene = observation['scene']
        action = scene['actions'][0]
        surface = next(item for item in scene['surfaces'] if action['ref'] in item['actions'])
        fields = [field for field in scene['controls'] if field['ref'] in surface['controls']]
        plan = {'catalog_id': CATALOG_ID, 'catalog_version': CATALOG_VERSION,
            'revision': observation['revision'], 'surface_ref': surface['ref'], 'anchor': action['ref'],
            'fields': [{'ref': field['ref'], 'component': 'TextInput'} for field in fields],
            'actions': [{'ref': action['ref'], 'presentation': 'primary',
                         'field_refs': [field['ref'] for field in fields]}], 'message_ref': None}
        await self.adapter.agent_present(observation['revision'], plan)
        private = await self.adapter.snapshot()
        await self.adapter.submit(private['revision'], private['actions'][0]['id'],
                                  {private['fields'][0]['name']: 'synthetic-private-identifier'})
        ready = await self.adapter.snapshot()
        self.assertTrue(ready['can_complete'])
        return ready

    async def assert_block(self, reason):
        private = await self.adapter.snapshot()
        self.assertEqual(private['stage'], 'provider_blocked')
        self.assertEqual(private['message'], BLOCK_MESSAGES[reason])
        self.assertEqual(private['fields'], [])
        self.assertEqual(private['actions'], [])
        self.assertFalse(private['can_complete'])
        self.assertFalse(await self.adapter.owner_finishable(private['revision']))
        self.assertFalse(await self.adapter.verified())
        public = await self.adapter.agent_observe()
        self.assertEqual(set(public), {'revision', 'stage', 'scene', 'progress'})
        serialized = json.dumps([private, public])
        for secret in ('synthetic-private-identifier', 'PRIVATE_ROUTE_CANARY', 'private.invalid', '_block_reason'):
            self.assertNotIn(secret, serialized)
        return private

    async def test_cross_origin_abort_keeps_oak_cause_and_revokes_prior_finishability(self):
        previous = await self.accepted_input()
        from playwright.async_api import Error
        with self.assertRaises(Error):
            await self.adapter.page.goto('https://private.invalid/next?token=PRIVATE_ROUTE_CANARY')
        await self.assert_block('origin_policy')
        self.assertNotIn('private.invalid', self.forwarded)
        # requestfailed from our abort must not override the origin-policy cause.
        self.assertEqual(self.adapter._document_error, 'network_error')
        with self.assertRaises(RecoverableFormError):
            await self.adapter.owner_finishable(previous['revision'])

    async def test_popup_is_closed_and_explained_without_disclosing_its_address(self):
        await self.accepted_input()
        async with self.context.expect_page() as created:
            await self.adapter.page.evaluate("window.open('about:blank')")
        popup = await created.value
        if not popup.is_closed():
            await popup.wait_for_event('close')
        self.assertTrue(popup.is_closed())
        await self.assert_block('popup')

    async def test_http_error_network_error_and_successful_recovery_are_distinct(self):
        await self.accepted_input()
        self.responses['/error'] = (503, '<h1>PRIVATE_ROUTE_CANARY</h1>')
        await self.adapter.page.goto(SITE + '/error')
        await self.assert_block('http_error')
        await self.adapter.page.goto(SITE + '/recovered')
        recovered = await self.adapter.snapshot()
        self.assertTrue(recovered['can_complete'])
        self.responses['/network'] = None
        from playwright.async_api import Error
        with self.assertRaises(Error):
            await self.adapter.page.goto(SITE + '/network')
        await self.assert_block('network_error')

    async def test_recognized_remote_text_and_challenge_disable_finish_after_input(self):
        await self.accepted_input()
        await self.adapter.page.set_content('<h1>Temporarily blocked</h1>')
        await self.assert_block('page_rejection')
        await self.adapter.page.set_content('<input id="captcha" aria-label="Challenge">')
        challenge = await self.adapter.snapshot()
        self.assertEqual(challenge['stage'], 'unsupported_challenge')
        self.assertFalse(challenge['can_complete'])
        self.assertFalse(await self.adapter.owner_finishable(challenge['revision']))
        self.assertFalse(await self.adapter.verified())
