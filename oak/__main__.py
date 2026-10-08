"""Command-line entry points; no API keys are needed or accepted."""

import argparse
import asyncio
from contextlib import suppress
import fcntl
import json
import os
from pathlib import Path
import signal
import tempfile
import uuid

from .runtime import RuntimeClient
from .controller import Controller, MODEL
from .context import load_context


def load_config(config_path):
    config_file = Path(config_path).expanduser().resolve()
    config = json.loads(config_file.read_text())
    for name in ('state_dir', 'workspace', 'runtime_home', 'telegram_token_file', 'telegram_api_directory', 'piper_model', 'vosk_model', 'whisper_model'):
        value = config.get(name)
        if value:
            path = Path(value).expanduser()
            config[name] = str(path if path.is_absolute() else config_file.parent / path)
    config.setdefault('state_dir', str(config_file.parent / '.state'))
    config.setdefault('workspace', str(Path(config['state_dir']) / 'workspace'))
    if config.get('web', {}).get('access_key_file'):
        key = Path(config['web']['access_key_file']).expanduser()
        config['web']['access_key_file'] = str(key if key.is_absolute() else config_file.parent / key)
    return config_file, config


async def doctor(config_path=None):
    config = load_config(config_path)[1] if config_path else {}
    computer = {'enabled': bool(config.get('computer', {}).get('enabled'))}
    if computer['enabled']:
        from .computer import ComputerTools
        from .desktop import desktop_environment
        computer['managed'] = bool(config['computer'].get('managed'))
        with desktop_environment(config):
            try:
                desktop = ComputerTools(config['workspace'], config['computer'].get('display', ':90'))
                computer.update(await desktop.status())
            except (RuntimeError, OSError) as exc:
                computer.update(available=False, detail=str(exc))
    async with RuntimeClient(home=config.get('runtime_home'), config=config.get('runtime_config')) as client:
        models = await client.request('model/list', {})
        available = any(m.get('model') == MODEL or m.get('id') == MODEL for m in models['data'])
        apps = await client.request('app/installed', {'forceRefresh': True})
        installed = apps.get('apps', [])
        print(json.dumps({'status': 'ready' if available else 'unavailable', 'auth': 'subscription',
                          'model': MODEL, 'model_available': available,
                          'computer': computer,
                          'connected_apps': [a.get('runtimeName') for a in installed if a.get('enabled') and a.get('callable')]}))
        if not available:
            raise RuntimeError(f'{MODEL} is not available to this account.')


async def smoke(config_path=None):
    """Real subscription-backed round trips; no Telegram or private memory access."""
    config = load_config(config_path)[1] if config_path else {}
    with tempfile.TemporaryDirectory(prefix='oak-bot-') as tmp:
        received = asyncio.Queue()

        async def emit(chat_id, event):
            await received.put(event)

        async def finish():
            events = []
            async with asyncio.timeout(180):
                while True:
                    event = await received.get()
                    events.append(event)
                    if event['type'] == 'RUN_ERROR':
                        raise RuntimeError(event.get('message', 'Agent turn failed'))
                    if event['type'] == 'RUN_FINISHED':
                        return events

        def output(events):
            return ''.join(e.get('delta', '') for e in events if e['type'] == 'TEXT_MESSAGE_CONTENT')

        token = 'LOCAL_' + uuid.uuid4().hex[:12]
        async with RuntimeClient(cwd=tmp, sandbox='read-only', home=config.get('runtime_home'),
                                 config={**config.get('runtime_config', {}), 'approval_policy': 'never'}) as client:
            controller = Controller(client, Path(tmp) / 'state.sqlite', tmp, emit)
            pump = asyncio.create_task(controller.run_events())
            try:
                await controller.submit(1, f'Remember this smoke-test token: {token}. Reply only READY. Do not use any tools.', 1)
                first = await finish()
                if 'READY' not in output(first):
                    raise RuntimeError('First turn did not produce expected streamed output.')
                thread_id = controller.threads[1]
                # Force thread/resume on the next turn, using the stored ID.
                controller.loaded.clear()
                await controller.submit(1, 'Repeat the exact smoke-test token I gave you. Reply only with that token. Do not use any tools.', 2)
                second = await finish()
                if token not in output(second) or controller.threads[1] != thread_id:
                    raise RuntimeError('Persistent same-thread continuation failed.')
                await controller.submit(1, 'Write an 800-word explanation of a binary search algorithm. Do not use tools.', 3)
                await controller.submit(1, 'Change of instruction: reply only STEERING_OK. Do not write the explanation.', 4)
                third = await finish()
                if 'STEERING_OK' not in output(third):
                    raise RuntimeError('Steered turn did not acknowledge the new instruction.')
                await controller.submit(1, 'Write a 1500-word explanation of merge sort. Do not use tools.', 5)
                # turn/start acknowledges scheduling. Wait for actual generation
                # before testing interruption, which targets an in-flight turn.
                async with asyncio.timeout(180):
                    while True:
                        event = await received.get()
                        if event['type'] == 'TEXT_MESSAGE_CONTENT':
                            break
                        if event['type'] in {'RUN_ERROR', 'RUN_FINISHED'}:
                            raise RuntimeError('Cancellation probe ended before streaming started.')
                await controller.stop(1)
                fourth = await finish()
                if fourth[-1].get('status') != 'interrupted':
                    raise RuntimeError('Turn was not interrupted.')
                print(json.dumps({'auth': 'chatgpt', 'model': MODEL,
                    'streaming': True, 'resume': True, 'steering': True, 'interrupt': True,
                    'telegram_live': False, 'thread_id': thread_id,
                    'events': sum(map(len, (first, second, third, fourth)))}))
            finally:
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump
                controller.close()


async def run(config_path):
    from .telegram import TelegramError, TelegramGateway
    from .tools import Tools
    from .bus import EventBus
    from .desktop import ManagedDesktop
    config_file, config = load_config(config_path)

    allowed = set(config['allowed_user_ids'])
    if not allowed or any(type(x) is not int for x in allowed):
        raise RuntimeError('Set explicit numeric allowed_user_ids before starting.')
    state_dir = Path(config['state_dir'])
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = Path(config['telegram_token_file'])
    if token_file.stat().st_mode & 0o077:
        raise RuntimeError('Telegram token file must be private (chmod 600).')
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError('Telegram token file is empty.')
    cwd = Path(config['workspace'])
    cwd.mkdir(parents=True, exist_ok=True)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    with (state_dir / 'gateway.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another gateway is already using this state directory.') from None
        ready_file = state_dir / 'gateway-ready.json'
        ready_file.unlink(missing_ok=True)
        async with ManagedDesktop(config) as desktop, RuntimeClient(cwd=cwd, sandbox=config.get('sandbox', 'workspace-write'),
                                 home=config.get('runtime_home'),
                                 config={'web_search': 'live', **config.get('runtime_config', {})}) as client:
            controller = Controller(client, state_dir / 'state.sqlite', cwd, None,
                                    load_context(config, config_file.parent), config=config)
            controller.tools = Tools(controller, config)
            client.tool_handler = controller.tools.handle
            client.approval_handler = controller.interactions.approval
            client.request_input_handler = controller.interactions.user_input
            gateway = TelegramGateway(controller, token, allowed, state_dir,
                                      api_url=config.get('telegram_api_url', 'https://api.telegram.org'),
                                      local_directory=config.get('telegram_api_directory'))
            controller.bus = EventBus(controller)
            controller.bus.add_sink(gateway.emit)
            controller.emit = controller.bus.emit
            web_gateway = None
            tunnel = None
            try:
                identity = await gateway._api('getMe', {})
                expected = config.get('telegram_username')
                if expected and identity.get('username') != expected.lstrip('@'):
                    raise RuntimeError('Telegram token belongs to a different bot.')
                webhook = await gateway._api('getWebhookInfo', {})
                if webhook.get('url'):
                    raise RuntimeError('An existing Telegram webhook owns this bot; gateway not started.')
                if config.get('web', {}).get('enabled'):
                    from .web import WebGateway
                    web_gateway = WebGateway(controller, controller.bus, token, allowed, config['web'])
                    controller.web = web_gateway
                    await web_gateway.start()
                    if config['web'].get('tunnel') in {'quick', 'localhost'}:
                        from .tunnel import PreviewTunnel
                        tunnel = PreviewTunnel(config['web']['tunnel'], state_dir)
                        web_gateway.public_url = await tunnel.start(config['web'].get('port', 8765))
                        web_gateway.config['transport'] = 'poll'
                    if web_gateway.public_url.startswith('https://'):
                        for owner in allowed:
                            await gateway._api('setChatMenuButton', {'chat_id': owner, 'menu_button': {
                                'type': 'web_app', 'text': 'Oak', 'web_app': {'url': web_gateway.public_url}}})
                ready = {'status': 'ready', 'pid': os.getpid(), 'bot': identity['username'],
                                  'auth': 'subscription', 'model': MODEL,
                                  'web_url': web_gateway.public_url if web_gateway else None}
                def save_ready():
                    with tempfile.NamedTemporaryFile(mode='w', dir=state_dir, delete=False) as receipt:
                        json.dump(ready, receipt)
                        receipt.flush()
                        os.fsync(receipt.fileno())
                    os.replace(receipt.name, ready_file)

                async def publish_tunnel(url):
                    web_gateway.public_url = url
                    # A temporary Telegram failure must not tear down a
                    # healthy replacement tunnel or restart active turns.
                    pending = set(allowed)
                    while pending:
                        for owner in tuple(pending):
                            update = asyncio.create_task(gateway._api('setChatMenuButton', {
                                'chat_id': owner, 'menu_button': {
                                    'type': 'web_app', 'text': 'Oak', 'web_app': {'url': url}}},
                                timeout=10, rate_limit_attempts=1))
                            try:
                                await asyncio.shield(update)
                            except asyncio.CancelledError:
                                # The HTTP call runs in a thread: finish it
                                # before a newer URL can replace this menu.
                                with suppress(TelegramError):
                                    await update
                                raise
                            except TelegramError:
                                continue
                            pending.remove(owner)
                        if pending:
                            await asyncio.sleep(5)
                    ready['web_url'] = url
                    save_ready()
                    print('HTTPS tunnel recovered; Telegram menus updated.', flush=True)

                await controller.recover()
                save_ready()
                print(json.dumps(ready), flush=True)
                async with asyncio.TaskGroup() as group:
                    group.create_task(controller.run_events())
                    group.create_task(gateway.run())
                    group.create_task(controller.scheduler.run())
                    if tunnel:
                        group.create_task(tunnel.maintain(config['web'].get('port', 8765), publish_tunnel))
                    if desktop.enabled:
                        group.create_task(desktop.wait())
            finally:
                with suppress(OSError, ValueError):
                    if json.loads(ready_file.read_text()).get('pid') == os.getpid():
                        ready_file.unlink()
                await client.close()
                if web_gateway:
                    await web_gateway.close()
                if tunnel:
                    await tunnel.close()
                await controller.tools.browser.close()
                gateway.db.close()
                controller.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    doctor_parser = sub.add_parser('doctor', help='Verify subscription auth and model availability')
    doctor_parser.add_argument('--config', help='Use this deployment runtime home and configuration')
    smoke_parser = sub.add_parser('smoke', help='Run real Agent streaming/resume/steer/stop checks locally')
    smoke_parser.add_argument('--config', help='Use this deployment runtime home without private conversation context')
    start = sub.add_parser('run', help='Start the configured private Telegram gateway')
    start.add_argument('--config', required=True)
    imported = sub.add_parser('import-memory', help='Import a selected private memory export')
    imported.add_argument('--config', required=True)
    imported.add_argument('--source', required=True)
    imported.add_argument('--chat-id', required=True, type=int)
    managed = sub.add_parser('service', help='Manage the local Oak service')
    managed.add_argument('action', choices=['start', 'stop', 'status', 'install-autostart'])
    managed.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'service':
            from .service import service
            print(json.dumps(service(args.action, args.config)))
            return
        if args.command == 'import-memory':
            _, config = load_config(args.config)
            if args.chat_id not in config['allowed_user_ids']:
                raise ValueError('Memory can only be imported for an allowed owner.')
            Path(config['state_dir']).mkdir(parents=True, exist_ok=True, mode=0o700)
            c = Controller(None, Path(config['state_dir']) / 'state.sqlite', config['workspace'], None)
            try:
                ids = c.memory.import_file(args.chat_id, args.source)
                print(json.dumps({'imported_notes': len(ids)}))
            finally:
                c.close()
            return
        operation = {'doctor': doctor, 'smoke': smoke, 'run': run}[args.command]
        asyncio.run(operation(args.config))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as exc:
        # Avoid traceback locals and token-bearing transport URLs.
        print(f'{type(exc).__name__}: {exc}')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
