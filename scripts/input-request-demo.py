#!/usr/bin/env python3
"""Loopback-only input demo; synthetic runtime/Telegram and temporary state.

No runtime process, Telegram poller, provider page or account is opened.
The /demo routes exist only in this fixture, never in WebGateway production.
"""
import argparse
import asyncio
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


async def main(port):
    with tempfile.TemporaryDirectory(prefix='oak-input-demo-') as folder:
        root = Path(folder)
        runtime = DemoRuntime()
        controller = Controller(runtime, root / 'state.sqlite', root, None,
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
        gateway = WebGateway(controller, bus, '123:synthetic-demo-token', [11], {})
        controller.web = gateway
        fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 11})}
        secret = hmac.new(b'WebAppData', gateway.token.encode(), hashlib.sha256).digest()
        fields['hash'] = hmac.new(secret, '\n'.join(k + '=' + v for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
        sdk = {'initData': urlencode(fields)}
        gateway.sdk_file.write_text('window.Telegram={WebApp:' + json.dumps(sdk) + '};for(const name of ["ready","expand","close","setHeaderColor","setBackgroundColor"]) window.Telegram.WebApp[name]=()=>{};')
        tasks, sequence = set(), 0

        async def begin(template, expired=False, uncertain=False):
            nonlocal sequence
            if template not in {'task_details', 'plan_details', 'instagram_sign_in'}:
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
                'requests': [dict(row) for row in controller.db.execute('SELECT id,outcome,delivery FROM input_requests ORDER BY rowid')]})

        gateway.app.router.add_post('/demo/request/{template}', create)
        gateway.app.router.add_get('/demo/result', result)
        identifier = await begin('plan_details')
        runner = web.AppRunner(gateway.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', port).start()
        print(f'Synthetic local demo: http://127.0.0.1:{port}?conversation=telegram&request={identifier}', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            for task in tuple(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.cleanup()
            controller.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18769)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged local port.')
    try:
        asyncio.run(main(args.port))
    except KeyboardInterrupt:
        pass
