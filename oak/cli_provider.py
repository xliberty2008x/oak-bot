"""Validate the fixed root-owned private Colab installation before launch."""

import hashlib
import json
from pathlib import Path
import stat

from .colab_auth import UPSTREAM_COMMIT


def immutable(path, *, symlink=False):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('An immutable absolute path is required.')
    for item in (path, *path.parents):
        info = item.lstat()
        link = stat.S_ISLNK(info.st_mode)
        if (info.st_uid != 0 or (link and (item != path or not symlink))
                or (not link and info.st_mode & 0o022)
                or not (link or stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
            raise ValueError('Private CLI dependencies must be root-owned and immutable.')
    if path.is_symlink():
        immutable(path.resolve())
    return path


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(python):
    python = Path(python)
    if python.name != 'python' or python.parent.name != 'bin':
        raise ValueError('Select the fixed private virtual environment Python.')
    prefix = python.parent.parent
    immutable(prefix)
    files, tracked = {}, set()
    for path in [prefix, *sorted(prefix.rglob('*'))]:
        immutable(path, symlink=True)
        tracked.update((path, *path.parents))
        if path.is_symlink():
            target = path.resolve()
            if not target.is_relative_to(prefix) and path not in {
                    prefix/'bin/python', prefix/'bin/python3', prefix/'bin/python3.12'}:
                raise ValueError('Unexpected private CLI dependency symlink.')
            tracked.update((target, *target.parents))
            if target.is_file():
                files[str(target)] = sha256(target)
        elif path.is_file():
            files[str(path)] = sha256(path)
    if len(files) > 20000:
        raise ValueError('Private CLI installation exceeds the manifest limit.')
    if 'include-system-site-packages = false' not in (prefix/'pyvenv.cfg').read_text():
        raise ValueError('Private CLI must not import global site packages.')
    direct = list(prefix.glob('lib/python*/site-packages/google_colab_cli-*.dist-info/direct_url.json'))
    if (len(direct) != 1 or json.loads(direct[0].read_text()).get('vcs_info', {}).get('commit_id') != UPSTREAM_COMMIT):
        raise ValueError('The approved Colab source revision is required.')
    return files, tracked


def manifest(python):
    files, _ = inventory(python)
    return {'upstream_commit': UPSTREAM_COMMIT, 'python': str(python), 'files': files}


def stamp(path):
    value = Path(path).lstat()
    return (value.st_uid, value.st_gid, value.st_mode, value.st_ino,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class Provider:
    def __init__(self, path, code_root):
        path = immutable(path)
        if path.stat().st_size > 4*1024*1024:
            raise ValueError('Private CLI manifest exceeds its limit.')
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or set(value) != {'upstream_commit', 'python', 'files'}
                or value['upstream_commit'] != UPSTREAM_COMMIT or not isinstance(value['python'], str)):
            raise ValueError('Invalid private CLI provider manifest.')
        actual, tracked = inventory(value['python'])
        if value['files'] != actual:
            raise ValueError('Private CLI installation differs from its approved manifest.')
        adapter = immutable(Path(code_root)/'oak/colab_auth.py')
        tracked.update((path, adapter, *path.parents, *adapter.parents))
        prefix = Path(value['python']).parent.parent
        content = {item for item in tracked if item.is_relative_to(prefix)
                   or item in {path, adapter, Path(value['python']).resolve()}}
        self.stamps = {item: stamp(item) for item in content}
        self.permissions = {item: stamp(item)[:3] for item in tracked-content}
        self.command = (value['python'], '-I', str(adapter))

    def ready(self):
        # A root package update, chmod, replacement or added file changes a
        # tracked file/directory. Do not use a cached ready flag after that.
        try:
            return (all(stamp(path) == previous for path, previous in self.stamps.items())
                    and all(stamp(path)[:3] == previous for path, previous in self.permissions.items()))
        except OSError:
            return False
