import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from oak.colab_auth import serve


class ColabAdapterTests(unittest.TestCase):
    def adapter(self, *, url='https://accounts.google.com/o/oauth2/v2/auth?state=PRIVATE_STATE', failure=None):
        flow = Mock(redirect_uri=None, credentials=SimpleNamespace(valid=True))
        flow.authorization_url.return_value = (url, 'PRIVATE_STATE')
        auth = SimpleNamespace(InstalledAppFlow=Mock(), PUBLIC_SCOPES=['scope'],
            REMOTE_REDIRECT_URI='https://sdk.cloud.google.com/applicationdefaultauthcode.html',
            AuthProvider=SimpleNamespace(OAUTH2='oauth2'), _run_remote_flow=Mock())
        auth.InstalledAppFlow.from_client_config.return_value = flow
        session = SimpleNamespace(credentials=flow.credentials, close=Mock())
        def credentials(**kwargs):
            self.assertEqual(kwargs, {'provider': 'oauth2'})
            self.assertIs(auth._run_remote_flow({'installed': {}}), flow.credentials)
            print('PRIVATE_DEPENDENCY_OUTPUT')
            return session
        auth.get_credentials = credentials
        backend = Mock()
        backend.get_consumption_user_info.side_effect = failure
        backend.get_consumption_user_info.return_value = object()
        client = SimpleNamespace(Prod=Mock(), Client=Mock(return_value=backend),
            Accelerator=[SimpleNamespace(value=v) for v in ('NONE','T4','L4','G4','H100','A100','V5E1')])
        return auth, client, flow, session, backend

    def test_private_code_is_consumed_once_and_only_allowlisted_result_is_emitted(self):
        auth, client, flow, session, backend = self.adapter()
        old = auth._run_remote_flow
        output = io.StringIO()
        serve(auth, client, io.StringIO('PRIVATE_CODE\n'), output)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(set(lines[0]), {'challenge'})
        self.assertEqual(lines[1], {'verified': {'authenticated': True,
            'gpu_types': ['A100','G4','H100','L4','T4'], 'availability': 'unknown'}})
        self.assertNotIn('PRIVATE_CODE', output.getvalue())
        self.assertNotIn('PRIVATE_DEPENDENCY_OUTPUT', output.getvalue())
        flow.fetch_token.assert_called_once_with(code='PRIVATE_CODE', timeout=30)
        backend.get_consumption_user_info.assert_called_once_with()
        self.assertIs(auth._run_remote_flow, old)
        session.close.assert_called_once_with()

    def test_failed_read_only_verification_never_emits_success_or_provider_error(self):
        auth, client, _, session, _ = self.adapter(failure=RuntimeError('PRIVATE_PROVIDER_ERROR'))
        output = io.StringIO()
        with self.assertRaises(RuntimeError):
            serve(auth, client, io.StringIO('PRIVATE_CODE\n'), output)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertNotIn('PRIVATE_PROVIDER_ERROR', output.getvalue())
        session.close.assert_called_once_with()

    def test_unsafe_challenge_and_unframed_or_control_input_never_exchange(self):
        for url, code in [('https://evil.invalid/auth', 'code\n'),
                          ('https://accounts.google.com/o/oauth2/auth', 'code'),
                          ('https://accounts.google.com/o/oauth2/auth', 'code\r\n'),
                          ('https://accounts.google.com/o/oauth2/auth', 'x'*4097+'\n')]:
            with self.subTest(url=url, size=len(code)):
                auth, client, flow, _, _ = self.adapter(url=url)
                with self.assertRaises(ValueError):
                    serve(auth, client, io.StringIO(code), io.StringIO())
                flow.fetch_token.assert_not_called()

    def test_unknown_gpu_contract_fails_after_verification_without_allocation(self):
        auth, client, _, _, backend = self.adapter()
        client.Accelerator = [SimpleNamespace(value='T4')]
        output = io.StringIO()
        with self.assertRaises(ValueError):
            serve(auth, client, io.StringIO('code\n'), output)
        self.assertNotIn('verified', output.getvalue())
        self.assertEqual([call[0] for call in backend.method_calls], ['get_consumption_user_info'])

    def test_installed_official_oauth_cache_contract_without_network(self):
        try:
            from colab_cli import auth, client
        except ImportError:
            self.skipTest('Optional pinned private Colab environment is not installed in this interpreter.')
        flow = Mock()
        flow.authorization_url.return_value = ('https://accounts.google.com/o/oauth2/auth?state=PRIVATE_STATE', 'PRIVATE_STATE')
        flow.credentials = SimpleNamespace(valid=True, to_json=lambda: '{"token":"PRIVATE_TOKEN"}')
        session = SimpleNamespace(credentials=flow.credentials, close=Mock())
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder)/'cache/token.json'
            output = io.StringIO()
            with patch.object(auth, 'TOKEN_CONFIG_PATH', str(token)), \
                    patch.object(auth.InstalledAppFlow, 'from_client_config', return_value=flow), \
                    patch.object(auth.requests, 'AuthorizedSession', return_value=session), \
                    patch.object(client.Client, 'get_consumption_user_info', return_value=object()):
                serve(auth, client, io.StringIO('PRIVATE_CODE\n'), output)
            self.assertIn('PRIVATE_TOKEN', token.read_text())
            self.assertNotIn('PRIVATE_TOKEN', output.getvalue())
            self.assertNotIn('PRIVATE_CODE', output.getvalue())
            self.assertTrue(json.loads(output.getvalue().splitlines()[-1])['verified']['authenticated'])


if __name__ == '__main__':
    unittest.main()
