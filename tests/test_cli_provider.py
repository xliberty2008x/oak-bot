import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.cli_provider import ADAPTER_MODULES, Provider, immutable, manifest
from oak.colab_auth import UPSTREAM_COMMIT


class PrivateProviderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.prefix = self.root/'venv'
        self.python = self.prefix/'bin/python'
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b'synthetic interpreter')
        (self.prefix/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
        self.direct = self.prefix/'lib/python3.12/site-packages/google_colab_cli-fixture.dist-info/direct_url.json'
        self.direct.parent.mkdir(parents=True)
        self.direct.write_text(json.dumps({'vcs_info': {'commit_id': UPSTREAM_COMMIT}}))
        self.source = self.root/'release/oak/colab_auth.py'
        self.source.parent.mkdir(parents=True)
        for name in ADAPTER_MODULES:
            (self.source.parent/(name + '.py')).write_text('# synthetic adapter import')
        # Synthetic temporary trees deliberately are not trusted root installs.
        # Ownership enforcement has its own fail-closed check below.
        self.trust = patch('oak.cli_provider.immutable', side_effect=lambda p, **kw: Path(p))
        self.trust.start()
        self.addCleanup(self.trust.stop)
        self.path = self.root/'provider.json'
        self.path.write_text(json.dumps(manifest(self.python)))

    def provider(self):
        return Provider(self.path, self.source.parents[1])

    def test_fixed_command_and_exact_inventory(self):
        provider = self.provider()
        self.assertTrue(provider.ready())
        self.assertEqual(provider.command, (str(self.python), '-I', str(self.source)))
        value = json.loads(self.path.read_text())
        value['argv'] = ['untrusted']
        self.path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.provider()

    def test_changed_dependency_added_file_or_adapter_revokes_readiness(self):
        for change in ('dependency', 'added', 'adapter', 'permissions'):
            with self.subTest(change=change):
                provider = self.provider()
                if change == 'dependency':
                    self.python.write_bytes(b'replaced interpreter')
                elif change == 'added':
                    (self.prefix/'new-module.py').write_text('unexpected')
                elif change == 'adapter':
                    self.source.write_text('changed adapter')
                else:
                    self.root.chmod(0o755 if self.root.stat().st_mode & 0o777 == 0o700 else 0o700)
                self.assertFalse(provider.ready())
                self.path.write_text(json.dumps(manifest(self.python)))

    def test_wrong_source_pin_global_packages_or_external_symlink_are_rejected(self):
        self.direct.write_text(json.dumps({'vcs_info': {'commit_id': '0'*40}}))
        with self.assertRaises(ValueError):
            manifest(self.python)
        self.direct.write_text(json.dumps({'vcs_info': {'commit_id': UPSTREAM_COMMIT}}))
        (self.prefix/'pyvenv.cfg').write_text('include-system-site-packages = true\n')
        with self.assertRaises(ValueError):
            manifest(self.python)
        (self.prefix/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
        (self.prefix/'unapproved').symlink_to('/usr/bin/true')
        with self.assertRaises(ValueError):
            manifest(self.python)

    def test_stale_digest_manifest_is_rejected_before_launch(self):
        self.python.write_bytes(b'mutated')
        with self.assertRaises(ValueError):
            self.provider()

    def test_global_site_setting_is_a_single_explicit_false_value(self):
        config = self.prefix/'pyvenv.cfg'
        for text in ('# include-system-site-packages = false\ninclude-system-site-packages = true\n',
                     'include-system-site-packages = false\ninclude-system-site-packages = true\n',
                     'include-system-site-packages = false\ninclude-system-site-packages = false\n',
                     '# include-system-site-packages = false\n',
                     'home = /synthetic/python\n'):
            with self.subTest(config=text):
                config.write_text(text)
                with self.assertRaises(ValueError):
                    manifest(self.python)
        config.write_text('# comment\n Include-System-Site-Packages = FALSE \n')
        self.assertTrue(manifest(self.python)['files'])

    def test_actual_untrusted_temporary_tree_cannot_be_approved(self):
        with self.assertRaises(ValueError):
            immutable(self.python)

    def test_each_adapter_import_mutation_revokes_readiness(self):
        for name in ADAPTER_MODULES:
            with self.subTest(module=name):
                provider = self.provider()
                (self.source.parent/(name + '.py')).write_text('# changed existing import')
                self.assertFalse(provider.ready())

    def test_adapter_import_permissions_and_symlinks_are_checked(self):
        original_stat = Path.lstat
        for name in ADAPTER_MODULES:
            source = self.source.parent/(name + '.py')
            for mutation in ('writable', 'owner', 'symlink'):
                with self.subTest(module=name, mutation=mutation):
                    def root_stat(path):
                        fields = list(original_stat(path))
                        # A synthetic root-owned tree; only this import is unsafe.
                        fields[4] = 0
                        fields[0] &= ~0o022
                        if path == source:
                            if mutation == 'writable':
                                fields[0] |= 0o020
                            elif mutation == 'owner':
                                fields[4] = 1000
                            else:
                                fields[0] = 0o120777
                        return os.stat_result(fields)
                    with patch('oak.cli_provider.immutable', wraps=immutable), \
                            patch.object(Path, 'lstat', autospec=True, side_effect=root_stat):
                        with self.assertRaises(ValueError):
                            self.provider()

    def test_existing_and_later_bytecode_or_import_shadow_are_rejected(self):
        for relative in ('__pycache__/cli_contract.cpython-312.pyc',
                         'cli_process.pyc', 'nested/cache.pyo',
                         'cli_contract/__init__.py'):
            with self.subTest(path=relative):
                target = self.source.parent/relative
                provider = self.provider()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'not approved')
                self.assertFalse(provider.ready())
                with self.assertRaises(ValueError):
                    self.provider()
                target.unlink()
                if target.parent != self.source.parent:
                    target.parent.rmdir()


if __name__ == '__main__':
    unittest.main()
