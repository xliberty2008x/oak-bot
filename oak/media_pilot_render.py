"""Bounded, offline Python rendering for the private media-harness pilot.

Only the selected run workspace and public interpreter/media dependencies enter
the sandbox. The scorer, other runs, runtime home and Oak source do not.
"""

import importlib.util
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time


_MAX_SOURCE = 64 * 1024
_MAX_OUTPUT = 12000
_READY = b'OAK_PILOT_SANDBOX_READY\n'
_BOOTSTRAP = '''import resource, sys
resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
resource.setrlimit(resource.RLIMIT_CPU, (45, 46))
resource.setrlimit(resource.RLIMIT_FSIZE, (128*1024**2, 128*1024**2))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
sys.stderr.write("OAK_PILOT_SANDBOX_READY\\n")
sys.stderr.flush()
source = sys.stdin.buffer.read(65537)
if len(source) > 65536:
    raise ValueError("Source exceeds the rendering limit")
exec(compile(source, "<oak-pilot-render>", "exec"), {"__name__": "__main__"})
'''


def _command(workspace, binary):
    base = Path(sys.base_prefix).resolve(strict=True)
    interpreter = Path(sys.executable).resolve(strict=True).relative_to(base)
    command = [binary, '--unshare-all', '--die-with-parent', '--new-session',
               '--cap-drop', 'ALL', '--clearenv']
    for root in ('/usr', '/lib', '/lib64', '/bin'):
        if Path(root).exists():
            command += ['--ro-bind', root, root]
    if Path('/etc/ld.so.cache').is_file():
        command += ['--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache']
    # Some distro FFmpeg dependencies use /lib -> /etc/alternatives -> /usr
    # links (BLAS/LAPACK). Bind only public resolved shared-library targets,
    # rather than exposing /etc or its unrelated configuration.
    for link in sorted(Path('/etc/alternatives').glob('lib*.so*'))[:128]:
        resolved = link.resolve(strict=True)
        if resolved.is_file() and any(resolved.is_relative_to(root) for root in
                                      (Path('/usr'), Path('/lib'))):
            command += ['--ro-bind', str(resolved), str(link)]
    command += ['--dir', '/opt', '--dir', '/opt/pilot-site',
                '--ro-bind', str(base), '/opt/pilot-python']
    # Mount Pillow and its bundled native libraries, never all site-packages or
    # a virtualenv containing project/editable paths or private configuration.
    pillow = importlib.util.find_spec('PIL')
    if pillow is not None and pillow.submodule_search_locations:
        package = Path(next(iter(pillow.submodule_search_locations))).resolve(strict=True)
        command += ['--ro-bind', str(package), '/opt/pilot-site/PIL']
        libraries = package.parent / 'pillow.libs'
        if libraries.is_dir():
            command += ['--ro-bind', str(libraries), '/opt/pilot-site/pillow.libs']
    command += ['--bind', str(workspace), '/workspace']
    inputs = workspace / 'inputs'
    if inputs.is_symlink() or inputs.exists() and not inputs.is_dir():
        raise ValueError('Inputs must be an ordinary directory.')
    if inputs.is_dir():
        command += ['--ro-bind', str(inputs), '/workspace/inputs']
    command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                '--chdir', '/workspace']
    environment = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp',
                   'LANG': 'C.UTF-8', 'PYTHONHOME': '/opt/pilot-python',
                   'PYTHONPATH': '/opt/pilot-site', 'PYTHONDONTWRITEBYTECODE': '1',
                   'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    for name, value in environment.items():
        command += ['--setenv', name, value]
    return [*command, str(Path('/opt/pilot-python') / interpreter), '-S', '-c', _BOOTSTRAP]


def _kill(process):
    # start_new_session gives this invocation its own group. Killing bubblewrap
    # also tears down the private PID namespace, including subprocess children.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _run(command, source, timeout):
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    timed_out = False
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                               start_new_session=True)
    pending = memoryview(source)
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for name, stream in [('stdin', process.stdin), ('stdout', process.stdout),
                                 ('stderr', process.stderr)]:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_WRITE if name == 'stdin'
                                  else selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _kill(process)
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == 'stdin':
                        try:
                            count = os.write(key.fd, pending[:8192])
                            pending = pending[count:]
                        except BrokenPipeError:
                            pending = pending[:0]
                        if not pending:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        else:
                            buffer = buffers[key.data]
                            buffer.extend(chunk[:max(0, _MAX_OUTPUT + len(_READY) - len(buffer))])
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _kill(process)
    finally:
        if process.poll() is None:
            _kill(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    return process.returncode, timed_out, buffers


def _files(workspace):
    """Return bounded relative regular files; never follow model-made symlinks."""
    pending = [(workspace, 0)]
    files = []
    inspected = 0
    while pending and inspected < 512 and len(files) < 64:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                inspected += 1
                if inspected > 512 or len(files) >= 64:
                    break
                if entry.is_file(follow_symlinks=False):
                    files.append(str(Path(entry.path).relative_to(workspace)))
                elif depth < 8 and entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), depth + 1))
    return sorted(files)


def render_python(workspace: Path, source: str, timeout=60) -> dict:
    """Execute Python without networking or access to another run's evidence.

    Run the pilot with its Pillow-enabled interpreter. Missing bubblewrap or an
    unavailable sandbox fails closed; this helper never runs code on the host.
    Call with ``asyncio.to_thread`` from the asynchronous runtime tool handler.
    """
    started = time.monotonic()
    result = {'ok': False, 'stdout': '', 'stderr': '', 'files': []}
    if (not isinstance(source, str) or not source.strip()
            or len(source.encode('utf-8')) > _MAX_SOURCE
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 60):
        return {**result, 'error': 'invalid_render_request'}
    binary = shutil.which('bwrap')
    if binary is None:
        return {**result, 'error': 'render_sandbox_unavailable'}
    try:
        workspace = Path(workspace).resolve(strict=True)
        if not workspace.is_dir() or workspace == Path('/'):
            return {**result, 'error': 'invalid_render_workspace'}
        status, timed_out, buffers = _run(_command(workspace, binary),
                                        source.encode('utf-8'), timeout)
        stderr = bytes(buffers['stderr'])
        if not stderr.startswith(_READY):
            # Bubblewrap setup messages can contain host mount paths. They are
            # not model evidence, and never become an unconfined fallback.
            result['error'] = 'render_sandbox_unavailable'
        else:
            result.update(stdout=bytes(buffers['stdout'])[:_MAX_OUTPUT].decode('utf-8', 'replace'),
                          stderr=stderr[len(_READY):len(_READY) + _MAX_OUTPUT].decode('utf-8', 'replace'),
                          files=_files(workspace))
            result['ok'] = status == 0 and not timed_out
            if not result['ok']:
                result['error'] = 'render_timeout' if timed_out else 'render_failed'
    except (OSError, ValueError, subprocess.SubprocessError):
        result['error'] = 'render_sandbox_unavailable'
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result
