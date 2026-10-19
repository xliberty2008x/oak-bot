import asyncio
import base64
import hashlib
import io
import os
import socket
import shutil
import subprocess
import sys
from types import SimpleNamespace
from oak import executor_worker as worker
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from oak.executor import (ExecutionClient, ExecutionUnavailable, boundary_check,
                          decode, encode, policy_digest, sandbox_command)


SPEC = {
    'source': '/srv/source', 'executor_user': 'oak-exec', 'executor_uid': 1900,
    'executor_gid': 1900, 'gateway_user': 'oak',
    'workspace': '/srv/oak/workspace', 'runtime_home': '/srv/oak/runtime',
    'desktop_state': '/srv/oak/executor-desktop',
    'python': '/usr/bin/python3', 'codex': '/opt/codex/bin/codex',
    'readonly': ['/usr', '/bin', '/lib', '/opt/codex', '/etc/resolv.conf'],
    'dns_addresses': ['127.0.0.53'], 'host_addresses': ['203.0.113.10'],
    'forbidden': ['/srv/oak/state', '/srv/oak/broker', '/srv/oak/private.json'],
}


def setup_module():
    path = Path(__file__).resolve().parents[1] / 'scripts/setup-execution-boundary.py'
    spec = importlib.util.spec_from_file_location('execution_setup', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PolicyTests(unittest.TestCase):
    def test_missing_provisioning_is_closed(self):
        self.assertFalse(boundary_check({'profile': '/nonexistent/oak-profile'}))
        self.assertFalse(boundary_check({}))

    def test_receipt_must_bind_current_boot_digest_and_all_checks(self):
        required = ('uid', 'private_pid', 'no_privileges', 'code_readonly',
                    'trusted_files_denied', 'host_proc_denied', 'sockets_denied',
                    'private_network_denied', 'native_policy', 'firewall_active')
        with tempfile.TemporaryDirectory() as temp:
            receipt_path = Path(temp) / 'receipt'
            profile = {'receipt': str(receipt_path), 'executor_uid': 1900, 'policy_digest': 'digest'}
            receipt = {'version': 1, 'executor_uid': 1900, 'policy_digest': 'digest',
                       'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       'checks': {name: True for name in required}}
            with patch('oak.executor.load_profile', return_value=profile), patch('oak.executor.immutable', return_value=True):
                receipt_path.write_bytes(encode(receipt))
                self.assertTrue(boundary_check({'profile': '/synthetic'}))
                for changes in ({'boot_id': 'old'}, {'policy_digest': 'old'},
                                {'executor_uid': 1000}, {'checks': {}},
                                {'checks': {**receipt['checks'], 'uid': 1}}):
                    receipt_path.write_bytes(encode({**receipt, **changes}))
                    self.assertFalse(boundary_check({'profile': '/synthetic'}))

    def test_json_rejects_nonfinite_duplicate_and_partial_messages(self):
        for value in (b'{"a":NaN}\n', b'{"a":1e999}\n', b'{"a":1,"a":2}\n', b'{}'):
            with self.assertRaises(ValueError):
                decode(value)
        with self.assertRaises(ValueError):
            encode({'value': float('inf')})

    def test_policy_digest_covers_mounts_not_its_own_digest(self):
        value = {'workspace': '/srv/work', 'policy_digest': 'old'}
        digest = policy_digest(value)
        value['policy_digest'] = 'new'
        self.assertEqual(digest, policy_digest(value))
        value['workspace'] = '/srv/other'
        self.assertNotEqual(digest, policy_digest(value))

    def test_fixed_sandbox_has_no_trusted_roots_environment_or_display(self):
        profile = {**SPEC, 'code_root': '/opt/oak-executor/release',
                   'path': '/usr/bin:/bin'}
        for mode in ('worker', 'native'):
            command = sandbox_command(profile, mode, '/etc/oak-executor/profile.json')
            self.assertIn('--unshare-pid', command)
            self.assertIn('--unshare-user', command)
            self.assertIn('--clearenv', command)
            self.assertIn('--die-with-parent', command)
            self.assertNotIn('--share-net', command)
            self.assertNotIn('--unshare-net', command)  # nft UID policy; honest semantics
            self.assertNotIn('DISPLAY', command)
            self.assertNotIn('XAUTHORITY', command)
            for forbidden in SPEC['forbidden']:
                self.assertNotIn(forbidden, command)
            self.assertEqual(SPEC['desktop_state'] in command, mode == 'worker')

    def test_spec_rejects_trusted_roots_shared_identity_and_writable_toolchain(self):
        setup = setup_module()
        setup.validate_spec(copy.deepcopy(SPEC))
        mutations = [ {'readonly': ['/usr', '/home']},
                      {'workspace': '/srv/oak'},
                      {'runtime_home': '/srv/oak/workspace/nested'},
                      {'executor_user': 'oak'},
                      {'forbidden': []},
                      {'path': '/srv/oak/workspace:/usr/bin'},
                      {'host_addresses': []} ]
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                setup.validate_spec({**copy.deepcopy(SPEC), **changes})

    def test_egress_is_scoped_to_uid_with_dns_only_exception(self):
        rules = setup_module().firewall(SPEC)
        self.assertIn('meta skuid 1900 jump executor', rules)
        self.assertNotIn('meta skuid !=', rules)
        self.assertIn(' chain executor {', rules)
        self.assertIn('ip daddr 127.0.0.53 tcp dport 53 accept', rules)
        self.assertLess(rules.index('udp dport 53 accept'), rules.index('fib daddr type local reject'))
        for address in ('127.0.0.0/8', '10.0.0.0/8', '169.254.0.0/16',
                        '203.0.113.10', 'fc00::/7', 'fe80::/10'):
            self.assertIn(address, rules)
        self.assertIn('ip6 daddr != 2000::/3 reject', rules)
        self.assertNotIn('flush ruleset', rules)

    def test_units_have_fixed_unprivileged_commands_and_kill_lifecycle(self):
        units = setup_module().units(SPEC, '/opt/oak-executor/oak/executor.py', '/opt/oak-executor/scripts/setup.py')
        for mode in ('native', 'worker'):
            service = units[f'oak-executor-{mode}@.service']
            self.assertIn('User=oak-exec', service)
            self.assertIn('NoNewPrivileges=yes', service)
            self.assertIn('KillMode=control-group', service)
            self.assertIn('StandardError=null', service)
            self.assertNotIn('%', service)
            self.assertNotIn('sudo', service)
            self.assertIn('SocketMode=0600', units[f'oak-executor-{mode}.socket'])

    def test_render_does_not_execute_or_copy_account_data(self):
        setup = setup_module()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            (source / 'oak').mkdir(parents=True)
            for name in ('executor.py', 'executor_worker.py'):
                (source / 'oak' / name).write_text('# synthetic reviewed code\n')
            (source / 'oak/login_web').mkdir()
            (source / 'oak/login_web/index.html').write_text('<p>Oak</p>')
            (source / 'private-account.json').write_text('private marker')
            with patch.object(setup, 'run', side_effect=AssertionError('must not run commands')):
                profile = setup.render({**SPEC, 'source': str(source)}, Path(temp) / 'bundle')
            self.assertEqual(profile['policy_digest'], policy_digest(profile))
            files = list((Path(temp) / 'bundle').rglob('*'))
            self.assertFalse(any(path.name == 'private-account.json' for path in files))
            self.assertTrue(any(path.name == 'index.html' for path in files))
            self.assertTrue(all('private marker' not in path.read_text() for path in files if path.is_file()))

    def test_render_packages_exact_harness2_core_license_and_isolated_release_import(self):
        setup = setup_module()
        source = Path(__file__).resolve().parents[1]
        vendor = source / 'third_party/harness2'
        locked = json.loads((vendor/'UPSTREAM.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)/'bundle'
            with patch.object(setup, 'run', side_effect=AssertionError('render must not provision')):
                profile = setup.render({**SPEC, 'source': str(source)}, destination)
            release = destination / profile['code_root'].lstrip('/')
            packaged = release / 'third_party/harness2'
            expected = {*locked['files'], 'UPSTREAM.json'}
            self.assertEqual({path.relative_to(packaged).as_posix() for path in packaged.rglob('*') if path.is_file()},
                             expected)
            for name in expected:
                with self.subTest(file=name):
                    data = (packaged/name).read_bytes()
                    self.assertEqual(data, (vendor/name).read_bytes())
                    installed = str(Path(profile['code_root'])/'third_party/harness2'/name)
                    self.assertEqual(profile['manifest'][installed], hashlib.sha256(data).hexdigest())
            self.assertEqual(profile['readonly'], SPEC['readonly'])
            self.assertEqual(profile['policy_digest'], policy_digest(profile))
            # A fresh isolated interpreter cannot fall back to this checkout's
            # vendor directory or an already imported harness2 package.
            code = ('import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
                    'from oak.harness2_upstream import upstream; core=upstream(); '
                    'print(json.dumps({"commit":core.commit,"module":str(Path(core.schedule.__file__).resolve())}))')
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(release)],
                                    cwd=temp, check=True, capture_output=True, text=True, timeout=20)
            imported = json.loads(result.stdout)
            self.assertEqual(imported['commit'], setup.HARNESS2_COMMIT)
            self.assertEqual(Path(imported['module']), packaged/'harness2/core/schedule.py')

    def test_harness2_packaging_rejects_missing_changed_linked_or_extra_code(self):
        setup = setup_module()
        original = Path(__file__).resolve().parents[1]/'third_party/harness2'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            vendor = source/'third_party/harness2'
            (source/'oak').mkdir(parents=True)
            for name in ('executor.py', 'executor_worker.py', 'harness2_upstream.py'):
                (source/'oak'/name).write_text('# local packaging protocol fixture\n')
            for change in ('missing_vendor', 'manifest', 'core', 'license', 'symlink', 'hardlink', 'extra_module'):
                with self.subTest(change=change):
                    if vendor.exists():
                        shutil.rmtree(vendor)
                    if change != 'missing_vendor':
                        shutil.copytree(original, vendor, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                    if change == 'manifest':
                        (vendor/'UPSTREAM.json').write_text('{}')
                    elif change == 'core':
                        (vendor/'harness2/core/prompts.py').write_text('# changed\n')
                    elif change == 'license':
                        (vendor/'LICENSE').unlink()
                    elif change == 'symlink':
                        (vendor/'LICENSE').unlink()
                        (vendor/'LICENSE').symlink_to(original/'LICENSE')
                    elif change == 'hardlink':
                        os.link(vendor/'LICENSE', vendor/'linked-license')
                    elif change == 'extra_module':
                        (vendor/'harness2/core/unreviewed.py').write_text('# unsupported\n')
                    with self.assertRaises(ValueError):
                        setup.render({**SPEC, 'source': str(source)}, Path(temp)/('bundle-'+change))

    def test_harness2_packaging_omits_unlocked_data_and_refuses_directory_symlink(self):
        setup = setup_module()
        original = Path(__file__).resolve().parents[1]/'third_party/harness2'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source'
            vendor = source/'third_party/harness2'
            vendor.parent.mkdir(parents=True)
            vendor.symlink_to(original, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'directories cannot be symlinks'):
                setup.harness2_files(source)
            vendor.unlink()
            shutil.copytree(original, vendor, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (vendor/'unrelated-private.json').write_text('PACKAGE_PRIVATE_CANARY')
            files = setup.harness2_files(source)
            self.assertFalse(any('unrelated-private' in name or b'PACKAGE_PRIVATE_CANARY' in data
                                 for name, data in files.items()))

    def test_migration_refuses_active_gateway_and_existing_destination(self):
        import fcntl
        setup = setup_module()
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            source = state / 'desktop'
            source.mkdir(parents=True)
            destination = Path(temp) / 'executor'
            destination.mkdir()
            (source / 'desktop.lock').touch()
            spec = {**SPEC, 'migrate_desktop_from': str(source), 'desktop_state': str(destination)}
            with (state / 'gateway.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with self.assertRaises(BlockingIOError):
                    setup.migrate_desktop(spec)
            self.assertTrue(source.is_dir())
            (destination / 'desktop').mkdir()
            with self.assertRaises(ValueError):
                setup.migrate_desktop(spec)

    def test_data_ownership_migration_requires_idle_and_validates_all_trees_first(self):
        import fcntl
        setup = setup_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('workspace', 'runtime', 'state'):
                (root / name).mkdir()
            lock_path = root / 'state/gateway.lock'
            lock_path.touch()
            spec = {**SPEC, 'workspace': str(root / 'workspace'), 'runtime_home': str(root / 'runtime'),
                    'gateway_state': str(root / 'state'), 'migrate_data_ownership': True}
            with lock_path.open('r+') as lock, patch.object(setup, '_chown_tree') as change:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with self.assertRaises(BlockingIOError):
                    setup.migrate_data_ownership(spec)
                change.assert_not_called()
            (root / 'runtime/auth-canary').write_bytes(b'synthetic')
            os.link(root / 'runtime/auth-canary', root / 'outside-hardlink')
            with patch.object(setup, '_chown_tree') as change:
                with self.assertRaises(ValueError):
                    setup.migrate_data_ownership(spec)
                change.assert_not_called()  # Even the valid workspace is untouched.

    def test_data_ownership_migration_preserves_bytes_and_never_follows_symlinks(self):
        setup = setup_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('workspace', 'runtime', 'state'):
                (root / name).mkdir()
            (root / 'state/gateway.lock').touch()
            (root / 'state/private').write_bytes(b'private-canary')
            (root / 'workspace/input').write_bytes(b'preserved')
            (root / 'workspace/link').symlink_to(root / 'state/private')
            spec = {**SPEC, 'workspace': str(root / 'workspace'), 'runtime_home': str(root / 'runtime'),
                    'gateway_state': str(root / 'state'), 'migrate_data_ownership': True}
            with patch.object(setup.os, 'chown') as change:
                setup.migrate_data_ownership(spec)
                self.assertTrue(change.call_args_list)
                for call in change.call_args_list:
                    self.assertFalse(call.kwargs['follow_symlinks'])
                    self.assertFalse(Path(call.args[0]).is_relative_to(root / 'state'))
            self.assertEqual((root / 'workspace/input').read_bytes(), b'preserved')
            self.assertEqual((root / 'state/private').read_bytes(), b'private-canary')

    def test_desktop_conflict_is_checked_before_any_data_ownership_change(self):
        import fcntl
        setup = setup_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('workspace', 'runtime', 'state/desktop', 'executor/desktop'):
                (root / name).mkdir(parents=True)
            (root / 'state/gateway.lock').touch()
            (root / 'state/desktop/desktop.lock').touch()
            spec = {**SPEC, 'workspace': str(root / 'workspace'), 'runtime_home': str(root / 'runtime'),
                    'gateway_state': str(root / 'state'), 'desktop_state': str(root / 'executor'),
                    'migrate_data_ownership': True, 'migrate_desktop_from': str(root / 'state/desktop')}
            with patch.object(setup, '_chown_tree') as change:
                with self.assertRaises(ValueError):
                    setup.migrate_data_ownership(spec)
                change.assert_not_called()
            (root / 'executor/desktop').rmdir()
            with (root / 'state/desktop/desktop.lock').open('r+') as lock, patch.object(setup, '_chown_tree') as change:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with self.assertRaises(BlockingIOError):
                    setup.migrate_data_ownership(spec)
                change.assert_not_called()


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket = str(Path(self.temp.name) / 'worker.sock')
        self.profile = {'worker_socket': self.socket, 'executor_uid': 1900, 'policy_digest': 'digest'}
        self.tasks = set()
        self.requests = []
        self.behavior = 'normal'

        async def handler(reader, writer):
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                while raw := await reader.readline():
                    message = decode(raw)
                    self.requests.append(message)
                    if self.behavior == 'block' and message['op'] != 'hello':
                        await reader.read()
                        break
                    result = {'version': 1, 'uid': 1900, 'policy_digest': 'digest'}
                    response = {'id': message['id'], 'ok': True, 'result': result}
                    if self.behavior == 'secret_error' and message['op'] != 'hello':
                        response = {'id': message['id'], 'ok': False, 'error': 'secret=private-token'}
                    elif self.behavior == 'browser_preflight' and message['op'] == 'browser.run':
                        result = await self.upload_worker.dispatch(message['op'], message['args'])
                        response = {'id': message['id'], 'ok': True, 'result': result}
                    writer.write(encode(response))
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                self.tasks.discard(task)

        self.server = await asyncio.start_unix_server(handler, self.socket)
        with patch('oak.executor.require_boundary', return_value=self.profile):
            self.client = await ExecutionClient({'profile': '/synthetic'}).start()

    async def asyncTearDown(self):
        await self.client.close()
        self.server.close()
        await self.server.wait_closed()
        if self.tasks:
            await asyncio.wait_for(asyncio.gather(*self.tasks), 2)
        self.temp.cleanup()

    async def test_hello_and_serialized_calls(self):
        await asyncio.gather(self.client.call('file.read', {'path': 'a'}),
                             self.client.call('file.read', {'path': 'b'}))
        self.assertEqual([item['op'] for item in self.requests], ['hello', 'file.read', 'file.read'])

    async def test_remote_exception_message_is_never_exposed(self):
        self.behavior = 'secret_error'
        with self.assertRaisesRegex(ExecutionUnavailable, '^ExecutionError$'):
            await self.client.call('browser.run', {})

    async def test_upload_preflight_evidence_survives_worker_wire_instead_of_class_only_error(self):
        from oak.browser import BrowserTools, MAX_UPLOAD
        source = Path(self.temp.name) / 'private-file-canary.mp4'
        with source.open('wb') as stream:
            stream.truncate(MAX_UPLOAD + 1)
        self.upload_worker = worker.Worker({'workspace': self.temp.name, 'runtime_home': self.temp.name})
        self.addAsyncCleanup(self.upload_worker.close)
        self.behavior = 'browser_preflight'
        with patch.object(BrowserTools, '_start', new_callable=AsyncMock) as start:
            result = await self.client.call('browser.run',
                                            {'action': 'upload', 'selector': '#file', 'files': [str(source)]})
        self.assertFalse(result['ok'])
        self.assertEqual(result['outcome'], 'not_attempted')
        self.assertEqual(result['error']['code'], 'payload_too_large')
        self.assertEqual(result['error']['stage'], 'upload_preflight')
        self.assertEqual(result['error']['observed_bytes'], MAX_UPLOAD + 1)
        self.assertEqual(result['error']['max_bytes'], MAX_UPLOAD)
        self.assertNotIn(source.name, json.dumps(result))
        start.assert_not_awaited()

    async def test_cancel_closes_connection_instead_of_leaving_adapter_running(self):
        self.behavior = 'block'
        task = asyncio.create_task(self.client.call('research.fetch', {'url': 'https://example.org'}))
        while len(self.requests) < 2:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.client.writer)
        self.behavior = 'normal'
        with patch('oak.executor.require_boundary', return_value=self.profile) as boundary:
            await self.client.call('computer.status', {})
            boundary.assert_called_once()
        self.assertEqual([item['op'] for item in self.requests],
                         ['hello', 'research.fetch', 'hello', 'computer.status'])

    async def test_unknown_operation_never_sent(self):
        with self.assertRaises(ValueError):
            await self.client.call('shell', {'command': 'anything'})
        self.assertEqual(len(self.requests), 1)


class RuntimeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unprovisioned_runtime_never_starts_local_codex(self):
        from oak.runtime import RuntimeClient
        client = RuntimeClient(execution={'profile': '/nonexistent/oak-profile'})
        with patch('oak.runtime.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaises(ExecutionUnavailable):
                await client.start()
            spawn.assert_not_called()

    async def test_runtime_uses_fixed_proxy_and_removes_shared_display(self):
        from oak.runtime import RuntimeClient
        from types import SimpleNamespace
        process = SimpleNamespace(returncode=0)
        client = RuntimeClient(execution={'profile': '/synthetic'})
        replies = [{'codexHome': '/synthetic/runtime'}, {'account': {'type': 'chatgpt'}}]
        with patch.dict('os.environ', {'DISPLAY': ':90', 'XAUTHORITY': '/private/cookie',
                                      'OPENAI_API_KEY': 'dummy', 'CODEX_API_KEY': 'dummy'}), \
                patch('oak.executor.runtime_command', return_value=['/fixed/proxy']) as command, \
                patch('oak.runtime.asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=process) as spawn, \
                patch.object(client, 'request', new_callable=AsyncMock, side_effect=replies), \
                patch.object(client, '_send', new_callable=AsyncMock), \
                patch.object(client, '_read_loop', new_callable=AsyncMock), \
                patch.object(client, '_drain_stderr', new_callable=AsyncMock):
            await client.start()
            self.assertEqual(spawn.call_args.args, ('/fixed/proxy',))
            self.assertEqual(spawn.call_args.kwargs['cwd'], '/')
            environment = spawn.call_args.kwargs['env']
            self.assertFalse({'DISPLAY', 'XAUTHORITY', 'OPENAI_API_KEY', 'CODEX_API_KEY'} & environment.keys())
            original = command.call_args.args[1]
            self.assertEqual(original[:4], ['codex', 'app-server', '--listen', 'stdio://'])
            self.assertIn('forced_login_method="chatgpt"', original)
            await client.close()



class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.home = self.root / 'runtime'
        (self.home / 'generated_images').mkdir(parents=True)
        self.profile = {'workspace': str(self.workspace), 'runtime_home': str(self.home), 'policy_digest': '0' * 64}
        self.files = worker.FileTransfers(self.profile)

    def tearDown(self):
        self.files.close_all()
        self.temp.cleanup()

    def test_atomic_write_snapshot_and_empty_file(self):
        result = self.files.write('inbox/example.txt', base64.b64encode(b'hello').decode())
        self.assertEqual(result, {'path': str(self.workspace / 'inbox/example.txt'), 'size': 5})
        self.assertEqual(self.files.read('inbox/example.txt')['data'], 'aGVsbG8=')
        self.files.write('empty', '')
        self.assertEqual(self.files.read('empty')['size'], 0)

    def test_relative_absolute_and_generated_namespace(self):
        (self.home / 'generated_images/result.png').write_bytes(b'png')
        result = self.files.open_read('result.png', namespace='generated_images')
        self.assertEqual(self.files.read_chunk(result['handle']), {'data': 'cG5n', 'eof': True})
        for value in ('../outside', 'folder/../outside', '//tmp/file', str(self.home / 'auth.json')):
            with self.subTest(value=value), self.assertRaises((ValueError, OSError)):
                self.files.open_read(value)
        with self.assertRaises(ValueError):
            self.files.open_read(str(self.home / 'auth.json'), namespace='generated_images')

    def test_symlink_ancestors_and_leaf_are_rejected_for_reads_and_writes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'data').write_bytes(b'private-canary')
        (self.workspace / 'link').symlink_to(outside, target_is_directory=True)
        (self.workspace / 'leaf').symlink_to(outside / 'data')
        for path in ('link/data', 'leaf'):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.files.open_read(path)
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.files.open_write(path)
        self.assertEqual((outside / 'data').read_bytes(), b'private-canary')

    def test_hardlinks_and_nonregular_files_are_rejected(self):
        source = self.workspace / 'source'
        source.write_bytes(b'data')
        os.link(source, self.workspace / 'alias')
        os.mkfifo(self.workspace / 'fifo')
        for path in ('source', 'alias', 'fifo'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.files.open_read(path)
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.files.open_write(path)

    def test_read_detects_concurrent_source_mutation_and_closes_handle(self):
        source = self.workspace / 'source'
        source.write_bytes(b'a' * (worker.CHUNK + 1))
        handle = self.files.open_read('source')['handle']
        self.files.read_chunk(handle)
        source.write_bytes(b'b' * (worker.CHUNK + 1))
        before = self.files.handles[handle]['identity'][3]
        os.utime(source, ns=(before + 1000000000, before + 1000000000))
        with self.assertRaises(ValueError):
            self.files.read_chunk(handle)
        self.assertNotIn(handle, self.files.handles)

    def test_streaming_24mib_transfer_and_consistency(self):
        block = bytes(range(256)) * 256
        handle = self.files.open_write('large.bin')['handle']
        digest = hashlib.sha256()
        encoded = base64.b64encode(block).decode()
        for _ in range(384):
            self.files.write_chunk(handle, encoded)
            digest.update(block)
        result = self.files.finish_write(handle)
        self.assertEqual(result['size'], 24 * 1024 * 1024)
        with self.assertRaises(ValueError):
            self.files.read('large.bin')
        handle = self.files.open_read('large.bin')['handle']
        actual = hashlib.sha256()
        while True:
            part = self.files.read_chunk(handle)
            actual.update(base64.b64decode(part['data']))
            if part['eof']:
                break
        self.assertEqual(actual.digest(), digest.digest())

    def test_partial_write_remains_invisible_and_close_discards_temporary(self):
        destination = self.workspace / 'result'
        destination.write_bytes(b'old')
        handle = self.files.open_write('result')['handle']
        self.files.write_chunk(handle, 'bmV3')
        self.assertEqual(destination.read_bytes(), b'old')
        self.files.close(handle)
        self.assertEqual(destination.read_bytes(), b'old')
        self.assertEqual(sorted(path.name for path in self.workspace.iterdir()), ['result'])

    def test_write_rejects_inter_chunk_mutation_and_changed_target(self):
        handle = self.files.open_write('result')['handle']
        temporary = self.workspace / self.files.handles[handle]['temporary']
        temporary.write_bytes(b'x')
        with self.assertRaises(ValueError):
            self.files.write_chunk(handle, 'eA==')
        self.assertFalse(temporary.exists())
        handle = self.files.open_write('result')['handle']
        (self.workspace / 'result').symlink_to(self.root / 'other')
        with self.assertRaises(ValueError):
            self.files.finish_write(handle)
        self.assertNotIn(handle, self.files.handles)

    def test_chunk_file_and_handle_limits(self):
        large = self.workspace / 'too-large'
        with large.open('wb') as file:
            file.truncate(worker.MAX_FILE + 1)
        with self.assertRaises(ValueError):
            self.files.open_read('too-large')
        handle = self.files.open_write('write')['handle']
        with self.assertRaises(ValueError):
            self.files.write_chunk(handle, base64.b64encode(b'x' * (worker.CHUNK + 1)).decode())
        for index in range(worker.MAX_HANDLES):
            self.files.open_write('transfer-' + str(index))
        with self.assertRaises(ValueError):
            self.files.open_write('one-too-many')
        self.files.close_all()
        self.assertEqual(self.files.handles, {})
        self.assertEqual([path.name for path in self.workspace.iterdir()], ['too-large'])


class ProtocolTests(unittest.TestCase):
    def test_profile_accepts_mapped_owner_on_readonly_mount_and_rejects_writable_mount(self):
        profile = {'workspace': '/workspace', 'runtime_home': '/runtime',
                   'code_root': str(Path(worker.__file__).absolute().parents[1]),
                   'python': '/usr/bin/python3', 'codex': '/opt/codex',
                   'executor_uid': 1111, 'executor_gid': 2222, 'policy_digest': '0' * 64}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            path.write_text(json.dumps(profile))
            path.chmod(0o644)
            actual_fstat = os.fstat
            def mapped(fd):
                info = actual_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size, st_uid=65534)
            with patch.object(worker.os, 'getuid', return_value=1111), patch.object(worker.os, 'geteuid', return_value=1111), \
                    patch.object(worker.os, 'getgid', return_value=2222), patch.object(worker.os, 'getegid', return_value=2222), \
                    patch.object(worker.os, 'fstat', side_effect=mapped), \
                    patch.object(worker.os, 'fstatvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
                self.assertEqual(worker.load_profile(path), profile)
                with patch.object(worker.os, 'fstatvfs', return_value=SimpleNamespace(f_flag=0)):
                    with self.assertRaises(PermissionError):
                        worker.load_profile(path)

    def test_code_mount_accepts_mapped_owner_but_not_worker_owned_files(self):
        root = str(Path(worker.__file__).absolute().parents[1])
        actual_fstat = os.fstat
        def mapped(fd):
            info = actual_fstat(fd)
            return SimpleNamespace(st_mode=info.st_mode & ~0o022, st_uid=65534)
        with patch.object(worker.os, 'getuid', return_value=1111), patch.object(worker.os, 'fstat', side_effect=mapped), \
                patch.object(worker.os, 'fstatvfs', return_value=SimpleNamespace(f_flag=os.ST_RDONLY)), \
                patch.object(worker.sys, 'path', []):
            worker._install_code_path({'code_root': root})
            self.assertEqual(worker.sys.path, [root])
            with patch.object(worker.os, 'getuid', return_value=65534):
                with self.assertRaises(PermissionError):
                    worker._install_code_path({'code_root': root})

    def test_json_rejects_duplicate_keys_nan_and_infinite_exponents(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.decode_json(value)

    def test_request_allows_only_exact_envelope_and_bounded_control(self):
        good = {'id': 'one-1', 'op': 'hello', 'args': {}}
        self.assertEqual(worker._request(worker._encode(good)), good)
        for value in ({**good, 'extra': 1}, {**good, 'id': '../bad'}, {**good, 'args': []},
                      {**good, 'args': {'value': 'x' * worker.MAX_CONTROL}}):
            with self.assertRaises(ValueError):
                worker._request(worker._encode(value))
        with self.assertRaises(ValueError):
            worker._request(b'{"id":"one","op":"hello","args":{}}')

    def test_native_command_is_fixed_and_chatgpt_overrides_are_last(self):
        profile = {'codex': '/opt/executor/bin/codex'}
        value = {'argv': ['codex', 'app-server', '--listen', 'stdio://', '-c', 'model="other"']}
        result = worker.native_argv(profile, value)
        self.assertEqual(result[0], profile['codex'])
        self.assertEqual(result[-6:], ['-c', 'model_provider="openai"', '-c', 'model="gpt-6.1-sol"', '-c', 'forced_login_method="chatgpt"'])
        for args in (['bash', '-c', 'anything'], ['codex', 'exec', '--listen', 'stdio://'],
                     ['codex', 'app-server', '--listen', 'http://localhost'],
                     ['codex', 'app-server', '--listen', 'stdio://', '--dangerously-bypass-approvals-and-sandbox'],
                     ['codex', 'app-server', '--listen', 'stdio://', '-c', 'model_providers.openai.base_url="https://example.invalid"'],
                     ['codex', 'app-server', '--listen', 'stdio://', '-c', 'bad-key']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                worker.native_argv(profile, {'argv': args})

    def test_environment_has_no_ambient_keys_or_gateway_display(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy', 'CODEX_API_KEY': 'dummy', 'DISPLAY': ':99', 'XAUTHORITY': '/private/dummy'}):
            result = worker.environment({'runtime_home': '/runtime', 'playwright_browsers_path': '/browsers'})
        self.assertEqual(result['CODEX_HOME'], '/runtime')
        self.assertEqual(result['PLAYWRIGHT_BROWSERS_PATH'], '/browsers')
        for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'DISPLAY', 'XAUTHORITY'):
            self.assertNotIn(name, result)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = {'workspace': self.temp.name, 'runtime_home': self.temp.name, 'policy_digest': '0' * 64}
        self.worker = worker.Worker(self.profile)

    async def asyncTearDown(self):
        await self.worker.close()
        self.temp.cleanup()

    async def test_unknown_operations_and_unknown_arguments_never_dispatch(self):
        for op, args in [('shell', {'command': 'id'}), ('media._run', {'args': []}),
                         ('browser.run', {'profile': '/private'}), ('computer.run', {'display': ':99'}),
                         ('file.read', {'path': 'x', 'namespace': 'generated_images'})]:
            with self.subTest(op=op), self.assertRaises(ValueError):
                await self.worker.dispatch(op, args)
        self.assertIsNone(self.worker.browser)
        self.assertIsNone(self.worker.computer)

    async def test_safe_media_dispatch_and_serializable_path(self):
        self.worker.media = SimpleNamespace(generate_image=lambda title: Path(self.temp.name) / 'result.png')
        result = await self.worker.dispatch('media.generate_image', {'title': 'dummy'})
        encoded = worker._encode({'result': result})
        self.assertEqual(json.loads(encoded)['result'], str(Path(self.temp.name) / 'result.png'))

    async def test_desktop_status_observes_existing_display_without_taking_ownership(self):
        self.profile.update(display=':92', desktop_state=self.temp.name)
        result = {'available': True, 'display': ':92', 'width': 1280, 'height': 800}
        async def status():
            self.assertEqual(os.environ['DISPLAY'], ':92')
            self.assertEqual(os.environ['XAUTHORITY'], str(Path(self.temp.name) / 'desktop/Xauthority'))
            return result
        computer = SimpleNamespace(status=AsyncMock(side_effect=status))
        with patch.dict(os.environ, {'DISPLAY': ':99', 'XAUTHORITY': '/synthetic/previous'}), \
                patch('oak.computer.ComputerTools', return_value=computer), \
                patch('oak.desktop.ManagedDesktop', side_effect=AssertionError('must not start desktop')):
            self.assertEqual(await self.worker.dispatch('computer.status', {}), result)
            self.assertEqual(os.environ['DISPLAY'], ':99')
            self.assertEqual(os.environ['XAUTHORITY'], '/synthetic/previous')
        self.assertIsNone(self.worker.desktop)
        self.assertIsNone(self.worker.computer)

    async def test_desktop_status_failure_restores_environment_without_starting_display(self):
        self.profile.update(display=':92', desktop_state=self.temp.name)
        computer = SimpleNamespace(status=AsyncMock(side_effect=RuntimeError('display unavailable')))
        with patch.dict(os.environ, {}, clear=True), \
                patch('oak.computer.ComputerTools', return_value=computer), \
                patch('oak.desktop.ManagedDesktop', side_effect=AssertionError('must not start desktop')):
            with self.assertRaises(RuntimeError):
                await self.worker.dispatch('computer.status', {})
            self.assertNotIn('DISPLAY', os.environ)
            self.assertNotIn('XAUTHORITY', os.environ)

    async def test_managed_browser_borrows_lazy_desktop_context_and_forwards_capabilities(self):
        self.profile.update(display=':92', desktop_state=self.temp.name)
        context = object()
        desktop = SimpleNamespace(display=':92', browser_context=AsyncMock(return_value=context),
                                  __aenter__=AsyncMock(), close=AsyncMock())
        browser = SimpleNamespace(run=AsyncMock(return_value={'ok': True}), close=AsyncMock())
        with patch('oak.desktop.ManagedDesktop', return_value=desktop), \
                patch('oak.browser.BrowserTools', return_value=browser) as construct:
            args = {'action': 'upload', 'selector': '#file', 'frame': 'frame-2',
                    'files': ['photo.png'], 'timeout_ms': 5000, 'tab': 'tab-1'}
            self.assertEqual(await self.worker.dispatch('browser.run', args), {'ok': True})
            provider = construct.call_args.kwargs['context_provider']
            self.assertIs(await provider(), context)
            self.assertIs(await provider(), context)
            desktop.__aenter__.assert_awaited_once()
            browser.run.assert_awaited_once_with(**args)
            self.assertIs(self.worker.desktop, desktop)

    async def test_unmanaged_browser_keeps_standalone_context(self):
        browser = SimpleNamespace(run=AsyncMock(return_value={'ok': True}), close=AsyncMock())
        with patch('oak.browser.BrowserTools', return_value=browser) as construct:
            await self.worker.dispatch('browser.run', {'action': 'read'})
        construct.assert_called_once_with(self.temp.name, context_provider=None)
        self.assertIsNone(self.worker.desktop)

    async def test_remote_lease_blocks_browser_observation_and_actions_before_start(self):
        self.worker.remote = {'id': 'a' * 32}
        try:
            with patch('oak.browser.BrowserTools') as construct:
                for action in ('read', 'screenshot', 'tabs', 'open', 'upload', 'download', 'click', 'type'):
                    with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Remote owns desktop'):
                        await self.worker.dispatch('browser.run', {'action': action})
                construct.assert_not_called()
            self.worker.browser = SimpleNamespace(run=AsyncMock(), close=AsyncMock())
            with self.assertRaisesRegex(ValueError, 'Remote owns desktop'):
                await self.worker.dispatch('browser.run', {'action': 'read'})
            self.worker.browser.run.assert_not_awaited()
            await self.worker.dispatch('browser.close', {})
            self.worker.browser.close.assert_awaited_once()
        finally:
            self.worker.remote = None

    async def test_remote_id_binding_read_write_without_any_desktop(self):
        first, second = socket.socketpair()
        first.setblocking(False); second.setblocking(False)
        self.worker.remote = {'id': 'a' * 32, 'socket': first, 'process': None, 'input_lock': asyncio.Lock()}
        try:
            with self.assertRaises(ValueError):
                await self.worker.remote_write('b' * 32, 'eA==')
            second.send(b'RFB test')
            result = await self.worker.remote_read('a' * 32)
            self.assertEqual(base64.b64decode(result['data']), b'RFB test')
            await self.worker.remote_write('a' * 32, 'aGk=')
            self.assertEqual(second.recv(2), b'hi')
            with self.assertRaises(ValueError):
                await self.worker.remote_write('a' * 32, base64.b64encode(b'x' * (worker.CHUNK + 1)).decode())
            await self.worker.remote_close('a' * 32)
            self.assertIsNone(self.worker.remote)
        finally:
            second.close()

    async def test_remote_text_and_key_require_lease_and_serialize_without_screenshot(self):
        first, second = socket.socketpair()
        self.worker.remote = {'id': 'a' * 32, 'socket': first, 'process': None, 'input_lock': asyncio.Lock()}
        entered, release = asyncio.Event(), asyncio.Event()
        async def typing(text):
            entered.set()
            await release.wait()
        computer = SimpleNamespace(_type=AsyncMock(side_effect=typing), _key=AsyncMock(), _exec=AsyncMock(), run=AsyncMock())
        self.worker.computer = computer
        try:
            for op, args in [('remote.type', {'id': 'b' * 32, 'text': 'dummy'}),
                             ('remote.type', {'id': 'a' * 32, 'text': '\x00'}),
                             ('remote.key', {'id': 'a' * 32, 'key': 'ctrl+a;anything'})]:
                with self.assertRaises(ValueError):
                    await self.worker.dispatch(op, args)
            first_call = asyncio.create_task(self.worker.dispatch('remote.type', {'id': 'a' * 32, 'text': 'dummy'}))
            await entered.wait()
            next_call = asyncio.create_task(self.worker.dispatch('remote.key', {'id': 'a' * 32, 'key': 'meta+a'}))
            await asyncio.sleep(0)
            computer._key.assert_not_awaited()
            release.set()
            self.assertEqual(await first_call, {'accepted': True})
            self.assertEqual(await next_call, {'accepted': True})
            computer._key.assert_awaited_once_with('super+a')
            computer.run.assert_not_awaited()
        finally:
            second.close()

    async def test_rpc_redacts_exception_text_and_closes_on_eof(self):
        reader = asyncio.StreamReader()
        reader.feed_data(worker._encode({'id': 'one', 'op': 'hello', 'args': {}}))
        output = io.BytesIO()
        fake = SimpleNamespace(dispatch=AsyncMock(side_effect=ValueError('secret-canary')), close=AsyncMock())
        with patch.object(worker, 'Worker', return_value=fake):
            serving = asyncio.create_task(worker.serve(self.profile, reader, output))
            while not output.getvalue():
                await asyncio.sleep(0)
            reader.feed_eof()
            await serving
        self.assertEqual(json.loads(output.getvalue()), {'id': 'one', 'ok': False, 'error': 'ValueError'})
        fake.close.assert_awaited_once()
        self.assertNotIn(b'secret-canary', output.getvalue())

    async def test_rpc_timeout_is_fatal_and_closes_adapters(self):
        reader = asyncio.StreamReader()
        reader.feed_data(worker._encode({'id': 'one', 'op': 'hello', 'args': {}}))
        output = io.BytesIO()
        async def blocked(*_):
            await asyncio.Future()
        fake = SimpleNamespace(dispatch=AsyncMock(side_effect=blocked), close=AsyncMock())
        with patch.object(worker, 'Worker', return_value=fake), patch.object(worker, 'OP_TIMEOUT', .01):
            await worker.serve(self.profile, reader, output)
        self.assertEqual(json.loads(output.getvalue())['error'], 'TimeoutError')
        self.assertEqual(fake.dispatch.await_count, 1)
        fake.close.assert_awaited_once()

    async def test_peer_eof_cancels_inflight_dispatch_and_closes_worker(self):
        reader = asyncio.StreamReader()
        reader.feed_data(worker._encode({'id': 'one', 'op': 'hello', 'args': {}}))
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def blocked(*_):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        fake = SimpleNamespace(dispatch=AsyncMock(side_effect=blocked), close=AsyncMock())
        output = io.BytesIO()
        with patch.object(worker, 'Worker', return_value=fake):
            serving = asyncio.create_task(worker.serve(self.profile, reader, output))
            await entered.wait()
            reader.feed_eof()
            await asyncio.wait_for(serving, 1)
        self.assertTrue(cancelled.is_set())
        fake.close.assert_awaited_once()
        self.assertEqual(output.getvalue(), b'')

    async def test_pipelined_request_fails_closed(self):
        reader = asyncio.StreamReader()
        for identifier in ('one', 'two'):
            reader.feed_data(worker._encode({'id': identifier, 'op': 'hello', 'args': {}}))
        output = io.BytesIO()
        fake = SimpleNamespace(dispatch=AsyncMock(return_value={}), close=AsyncMock())
        with patch.object(worker, 'Worker', return_value=fake):
            await asyncio.wait_for(worker.serve(self.profile, reader, output), 1)
        self.assertEqual(json.loads(output.getvalue())['error'], 'PipelineError')
        fake.close.assert_awaited_once()



if __name__ == '__main__':
    unittest.main()
