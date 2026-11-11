"""Offline fixture/state checks; no account, telephony or model access."""

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from oak.voice_control import TOOLS, VoiceControl, VoiceError, capabilities, dispatch


class VoiceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = VoiceControl(self.root, 'conversation-A')
        self.addCleanup(self.control.close)

    def prepare(self, **changes):
        args = {'request_key': 'owner-test-1', 'destination_ref': 'owner-test'}
        return self.control.prepare(**{**args, **changes})

    def test_no_network_or_live_call_in_happy_path(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')):
            prepared = self.prepare()
            connected = self.control.simulate(prepared['call_id'], 'connected')
            self.assertEqual(connected['state'], 'simulated_connected')
            self.assertEqual(self.control.status(prepared['call_id']), connected)
            ended = self.control.cancel(prepared['call_id'])
        self.assertEqual(ended['state'], 'simulated_ended')
        self.assertTrue(ended['cancel_requested'])
        self.assertFalse(ended['live_call_started'])
        self.assertEqual(ended['language'], 'uk-UA')
        self.assertEqual(ended['incremental_provider_charge'], 0)

    def test_preparation_is_durable_and_idempotent(self):
        first = self.prepare()
        self.assertEqual(self.prepare(), first)
        self.control.close()
        with VoiceControl(self.root, 'conversation-A') as reopened:
            self.assertEqual(reopened.prepare('owner-test-1', 'owner-test'), first)
        for path in self.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_idempotency_conflict_cannot_change_call_details(self):
        original = self.prepare()
        for changes in ({'destination_ref': 'someone-else'}, {'max_duration_seconds': 30}):
            with self.assertRaisesRegex(VoiceError, 'different call details'):
                self.prepare(**changes)
        self.assertEqual(self.control.status(original['call_id']), original)

    def test_scope_isolation_even_with_known_call_id_or_request_key(self):
        original = self.prepare()
        with VoiceControl(self.root, 'conversation-B') as other:
            for action in (other.status, other.cancel, other.simulate):
                with self.assertRaisesRegex(VoiceError, 'not found'):
                    action(original['call_id'])
            self.assertNotEqual(other.prepare('owner-test-1', 'owner-test')['call_id'], original['call_id'])

    def test_another_process_cannot_recover_a_running_scope(self):
        with self.assertRaisesRegex(VoiceError, 'already has a voice service'):
            VoiceControl(self.root, 'conversation-A')

    def test_only_explicit_scope_is_accepted(self):
        for scope in ('', '../other', '/private', None, 'a' * 129, 'line\nbreak'):
            with self.subTest(scope=scope), self.assertRaises(VoiceError):
                VoiceControl(self.root, scope)

    def test_aliases_reject_numbers_urls_and_credentials(self):
        for value in ('+380000000000', '380000000000', 'sip:name@example.test',
                      'https://example.test', '', 'x' * 65, None, True, {'alias': 'test'}):
            for key in ('request_key', 'destination_ref'):
                with self.subTest(key=key, value=value), self.assertRaises(VoiceError):
                    self.prepare(**{key: value})

    def test_duration_is_bounded_integer(self):
        for value in (True, False, 0, 14, 601, 15.0, '60', None):
            with self.subTest(value=value), self.assertRaises(VoiceError):
                self.prepare(max_duration_seconds=value)
        for value in (15, 600):
            self.assertEqual(self.prepare(request_key=f'test-{value}', max_duration_seconds=value)['max_duration_seconds'], value)

    def test_simulation_is_consumed_once_even_with_different_outcome(self):
        adapter = Mock()
        adapter.simulate.return_value = 'simulated_connected'
        self.control._adapter = adapter
        call_id = self.prepare()['call_id']
        first = self.control.simulate(call_id, 'connected')
        self.assertEqual(self.control.simulate(call_id, 'failed'), first)
        adapter.simulate.assert_called_once_with('connected')
        self.control.close()
        with VoiceControl(self.root, 'conversation-A', adapter=adapter) as reopened:
            self.assertEqual(reopened.simulate(call_id), first)
        adapter.simulate.assert_called_once()

    def test_cancel_before_start_cannot_be_replayed(self):
        call_id = self.prepare()['call_id']
        cancelled = self.control.cancel(call_id)
        self.assertEqual(cancelled['state'], 'cancelled')
        self.assertEqual(self.control.cancel(call_id), cancelled)
        with self.assertRaises(VoiceError):
            self.control.simulate(call_id)

    def test_uncertainty_is_preserved_after_cancel(self):
        call_id = self.prepare()['call_id']
        uncertain = self.control.simulate(call_id, 'uncertain')
        self.assertEqual(uncertain['state'], 'uncertain')
        self.assertEqual(self.control.cancel(call_id)['state'], 'uncertain')
        self.assertEqual(self.control.simulate(call_id, 'completed')['state'], 'uncertain')

    def test_adapter_exception_never_leaks_or_replays(self):
        adapter = Mock()
        adapter.simulate.side_effect = RuntimeError('secret-personal-data')
        self.control._adapter = adapter
        call_id = self.prepare()['call_id']
        result = self.control.simulate(call_id)
        self.assertEqual(result['state'], 'uncertain')
        self.assertNotIn('secret-personal-data', json.dumps(result))
        self.control.simulate(call_id)
        adapter.simulate.assert_called_once()

    def test_crash_after_durable_attempt_is_recovered_as_uncertain(self):
        adapter = Mock()
        adapter.simulate.side_effect = KeyboardInterrupt
        self.control._adapter = adapter
        call_id = self.prepare()['call_id']
        with self.assertRaises(KeyboardInterrupt):
            self.control.simulate(call_id)
        self.control.close()
        with VoiceControl(self.root, 'conversation-A', adapter=adapter) as reopened:
            self.assertEqual(reopened.status(call_id)['state'], 'uncertain')
            reopened.simulate(call_id)
        adapter.simulate.assert_called_once()

    def test_failed_fixture_and_unknown_adapter_results(self):
        call_id = self.prepare()['call_id']
        self.assertEqual(self.control.simulate(call_id, 'failed')['state'], 'simulated_failed')
        self.control._adapter = Mock(simulate=Mock(return_value='unverified-live-success'))
        other = self.prepare(request_key='another')['call_id']
        self.assertEqual(self.control.simulate(other)['state'], 'uncertain')

    def test_bad_call_references_and_outcomes(self):
        for value in (None, True, '../other', 'x' * 32, 'a' * 31, []):
            for action in (self.control.status, self.control.cancel, self.control.simulate):
                with self.subTest(value=value), self.assertRaises(VoiceError):
                    action(value)
        call_id = self.prepare()['call_id']
        for value in (None, True, [], {}, 'live'):
            with self.assertRaises(VoiceError):
                self.control.simulate(call_id, value)
        self.assertEqual(self.control.status(call_id)['state'], 'prepared')

    def test_rejects_nonprivate_or_symlink_state(self):
        public = self.root / 'public'
        public.mkdir(mode=0o755)
        # Other repository tests use a restrictive process umask. Make this
        # deliberately unsafe fixture explicit instead of inheriting that mask.
        public.chmod(0o755)
        with self.assertRaisesRegex(VoiceError, 'private'):
            VoiceControl(public, 'scope')
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(VoiceError):
            VoiceControl(link, 'scope')

    def test_rejects_symlink_or_public_database(self):
        import hashlib
        path = self.root / (hashlib.sha256(b'new-scope').hexdigest() + '.sqlite3')
        path.write_text('')
        os.chmod(path, 0o644)
        with self.assertRaises(VoiceError):
            VoiceControl(self.root, 'new-scope')
        path.unlink()
        path.symlink_to(self.root / 'missing')
        with self.assertRaises(OSError):
            VoiceControl(self.root, 'new-scope')

    def test_capabilities_are_honest_and_no_live_tools_exist(self):
        info = capabilities()
        self.assertFalse(info['live_calls_enabled'])
        self.assertFalse(info['audio_bridge_implemented'])
        self.assertEqual(info['readiness'], 'not_ready_for_live_calls')
        self.assertEqual(len(TOOLS), 5)
        with self.assertRaises(VoiceError):
            dispatch(self.control, 'voice_start_live_call', {})

    def test_dispatch_cannot_change_scope_or_self_authorize(self):
        for args in ({'scope': 'conversation-B'}, {'approved': True}, {'live': True}, {'api_key': 'not-a-real-key'}):
            with self.assertRaises(VoiceError):
                dispatch(self.control, 'voice_capabilities', args)
        for name, args in (([], {}), ('voice_prepare_call', []), ('voice_prepare_call', {})):
            with self.assertRaises(VoiceError):
                dispatch(self.control, name, args)
        prepared = dispatch(self.control, 'voice_prepare_call', {'request_key': 'fixture', 'destination_ref': 'owner'})
        self.assertEqual(dispatch(self.control, 'voice_call_status', {'call_id': prepared['call_id']}), prepared)


if __name__ == '__main__':
    unittest.main()
