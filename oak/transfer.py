"""Stream untrusted file bytes across the execution boundary, never paths."""

import base64
import contextlib
import os
from pathlib import Path
import tempfile
import uuid


LIMIT = 2 * 1024 * 1024 * 1024
CHUNK = 64 * 1024


class ExecutorFiles:
    def __init__(self, client, workspace, state_dir):
        self.client, self.workspace = client, Path(workspace)
        self.directory = Path(state_dir) / 'artifact-snapshots'
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)

    def path(self, value):
        path = Path(value)
        if not path.is_absolute():
            path = self.workspace / path
        # Purely lexical. All untrusted filesystem access happens in the worker.
        path = Path(os.path.normpath(path))
        try:
            parts = path.relative_to(self.workspace).parts
        except ValueError:
            raise ValueError('Файл має бути в робочій папці Oak.') from None
        if not parts or any(p.startswith('.') or p.lower() in {'secrets', 'credentials', 'browser-profile'} for p in parts):
            raise ValueError('Приватні службові файли не надсилаються.')
        return path

    def stored(self, path):
        path = Path(path)
        if path.parent != self.directory or path.is_symlink() or not path.is_file():
            return False
        info = path.stat()
        return (info.st_uid == os.geteuid() and not info.st_mode & 0o077
                and info.st_nlink == 1)

    async def chunks(self, path, *, namespace='workspace', limit=LIMIT):
        opened = await self.client.call('file.open_read', {'path': str(path), 'namespace': namespace})
        handle = opened['handle']
        count = 0
        try:
            if type(opened['size']) is not int or not 0 <= opened['size'] <= limit:
                raise ValueError('Файл перевищує дозволений розмір.')
            while True:
                value = await self.client.call('file.read_chunk', {'handle': handle})
                data = base64.b64decode(value['data'], validate=True)
                count += len(data)
                if len(data) > CHUNK or count > opened['size'] or count > limit:
                    raise ValueError('Invalid file stream.')
                yield data
                if value['eof'] is True:
                    break
                if not data:
                    raise ValueError('Empty file stream chunk.')
            if count != opened['size']:
                raise ValueError('File changed during transfer.')
        finally:
            with contextlib.suppress(Exception):
                await self.client.call('file.close', {'handle': handle})

    async def read_bytes(self, path, *, limit=8 * 1024 * 1024, namespace='workspace'):
        data = bytearray()
        async for chunk in self.chunks(path, namespace=namespace, limit=limit):
            data.extend(chunk)
        return bytes(data)

    async def snapshot(self, path, *, namespace='workspace', limit=LIMIT):
        if type(limit) is not int or not 0 < limit <= LIMIT:
            raise ValueError('Invalid file-transfer limit.')
        if namespace == 'workspace' and self.stored(path):
            if Path(path).stat().st_size > limit:
                raise ValueError('Файл перевищує дозволений розмір.')
            return Path(path)
        suffix = Path(path).suffix
        if len(suffix) > 16 or not all(c.isalnum() or c == '.' for c in suffix):
            suffix = ''
        descriptor, temporary = tempfile.mkstemp(prefix='.incoming-', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                async for data in self.chunks(path, namespace=namespace, limit=limit):
                    output.write(data)
                output.flush()
                os.fsync(output.fileno())
            destination = self.directory / (uuid.uuid4().hex + suffix)
            os.replace(temporary, destination)
            return destination
        finally:
            Path(temporary).unlink(missing_ok=True)

    async def write_trusted(self, source, destination):
        # Source must be gateway-owned immutable input/snapshot, never a model
        # path. The caller validates that trusted root before calling this method.
        target = self.path(destination)
        opened = await self.client.call('file.open_write', {'path': str(target)})
        handle = opened['handle']
        count = 0
        try:
            with Path(source).open('rb') as input_file:
                while data := input_file.read(CHUNK):
                    count += len(data)
                    if count > LIMIT:
                        raise ValueError('File exceeds transfer limit.')
                    await self.client.call('file.write_chunk', {'handle': handle, 'data': base64.b64encode(data).decode('ascii')})
            result = await self.client.call('file.finish_write', {'handle': handle})
            if result.get('size') != count or result.get('path') != str(target):
                raise ValueError('Invalid file-transfer completion.')
            return target, count
        finally:
            with contextlib.suppress(Exception):
                await self.client.call('file.close', {'handle': handle})
