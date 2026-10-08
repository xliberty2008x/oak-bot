#!/usr/bin/env python3
"""Loopback-only input demo; synthetic runtime/Telegram and temporary state.

No runtime process, Telegram poller, provider page or account is opened.
The /demo routes exist only in this fixture, never in WebGateway production.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import os
import signal
import hashlib
import hmac
import json
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from oak.bus import EventBus
from oak.controller import Controller
from oak.runtime import RpcError, RuntimeClient
from oak.tools import Tools
from oak.web import WebGateway


class DemoRuntime(RuntimeClient):
    def __init__(self):
        super().__init__()
        self.native_attempts = 0
        self.new_turn_starts = 0
        self.fail_next_send = False

    async def request(self, method, params):
        if method == 'model/list':
            return {'data': [{'model': 'gpt-6.1-sol', 'displayName': 'gpt-6.1-sol', 'hidden': False,
                             'defaultReasoningEffort': 'low', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}], 'nextCursor': None}
        if method == 'turn/start':
            self.new_turn_starts += 1
        if method in {'mcpServerStatus/list', 'skills/list', 'app/list'}:
            return {'data': [], 'nextCursor': None}
        raise RpcError(-32601, 'Synthetic demo: method unavailable.')

    async def _send(self, message):
        self.native_attempts += 1
        if self.fail_next_send:
            self.fail_next_send = False
            raise ConnectionError('Synthetic uncertain transport.')


@asynccontextmanager
async def serve_demo(port, broker_enabled=False, browsers_path=None):
    browsers_path = browsers_path or os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    with tempfile.TemporaryDirectory(prefix='oak-input-demo-') as folder:
        process = runner = gateway = controller = None
        tasks = set()
        try:
            root = Path(folder)
            runtime = DemoRuntime()
            workspace = root / 'workspace'
            workspace.mkdir(mode=0o700)
            controller = Controller(runtime, root / 'state.sqlite', workspace, None,
                                    config={'state_dir': folder, 'timezone': 'Europe/Kyiv'})
            controller.tools = Tools(controller, {})
            runtime.tool_handler = controller.tools.handle
            runtime.request_input_handler = controller.interactions.user_input
            bus = EventBus(controller)
            controller.emit = bus.emit
            controller.threads[11] = 'demo-thread'
            controller.loaded.add('demo-thread')
            with controller.db:
                controller.db.execute('INSERT INTO chats(chat_id,thread_id,tools_version) VALUES (?,?,?)',
                                      (11, 'demo-thread', controller.tools.version))
            process = None
            # Eligibility marker only: all fixture traffic stays on loopback.
            web_config = {'public_url': 'https://oak.synthetic.invalid'}
            if broker_enabled:
                if port > 65533:
                    raise ValueError('Reserve three consecutive ports.')
                private = root / 'broker'
                private.mkdir(mode=0o700)
                socket = private / 'control.sock'
                process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'oak.signin_fixture',
                    '--socket', str(socket), '--parent-port', str(port), '--human-port', str(port + 1),
                    '--provider-port', str(port + 2), cwd=Path(__file__).resolve().parents[1],
                    env={**{'PATH': '/usr/bin:/bin', 'HOME': str(Path.home())},
                         **({'PLAYWRIGHT_BROWSERS_PATH': browsers_path} if browsers_path else {})},
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                for _ in range(100):
                    if socket.exists():
                        break
                    if process.returncode is not None:
                        raise RuntimeError('Synthetic broker did not start.')
                    await asyncio.sleep(0.1)
                else:
                    raise RuntimeError('Synthetic broker unavailable.')
                web_config['credential_broker'] = {'socket': str(socket), 'origin': f'http://127.0.0.1:{port+1}',
                                                    'synthetic_only': True}
            gateway = WebGateway(controller, bus, '123:synthetic-demo-token', [11], web_config)
            controller.web = gateway
            fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 11})}
            secret = hmac.new(b'WebAppData', gateway.token.encode(), hashlib.sha256).digest()
            fields['hash'] = hmac.new(secret, '\n'.join(k + '=' + v for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
            sdk = {'initData': urlencode(fields)}
            gateway.sdk_file.write_text('window.Telegram={WebApp:' + json.dumps(sdk) + '};for(const name of ["ready","expand","close","setHeaderColor","setBackgroundColor"]) window.Telegram.WebApp[name]=()=>{};')
            tasks, sequence = set(), 0

            async def begin(template, expired=False, uncertain=False):
                nonlocal sequence
                if template not in {'task_details', 'plan_details', 'instagram_sign_in'} | ({'synthetic_sign_in'} if broker_enabled else set()):
                    raise web.HTTPBadRequest()
                controller.requests.invalidate_turn('demo-thread', controller.active.get(11))
                sequence += 1
                turn = 'demo-original-' + str(sequence)
                controller.active[11] = turn
                runtime.fail_next_send = uncertain
                with controller.db:
                    controller.db.execute('INSERT INTO turns(chat_id,thread_id,turn_id,status) VALUES (?,?,?,?)',
                                          (11, 'demo-thread', turn, 'inProgress'))
                task = asyncio.create_task(runtime._handle_server_request({'id': sequence, 'method': 'item/tool/call',
                    'params': {'threadId': 'demo-thread', 'turnId': turn, 'tool': 'oak_request_input',
                               'arguments': {'template': template}}}))
                tasks.add(task)
                def done(value):
                    tasks.discard(value)
                    if not value.cancelled():
                        value.exception()  # No diagnostics, request contents or transport data are logged.
                task.add_done_callback(done)
                for _ in range(20):
                    await asyncio.sleep(0)
                    row = controller.db.execute('SELECT id FROM input_requests WHERE turn_id=?', (turn,)).fetchone()
                    if row:
                        if expired:
                            with controller.db:
                                controller.db.execute('UPDATE input_requests SET expires=? WHERE id=?', (time.time() - 1, row[0]))
                        return row[0]
                raise RuntimeError('Synthetic request was not created.')

            async def create(request):
                identifier = await begin(request.match_info['template'], 'expired' in request.query, 'uncertain' in request.query)
                return web.json_response({'id': identifier})

            async def result(request):
                return web.json_response({'native_attempts': runtime.native_attempts, 'new_turn_starts': runtime.new_turn_starts,
                    'requests': [dict(row) for row in controller.db.execute('SELECT id,outcome,delivery FROM input_requests ORDER BY rowid')],
                    'broker': [dict(row) for row in controller.db.execute('SELECT phase,cleanup FROM signin_attempts')] if broker_enabled else []})

            gateway.app.router.add_post('/demo/request/{template}', create)
            gateway.app.router.add_get('/demo/result', result)
            identifier = await begin('synthetic_sign_in' if broker_enabled else 'plan_details')
            runner = web.AppRunner(gateway.app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, '127.0.0.1', port).start()
            yield f'http://127.0.0.1:{port}?conversation=telegram&request={identifier}'
        finally:
            cleanup = asyncio.create_task(close_demo(tasks, gateway, runner, process, controller))
            cancelled = False
            while True:
                try:
                    confirmed = await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    if cleanup.cancelled():
                        raise RuntimeError('Synthetic fixture cleanup unconfirmed.') from None
                    cancelled = True
            if not confirmed:
                raise RuntimeError('Synthetic fixture cleanup unconfirmed.')
            if cancelled:
                raise asyncio.CancelledError


async def close_demo(tasks, gateway, runner, process, controller):
    errors = []
    for task in tuple(tasks):
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
    except Exception:
        errors.append(True)
    for action in ([gateway.signin.close] if gateway and gateway.signin else []) + ([runner.cleanup] if runner else []):
        try:
            await asyncio.wait_for(action(), 15)
        except Exception:
            errors.append(True)
    try:
        if not await stop_child(process):
            errors.append(True)
    except Exception:
        errors.append(True)
    if controller:
        try:
            controller.close()
        except Exception:
            errors.append(True)
    return not errors


async def stop_child(process):
    if process is None:
        return True
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 12)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), 3)
            return False
    return process.returncode == 0


async def main(port, broker_enabled=False, browsers_path=None):
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
        async with serve_demo(port, broker_enabled, browsers_path) as url:
            print('Synthetic local demo: '+url, flush=True)
            await asyncio.Event().wait()
    finally:
        for value in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(value)
            signal.signal(value, previous[value])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18769)
    parser.add_argument('--broker', action='store_true', help='Separate synthetic-only broker process and localhost provider')
    parser.add_argument('--browsers-path', help='Optional explicit Playwright cache; otherwise use the installed default')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged local port.')
    try:
        asyncio.run(main(args.port, args.broker, args.browsers_path))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
