"""Fixed operations inside the untrusted executor's OS confinement.

This is not itself a sandbox. The immutable launcher establishes UID, mounts,
PID and network restrictions before invoking this file with Python -I.
"""

import argparse
import asyncio
import base64
import contextlib
import inspect
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import sys


MAX_MESSAGE = 16 * 1024 * 1024
MAX_CONTROL = 1024 * 1024
MAX_SNAPSHOT = 8 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024 * 1024
CHUNK = 64 * 1024
MAX_HANDLES = 8
OP_TIMEOUT = 180
WRITE_TIMEOUT = 15
ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,96}\Z')


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('Duplicate key')
        value[key] = item
    return value


def decode_json(data):
    def invalid(_value):
        raise ValueError('Invalid number')
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=invalid)
    json.dumps(value, allow_nan=False)  # Also reject overflowing exponent literals.
    return value


def _keys(value, required=(), optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Invalid arguments')


def _path_parts(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value:
        raise ValueError('Invalid path')
    parts = value.split('/')
    if value.startswith('/'):
        parts = parts[1:]
    if any(not part or part in {'.', '..'} for part in parts):
        raise ValueError('Invalid path')
    return parts


def _directory(path, *, trusted=False):
    path = str(path)
    if not path.startswith('/'):
        raise ValueError('Absolute directory required')
    parts = [] if path == '/' else _path_parts(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            if trusted:
                info = os.fstat(fd)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise PermissionError('Untrusted directory')
        return fd
    except BaseException:
        os.close(fd)
        raise


def load_profile(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute profile required')
    directory = _directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid == os.getuid() or info.st_mode & 0o022
                    or info.st_size > MAX_CONTROL or not os.fstatvfs(source.fileno()).f_flag & os.ST_RDONLY):
                raise PermissionError('Untrusted profile')
            profile = decode_json(source.read(MAX_CONTROL + 1))
    finally:
        os.close(directory)
    if not isinstance(profile, dict):
        raise ValueError('Invalid profile')
    for name in ('workspace', 'runtime_home', 'code_root', 'python', 'codex'):
        value = profile.get(name)
        if not isinstance(value, str) or not value.startswith('/'):
            raise ValueError('Invalid profile path')
        _path_parts(value)
    for name in ('executor_uid', 'executor_gid'):
        if type(profile.get(name)) is not int or profile[name] <= 0:
            raise ValueError('Invalid executor identity')
    if (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (
            profile['executor_uid'], profile['executor_uid'], profile['executor_gid'], profile['executor_gid']):
        raise PermissionError('Wrong executor identity')
    if not isinstance(profile.get('policy_digest'), str) or not re.fullmatch(r'[a-f0-9]{64}', profile['policy_digest']):
        raise ValueError('Invalid policy digest')
    if Path(profile['code_root']) != Path(__file__).absolute().parents[1]:
        raise PermissionError('Wrong executor code')
    return profile


def _identity(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= MAX_FILE:
        raise ValueError('Regular bounded file required')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _base64(value, maximum):
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4:
        raise ValueError('Invalid bytes')
    data = base64.b64decode(value, validate=True)
    if len(data) > maximum:
        raise ValueError('Too many bytes')
    return data


class FileTransfers:
    """Only held descriptors cross mutable workspace directory boundaries."""

    def __init__(self, profile):
        self.workspace = Path(profile['workspace'])
        self.generated = Path(profile['runtime_home']) / 'generated_images'
        self.handles = {}

    def _parent(self, path, namespace='workspace', create=False):
        if namespace not in {'workspace', 'generated_images'} or create and namespace != 'workspace':
            raise ValueError('Invalid namespace')
        root = self.workspace if namespace == 'workspace' else self.generated
        parts = _path_parts(path)
        if path.startswith('/'):
            prefix = _path_parts(str(root))
            if parts[:len(prefix)] != prefix or len(parts) <= len(prefix):
                raise ValueError('Path outside namespace')
            parts = parts[len(prefix):]
        if not parts:
            raise ValueError('File required')
        directory = _directory(root)
        try:
            for part in parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                os.close(directory)
                directory = child
            return directory, parts[-1], str(root.joinpath(*parts))
        except BaseException:
            os.close(directory)
            raise

    def _new(self, value):
        if len(self.handles) >= MAX_HANDLES:
            raise ValueError('Too many transfers')
        handle = secrets.token_urlsafe(32)
        self.handles[handle] = value
        return handle

    def _get(self, handle, kind=None):
        if not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', handle):
            raise ValueError('Invalid transfer')
        item = self.handles.get(handle)
        if item is None or kind is not None and item['kind'] != kind:
            raise ValueError('Unknown transfer')
        return item

    def open_read(self, path, namespace='workspace'):
        if len(self.handles) >= MAX_HANDLES:
            raise ValueError('Too many transfers')
        directory, name, absolute = self._parent(path, namespace)
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
            identity = _identity(fd)
            handle = self._new({'kind': 'read', 'fd': fd, 'identity': identity, 'size': 0, 'path': absolute})
            fd = None
            return {'handle': handle, 'size': identity[2]}
        finally:
            os.close(directory)
            if fd is not None:
                os.close(fd)

    def read_chunk(self, handle):
        item = self._get(handle, 'read')
        try:
            if _identity(item['fd']) != item['identity']:
                raise ValueError('Source changed')
            data = os.read(item['fd'], CHUNK)
            if _identity(item['fd']) != item['identity']:
                raise ValueError('Source changed')
            item['size'] += len(data)
            if not data and item['size'] != item['identity'][2]:
                raise ValueError('Source changed')
            return {'data': base64.b64encode(data).decode('ascii'), 'eof': item['size'] == item['identity'][2]}
        except BaseException:
            self.close(handle)
            raise

    @staticmethod
    def _target(directory, name):
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unsafe destination')

    def open_write(self, path):
        if len(self.handles) >= MAX_HANDLES:
            raise ValueError('Too many transfers')
        directory, name, absolute = self._parent(path, create=True)
        fd, temporary = None, None
        try:
            self._target(directory, name)
            temporary = '.oak-transfer-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
            handle = self._new({'kind': 'write', 'fd': fd, 'directory': directory, 'temporary': temporary,
                                'name': name, 'path': absolute, 'size': 0, 'identity': _identity(fd)})
            directory, fd = None, None
            return {'handle': handle}
        finally:
            if fd is not None:
                os.close(fd)
            if directory is not None:
                if temporary:
                    with contextlib.suppress(OSError):
                        os.unlink(temporary, dir_fd=directory)
                os.close(directory)

    def write_chunk(self, handle, data):
        item = self._get(handle, 'write')
        try:
            content = _base64(data, CHUNK)
            if item['size'] + len(content) > MAX_FILE or _identity(item['fd']) != item['identity']:
                raise ValueError('Destination changed or exceeded limit')
            remaining = memoryview(content)
            while remaining:
                count = os.write(item['fd'], remaining)
                if count <= 0:
                    raise OSError('Write failed')
                remaining = remaining[count:]
            item['size'] += len(content)
            identity = _identity(item['fd'])
            if identity[:3] != (*item['identity'][:2], item['size']):
                raise ValueError('Destination changed')
            item['identity'] = identity
            return {'size': item['size']}
        except BaseException:
            self.close(handle)
            raise

    def finish_write(self, handle):
        item = self._get(handle, 'write')
        try:
            if _identity(item['fd']) != item['identity']:
                raise ValueError('Destination changed')
            named = os.stat(item['temporary'], dir_fd=item['directory'], follow_symlinks=False)
            if (named.st_dev, named.st_ino) != item['identity'][:2]:
                raise ValueError('Destination changed')
            self._target(item['directory'], item['name'])
            os.fsync(item['fd'])
            os.replace(item['temporary'], item['name'], src_dir_fd=item['directory'], dst_dir_fd=item['directory'])
            item['temporary'] = None
            os.fsync(item['directory'])
            return {'path': item['path'], 'size': item['size']}
        finally:
            self.close(handle)

    def close(self, handle):
        item = self._get(handle)
        self.handles.pop(handle)
        try:
            os.close(item['fd'])
        finally:
            if item['kind'] == 'write':
                try:
                    if item['temporary']:
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(item['temporary'], dir_fd=item['directory'])
                finally:
                    os.close(item['directory'])
        return {'closed': True}

    def close_all(self):
        for handle in list(self.handles):
            with contextlib.suppress(OSError, ValueError):
                self.close(handle)

    def read(self, path):
        opened = self.open_read(path)
        handle = opened['handle']
        try:
            if opened['size'] > MAX_SNAPSHOT:
                raise ValueError('Use streaming transfer')
            chunks = []
            while True:
                chunk = self.read_chunk(handle)
                chunks.append(base64.b64decode(chunk['data']))
                if chunk['eof']:
                    break
            data = b''.join(chunks)
            return {'path': str(Path(self._get(handle)['path']).relative_to(self.workspace)),
                    'data': base64.b64encode(data).decode('ascii'), 'size': len(data)}
        finally:
            if handle in self.handles:
                self.close(handle)

    def write(self, path, data):
        content = _base64(data, MAX_SNAPSHOT)
        handle = self.open_write(path)['handle']
        try:
            for offset in range(0, len(content), CHUNK):
                self.write_chunk(handle, base64.b64encode(content[offset:offset + CHUNK]).decode('ascii'))
            return self.finish_write(handle)
        finally:
            if handle in self.handles:
                self.close(handle)


OPERATIONS = {
    'hello': ((), ()), 'probe': ((), None),
    'media.generate_image': (('title',), ('subtitle', 'width', 'height', 'background', 'accent')),
    'media.generate_video': ((), ('title', 'image_path', 'duration')),
    'media.montage': (('image_paths',), ('seconds_per_image',)),
    'media.speak': (('text',), ('language',)), 'media.transcribe': (('path',), ('language',)),
    'research.search': (('query',), ('limit',)), 'research.fetch': (('url',), ()), 'research.youtube': (('url',), ()),
    'browser.run': ((), ('action', 'url', 'selector', 'text', 'frame', 'files', 'timeout_ms', 'tab')),
    'browser.close': ((), ()),
    'computer.run': ((), ('action', 'x', 'y', 'end_x', 'end_y', 'button', 'clicks', 'text', 'key', 'direction', 'steps', 'seconds')),
    'computer.type': (('text',), ()), 'computer.key': (('key',), ()), 'computer.status': ((), ()),
    'file.read': (('path',), ()), 'file.write': (('path', 'data'), ()),
    'file.open_read': (('path',), ('namespace',)), 'file.read_chunk': (('handle',), ()),
    'file.close': (('handle',), ()), 'file.open_write': (('path',), ()),
    'file.write_chunk': (('handle', 'data'), ()), 'file.finish_write': (('handle',), ()),
    'quality.inspect': (('path', 'sha256'), ()),
    'spaces.search': (('query',), ('limit',)), 'spaces.inspect': (('space_id',), ()),
    'animation.render': (('image_path', 'closed_eyes_path', 'image_sha256', 'closed_eyes_sha256',
                          'eye_regions', 'hair_polygons'), ('seconds', 'fps', 'amplitude')),
    'remote.start': ((), ()), 'remote.read': (('id',), ()), 'remote.write': (('id', 'data'), ()), 'remote.close': (('id',), ()),
    'remote.type': (('id', 'text'), ()), 'remote.key': (('id', 'key'), ()),
}


class Worker:
    def __init__(self, profile):
        self.profile = profile
        self.files = FileTransfers(profile)
        self.media = self.research = self.browser = self.computer = self.desktop = self.remote = None

    def hello(self):
        return {'version': 1, 'uid': os.getuid(), 'policy_digest': self.profile['policy_digest']}

    def desktop_config(self):
        if not self.profile.get('display') or not self.profile.get('desktop_state'):
            raise ValueError('Desktop not configured')
        return {'workspace': self.profile['workspace'], 'state_dir': self.profile['desktop_state'],
                'computer': {'enabled': True, 'managed': True, 'display': self.profile['display'],
                             'width': self.profile.get('width', 1280), 'height': self.profile.get('height', 800)}}

    async def computer_status(self):
        # A doctor connection has its own worker. Reading the existing X server
        # must never start a second browser or take ownership of its profile.
        if self.computer is not None:
            return await self.computer.status()
        from oak.computer import ComputerTools
        from oak.desktop import desktop_environment
        with desktop_environment(self.desktop_config()):
            computer = ComputerTools(self.profile['workspace'], self.profile['display'])
            return await computer.status()

    async def ensure_desktop(self):
        if self.desktop is None:
            from oak.computer import ComputerTools
            from oak.desktop import ManagedDesktop
            desktop = ManagedDesktop(self.desktop_config())
            try:
                await desktop.__aenter__()
            except BaseException:
                await asyncio.shield(desktop.close())
                raise
            self.desktop = desktop
            self.computer = ComputerTools(self.profile['workspace'], desktop.display)
        return self.computer

    async def browser_context(self):
        await self.ensure_desktop()
        return await self.desktop.browser_context()

    async def remote_start(self):
        if self.remote is not None:
            raise ValueError('Remote already active')
        computer = await self.ensure_desktop()
        transport, child = socket.socketpair()
        transport.setblocking(False)
        state = {'id': secrets.token_hex(16), 'socket': transport, 'process': None, 'input_lock': asyncio.Lock()}
        self.remote = state
        launch = None
        try:
            launch = asyncio.create_task(asyncio.create_subprocess_exec(
                'x11vnc', '-norc', '-quiet', '-inetd', '-nopw', '-display', computer.display,
                '-nosel', '-noremote', '-clear_keys', '-timeout', '10', '-wait', '30',
                stdin=child, stdout=child, stderr=asyncio.subprocess.DEVNULL, start_new_session=True))
            state['process'] = await asyncio.shield(launch)
            return {'id': state['id']}
        except BaseException:
            if launch is not None:
                with contextlib.suppress(Exception):
                    state['process'] = await asyncio.shield(launch)
            await asyncio.shield(self.remote_close(state['id']))
            raise
        finally:
            child.close()

    def _remote(self, identifier):
        if not isinstance(identifier, str) or self.remote is None or not secrets.compare_digest(self.remote['id'], identifier):
            raise ValueError('Unknown remote')
        return self.remote

    async def remote_read(self, id):
        state = self._remote(id)
        try:
            data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(state['socket'], CHUNK), 1)
        except asyncio.TimeoutError:
            return {'data': '', 'eof': False}
        return {'data': base64.b64encode(data).decode('ascii'), 'eof': not data}

    async def remote_write(self, id, data):
        state = self._remote(id)
        content = _base64(data, CHUNK)
        async with state['input_lock']:
            self._remote(id)
            await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(state['socket'], content), 3)
        return {'size': len(content)}

    async def remote_type(self, id, text):
        state = self._remote(id)
        if not isinstance(text, str) or not 1 <= len(text) <= 10000 or '\x00' in text:
            raise ValueError('Invalid text')
        async with state['input_lock']:
            self._remote(id)
            await self.computer._type(text)
        return {'accepted': True}

    async def remote_key(self, id, key):
        state = self._remote(id)
        if not isinstance(key, str) or len(key) > 100 or not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+){0,5}', key):
            raise ValueError('Invalid key')
        chord = '+'.join('super' if part.lower() == 'meta' else part for part in key.split('+'))
        async with state['input_lock']:
            self._remote(id)
            await self.computer._key(chord)
        return {'accepted': True}

    async def remote_close(self, id):
        state = self._remote(id)
        self.remote = None
        state['socket'].close()
        process = state['process']
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        if self.computer is not None:
            with contextlib.suppress(Exception):
                await self.computer._exec('xdotool', 'keyup', 'Shift_L', 'Shift_R', 'Control_L', 'Control_R',
                                          'Alt_L', 'Alt_R', 'Super_L', 'Super_R')
            with contextlib.suppress(Exception):
                await self.computer._exec('xdotool', *(part for button in range(1, 10) for part in ('mouseup', str(button))))
        return {'closed': True}

    async def dispatch(self, op, args):
        if not isinstance(op, str) or op not in OPERATIONS:
            raise ValueError('Unknown operation')
        required, optional = OPERATIONS[op]
        if optional is not None:
            _keys(args, required, optional)
        elif not isinstance(args, dict):
            raise ValueError('Invalid arguments')
        if op == 'hello':
            return self.hello()
        if op == 'probe':
            from oak.executor import probe_local
            result = await asyncio.to_thread(probe_local, self.profile, args)
            return await result if inspect.isawaitable(result) else result
        group, method = op.split('.')
        if group == 'spaces':
            from oak.hf_spaces import Spaces
            return await asyncio.to_thread(getattr(Spaces(), method), **args)
        if group == 'animation':
            from oak.animation_tools import render
            return await asyncio.to_thread(render, self.files, **args)
        if group == 'quality':
            from oak.quality_media import inspect_media
            return await asyncio.to_thread(inspect_media, self.files, **args)
        if group == 'file':
            return getattr(self.files, method)(**args)
        if group == 'remote':
            return await getattr(self, 'remote_' + method)(**args)
        if group == 'computer':
            if method == 'status':
                return await self.computer_status()
            computer = await self.ensure_desktop()
            if self.remote is not None:
                raise ValueError('Remote owns desktop')
            return await computer.run(**({'action': method, **args} if method in {'type', 'key'} else args))
        if group == 'browser':
            if method == 'run' and self.remote is not None:
                # Observation would also reveal the owner's private manual input.
                raise ValueError('Remote owns desktop')
            if self.browser is None:
                if method == 'close':
                    return None
                from oak.browser import BrowserTools
                managed = self.profile.get('display') and self.profile.get('desktop_state')
                self.browser = BrowserTools(self.profile['workspace'],
                                            context_provider=self.browser_context if managed else None)
            return await getattr(self.browser, method)(**args)
        if group == 'media':
            if self.media is None:
                from oak.media import MediaTools
                models = self.profile.get('models', {})
                _keys(models, (), ('piper_model', 'vosk_model', 'whisper_model'))
                self.media = MediaTools(self.profile['workspace'], **models)
            return await asyncio.to_thread(getattr(self.media, method), **args)
        if self.research is None:
            from oak.research import ResearchTools
            self.research = ResearchTools(search_url=self.profile.get('search_url'))
        return await asyncio.to_thread(getattr(self.research, method), **args)

    async def close(self):
        self.files.close_all()
        if self.remote is not None:
            with contextlib.suppress(Exception):
                await self.remote_close(self.remote['id'])
        if self.browser is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.browser.close(), 10)
        if self.desktop is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.desktop.close(), 15)


def _encode(value):
    def convert(item):
        if isinstance(item, Path):
            return str(item)
        raise TypeError('Unsupported result')
    result = json.dumps(value, ensure_ascii=False, allow_nan=False, default=convert, separators=(',', ':')).encode('utf-8') + b'\n'
    if len(result) > MAX_MESSAGE:
        raise ValueError('Response too large')
    return result


def _request(raw):
    if len(raw) > MAX_MESSAGE or not raw.endswith(b'\n'):
        raise ValueError('Invalid message length')
    value = decode_json(raw)
    _keys(value, ('id', 'op', 'args'))
    if not isinstance(value['id'], str) or not ID_PATTERN.fullmatch(value['id']) or not isinstance(value['args'], dict):
        raise ValueError('Invalid request')
    if value['op'] == 'file.write':
        control = {**value, 'args': {key: item for key, item in value['args'].items() if key != 'data'}}
    else:
        control = value
    if len(_encode(control)) > MAX_CONTROL:
        raise ValueError('Control message too large')
    return value


async def _write_all(output, data):
    """Drain one framed response completely on the shared nonblocking wire FD.

    The stdin read transport sets O_NONBLOCK on the socket's open-file
    description, including stdout duplicates. Raw FileIO.write may therefore
    write only a prefix (or return None); a flush cannot send the remainder.
    """
    try:
        descriptor = output.fileno()
    except (AttributeError, OSError, ValueError):
        # In-memory streams in protocol tests have no readiness descriptor.
        view = memoryview(data)
        while view:
            written = output.write(view)
            if not isinstance(written, int) or written <= 0:
                raise BrokenPipeError('Output stream made no progress')
            view = view[written:]
        output.flush()
        return
    os.set_blocking(descriptor, False)
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(WRITE_TIMEOUT):
        view = memoryview(data)
        while view:
            try:
                written = os.write(descriptor, view)
            except InterruptedError:
                continue
            except BlockingIOError:
                ready = loop.create_future()
                def writable():
                    if not ready.done():
                        ready.set_result(None)
                loop.add_writer(descriptor, writable)
                try:
                    await ready
                finally:
                    loop.remove_writer(descriptor)
                continue
            if written <= 0:
                raise BrokenPipeError('Output stream made no progress')
            view = view[written:]


async def serve(profile, reader, output):
    worker = Worker(profile)
    try:
        while True:
            identifier = None
            try:
                raw = await reader.readline()
                if not raw:
                    return
                message = _request(raw)
                identifier = message['id']
                result = await _dispatch_connected(worker, message, reader)
                response = _encode({'id': identifier, 'ok': True, 'result': result})
            except asyncio.CancelledError:
                raise
            except PeerClosed:
                return
            except Exception as exc:
                response = _encode({'id': identifier, 'ok': False, 'error': type(exc).__name__})
                await _write_all(output, response)
                # Timed-out threads cannot be safely reused. The launcher tears
                # down the complete private PID namespace on this process exit.
                if isinstance(exc, (asyncio.TimeoutError, PipelineError)) or identifier is None:
                    return
                continue
            await _write_all(output, response)
    finally:
        await asyncio.shield(worker.close())


class PeerClosed(Exception):
    pass


class PipelineError(ValueError):
    pass


async def _dispatch_connected(worker, message, reader):
    """The private client sends its next request only after this response."""
    operation = asyncio.create_task(asyncio.wait_for(worker.dispatch(message['op'], message['args']), OP_TIMEOUT))
    connection = asyncio.create_task(reader.read(1))
    try:
        done, _ = await asyncio.wait((operation, connection), return_when=asyncio.FIRST_COMPLETED)
        if connection in done:
            if connection.result():
                raise PipelineError('Requests must be serialized')
            raise PeerClosed()
        return await operation
    finally:
        operation.cancel(); connection.cancel()
        await asyncio.gather(operation, connection, return_exceptions=True)


def native_argv(profile, value):
    _keys(value, ('argv',))
    args = value['argv']
    if (not isinstance(args, list) or len(args) > 128 or not all(isinstance(item, str) and '\x00' not in item for item in args)
            or len(args) < 4 or args[0] not in {'codex', profile['codex']}
            or args[1:4] != ['app-server', '--listen', 'stdio://'] or (len(args) - 4) % 2):
        raise ValueError('Invalid native command')
    for index in range(4, len(args), 2):
        if args[index] != '-c' or not re.match(r'[A-Za-z_][A-Za-z0-9_.-]*=', args[index + 1]):
            raise ValueError('Invalid native override')
        if args[index + 1].split('=', 1)[0].startswith('model_providers'):
            raise ValueError('Provider definitions are fixed')
    return [profile['codex'], *args[1:],
            '-c', 'model_provider="openai"', '-c', 'model="gpt-6.1-sol"',
            '-c', 'forced_login_method="chatgpt"']


def environment(profile):
    env = {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'HOME': profile['runtime_home'],
           'CODEX_HOME': profile['runtime_home'], 'PYTHONNOUSERSITE': '1'}
    if profile.get('playwright_browsers_path'):
        env['PLAYWRIGHT_BROWSERS_PATH'] = profile['playwright_browsers_path']
    return env


def _install_code_path(profile):
    root = Path(__file__).absolute().parents[1]
    if root != Path(profile['code_root']):
        raise PermissionError('Wrong code root')
    # Root-owned files are mapped to the overflow UID inside the user namespace;
    # synthetic parent directories need not have the host's original owner.
    # The outer launcher verifies their original root ownership and manifest.
    for path, directory in ((root, True), (root / 'oak', True), (Path(__file__).absolute(), False)):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_DIRECTORY if directory else 0))
        try:
            info = os.fstat(fd)
            if (info.st_uid == os.getuid() or info.st_mode & 0o022 or not os.fstatvfs(fd).f_flag & os.ST_RDONLY
                    or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
                raise PermissionError('Untrusted code')
        finally:
            os.close(fd)
    sys.path.insert(0, str(root))


async def _stdio_reader():
    reader = asyncio.StreamReader(limit=MAX_MESSAGE)
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    return reader


async def _native(profile, output):
    reader = await _stdio_reader()
    raw = await asyncio.wait_for(reader.readline(), 15)
    if not raw.endswith(b'\n') or len(raw) > MAX_CONTROL:
        raise ValueError('Invalid startup')
    value = decode_json(raw)
    worker = Worker(profile)
    if isinstance(value, dict) and set(value) == {'probe'}:
        result = await asyncio.wait_for(worker.dispatch('probe', value['probe']), OP_TIMEOUT)
        await _write_all(output, _encode({'ready': True, **worker.hello(), 'probe': result}))
        return
    args = native_argv(profile, value)
    await _write_all(output, _encode({'ready': True, **worker.hello()}))
    os.set_blocking(sys.stdin.fileno(), True)
    os.set_blocking(output.fileno(), True)
    os.execve(profile['codex'], args, environment(profile))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--native', action='store_true')
    modes.add_argument('--worker', action='store_true')
    arguments = parser.parse_args(argv)
    output = sys.stdout.buffer
    loop = None
    status = 0
    try:
        profile = load_profile(arguments.profile)
        _install_code_path(profile)
        os.chdir(profile['workspace'])
        os.umask(0o077)
        env = environment(profile)
        os.environ.clear(); os.environ.update(env)
        if not arguments.native:
            # Preserve one non-inherited wire FD; all native/Python adapter noise
            # goes to the launcher's stderr sink, never into JSON responses.
            output = os.fdopen(os.dup(sys.stdout.fileno()), 'wb', buffering=0)
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
            sys.stdout = sys.stderr
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run():
            if arguments.native:
                await _native(profile, output)
            else:
                await serve(profile, await _stdio_reader(), output)

        task = loop.create_task(run())
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, task.cancel)
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        status = 0
    except BaseException as exc:
        status = 1
        with contextlib.suppress(Exception):
            output.write(_encode({'ok': False, 'error': type(exc).__name__})); output.flush()
    finally:
        if loop is not None:
            loop.close()
    # Avoid waiting forever for a timed-out native parser thread. The immutable
    # launcher owns and reaps this worker's entire PID namespace/control group.
    os._exit(status)


if __name__ == '__main__':
    main()
