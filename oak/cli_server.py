"""Standalone trusted private CLI service; never starts a native/runtime PTY."""

import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys

sys.dont_write_bytecode = True
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from oak.cli_broker import CLIBroker, cleanup_orphan_caches
from oak.cli_process import PrivateProcess
from oak.cli_provider import Provider, immutable
from oak.executor import boundary_check, load_profile
from oak.signin_broker import origin


FIELDS = {'origin', 'parent_origin', 'gateway_uid', 'broker_uid', 'socket', 'port',
          'execution', 'state_dir', 'provider_manifest'}


def private_directory(path, uid, *, socket_directory=False):
    path = Path(path)
    if '..' in path.parts or path.resolve() != path:
        raise ValueError('Private CLI paths must be canonical.')
    immutable(path.parent)
    info = path.lstat()
    if (not path.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != uid
            or info.st_mode & (0o022 if socket_directory else 0o077)):
        raise ValueError('Private CLI directory unavailable.')
    return path


def settings(path):
    path = immutable(path)
    if '..' in path.parts or path.resolve() != path:
        raise ValueError('Private CLI paths must be canonical.')
    if path.stat().st_size > 65536:
        raise ValueError('Private CLI configuration exceeds its limit.')
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != FIELDS
            or any(type(value[k]) is not int or value[k] <= 0 for k in ('gateway_uid', 'broker_uid'))
            or value['gateway_uid'] == value['broker_uid'] or value['broker_uid'] != os.geteuid()
            or type(value['port']) is not int or not 1024 <= value['port'] <= 65535
            or not isinstance(value['execution'], dict) or set(value['execution']) != {'profile'}):
        raise ValueError('Invalid private CLI configuration.')
    if origin(value['origin']) == origin(value['parent_origin']):
        raise ValueError('Private CLI requires separate origins.')
    state = private_directory(value['state_dir'], value['broker_uid'])
    sock = Path(value['socket'])
    private_directory(sock.parent, value['broker_uid'], socket_directory=True)
    if sock.name != 'control.sock':
        raise ValueError('Private CLI requires its dedicated control socket.')
    profile = load_profile(value['execution'])
    if len({value['gateway_uid'], value['broker_uid'], profile['executor_uid']}) != 3:
        raise ValueError('Gateway, broker and executor identities must be distinct.')
    code_root = Path(__file__).resolve().parents[1]
    if profile['code_root'] != str(code_root):
        raise ValueError('The service must use the current immutable execution release.')
    provider_path = immutable(value['provider_manifest'])
    if ('..' in provider_path.parts or provider_path.resolve() != provider_path
            or provider_path.stat().st_size > 4*1024*1024):
        raise ValueError('Private CLI provider manifest unavailable.')
    provider_value = json.loads(provider_path.read_text())
    provider_prefix = Path(provider_value['python']).parent.parent
    if '..' in Path(provider_value['python']).parts:
        raise ValueError('Private CLI paths must be canonical.')
    exposed = [Path(profile[k]) for k in ('workspace', 'runtime_home', 'desktop_state')]
    exposed.extend(Path(p) for p in profile['readonly'])
    protected = [state, sock.parent, path, provider_path, provider_prefix]
    if any(a.resolve().is_relative_to(b.resolve()) or b.resolve().is_relative_to(a.resolve())
           for a in protected for b in exposed):
        raise ValueError('Private CLI resources must stay outside all executor mounts.')
    return value, code_root


async def run(path):
    config, code_root = settings(path)
    state, socket_path = Path(config['state_dir']), Path(config['socket'])
    descriptor = os.open(state/'broker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ValueError('Private CLI lock unavailable.')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cache = state/'cache'
        cache.mkdir(mode=0o700, exist_ok=True)
        cleanup_orphan_caches(cache)
        database = state/'receipts.sqlite3'
        if database.exists() and (database.is_symlink() or not database.is_file() or database.stat().st_nlink != 1):
            raise ValueError('Private CLI receipt store unavailable.')
        try:
            provider = Provider(config['provider_manifest'], code_root)
        except (OSError, ValueError, KeyError, TypeError):
            provider = None

        def boundary():
            return (boundary_check(config['execution'])
                    and load_profile(config['execution'])['code_root'] == str(code_root)
                    and (provider is None or provider.ready()))

        factory = (lambda: PrivateProcess(provider.command, cache)) if provider else None
        broker = CLIBroker(config['origin'], config['parent_origin'], config['gateway_uid'], database,
                           process_factory=factory, boundary_check=boundary)
        human, control = web.AppRunner(broker.human, access_log=None), web.AppRunner(broker.control, access_log=None)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        try:
            if socket_path.exists() or socket_path.is_symlink():
                info = socket_path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                    raise ValueError('Private CLI socket unavailable.')
                socket_path.unlink()
            await human.setup()
            await control.setup()
            await web.UnixSite(control, str(socket_path)).start()
            socket_path.chmod(0o660)
            await web.TCPSite(human, '127.0.0.1', config['port']).start()
            broker.sweeper = asyncio.create_task(broker.sweep())
            print(json.dumps({'service': 'private_cli', 'status': 'ready',
                              'provider_available': provider is not None and boundary()}), flush=True)
            await stop.wait()
        finally:
            try:
                await human.cleanup()
            finally:
                try:
                    await control.cleanup()
                finally:
                    try:
                        await broker.close()
                    finally:
                        socket_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        asyncio.run(run(args.config))
    except BaseException:
        print('{"service":"private_cli","status":"unavailable"}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
