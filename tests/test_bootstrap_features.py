"""Bootstrap contracts with synthetic state; no deployment/account inspection."""

import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

from oak.features import feature_manifest, matches_manifest

REPO = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootstrapFeatureTests(unittest.TestCase):
    def setUp(self):
        self.vm = script('bootstrap_vm')
        self.verifier = script('verify-bootstrap-features')

    def test_missing_skipped_or_stale_proof_is_rejected_without_details(self):
        manifest = feature_manifest()
        self.assertTrue(matches_manifest(manifest, manifest))
        self.assertFalse(matches_manifest({}, manifest))
        changed = {**manifest, 'code_digest': '0' * 64}
        self.assertFalse(matches_manifest(changed, manifest))
        for report in ({}, {'status': 'verified', 'features': changed, 'protocol': {'verified': True}},
                       {'status': 'verified', 'features': manifest, 'protocol': {'verified': False}}):
            with self.subTest(report=report), patch.object(self.vm, 'run', return_value=SimpleNamespace(stdout=json.dumps(report))):
                with self.assertRaisesRegex(RuntimeError, '^Local feature verification failed; no account or gateway was started.$'):
                    self.vm.verify_features()

    def test_browser_failure_does_not_emit_exception_or_claim_readiness(self):
        with patch.object(self.verifier, 'protocol_checks', return_value={'verified': True}), \
                patch.object(self.verifier, 'browser_checks', AsyncMock(side_effect=RuntimeError('secret-test-canary'))):
            report = self.verifier.verify(True)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['browser']['verified'])
        self.assertNotIn('secret-test-canary', json.dumps(report))
        self.assertFalse(report['credential_isolation_verified'])

    def test_worker_nonzero_or_inconsistent_counters_never_pass(self):
        proof = {'verified': True, 'tests': 48, 'failures': 0, 'errors': 0, 'skipped': 0}
        for code, changed in ((1, {}), (0, {'tests': 0}), (0, {'errors': 1}),
                              (0, {'failures': 1}), (0, {'skipped': 1})):
            with self.subTest(code=code, changed=changed), patch.object(self.verifier.subprocess, 'run',
                    return_value=SimpleNamespace(stdout=json.dumps({**proof, **changed}), returncode=code)):
                self.assertFalse(self.verifier.protocol_checks()['verified'])

    def test_binding_and_launch_sources_change_digest(self):
        from oak.features import SOURCE_FILES
        original = Path.read_bytes
        baseline = feature_manifest()
        for name in ('oak/controller.py', 'oak/telegram.py', 'oak/web/app.js', 'oak/web/index.html'):
            self.assertIn(name, SOURCE_FILES)
            def changed(path):
                return original(path) + (b'\n# change' if path == REPO / name else b'')
            with patch.object(Path, 'read_bytes', changed):
                self.assertNotEqual(feature_manifest()['code_digest'], baseline['code_digest'])

    def test_verifier_timeout_terminates_then_reaps_its_owned_process(self):
        process = Mock(pid=12345, returncode=None)
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired('synthetic', 1), ('', ''), ('', '')]
        with patch.object(self.vm.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(self.vm.os, 'killpg') as send, \
                patch.object(self.vm, 'group_exists', return_value=False):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.vm.managed_check(['synthetic'], timeout=1, cleanup_timeout=55, capture_output=True, text=True)
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        send.assert_called_once_with(12345, signal.SIGTERM)
        self.assertEqual(process.communicate.call_args_list[-2].kwargs['timeout'], 55)

    def test_exited_verifier_leader_still_cleans_owned_descendants(self):
        process = Mock(pid=12345, returncode=1)
        process.communicate.return_value = ('', '')
        with patch.object(self.vm.subprocess, 'Popen', return_value=process), \
                patch.object(self.vm.os, 'killpg') as send, \
                patch.object(self.vm, 'group_exists', return_value=True), \
                patch.object(self.vm.time, 'monotonic', side_effect=[0, 100]):
            with self.assertRaises(subprocess.CalledProcessError):
                self.vm.managed_check(['synthetic'], timeout=1, cleanup_timeout=55, capture_output=True, text=True)
        self.assertEqual([call.args for call in send.call_args_list],
                         [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])

    def test_live_old_gateway_is_rejected_using_only_synthetic_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            origin = 'https://synthetic.invalid'
            config = {'state_dir': str(root), 'telegram_username': 'synthetic_bot',
                      'allowed_user_ids': [11], 'web': {}}
            (root / 'web-access-keys.json').write_text(json.dumps({'11': 'synthetic-key'}))
            (root / 'gateway-ready.json').write_text(json.dumps({
                'pid': 123, 'model': 'gpt-6.1-sol', 'auth': 'subscription',
                'bot': config['telegram_username'], 'web_url': origin}))
            response = Mock(status=200)
            response.read.return_value = b'computer-switch'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)

            def request(url, data=None, headers=None):
                if url.endswith('/api/panel') and not headers:
                    raise HTTPError(url, 401, 'synthetic', None, None)
                if url.endswith('/api/session'):
                    return {'access_token': 'synthetic-session'}
                if url.endswith('/api/bootstrap'):
                    return {'name': 'Oak', 'transport': 'sse'}
                self.fail('Old gateway must stop at manifest validation')

            with patch.object(self.vm, 'verify_bot'), \
                    patch.object(self.vm, 'status', return_value={'worker_running': True, 'worker_pid': 123}), \
                    patch.object(self.vm, 'telegram', return_value={'web_app': {'url': origin}}), \
                    patch.object(self.vm, 'urlopen', return_value=response), \
                    patch.object(self.vm, 'request_json', side_effect=request) as http:
                with self.assertRaisesRegex(RuntimeError, '^Public panel verification failed;'):
                    self.vm.verify_live(root / 'config.json', config)
            self.assertTrue(http.call_args.args[0].endswith('/api/bootstrap'))

    def test_prepare_and_running_paths_never_start_account_actions(self):
        for running in (False, True):
            with self.subTest(running=running), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                config = {'state_dir': str(root / 'state'), 'workspace': str(root / 'work'),
                          'runtime_home': str(root / 'runtime')}
                with patch.object(self.vm.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '24.04'}), \
                        patch.object(self.vm.platform, 'machine', return_value='x86_64'), \
                        patch.object(self.vm.os, 'geteuid', return_value=1000), \
                        patch.dict(self.vm.os.environ), \
                        patch.object(self.vm, 'settings', return_value=(root / 'config.json', config)), \
                        patch.object(self.vm, 'locked', return_value=running), \
                        patch.object(self.vm.shutil, 'which', return_value='/synthetic/codex'), \
                        patch.object(self.vm, 'private_json'), \
                        patch.object(self.vm, 'run') as run, \
                        patch.object(self.vm, 'verify_features', return_value={'status': 'verified'}) as verify, \
                        patch.object(self.vm, 'verify_live', return_value={'status': 'ready'}) as live, \
                        patch.object(self.vm, 'verify_bot', side_effect=AssertionError('account action')), \
                        patch.object(self.vm.subprocess, 'run', side_effect=AssertionError('runtime action')), \
                        patch.object(sys, 'argv', ['bootstrap_vm', '--prepare-only', '--skip-system', '--skip-voice', '--skip-autostart']), \
                        redirect_stdout(io.StringIO()):
                    self.vm.main()
                if running:
                    run.assert_not_called()
                    verify.assert_not_called()
                    live.assert_called_once()
                else:
                    verify.assert_called_once_with(False)
                    live.assert_not_called()
                    commands = [list(map(str, call.args[0])) for call in run.call_args_list]
                    self.assertFalse(any('login' in command or 'service' in command or 'smoke' in command for command in commands))

    def test_unsupported_oracle_platform_is_rejected_before_provisioning(self):
        with patch.object(self.vm.platform, 'freedesktop_os_release', return_value={'ID': 'ubuntu', 'VERSION_ID': '22.04'}), \
                patch.object(self.vm.platform, 'machine', return_value='aarch64'), \
                patch.object(self.vm.os, 'geteuid', return_value=1000), \
                patch.object(sys, 'argv', ['bootstrap_vm', '--prepare-only']), \
                patch.object(self.vm, 'settings') as settings, patch.object(self.vm, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Supported:'):
                self.vm.main()
        settings.assert_not_called()
        run.assert_not_called()


class FixtureLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.demo = script('input-request-demo')
        self.verifier = script('verify-bootstrap-features')

    async def test_occupied_parent_port_reaps_owned_child_and_preserves_listener(self):
        port = self.verifier.local_ports()
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', port))
        listener.listen(1)
        children = []
        original = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            children.append((process, Path(args[args.index('--socket') + 1])))
            return process

        with patch.object(self.demo.asyncio, 'create_subprocess_exec', side_effect=spawn):
            with self.assertRaises(OSError):
                async with self.demo.serve_demo(port, broker_enabled=True):
                    self.fail('Occupied port must not be replaced')
        self.assertEqual(len(children), 1)
        process, socket_path = children[0]
        self.assertEqual(process.returncode, 0)
        self.assertFalse(socket_path.parent.parent.exists())
        self.assertEqual(listener.getsockname()[1], port)

    async def test_cancellation_during_cleanup_waits_then_propagates(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.demo.close_demo

        async def close(*args):
            entered.set()
            await release.wait()
            return await original(*args)

        async def use():
            async with self.demo.serve_demo(self.verifier.local_ports()):
                pass

        with patch.object(self.demo, 'close_demo', side_effect=close):
            task = asyncio.create_task(use())
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_cleanup_failures_do_not_skip_child_or_controller(self):
        controller = Mock()
        gateway = SimpleNamespace(signin=SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('secret-canary'))))
        runner = SimpleNamespace(cleanup=AsyncMock(side_effect=RuntimeError('secret-canary')))
        with patch.object(self.demo, 'stop_child', AsyncMock(return_value=True)) as stop:
            self.assertFalse(await self.demo.close_demo(set(), gateway, runner, object(), controller))
        stop.assert_awaited_once()
        controller.close.assert_called_once()

    async def test_child_races_and_kill_are_bounded_and_report_uncertain(self):
        process = Mock(returncode=None)
        process.terminate.side_effect = ProcessLookupError()

        async def reaped():
            process.returncode = 0

        process.wait = AsyncMock(side_effect=reaped)
        self.assertTrue(await self.demo.stop_child(process))
        process.wait.assert_awaited_once()
        process.returncode = None
        process.wait = AsyncMock()
        seen = []

        async def timed(awaitable, timeout):
            seen.append(timeout)
            awaitable.close()
            if len(seen) == 1:
                raise asyncio.TimeoutError
            process.returncode = -9

        with patch.object(self.demo.asyncio, 'wait_for', side_effect=timed):
            self.assertFalse(await self.demo.stop_child(process))
        self.assertEqual(seen, [12, 3])
        process.kill.assert_called_once()

    async def test_cli_sigterm_closes_all_fixture_ports(self):
        port = self.verifier.local_ports()
        process = await asyncio.create_subprocess_exec(sys.executable, str(REPO / 'scripts/input-request-demo.py'),
            '--port', str(port), '--broker', cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            line = await asyncio.wait_for(process.stdout.readline(), 30)
            self.assertTrue(line.startswith(b'Synthetic local demo:'))
            process.send_signal(signal.SIGTERM)
            self.assertEqual(await asyncio.wait_for(process.wait(), 40), 0)
            for value in (port, port + 1, port + 2):
                with socket.socket() as candidate:
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    candidate.bind(('127.0.0.1', value))
                    candidate.listen(1)
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 55)
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 3)


class VerifierCliSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_verifier_sigterm_waits_for_broker_cleanup(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            if not Path(pw.chromium.executable_path).exists():
                self.skipTest('Optional Chromium CLI signal smoke requires installed browser')
        verifier = script('verify-bootstrap-features')
        port = verifier.local_ports()
        process = await asyncio.create_subprocess_exec(sys.executable, str(REPO / 'scripts/verify-bootstrap-features.py'),
            '--broker-browser', '--fixture-port', str(port), cwd=REPO,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            for _ in range(1200):
                try:
                    reader, writer = await asyncio.open_connection('127.0.0.1', port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    if process.returncode is not None:
                        self.fail('Verifier ended before browser fixture became ready')
                    await asyncio.sleep(0.05)
            else:
                self.fail('Verifier fixture startup deadline exceeded')
            process.send_signal(signal.SIGTERM)
            stdout, _ = await asyncio.wait_for(process.communicate(), 55)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(json.loads(stdout)['status'], 'failed')
            for value in (port, port + 1, port + 2):
                with socket.socket() as candidate:
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    candidate.bind(('127.0.0.1', value))
                    candidate.listen(1)
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 55)
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 3)
