"""Explicit configuration, private credential access and bounded helper use."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.website_composer import LeaseComposer, composer_factory, read_key, validate_config
from oak.website_planner import WebsitePlannerError


CONFIG = {'backend': 'jev', 'model': 'typesafe/jev-1.13',
          'key_file': '/var/lib/oak-website-forms/openrouter.key', 'max_calls': 12}
CANARY = 'synthetic-openrouter-key-never-print-this'


class ComposerConfigurationTests(unittest.TestCase):
    def test_only_explicit_pinned_bounded_configuration_is_accepted(self):
        self.assertEqual(validate_config(CONFIG), CONFIG)
        for changed in ({'model': 'other/model'}, {'backend': 'chat'}, {'api_key': CANARY},
                        {'max_calls': 0}, {'max_calls': 13}, {'max_calls': True},
                        {'key_file': 'relative'}, {'key_file': '/tmp/../key'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_config({**CONFIG, **changed})

    def test_private_key_permissions_symlinks_and_oversize_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / 'key'
            key.write_text(CANARY)
            key.chmod(0o600)
            self.assertEqual(read_key(key, os.geteuid()), CANARY)
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, '^A private broker-readable API key file is required.$'):
                read_key(key, os.geteuid())
            key.chmod(0o600)
            link = Path(temp) / 'link'
            link.symlink_to(key)
            with self.assertRaises(ValueError):
                read_key(link, os.geteuid())
            key.write_text(CANARY * 200)
            with self.assertRaises(ValueError):
                read_key(key, os.geteuid())
            key.unlink()
            os.mkfifo(key, 0o600)
            with self.assertRaises(ValueError):
                read_key(key, os.geteuid())

    def test_factory_reuses_credential_but_gives_each_lease_its_own_quota(self):
        with patch('oak.website_composer.read_key', return_value=CANARY):
            factory = composer_factory(CONFIG, 1901)
        first, second = factory(), factory()
        self.assertIsNot(first, second)
        self.assertIs(first.planner, second.planner)
        self.assertNotIn(CANARY, repr(first))
        self.assertNotIn(CANARY, repr(first.planner))

    def test_service_render_passes_only_private_key_reference(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('setup_website_forms', root / 'scripts/setup-website-forms.py')
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        installation = {'origin': 'https://forms.example.test', 'parent_origin': 'https://panel.example.test',
                        'gateway_user': 'gateway', 'broker_user': 'broker', 'broker_uid': 1901,
                        'broker_gid': 1901, 'python': '/opt/oak/python', 'code_root': '/opt/oak/code',
                        'browsers': '/opt/oak/browsers', 'execution': '/etc/oak-executor/profile.json',
                        'composer': CONFIG}
        with tempfile.TemporaryDirectory() as temp, patch.object(setup.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1001, pw_gid=1001)):
            output = Path(temp) / 'bundle'
            setup.render(installation, output)
            rendered = json.loads((output / 'config.json').read_text())
            self.assertEqual(rendered['composer'], CONFIG)
            self.assertNotIn('tiktok_static_resources', rendered)
            self.assertNotIn('static_resources', rendered)
            self.assertNotIn('auth_hosts', rendered)
            self.assertNotIn('anonymous_static_resources', rendered)
            self.assertNotIn(CANARY, (output / 'config.json').read_text())
            self.assertNotIn('OPENROUTER_API_KEY=', (output / 'oak-website-forms.service').read_text())
            setup.render({**installation, 'tiktok_static_resources': True}, Path(temp) / 'enabled')
            enabled = json.loads((Path(temp) / 'enabled/config.json').read_text())
            self.assertIs(enabled['tiktok_static_resources'], True)
            setup.render({**installation, 'anonymous_static_resources': True}, Path(temp) / 'anonymous')
            anonymous = json.loads((Path(temp) / 'anonymous/config.json').read_text())
            self.assertIs(anonymous['anonymous_static_resources'], True)
            self.assertNotIn('auth_hosts', anonymous)
            setup.render({**installation, 'anonymous_static_resources': False}, Path(temp) / 'anonymous-disabled')
            self.assertIs(json.loads((Path(temp) / 'anonymous-disabled/config.json').read_text())['anonymous_static_resources'], False)
            setup.render({**installation, 'auth_hosts': {'https://login.example.com': ['auth.example.com']}}, Path(temp) / 'auth')
            auth = json.loads((Path(temp) / 'auth/config.json').read_text())
            self.assertEqual(auth['auth_hosts'], {'https://login.example.com': ['auth.example.com']})
            for invalid in (True, None, {'https://login.example.com': ['*.example.com']}):
                with self.assertRaises(ValueError):
                    setup.render({**installation, 'auth_hosts': invalid}, Path(temp) / 'invalid-auth')
            for invalid in (1, 'true', None):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    setup.render({**installation, 'tiktok_static_resources': invalid}, Path(temp) / 'invalid')
                with self.subTest(anonymous=invalid), self.assertRaises(ValueError):
                    setup.render({**installation, 'anonymous_static_resources': invalid}, Path(temp) / 'invalid-anonymous')

    def test_service_render_accepts_only_explicit_bounded_static_map(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('setup_website_forms', root / 'scripts/setup-website-forms.py')
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        installation = {'origin': 'https://forms.example.test', 'parent_origin': 'https://panel.example.test',
                        'gateway_user': 'gateway', 'broker_user': 'broker', 'broker_uid': 1901,
                        'broker_gid': 1901, 'python': '/opt/oak/python', 'code_root': '/opt/oak/code',
                        'browsers': '/opt/oak/browsers', 'execution': '/etc/oak-executor/profile.json'}
        policy = {'https://login.example.com': {'assets.example.com': ['stylesheet', 'script']}}
        from oak.website_resources import TIKTOK_ORIGIN, STATIC_HOSTS
        with tempfile.TemporaryDirectory() as temp, patch.object(setup.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1001, pw_gid=1001)):
            setup.render({**installation, 'static_resources': policy, 'tiktok_static_resources': True}, Path(temp) / 'valid')
            rendered = json.loads((Path(temp) / 'valid/config.json').read_text())
            self.assertEqual(rendered['static_resources'], {'https://login.example.com': {'assets.example.com': ['script', 'stylesheet']}})
            self.assertTrue(rendered['tiktok_static_resources'])
            self.assertNotIn(TIKTOK_ORIGIN, rendered['static_resources'], 'Legacy switch must not silently rewrite the explicit map')
            for invalid in (None, True, [], {'https://login.example.com:443': {'assets.example.com': ['script']}},
                            {'https://login.example.com': {'assets.example.com': ['fetch']}},
                            {TIKTOK_ORIGIN: {next(iter(STATIC_HOSTS)): ['font']}}):
                with self.subTest(policy=invalid), self.assertRaises(ValueError):
                    setup.render({**installation, 'static_resources': invalid, 'tiktok_static_resources': True}, Path(temp) / 'invalid')


class BrokerResourceConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_runtime_map_is_rejected_before_profile_keys_or_listeners(self):
        from oak.login_broker import serve
        from oak.website_resources import TIKTOK_ORIGIN, STATIC_HOSTS
        config = {'origin': 'https://forms.example.test', 'parent_origin': 'https://panel.example.test',
                  'gateway_uid': 1001, 'broker_uid': 1901, 'socket': '/run/oak-test/control.sock',
                  'port': 18766, 'execution': {}, 'tiktok_static_resources': True}
        with tempfile.TemporaryDirectory() as temp, patch('oak.executor.immutable', return_value=True), \
                patch('oak.login_broker.os.geteuid', return_value=1901), \
                patch('oak.executor.load_profile') as profile, \
                patch('oak.website_composer.read_key') as key, patch('aiohttp.web.TCPSite') as listener:
            path = Path(temp) / 'config.json'
            for invalid in (None, {'https://login.example.com': {'assets.example.com': ['fetch']}},
                            {TIKTOK_ORIGIN: {next(iter(STATIC_HOSTS)): ['font']}}):
                path.write_text(json.dumps({**config, 'static_resources': invalid}))
                with self.assertRaises(ValueError):
                    await serve(path)
            profile.assert_not_called()
            key.assert_not_called()
            listener.assert_not_called()
            for invalid in (1, 'true', None, {}):
                path.write_text(json.dumps({**config, 'anonymous_static_resources': invalid}))
                with self.assertRaises(ValueError):
                    await serve(path)
            profile.assert_not_called()
            key.assert_not_called()
            listener.assert_not_called()


class LeaseQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_calls_consume_quota_and_never_retry(self):
        planner = SimpleNamespace(plan=AsyncMock(side_effect=[WebsitePlannerError('unavailable'),
            {'plan': {}, 'usage': {'cost': 0.0001}}]))
        composer = LeaseComposer(planner, 2)
        with self.assertRaises(WebsitePlannerError):
            await composer.plan({})
        await composer.plan({})
        with self.assertRaises(WebsitePlannerError):
            await composer.plan({})
        self.assertEqual(planner.plan.await_count, 2)
        self.assertEqual(composer.calls, 2)
        self.assertEqual(composer.cost_usd, 0.0001)


class SceneLeaseQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_phase_request_budget_and_failed_cost_are_per_lease(self):
        class TwoPhase:
            scene_capable = True

            async def plan(self, observation, *, reserve, record):
                for phase in ('anchor', 'components'):
                    reserve()
                    record({'phase': phase, 'status': 'selected', 'model': 'typesafe/jev-1.13-20260917',
                            'cost': 0.001, 'elapsed_ms': 5, 'error': None})
                return {'plan': {}}
        first, second = LeaseComposer(TwoPhase(), 1), LeaseComposer(TwoPhase(), 2)
        with self.assertRaises(WebsitePlannerError):
            await first.plan({})
        self.assertEqual(first.calls, 1)
        self.assertEqual(first.cost_usd, 0.001)
        self.assertEqual(len(first.diagnostics()['events']), 1)
        self.assertEqual(first.diagnostics()['last_error'], 'unavailable')
        await second.plan({})
        self.assertEqual(second.calls, 2)
        self.assertEqual(second.diagnostics()['plan_status'], 'composed')
        with self.assertRaises(WebsitePlannerError):
            await second.plan({})
        self.assertEqual(second.calls, 2)
        copy = second.diagnostics()
        copy['events'].clear()
        self.assertEqual(len(second.events), 2)

    async def test_unknown_failed_attempt_consumes_quota_without_claiming_zero_cost(self):
        class Failure:
            scene_capable = True

            async def plan(self, observation, *, reserve, record):
                reserve()
                record({'phase': 'anchor', 'status': 'failed', 'model': None,
                        'cost': None, 'elapsed_ms': 5, 'error': 'unavailable'})
                raise WebsitePlannerError('unavailable')
        composer = LeaseComposer(Failure(), 1)
        with self.assertRaises(WebsitePlannerError):
            await composer.plan({})
        self.assertEqual(composer.diagnostics()['unknown_cost_calls'], 1)
        self.assertEqual(composer.calls, 1)
