import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.cli_server import settings


class PrivateCLIServerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root/'state').mkdir(mode=0o700)
        (self.root/'socket').mkdir(mode=0o750)
        self.provider = self.root/'provider.json'
        self.provider.write_text(json.dumps({'python': str(self.root/'venv/bin/python')}))
        self.config = {'origin': 'https://private.invalid', 'parent_origin': 'https://panel.invalid',
            'gateway_uid': os.geteuid()+1, 'broker_uid': os.geteuid(),
            'socket': str(self.root/'socket/control.sock'), 'port': 18767,
            'execution': {'profile': '/etc/synthetic-profile.json'},
            'state_dir': str(self.root/'state'), 'provider_manifest': str(self.provider)}
        self.profile = {'code_root': str(Path(__file__).resolve().parents[1]),
            'executor_uid': os.geteuid()+2,
            'workspace': str(self.root/'executor/workspace'), 'runtime_home': str(self.root/'executor/runtime'),
            'desktop_state': str(self.root/'executor/desktop'), 'readonly': ['/usr']}
        self.path = self.root/'broker.json'
        self.trust = patch('oak.cli_server.immutable', side_effect=lambda p: Path(p))
        self.trust.start()
        self.addCleanup(self.trust.stop)
        self.load = patch('oak.cli_server.load_profile', side_effect=lambda value: self.profile)
        self.load.start()
        self.addCleanup(self.load.stop)

    def read(self):
        self.path.write_text(json.dumps(self.config))
        return settings(self.path)

    def test_dedicated_identity_and_private_resources_are_accepted(self):
        value, code = self.read()
        self.assertEqual(value, self.config)
        self.assertEqual(str(code), self.profile['code_root'])

    def test_extra_command_fields_wrong_identity_and_shared_origin_are_rejected(self):
        changes = [('argv', ['untrusted']), ('broker_uid', os.geteuid()+7),
                   ('gateway_uid', os.geteuid()), ('origin', 'https://panel.invalid')]
        for key, value in changes:
            with self.subTest(key=key):
                original = dict(self.config)
                self.config[key] = value
                with self.assertRaises(ValueError):
                    self.read()
                self.config = original

    def test_private_state_or_socket_permissions_cannot_be_relaxed(self):
        for name, mode in [('state', 0o750), ('socket', 0o770)]:
            with self.subTest(name=name):
                (self.root/name).chmod(mode)
                with self.assertRaises(ValueError):
                    self.read()
                (self.root/name).chmod(0o700)

    def test_symlink_state_is_rejected_without_following_it(self):
        (self.root/'state').rmdir()
        (self.root/'state').symlink_to(self.root/'socket', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.read()

    def test_private_resources_inside_any_executor_mount_are_rejected(self):
        for exposed in [self.root/'state', self.root/'socket', self.path,
                        self.provider, self.root/'venv']:
            with self.subTest(exposed=exposed):
                self.profile['readonly'] = [str(exposed)]
                with self.assertRaises(ValueError):
                    self.read()

    def test_stale_service_release_and_non_dedicated_socket_are_rejected(self):
        self.profile['code_root'] = str(self.root/'old-release')
        with self.assertRaises(ValueError):
            self.read()
        self.profile['code_root'] = str(Path(__file__).resolve().parents[1])
        self.config['socket'] = str(self.root/'socket/other.sock')
        with self.assertRaises(ValueError):
            self.read()

    def test_executor_identity_and_parent_traversal_cannot_bypass_boundaries(self):
        self.config['gateway_uid'] = self.profile['executor_uid']
        with self.assertRaises(ValueError):
            self.read()
        self.config['gateway_uid'] = os.geteuid()+1
        self.config['state_dir'] = str(self.root/'state/../state')
        with self.assertRaises(ValueError):
            self.read()


if __name__ == '__main__':
    unittest.main()
