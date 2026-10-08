#!/usr/bin/env python3
"""Account-free local feature verification; optional loopback synthetic Chromium.

Does not load a deployment config, start a runtime/Telegram poller, contact a
real provider, or save browser credentials. Reports bounded non-secret JSON.
"""

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from oak.features import feature_manifest


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protocol_worker():
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for name in ('test_requests.py', 'test_a2ui.py', 'test_signin_broker.py'):
        mandatory = loader.discover(str(REPO / 'tests'), pattern=name)
        if mandatory.countTestCases() == 0:
            return {'verified': False, 'tests': 0, 'failures': 0, 'errors': 1, 'skipped': 0}
        suite.addTests(mandatory)
    # Process lifecycle/CLI smoke runs separately. This worker owns no child
    # services, so interruption cannot orphan another demo or verifier.
    mandatory = loader.loadTestsFromName('test_bootstrap_features.BootstrapFeatureTests')
    if mandatory.countTestCases() == 0:
        return {'verified': False, 'tests': 0, 'failures': 0, 'errors': 1, 'skipped': 0}
    suite.addTests(mandatory)
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    finally:
        logging.disable(previous)
    return {'verified': result.wasSuccessful() and not result.skipped and result.testsRun > 0,
            'tests': result.testsRun, 'failures': len(result.failures),
            'errors': len(result.errors), 'skipped': len(result.skipped)}


def protocol_checks():
    # Capture the entire worker lifetime, including interpreter cleanup warnings.
    # Only the bounded counters leave this process; test diagnostics are discarded.
    worker = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--protocol-worker'],
                            capture_output=True, text=True, timeout=90, cwd=REPO)
    value = json.loads(worker.stdout)
    if (set(value) != {'verified', 'tests', 'failures', 'errors', 'skipped'}
            or type(value['verified']) is not bool
            or any(type(value[key]) is not int or value[key] < 0 for key in ('tests', 'failures', 'errors', 'skipped'))):
        raise RuntimeError('Invalid local protocol proof.')
    if (worker.returncode != 0 or value['tests'] == 0
            or any(value[key] for key in ('failures', 'errors', 'skipped'))):
        value['verified'] = False
    return value


def local_ports():
    """Reserve candidates briefly; a bind race fails safely without evicting anyone."""
    for _ in range(100):
        sockets = []
        try:
            first = socket.socket()
            sockets.append(first)
            first.bind(('127.0.0.1', 0))
            port = first.getsockname()[1]
            if not 1024 <= port <= 65533:
                continue
            for value in (port + 1, port + 2):
                other = socket.socket()
                sockets.append(other)
                other.bind(('127.0.0.1', value))
            return port
        except OSError:
            continue
        finally:
            for item in sockets:
                item.close()
    raise RuntimeError('No free local fixture ports.')


async def browser_checks(browsers_path=None, port=None):
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    previous = {value: signal.getsignal(value) for value in (signal.SIGINT, signal.SIGTERM)}
    signalled = False
    def stop():
        nonlocal signalled
        if not signalled:
            signalled = True
            task.cancel()
    for value in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(value, stop)
    try:
        return await run_browser_checks(browsers_path, port)
    finally:
        for value in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(value)
            signal.signal(value, previous[value])


async def run_browser_checks(browsers_path=None, port=None):
    demo = load_script('input-request-demo')
    client = load_script('verify-signin-demo')
    async with demo.serve_demo(port or local_ports(), broker_enabled=True, browsers_path=browsers_path) as url:
        from urllib.parse import urlsplit
        result = await asyncio.wait_for(client.verify(urlsplit(url).port), 180)
    # Reaching here also confirms teardown of the owned fixture processes.
    return {'verified': True, 'native_responses': result['native_attempts'],
            'new_turn_starts': result['new_turn_starts'], 'cleanup_verified': True,
            'ordinary_submit_cancel': True, 'synthetic_login_otp_cancel': True}


def verify(broker_browser=False, browsers_path=None, port=None):
    report = {'status': 'failed', 'features': feature_manifest(),
              'protocol': protocol_checks(), 'browser': {'verified': False, 'requested': broker_browser},
              'model_verified': False, 'live_telegram_verified': False,
              'telegram_client_verified': False, 'credential_isolation_verified': False}
    if not report['protocol']['verified']:
        return report
    previous = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    try:
        if broker_browser:
            if browsers_path:
                os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browsers_path
            report['browser'] = {'requested': True, **asyncio.run(browser_checks(browsers_path, port))}
        report['status'] = 'verified'
    except (Exception, asyncio.CancelledError):
        # No exception strings, provider data, traces, screenshots or request IDs.
        report['browser']['error'] = 'synthetic_browser_or_cleanup_failed'
    finally:
        if previous is None:
            os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
        else:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = previous
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--broker-browser', action='store_true', help='Use installed Chromium with fixed dummy data')
    parser.add_argument('--browsers-path', help='Optional installed Playwright cache override')
    parser.add_argument('--fixture-port', type=int, help='Optional first of three unused loopback ports')
    parser.add_argument('--protocol-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.protocol_worker:
        result = protocol_worker()
        print(json.dumps(result))
        return 0 if result['verified'] else 1
    if (args.browsers_path or args.fixture_port is not None) and not args.broker_browser:
        parser.error('--browsers-path/--fixture-port require --broker-browser')
    if args.fixture_port is not None and not 1024 <= args.fixture_port <= 65533:
        parser.error('Reserve three consecutive unprivileged ports.')
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        result = verify(args.broker_browser, args.browsers_path, args.fixture_port)
    except (Exception, KeyboardInterrupt):
        result = {'status': 'failed', 'error': 'feature_verification_failed'}
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
