#!/usr/bin/env python3
"""Loopback-only Mini App demo: real Oak gateway/tools, synthetic agent/Telegram.

Never starts a Telegram poller or model process. All state is temporary.
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
from oak.a2ui_demo import form_messages, message, result_messages
from oak.bus import EventBus
from oak.controller import Controller
from oak.runtime import RpcError
from oak.tools import Tools
from oak.web import WebGateway


class DemoRuntime:
    def __init__(self):
        self.controller = None
        self.sequence = 0
        self.tasks = set()

    async def request(self, method, params):
        if method == 'model/list':
            return {'data': [{'model': 'gpt-6.1-sol', 'displayName': 'gpt-6.1-sol', 'hidden': False,
                             'defaultReasoningEffort': 'low', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}], 'nextCursor': None}
        if method == 'turn/start':
            self.sequence += 1
            run = 'demo-action-' + str(self.sequence)
            task = asyncio.create_task(self.complete(run, params['input'][0]['text']))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return {'turn': {'id': run}}
        raise RpcError(-32601, 'Synthetic demo: method unavailable.')

    async def complete(self, run, prompt):
        await asyncio.sleep(0.1)  # Allow normal Controller acknowledgement first.
        action = json.loads(prompt.split('\n')[-1])['action']
        name = action['name']
        if name == 'submit':
            messages = result_messages(action['context'])
            fallback = 'Демонстраційний план готовий. Картку оновлено в панелі Oak.'
        elif name == 'edit':
            messages = [message('deleteSurface', {}), *form_messages()]
            fallback = 'Демонстраційну форму відкрито для нового вибору.'
        else:
            messages = [message('deleteSurface', {})]
            fallback = 'Демонстраційну форму скасовано.'
        await self.controller.tools.execute(11, 'oak_a2ui', {'action': 'publish', 'messages_json': json.dumps(messages), 'fallback': fallback},
                                            {'threadId': 'demo-thread', 'turnId': run})
        async with self.controller._lock(11):
            self.controller.active.pop(11, None)
            with self.controller.db:
                self.controller.db.execute("UPDATE turns SET status='completed' WHERE turn_id=?", (run,))


async def main(port):
    with tempfile.TemporaryDirectory(prefix='oak-a2ui-demo-') as folder:
        root = Path(folder)
        runtime = DemoRuntime()
        controller = Controller(runtime, root / 'state.sqlite', root, None, config={'state_dir': folder, 'timezone': 'Europe/Kyiv'})
        controller.tools = Tools(controller, {})
        runtime.controller = controller
        bus = EventBus(controller)
        controller.emit = bus.emit
        controller.threads[11] = 'demo-thread'
        controller.loaded.add('demo-thread')
        with controller.db:
            controller.db.execute('INSERT INTO chats(chat_id,thread_id,tools_version) VALUES (?,?,?)', (11, 'demo-thread', controller.tools.version))
        gateway = WebGateway(controller, bus, '123:synthetic-demo-token', [11], {})
        # A synthetic, signed launch exercises the actual server-side validation.
        fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 11})}
        secret = hmac.new(b'WebAppData', gateway.token.encode(), hashlib.sha256).digest()
        fields['hash'] = hmac.new(secret, '\n'.join(k + '=' + v for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
        sdk = {'initData': urlencode(fields)}
        gateway.sdk_file.write_text('window.Telegram={WebApp:' + json.dumps(sdk) + '};for(const name of ["ready","expand","close","setHeaderColor","setBackgroundColor"]) window.Telegram.WebApp[name]=()=>{};')
        await controller.tools.execute(11, 'oak_a2ui', {'action': 'publish', 'messages_json': json.dumps(form_messages()),
                                                       'fallback': 'Синтетична локальна форма: реальний агент і Telegram не підключені.'},
                                       {'threadId': 'demo-thread', 'turnId': 'demo-initial'})
        runner = web.AppRunner(gateway.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', port).start()
        print(f'Синтетична локальна демонстрація: http://127.0.0.1:{port}', flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            for task in runtime.tasks:
                task.cancel()
            await asyncio.gather(*runtime.tasks, return_exceptions=True)
            await runner.cleanup()
            controller.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged local port.')
    try:
        asyncio.run(main(args.port))
    except KeyboardInterrupt:
        pass
