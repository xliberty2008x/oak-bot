"""Sealing permissions use a real temporary output and synthetic root trust."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class SealProviderTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1]/'scripts/seal-cli-provider.py'
        spec = importlib.util.spec_from_file_location('seal_cli_provider_fixture', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name)/'provider.json'
        self.value = {'upstream_commit': 'synthetic-reviewed-revision',
                      'python': '/synthetic/bin/python', 'files': {'/synthetic/module.py': 'digest'}}
        self.arguments = ['seal-cli-provider.py', '--python', '/synthetic/bin/python',
                          '--output', str(self.output)]

    def test_restrictive_umask_keeps_manifest_readable_without_overwriting(self):
        original_umask = os.umask(0o077)
        try:
            with patch.object(self.module.sys, 'argv', self.arguments), \
                    patch.object(self.module.os, 'geteuid', return_value=0), \
                    patch.object(self.module, 'immutable', side_effect=lambda path: path), \
                    patch.object(self.module, 'manifest', return_value=self.value), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.module.main()
                before = self.output.read_bytes()
                self.assertEqual(json.loads(before), self.value)
                self.assertEqual(self.output.stat().st_mode & 0o777, 0o644)
                self.assertFalse(json.loads(output.getvalue())['authentication_started'])
                with self.assertRaises(FileExistsError):
                    self.module.main()
                self.assertEqual(self.output.read_bytes(), before)
                self.assertEqual(self.output.stat().st_mode & 0o777, 0o644)
        finally:
            os.umask(original_umask)

    def test_non_root_fails_before_inventory_or_file_creation(self):
        with patch.object(self.module.sys, 'argv', self.arguments), \
                patch.object(self.module.os, 'geteuid', return_value=1000), \
                patch.object(self.module, 'manifest') as inventory:
            with self.assertRaises(PermissionError):
                self.module.main()
        inventory.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
