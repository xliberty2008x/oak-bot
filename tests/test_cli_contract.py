import unittest

from oak.cli_contract import capability, metadata, status


class CLIContractTests(unittest.TestCase):
    def test_unavailable_is_explicit_and_never_offers_native_attach(self):
        self.assertEqual(capability(), {'provider': 'colab', 'available': False,
            'reason': 'private_cli_unavailable', 'native_session_attach': False})
        self.assertFalse(capability(1)['available'])

    def test_provider_output_is_not_an_arbitrary_public_dictionary(self):
        self.assertEqual(metadata({'authenticated': True, 'gpu_types': ['T4', 'A100'], 'availability': 'unknown'}),
                         {'authenticated': True, 'gpu_types': ['T4', 'A100'], 'availability': 'unknown'})
        for value in ({'authenticated': True, 'gpu_types': [], 'token': 'canary'},
                      {'authenticated': False, 'gpu_types': []},
                      {'authenticated': True, 'gpu_types': ['user@example.test']},
                      {'authenticated': True, 'gpu_types': ['PERSONAL_TOKEN'], 'availability': 'unknown'},
                      {'authenticated': True, 'gpu_types': ['T4'], 'availability': 'available'}):
            with self.assertRaises(ValueError):
                metadata(value)

    def test_status_rejects_private_or_nonfinite_fields(self):
        good = {'request_id': 'a'*32, 'session_id': 'b'*32, 'runtime_epoch': 'epoch',
                'provider': 'colab', 'state': 'waiting', 'cleanup': 'waiting', 'session_expires': 0}
        self.assertEqual(status(good), good)
        for change in ({'stdout': 'canary'}, {'session_expires': float('nan')}, {'state': 'owner_confirmed'}):
            with self.assertRaises(ValueError):
                status({**good, **change})
