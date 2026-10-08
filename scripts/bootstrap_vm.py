#!/usr/bin/env python3
"""Provision a supported Linux VM and deploy an owner-configured Oak instance."""

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / '.venv/bin/python'
RUNTIME_VERSION = '0.159.2'
PACKAGES = ['ca-certificates', 'curl', 'git', 'python3', 'python3-venv', 'python3-pip',
            'nodejs', 'npm', 'ffmpeg', 'fonts-dejavu-core', 'fonts-noto-color-emoji',
            'openssh-client', 'cron', 'xvfb', 'openbox', 'dbus-x11', 'xauth',
            'xdotool', 'x11-utils', 'x11-xserver-utils', 'x11vnc', 'xterm', 'tzdata', 'apparmor']


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=REPO, check=True, **kwargs)


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2)
            output.write('\n')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def absolute(value, base):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def settings(args):
    target = Path(args.config).expanduser().resolve()
    if target.is_relative_to(REPO):
        raise ValueError('Keep the VM configuration outside the repository.')
    exists = target.exists()
    config = json.loads(target.read_text()) if exists else json.loads((REPO / 'config.example.json').read_text())
    prepared = exists and not (absolute(config.get('state_dir', '.state'), target.parent) / 'state.sqlite').exists()
    if exists and target.stat().st_mode & 0o077:
        raise ValueError('The existing configuration must be private (chmod 600).')
    if not exists:
        config.update(state_dir=str(target.parent / 'state'), workspace=str(target.parent / 'workspace'),
                      runtime_home=str(target.parent / 'runtime'),
                      telegram_token_file=str(target.parent / 'telegram-token'), timezone=args.timezone or 'UTC')
        config['computer'] = {'enabled': True, 'managed': True, 'display': args.display or ':90',
                              'width': 1280, 'height': 800}
        config['web'] = {'enabled': True, 'host': '127.0.0.1', 'port': args.port or 18765,
                         'transport': 'poll', 'tunnel': 'localhost'}
        if args.public_url:
            config['web'].update(public_url=args.public_url, tunnel=None, transport='sse')
    requested = {'telegram_username': args.bot_username.lstrip('@') if args.bot_username else None,
                 'allowed_user_ids': [args.owner_id] if args.owner_id else None,
                 'telegram_token_file': str(Path(args.token_file).expanduser().resolve()) if args.token_file else None,
                 'timezone': args.timezone}
    for key, value in requested.items():
        if value is None:
            continue
        previous = config.get(key)
        if key == 'telegram_token_file' and previous:
            previous = str(absolute(previous, target.parent))
        if exists and not prepared and previous not in (None, [], value):
            raise ValueError(f'Existing {key} differs; edit your private config explicitly before retrying.')
        config[key] = value
    for key, value, previous in [('display', args.display, config['computer'].get('display')),
                                 ('port', args.port, config['web'].get('port')),
                                 ('public_url', args.public_url, config['web'].get('public_url'))]:
        if exists and not prepared and value is not None and previous != value:
            raise ValueError(f'Existing {key} differs; existing deployment was preserved.')
    if prepared:
        if args.display:
            config['computer']['display'] = args.display
        if args.port:
            config['web']['port'] = args.port
        if args.public_url:
            config['web'].update(public_url=args.public_url, tunnel=None, transport='sse')
    for key in ('state_dir', 'workspace', 'runtime_home', 'telegram_token_file'):
        config[key] = str(absolute(config[key], target.parent))
    ZoneInfo(config['timezone'])
    return target, config


def locked(path):
    if not path.exists():
        return False
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, data=None, headers=None, *, follow_redirects=True):
    request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers={'Content-Type': 'application/json', **(headers or {})})
    open_request = urlopen if follow_redirects else build_opener(NoRedirect()).open
    with open_request(request, timeout=25) as response:
        return json.load(response)


def telegram(config, method, data):
    endpoint = config.get('telegram_api_url', 'https://api.telegram.org')
    parsed = urlsplit(endpoint)
    try:
        local = ipaddress.ip_address(parsed.hostname or '').is_loopback and parsed.port is not None
    except ValueError:
        local = False
    if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')
            or not (endpoint.rstrip('/') == 'https://api.telegram.org'
                    or (local and parsed.scheme in ('http', 'https')))):
        raise ValueError('Telegram verification requires the official HTTPS or an explicit loopback endpoint.')
    path = Path(config['telegram_token_file'])
    if path.stat().st_mode & 0o077:
        raise ValueError('Telegram token must be private (chmod 600).')
    token = path.read_text().strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise ValueError('Invalid Telegram token file; its contents were not printed.')
    try:
        result = request_json(endpoint.rstrip('/') + '/bot' + token + '/' + method, data,
                              follow_redirects=False)
    except Exception:
        raise RuntimeError('Telegram verification failed; check the token file and network.') from None
    if not result.get('ok'):
        raise RuntimeError('Telegram rejected the verification request.')
    return result['result']


def verify_bot(config):
    identity = telegram(config, 'getMe', {})
    if identity.get('username') != config['telegram_username']:
        raise ValueError('The token belongs to a different bot. No gateway was started.')
    if telegram(config, 'getWebhookInfo', {}).get('url'):
        raise ValueError('An existing webhook owns this bot. Migrate it explicitly before starting Oak.')


def status(target):
    output = run([PYTHON, '-m', 'oak', 'service', 'status', '--config', target],
                 capture_output=True, text=True)
    return json.loads(output.stdout)


def verify_live(target, config):
    verify_bot(config)
    state = Path(config['state_dir'])
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        current = status(target)
        try:
            ready = json.loads((state / 'gateway-ready.json').read_text())
        except (OSError, ValueError):
            ready = {}
        if (current.get('worker_running') and ready.get('pid') == current.get('worker_pid')
                and ready.get('model') == 'gpt-6.1-sol' and ready.get('auth') == 'subscription'):
            if ready.get('bot') != config['telegram_username']:
                raise RuntimeError('The running bot differs from the selected configuration.')
            break
        if current.get('restarts', 0) >= 2 or current.get('status') != 'running':
            raise RuntimeError('Gateway did not become ready. Inspect its private service log.')
        time.sleep(1)
    else:
        raise RuntimeError('Gateway readiness timed out. Inspect its private service log.')
    origin = (ready.get('web_url') or '').rstrip('/')
    if urlsplit(origin).scheme != 'https':
        raise RuntimeError('The gateway has no HTTPS Mini App address.')
    for owner in config['allowed_user_ids']:
        menu = telegram(config, 'getChatMenuButton', {'chat_id': owner})
        if menu.get('web_app', {}).get('url', '').rstrip('/') != origin:
            raise RuntimeError('Telegram menu does not match the running Mini App.')
    key_file = absolute(config['web'].get('access_key_file') or state / 'web-access-keys.json', target.parent)
    key = json.loads(key_file.read_text())[str(config['allowed_user_ids'][0])]
    try:
        with urlopen(origin + '/', timeout=25) as response:
            if response.status != 200 or b'computer-switch' not in response.read(1024 * 1024):
                raise RuntimeError('Unexpected panel page')
        try:
            request_json(origin + '/api/panel')
        except HTTPError as error:
            if error.code != 401:
                raise
        else:
            raise RuntimeError('The panel unexpectedly allows anonymous access')
        session = request_json(origin + '/api/session', {'key': key}, {'Origin': origin})
        panel = request_json(origin + '/api/panel', headers={
            'Origin': origin, 'Authorization': 'Bearer ' + session['access_token']})
        if panel['settings']['auth'] != 'chatgpt' or not panel['computer']['configured']:
            raise RuntimeError('Runtime or computer is unavailable')
        remote = request_json(origin + '/api/remote', headers={
            'Origin': origin, 'Authorization': 'Bearer ' + session['access_token']})
        if remote.get('configured') is not True:
            raise RuntimeError('Manual remote desktop is unavailable')
    except Exception:
        raise RuntimeError('Public panel verification failed; inspect HTTPS routing and the private log.') from None
    return {'status': 'ready', 'bot': config['telegram_username'], 'public_url': origin,
            'model': ready['model'], 'computer_enabled': panel['computer']['enabled'],
            'remote_configured': remote['configured'],
            'telegram_client_verified': False}


def browser_policy():
    """Ubuntu restricts user namespaces for otherwise unconfined downloads."""
    control = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
    if not control.exists() or control.read_text().strip() != '1':
        return
    output = run([PYTHON, '-c', 'from playwright.sync_api import sync_playwright; '
                  'p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()'],
                 capture_output=True, text=True)
    browser = Path(output.stdout.strip()).resolve(strict=True)
    if not re.fullmatch(r'/[A-Za-z0-9/._ +@-]+', str(browser)):
        raise ValueError('Browser path contains unsupported AppArmor pattern characters.')
    name = 'oak-browser-' + hashlib.sha256(str(Path.home()).encode()).hexdigest()[:12]
    profile = ('abi <abi/4.0>,\ninclude <tunables/global>\n'
               f'profile {name} "{browser}" flags=(unconfined) {{\n  userns,\n}}\n')
    with tempfile.NamedTemporaryFile(mode='w') as source:
        source.write(profile)
        source.flush()
        destination = '/etc/apparmor.d/' + name
        run(['sudo', 'install', '-m', '0644', source.name, destination])
        run(['sudo', 'apparmor_parser', '-r', destination])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='~/.local/share/oak-bot/default/config.json')
    parser.add_argument('--owner-id', type=int)
    parser.add_argument('--bot-username')
    parser.add_argument('--token-file')
    parser.add_argument('--display')
    parser.add_argument('--port', type=int)
    parser.add_argument('--timezone')
    parser.add_argument('--public-url')
    for flag in ('prepare-only', 'skip-system', 'skip-voice', 'skip-autostart'):
        parser.add_argument('--' + flag, action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() == 0:
        raise ValueError('Run as a normal deployment user with sudo, not root.')
    release = platform.freedesktop_os_release()
    if ((release.get('ID'), release.get('VERSION_ID')) not in
            {('ubuntu', '24.04'), ('debian', '12'), ('debian', '13')}
            or platform.machine() != 'x86_64' or sys.version_info < (3, 11)):
        raise ValueError('Supported: Ubuntu 24.04 or Debian 12/13 on amd64, Python 3.11+.')
    if args.owner_id is not None and args.owner_id <= 0:
        raise ValueError('Use a positive numeric Telegram owner ID.')
    if args.bot_username and not re.fullmatch(r'@?[A-Za-z0-9_]{5,32}', args.bot_username):
        raise ValueError('Invalid bot username.')
    if args.display and not re.fullmatch(r':[0-9]{1,4}', args.display):
        raise ValueError('Use a local X11 display such as :90.')
    if args.port is not None and not 1024 <= args.port <= 65535:
        raise ValueError('Use a nonprivileged web port (1024..65535).')
    if args.public_url:
        url = urlsplit(args.public_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('Use a public HTTPS origin without a path, query or credentials.')
    target, config = settings(args)
    state = Path(config['state_dir'])
    if locked(state / 'gateway.lock') or locked(state / 'oak-service.lock'):
        print(json.dumps(verify_live(target, config)))
        print('Existing deployment is running; dependencies and private state were left unchanged.')
        return
    if not args.skip_system:
        run(['sudo', 'apt-get', 'update'])
        run(['sudo', 'env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', *PACKAGES])
    local_bin = str(Path.home() / '.local/bin')
    os.environ['PATH'] = local_bin + os.pathsep + os.environ.get('PATH', '')
    if not shutil.which('codex'):
        run(['npm', 'install', '--prefix', str(Path.home() / '.local'), '-g',
             '@openai/codex@' + RUNTIME_VERSION])
    if not PYTHON.exists():
        run([sys.executable, '-m', 'venv', REPO / '.venv'])
    run([PYTHON, '-m', 'pip', 'install', '-r', REPO / 'requirements.txt'])
    if not args.skip_system:
        run(['sudo', PYTHON, '-m', 'playwright', 'install-deps', 'chromium'])
    run([PYTHON, '-m', 'playwright', 'install', 'chromium'])
    if not args.skip_system:
        browser_policy()
    for name in ('state_dir', 'workspace', 'runtime_home'):
        Path(config[name]).mkdir(parents=True, exist_ok=True, mode=0o700)
    if not args.skip_voice:
        result = run([PYTHON, 'scripts/setup-voice.py', '--directory', target.parent / 'models'],
                     capture_output=True, text=True)
        models = json.loads(result.stdout)
        for key, value in models.items():
            if not config.get(key):
                config[key] = value
    private_json(target, config)
    run([PYTHON, '-m', 'oak.desktop', '--config', target, '--check'])
    if args.prepare_only:
        print(json.dumps({'status': 'prepared', 'config': str(target), 'gateway_started': False,
                          'requires': ['ChatGPT login', 'Telegram bot token, username and owner ID']}))
        return
    if (not config.get('telegram_username') or not config.get('allowed_user_ids')
            or any(type(owner) is not int or owner <= 0 for owner in config['allowed_user_ids'])):
        raise ValueError('Machine prepared. Rerun with --bot-username and --owner-id to deploy.')
    token = Path(config['telegram_token_file'])
    if not token.exists():
        if not sys.stdin.isatty():
            raise ValueError('Save the token using scripts/set_telegram_token.py --output ' +
                             shlex.quote(str(token)) + ' in a private interactive terminal, then rerun.')
        run([PYTHON, 'scripts/set_telegram_token.py', '--output', token])
    verify_bot(config)
    runtime_env = {**os.environ, 'CODEX_HOME': config['runtime_home']}
    login = subprocess.run(['codex', 'login', 'status'], env=runtime_env, capture_output=True)
    if login.returncode:
        if not sys.stdin.isatty():
            raise ValueError('Machine prepared. Complete subscription login: env CODEX_HOME=' +
                             shlex.quote(config['runtime_home']) + ' codex login --device-auth ; then rerun.')
        run(['codex', 'login', '--device-auth'], env=runtime_env)
    run([PYTHON, '-m', 'oak', 'doctor', '--config', target])
    run([PYTHON, '-m', 'oak', 'smoke', '--config', target])
    if not args.skip_autostart:
        if not Path('/run/systemd/system').is_dir():
            raise ValueError('Automatic boot requires systemd/cron. For container-only checks use --skip-autostart.')
        run(['sudo', 'systemctl', 'enable', '--now', 'cron'])
        run([PYTHON, '-m', 'oak', 'service', 'install-autostart', '--config', target])
    run([PYTHON, '-m', 'oak', 'service', 'start', '--config', target])
    result = verify_live(target, config)
    result['autostart_installed'] = not args.skip_autostart
    print(json.dumps(result))
    print('Open the Oak menu in Telegram and send a task to verify your actual client and chat delivery.')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, Exception) as error:
        # Never print HTTP exceptions (URLs may contain the bot token), captured
        # process output, private config or command environments.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print('Bootstrap stopped: ' + message, file=sys.stderr)
        raise SystemExit(1) from None
