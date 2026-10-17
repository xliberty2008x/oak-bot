"""Fixed, fail-closed transport into Oak's separately provisioned execution UID.

This module never escalates privileges. systemd owns the two fixed socket services;
their immutable profile determines all mounts, executables and the execution UID.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import sys
import uuid

MAX_MESSAGE = 16 * 1024 * 1024
VERSION = 1
OPERATIONS = frozenset({
    'hello', 'probe', 'file.read', 'file.write', 'file.open_read', 'file.read_chunk',
    'file.close', 'file.open_write', 'file.write_chunk', 'file.finish_write',
    'media.generate_image', 'media.generate_video', 'media.montage',
    'media.speak', 'media.transcribe', 'research.search', 'research.fetch',
    'research.youtube', 'browser.run', 'browser.close', 'computer.run',
    'computer.type', 'computer.key', 'computer.status', 'remote.start',
    'remote.read', 'remote.write', 'remote.close', 'remote.type', 'remote.key',
    'quality.inspect',
})


class ExecutionUnavailable(RuntimeError):
    """Deliberately contains no remote diagnostics or paths."""


def encode(value):
    result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                        separators=(',', ':')).encode() + b'\n'
    if len(result) > MAX_MESSAGE:
        raise ValueError('Execution message exceeds limit')
    return result


def decode(raw):
    if not raw or len(raw) > MAX_MESSAGE or not raw.endswith(b'\n'):
        raise ValueError('Invalid execution message')
    def bad_constant(_):
        raise ValueError('Invalid JSON constant')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(raw, parse_constant=bad_constant, object_pairs_hook=unique)
    json.dumps(value, allow_nan=False)  # Exponent overflow also produces infinity.
    return value


def policy_digest(profile):
    return hashlib.sha256(encode({k: v for k, v in profile.items()
                                 if k != 'policy_digest'})).hexdigest()


def immutable(path):
    """No symlinks, writable ancestors, or non-root owners in trusted files."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        return False
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return False
    return True


def load_profile(execution):
    path = Path(execution['profile'])
    if not immutable(path) or path.stat().st_size > 1024 * 1024:
        raise ExecutionUnavailable('Execution boundary unavailable')
    profile = decode(path.read_bytes().rstrip(b'\n') + b'\n')
    if (profile.get('version') != VERSION
            or profile.get('policy_digest') != policy_digest(profile)
            or type(profile.get('executor_uid')) is not int
            or profile['executor_uid'] <= 0):
        raise ExecutionUnavailable('Execution boundary unavailable')
    manifest = profile.get('manifest')
    if not isinstance(manifest, dict) or not manifest:
        raise ExecutionUnavailable('Execution boundary unavailable')
    for filename, expected in manifest.items():
        if not immutable(filename) or hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
            raise ExecutionUnavailable('Execution boundary unavailable')
    return profile


def boundary_check(execution):
    """A current-boot root attestation is mandatory, never a configuration flag."""
    try:
        profile = load_profile(execution)
        path = Path(profile['receipt'])
        if not immutable(path) or path.stat().st_size > 64 * 1024:
            return False
        receipt = decode(path.read_bytes().rstrip(b'\n') + b'\n')
        checks = receipt.get('checks', {})
        required = {'uid', 'private_pid', 'no_privileges', 'code_readonly',
                    'trusted_files_denied', 'host_proc_denied', 'sockets_denied',
                    'private_network_denied', 'native_policy', 'firewall_active'}
        return (receipt.get('version') == VERSION
                and receipt.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                and receipt.get('policy_digest') == profile['policy_digest']
                and receipt.get('executor_uid') == profile['executor_uid']
                and required <= checks.keys()
                and all(checks[name] is True for name in required))
    except (OSError, ValueError, KeyError, TypeError, ExecutionUnavailable):
        return False


def require_boundary(execution):
    if not boundary_check(execution):
        raise ExecutionUnavailable('Execution boundary unavailable')
    return load_profile(execution)


class ExecutionClient:
    def __init__(self, execution):
        self.execution = dict(execution)
        self.profile = None
        self.reader = self.writer = None
        self._lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()

    async def start(self):
        async with self._connection_lock:
            if self.writer is not None:
                if not self.writer.is_closing() and not self.reader.at_eof():
                    return self
                await self._disconnect()
            self.profile = require_boundary(self.execution)
            self.reader, self.writer = await asyncio.wait_for(asyncio.open_unix_connection(
                self.profile['worker_socket'], limit=MAX_MESSAGE), 10)
            try:
                hello = await self.call('hello', {})
                if (hello.get('version') != VERSION
                        or hello.get('uid') != self.profile['executor_uid']
                        or hello.get('policy_digest') != self.profile['policy_digest']):
                    raise ExecutionUnavailable('Execution boundary unavailable')
            except BaseException:
                await self._disconnect()
                raise
        return self

    async def call(self, op, args=None):
        if op not in OPERATIONS or (args is not None and not isinstance(args, dict)):
            raise ValueError('Unsupported execution operation')
        if op != 'hello':
            await self.start()
        elif self.writer is None:
            raise ExecutionUnavailable('Execution boundary unavailable')
        async with self._lock:
            request_id = uuid.uuid4().hex
            try:
                self.writer.write(encode({'id': request_id, 'op': op, 'args': args or {}}))
                await self.writer.drain()
                raw = await asyncio.wait_for(self.reader.readline(), 185)
                result = decode(raw)
                if (not isinstance(result, dict) or result.get('id') != request_id
                        or type(result.get('ok')) is not bool):
                    raise ValueError('Invalid execution response')
                if not result['ok']:
                    error = result.get('error')
                    if not isinstance(error, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', error):
                        error = 'ExecutionError'
                    raise ExecutionUnavailable(error)
                return result.get('result')
            except ExecutionUnavailable:
                raise
            except BaseException:
                if op == 'hello':
                    await self._disconnect()
                else:
                    await self.close()
                raise

    async def close(self):
        async with self._connection_lock:
            await self._disconnect()

    async def _disconnect(self):
        writer, reader, self.writer, self.reader = self.writer, self.reader, None, None
        if writer:
            # Half-close first: EOF cancels the adapter and lets the worker reap
            # its desktop before another operation reconnects. Never replay the
            # operation whose outcome is uncertain.
            with contextlib.suppress(Exception):
                writer.write_eof()
                await asyncio.wait_for(reader.read(MAX_MESSAGE), 25)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def runtime_command(execution, original_argv):
    profile = require_boundary(execution)
    return [sys.executable, '-I', str(Path(profile['code_root']) / 'oak/executor.py'),
            'runtime', '--profile', execution['profile'], '--', *original_argv]


def sandbox_command(profile, mode, profile_path):
    """Fixed mounts; no caller-supplied argv is interpreted by the launcher."""
    if mode not in {'native', 'worker'}:
        raise ValueError('Unsupported execution mode')
    command = ['/usr/bin/bwrap', '--die-with-parent', '--new-session',
               '--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
               '--uid', str(profile['executor_uid']), '--gid', str(profile['executor_gid']),
               '--cap-drop', 'ALL', '--clearenv', '--proc', '/proc', '--dev', '/dev',
               '--tmpfs', '/tmp', '--dir', '/run', '--dir', '/home/executor']
    for source in profile['readonly']:
        command += ['--ro-bind', source, source]
    command += ['--ro-bind', profile['code_root'], profile['code_root'],
                '--ro-bind', profile_path, profile_path]
    for name in ('workspace', 'runtime_home'):
        command += ['--bind', profile[name], profile[name]]
    if mode == 'worker':
        command += ['--bind', profile['desktop_state'], profile['desktop_state']]
    environment = {'HOME': '/home/executor', 'PATH': profile['path'],
                   'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                   'CODEX_HOME': profile['runtime_home'], 'PYTHONNOUSERSITE': '1'}
    if profile.get('playwright_browsers_path'):
        environment['PLAYWRIGHT_BROWSERS_PATH'] = profile['playwright_browsers_path']
    for name, value in environment.items():
        command += ['--setenv', name, value]
    return command + ['--chdir', profile['workspace'], profile['python'], '-I',
                      str(Path(profile['code_root']) / 'oak/executor_worker.py'),
                      '--profile', profile_path, '--' + mode]


def probe_local(profile, args):
    """Probe dummy resources created by the root setup tool, never account data."""
    def denied_open(path, flags=os.O_RDONLY):
        try:
            fd = os.open(path, flags | os.O_CLOEXEC)
        except OSError:
            return True
        os.close(fd)
        return False

    def denied_connect(family, address):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.4)
                connection.connect(address)
        except OSError:
            return True
        return False

    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    files, sockets, hosts = args.get('files', []), args.get('sockets', []), args.get('hosts', [])
    if not (1 <= len(files) <= 8 and 1 <= len(sockets) <= 8 and 1 <= len(hosts) <= 8):
        raise ValueError('Probe requires bounded dummy resources')
    host_pid = args['host_pid']
    if type(host_pid) is not int or host_pid <= 1:
        raise ValueError('Invalid probe PID')
    return {'uid': os.getuid() == profile['executor_uid'],
            'private_pid': os.readlink('/proc/self/ns/pid') != args['host_pid_namespace'],
            'no_privileges': status.get('NoNewPrivs', '').strip() == '1'
                             and int(status.get('CapEff', '1').strip(), 16) == 0,
            'code_readonly': denied_open(str(Path(profile['code_root']) / 'oak/executor.py'), os.O_WRONLY),
            'trusted_files_denied': all(denied_open(path) for path in files),
            'host_proc_denied': denied_open(f'/proc/{host_pid}/environ'),
            'sockets_denied': all(denied_connect(socket.AF_UNIX, path) for path in sockets),
            'private_network_denied': all(denied_connect(
                socket.AF_INET6 if ':' in host else socket.AF_INET, (host, port))
                for host, port in hosts)}


def _runtime_proxy(profile, argv):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(profile['native_socket'])
        connection.sendall(encode({'argv': argv}))
        raw = bytearray()
        while not raw.endswith(b'\n') and len(raw) <= 65536:
            chunk = connection.recv(1)
            if not chunk:
                raise ExecutionUnavailable('Execution boundary unavailable')
            raw.extend(chunk)
        ready = decode(raw)
        if (ready.get('ready') is not True or ready.get('uid') != profile['executor_uid']
                or ready.get('policy_digest') != profile['policy_digest']):
            raise ExecutionUnavailable('Execution boundary unavailable')
        connection.settimeout(None)
        inputs = [connection, sys.stdin.buffer]
        while inputs:
            readable, _, _ = select.select(inputs, [], [])
            for source in readable:
                data = os.read(source.fileno(), 65536)
                if not data:
                    if source is connection:
                        return
                    connection.shutdown(socket.SHUT_WR)
                    inputs.remove(source)
                elif source is connection:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                else:
                    connection.sendall(data)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('serve-worker', 'serve-native', 'runtime'))
    parser.add_argument('--profile', required=True)
    options, remaining = parser.parse_known_args(argv)
    execution = {'profile': options.profile}
    try:
        if options.mode == 'runtime':
            profile = require_boundary(execution)
            _runtime_proxy(profile, remaining[1:] if remaining[:1] == ['--'] else remaining)
        else:
            profile = load_profile(execution)
            if (os.getuid(), os.getgid()) != (profile['executor_uid'], profile['executor_gid']):
                raise ExecutionUnavailable('Wrong execution identity')
            command = sandbox_command(profile, options.mode.removeprefix('serve-'), options.profile)
            os.execv(command[0], command)
    except Exception as error:
        # No exception message: command lines, paths and adapter errors can hold secrets.
        print(type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
