"""An explicitly managed, private X11 desktop for Oak deployments."""

import argparse
import asyncio
from contextlib import contextmanager, suppress
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sys
import tempfile

from .computer import ComputerTools


_CHILD = '''
import ctypes, os, signal, sys
parent = int(sys.argv[1])
if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
    raise OSError("Cannot bind desktop child lifetime")
if os.getppid() != parent:
    raise SystemExit(1)
os.execvp(sys.argv[2], sys.argv[2:])
'''


@contextmanager
def desktop_environment(config):
    """Use the deployment's cookie without starting or taking over its display."""
    settings = config.get('computer', {})
    previous = {}
    if settings.get('enabled') and settings.get('managed'):
        values = {'DISPLAY': settings.get('display', ':90'),
                  'XAUTHORITY': str(Path(config['state_dir']).resolve() / 'desktop' / 'Xauthority')}
        previous = {name: os.environ.get(name) for name in values}
        os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class ManagedDesktop:
    def __init__(self, config):
        self.config = config
        settings = config.get('computer', {})
        self.enabled = bool(settings.get('enabled') and settings.get('managed'))
        self.display = settings.get('display', ':90')
        self.width, self.height = settings.get('width', 1280), settings.get('height', 800)
        if self.enabled:
            settings.setdefault('display', self.display)
            if not isinstance(self.display, str) or not re.fullmatch(r':[0-9]{1,5}', self.display):
                raise ValueError('Managed desktop display must be a local server such as :90.')
            if any(type(n) is not int or not 640 <= n <= 3840 for n in (self.width, self.height)):
                raise ValueError('Managed desktop dimensions must be integers between 640 and 3840.')
        self.directory = Path(config['state_dir']).resolve() / 'desktop'
        self.processes = []
        self.lock = None
        self.environment = None
        self.log = None

    async def _spawn(self, name, *args):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', _CHILD, str(os.getpid()), *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=self.log,
            stderr=self.log, start_new_session=True)
        self.processes.append((name, process))
        return process

    async def _command(self, *args, data=None):
        process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(data), 10)
        except BaseException:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError('Managed desktop command failed.')
        return output

    async def _until(self, probe):
        try:
            async with asyncio.timeout(20):
                while True:
                    for name, process in self.processes:
                        if process.returncode is not None:
                            raise RuntimeError(f'Managed {name} stopped; inspect {self.directory / "desktop.log"}.')
                    try:
                        result = await probe()
                        if result:
                            return result
                    except RuntimeError:
                        pass
                    await asyncio.sleep(0.1)
        except TimeoutError:
            raise RuntimeError(f'Managed desktop timed out; inspect {self.directory / "desktop.log"}.') from None

    async def __aenter__(self):
        if not self.enabled:
            return self
        if os.geteuid() == 0:
            raise RuntimeError('Run Oak as a non-root user; the desktop browser requires its sandbox.')
        missing = [name for name in ('Xvfb', 'xauth', 'openbox', 'xdotool', 'xmodmap') if not shutil.which(name)]
        if missing:
            raise RuntimeError('Install managed desktop dependencies: ' + ', '.join(missing))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.lock = (self.directory / 'desktop.lock').open('a')
        try:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('This deployment already owns a managed desktop.') from None
            number = self.display[1:]
            if Path(f'/tmp/.X{number}-lock').exists() or Path(f'/tmp/.X11-unix/X{number}').exists():
                raise RuntimeError(f'Display {self.display} is occupied; choose an unused display. Nothing was stopped.')
            from playwright.async_api import async_playwright
            async with async_playwright() as playwright:
                browser = playwright.chromium.executable_path
            if not Path(browser).is_file():
                raise RuntimeError('Install the desktop browser with python -m playwright install chromium.')
            self.environment = desktop_environment(self.config)
            self.environment.__enter__()
            authority = self.directory / 'Xauthority'
            authority.touch(mode=0o600)
            authority.chmod(0o600)
            await self._command('xauth', '-f', str(authority), data=(
                f'add {self.display} MIT-MAGIC-COOKIE-1 {secrets.token_hex(16)}\n').encode())
            self.log = (self.directory / 'desktop.log').open('wb')
            (self.directory / 'desktop.log').chmod(0o600)
            await self._spawn('X server', 'Xvfb', self.display, '-screen', '0',
                              f'{self.width}x{self.height}x24', '-auth', str(authority), '-nolisten', 'tcp')
            computer = ComputerTools(self.config['workspace'], self.display)
            await self._until(computer.status)
            await self._spawn('window manager', 'openbox')
            profile = self.directory / 'browser'
            profile.mkdir(mode=0o700, exist_ok=True)
            profile.chmod(0o700)
            await self._spawn('browser', browser, f'--user-data-dir={profile}', '--ozone-platform=x11',
                              '--no-first-run', '--no-default-browser-check', '--disable-dev-shm-usage',
                              f'--window-size={self.width},{self.height}', '--start-maximized', 'about:blank')
            await self._until(lambda: self._command('xdotool', 'search', '--onlyvisible', '--class', 'chromium'))
            return self
        except BaseException:
            await asyncio.shield(self.close())
            raise

    async def wait(self):
        """Any desktop child exit restarts the gateway through its supervisor."""
        tasks = {asyncio.create_task(process.wait()): name for name, process in self.processes}
        if not tasks:
            return
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            name = tasks[next(iter(done))]
            raise RuntimeError(f'Managed {name} stopped; inspect {self.directory / "desktop.log"}.')
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        for _, process in reversed(self.processes):
            # Each process was launched as the leader of a new, private group.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        self.processes.clear()
        if self.log:
            self.log.close()
            self.log = None
        if self.environment:
            self.environment.__exit__(None, None, None)
            self.environment = None
        if self.lock:
            self.lock.close()
            self.lock = None

    async def __aexit__(self, *_):
        await asyncio.shield(self.close())


async def check(config):
    if not config.get('computer', {}).get('enabled') or not config['computer'].get('managed'):
        raise ValueError('Desktop check requires computer.enabled and computer.managed.')
    # Never open the deployment browser profile or its logged-in sessions during a check.
    with check_directory(config) as folder:
        probe = {**config, 'state_dir': folder, 'workspace': folder}
        async with ManagedDesktop(probe) as desktop:
            computer = ComputerTools(folder, desktop.display)
            status = await computer.status()
            page = Path(folder) / 'input.html'
            page.write_text('<!doctype html><meta charset="utf-8"><title>Oak desktop check</title>'
                            '<textarea autofocus oninput="document.title=this.value"></textarea>')
            await computer.run(action='key', key='ctrl+l')
            await computer.run(action='type', text=page.as_uri())
            await computer.run(action='key', key='Return')

            async def title_is(expected):
                title = await desktop._command('xdotool', 'getactivewindow', 'getwindowname')
                return expected in title.decode()

            await desktop._until(lambda: title_is('Oak desktop check'))
            marker = 'Oak перевірка 123'
            await computer.run(action='type', text=marker)
            await desktop._until(lambda: title_is(marker))
            await computer.run(action='move', x=37, y=41)
            pointer = (await desktop._command('xdotool', 'getmouselocation', '--shell')).decode()
            if 'X=37\n' not in pointer or 'Y=41\n' not in pointer:
                raise RuntimeError('Desktop pointer check failed.')
            screenshot = await computer.run(action='screenshot')
            if Path(screenshot['path']).stat().st_size < 100:
                raise RuntimeError('Desktop screenshot check failed.')
            print(json.dumps({**status, 'screenshot': True, 'keyboard': True, 'mouse': True,
                              'browser_sandbox': True, 'model_tested': False, 'telegram_tested': False}))


@contextmanager
def check_directory(config):
    with tempfile.TemporaryDirectory(prefix='oak-desktop-check-') as folder:
        try:
            yield folder
        except Exception as exc:
            log = Path(folder) / 'desktop' / 'desktop.log'
            if log.exists():
                target = Path(config['state_dir']) / 'desktop-check.log'
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    output.write(log.read_bytes()[-1024 * 1024:])
                os.replace(output.name, target)
                raise RuntimeError(f'Desktop check failed; inspect {target}.') from exc
            raise


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--check', action='store_true', required=True,
                        help='Start an isolated desktop, verify input and screenshot, then stop it')
    args = parser.parse_args()
    from .__main__ import load_config
    try:
        asyncio.run(check(load_config(args.config)[1]))
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise SystemExit(130) from None
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
