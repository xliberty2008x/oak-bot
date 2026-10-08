#!/usr/bin/env python3
"""Provision a supported Linux VM and deploy an owner-configured Oak instance."""

import argparse
import fcntl
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import signal
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from oak.features import feature_manifest, matches_manifest

PYTHON = REPO / '.venv/bin/python'
RUNTIME_VERSION = '0.159.2'
PACKAGES = ['ca-certificates', 'curl', 'git', 'python3', 'python3-venv', 'python3-pip',
            'nodejs', 'npm', 'ffmpeg', 'fonts-dejavu-core', 'fonts-noto-color-emoji',
            'openssh-client', 'cron', 'xvfb', 'openbox', 'dbus-x11', 'xauth',
            'xdotool', 'x11-utils', 'x11-xserver-utils', 'x11vnc', 'xterm', 'tzdata', 'apparmor']


def run(args, **kwargs):
    cleanup_timeout = kwargs.pop('cleanup_timeout', None)
    if cleanup_timeout is not None:
        return managed_check(args, cleanup_timeout=cleanup_timeout, **kwargs)
    return subprocess.run([str(arg) for arg in args], cwd=REPO, check=True, **kwargs)


def managed_check(args, timeout, cleanup_timeout, capture_output, text):
    """Own the verifier process group; give cancellation time to close fixtures."""
    command = [str(arg) for arg in args]
    process = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=text, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        stop_owned_group(process, cleanup_timeout)
        raise
    if process.returncode:
        stop_owned_group(process, cleanup_timeout)
        raise subprocess.CalledProcessError(process.returncode, command)
    if group_exists(process.pid):
        stop_owned_group(process, cleanup_timeout)
        raise RuntimeError('Verifier process group cleanup was required; verification is unconfirmed.')
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def group_exists(identifier):
    try:
        os.killpg(identifier, 0)
        return True
    except ProcessLookupError:
        return False


def stop_owned_group(process, timeout):
    previous = {value: signal.getsignal(value) for value in (signal.SIGINT, signal.SIGTERM)}
    for value in previous:
        signal.signal(value, signal.SIG_IGN)
    try:
        reap_owned_group(process, timeout)
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)


def reap_owned_group(process, timeout):
    # Descendants can survive their leader; never condition group cleanup on poll.
    deadline = time.monotonic() + timeout
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    while group_exists(process.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.communicate(timeout=3)


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
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


def prepare_telegram_image(target):
    """Use the local Docker daemon, without granting the user Docker-group access."""
    env = {key: value for key, value in os.environ.items() if key not in
           ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
    command = ['docker', '--host=unix:///var/run/docker.sock']
    if not shutil.which('docker'):
        raise ValueError('Install docker.io, or rerun without --skip-system.')
    if subprocess.run(command + ['info'], env=env, capture_output=True).returncode:
        # Managed development containers can expose their local daemon on TCP.
        # Never send deployment credentials to an arbitrary inherited remote host.
        configured = urlsplit(os.environ.get('DOCKER_HOST', ''))
        try:
            loopback = ipaddress.ip_address(configured.hostname or '').is_loopback
            tcp_port = configured.port
        except ValueError:
            loopback, tcp_port = False, None
        if (configured.scheme == 'tcp' and loopback and tcp_port and not configured.username
                and not configured.password and not configured.path and not configured.query and not configured.fragment):
            command = ['docker', '--host=' + configured.geturl()]
        else:
            command = ['sudo', '-n', *command]
        if subprocess.run(command + ['info'], env=env, capture_output=True).returncode:
            raise RuntimeError('The local Docker daemon is unavailable; check its service and sudo access.')

    def docker(arguments, **kwargs):
        kwargs.setdefault('check', True)
        return subprocess.run(command + [str(arg) for arg in arguments], cwd=REPO, env=env, **kwargs)

    revision = hashlib.sha256((REPO / 'Dockerfile.telegram-api').read_bytes() +
                              (REPO / 'patches/telegram-mtproto-port.patch').read_bytes()).hexdigest()[:16]
    image = 'oak-telegram-api:' + revision
    if docker(['image', 'inspect', image], capture_output=True, check=False).returncode:
        log = target.parent / 'telegram-api-build.log'
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        print('Building the pinned local Telegram API; this can take several minutes. Private log: ' + str(log), flush=True)
        with os.fdopen(fd, 'w') as output:
            built = docker(['build', '-f', 'Dockerfile.telegram-api', '-t', image, '.'],
                           stdout=output, stderr=subprocess.STDOUT, check=False)
        if built.returncode:
            raise RuntimeError('Local Telegram API build failed; inspect the private build log.')
    return docker, image


def setup_telegram_api(args, target, config, docker, image):
    """Provision a deployment-wide local API and checkpoint its one-time migration."""
    old_endpoint = config.get('telegram_api_url', 'https://api.telegram.org').rstrip('/')
    cloud = old_endpoint == 'https://api.telegram.org'
    parsed = urlsplit(old_endpoint)
    checkpoint = target.parent / 'telegram-api-migration.json'
    migration = json.loads(checkpoint.read_text()) if cloud and checkpoint.exists() else None
    if migration and (migration.get('bot') != config['telegram_username']
                      or migration.get('phase') not in ('logout-requested', 'logged-out')):
        raise ValueError('Telegram migration checkpoint differs; preserve the selected bot and migration state.')
    resumed = urlsplit(migration['api_url']) if migration else None
    if not cloud and (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                      or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Managed bootstrap requires a local http://127.0.0.1:PORT Telegram endpoint.')
    port = args.telegram_api_port or (resumed.port if resumed else parsed.port if not cloud else 8081)
    if not port or not 1024 <= port <= 65535:
        raise ValueError('Use a nonprivileged Telegram API port (1024..65535).')
    if not cloud and args.telegram_api_port and parsed.port != port:
        raise ValueError('Existing Telegram API port differs; existing deployment was preserved.')
    directory = Path(config.get('telegram_api_directory') or (migration.get('directory') if migration else None)
                     or target.parent / 'telegram-api-data').expanduser()
    directory = (directory if directory.is_absolute() else target.parent / directory).absolute()
    endpoint = 'http://127.0.0.1:' + str(port)
    if migration and (migration.get('api_url') != endpoint or migration.get('directory') != str(directory)):
        raise ValueError('Telegram migration checkpoint differs; preserve its selected endpoint and directory.')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.resolve() != directory or directory.is_relative_to(REPO) or directory.stat().st_mode & 0o077:
        raise ValueError('The Telegram API directory must be private, outside Git and without symlinks.')
    selected = {**config, 'telegram_api_url': endpoint, 'telegram_api_directory': str(directory)}
    # A deliberately configured, healthy local server may be managed externally.
    if not cloud:
        try:
            verify_bot(selected)
        except RuntimeError:
            pass
        else:
            return

    environment = absolute(args.telegram_api_env_file or (migration.get('env_file') if migration else None)
                           or target.parent / 'telegram-api.env', target.parent)
    if environment.is_relative_to(REPO):
        raise ValueError('Keep Telegram application credentials outside Git.')
    if not environment.exists():
        if not sys.stdin.isatty():
            raise ValueError('Save TELEGRAM_API_ID and TELEGRAM_API_HASH in a private environment file, then '
                             'rerun with --telegram-api-env-file ' + shlex.quote(str(environment)))
        print('Obtain Telegram application credentials at https://my.telegram.org/apps; do not paste them into chat.')
        values = {'TELEGRAM_API_ID': getpass.getpass('Telegram application ID (hidden): ').strip(),
                  'TELEGRAM_API_HASH': getpass.getpass('Telegram application hash (hidden): ').strip()}
    else:
        if not environment.is_file() or environment.stat().st_mode & 0o077:
            raise ValueError('Telegram API environment file must be private (chmod 600).')
        values = {}
        for line in environment.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator or key in values:
                raise ValueError('Invalid Telegram API environment file; use one KEY=value per line.')
            values[key] = value
    if (set(values) - {'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'OAK_TELEGRAM_MTPROTO_PORT'}
            or not re.fullmatch(r'[1-9][0-9]*', values.get('TELEGRAM_API_ID', ''))
            or not re.fullmatch(r'[A-Fa-f0-9]{32}', values.get('TELEGRAM_API_HASH', ''))):
        raise ValueError('Invalid Telegram application credentials; their contents were not printed.')
    mtproto = values.get('OAK_TELEGRAM_MTPROTO_PORT')
    if mtproto is not None and (not re.fullmatch(r'[0-9]{1,5}', mtproto) or not 1 <= int(mtproto) <= 65535):
        raise ValueError('OAK_TELEGRAM_MTPROTO_PORT must be a port in range 1..65535.')
    if not environment.exists():
        environment.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
            output.flush()
            os.fsync(output.fileno())

    host_input = args.telegram_api_host_directory or (migration.get('host_directory') if migration else None)
    host_directory = str(Path(host_input).expanduser().absolute()) if host_input else str(directory)
    # Docker may live outside a development container: prove the bind source
    # sees this exact private directory before touching any bot authorization.
    marker = directory / ('.oak-bootstrap-' + os.urandom(12).hex())
    try:
        marker.write_text(os.urandom(32).hex())
        marker.chmod(0o600)
        mounted = docker(['run', '--rm', '--network', 'none', '--user', str(os.getuid()) + ':' + str(os.getgid()),
                          '--entrypoint', '/bin/cat', '--mount',
                          'type=bind,src=' + host_directory + ',dst=' + str(directory), image, marker],
                         capture_output=True, text=True, check=False)
        if mounted.returncode or mounted.stdout != marker.read_text():
            raise ValueError('Docker cannot see the private Telegram directory; use --telegram-api-host-directory '
                             'for the matching Docker-host path.')
    finally:
        marker.unlink(missing_ok=True)
    name = 'oak-telegram-api-' + hashlib.sha256(str(target).encode()).hexdigest()[:12]
    inspected = docker(['inspect', name], capture_output=True, text=True, check=False)
    if inspected.returncode == 0:
        existing = json.loads(inspected.stdout)[0]
        if existing['Config'].get('Labels', {}).get('org.oak.config') != str(target):
            raise ValueError('An unrelated container owns the selected Telegram API name.')
        # Never replace a cache mount, image or environment beneath a running bot.
        docker(['stop', '--time', '30', name], capture_output=True)
        docker(['rm', name], capture_output=True)
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            raise ValueError('The selected Telegram API port is already in use; choose another port '
                             'instead of borrowing another server or its cache.') from None
    docker(['run', '-d', '--name', name, '--restart', 'unless-stopped', '--network', 'host',
            '--user', str(os.getuid()) + ':' + str(os.getgid()), '--log-driver', 'none',
            '--label', 'org.oak.config=' + str(target), '--env-file', environment,
            '--mount', 'type=bind,src=' + host_directory + ',dst=' + str(directory), image,
            '--local', '--http-ip-address=127.0.0.1', '--http-port=' + str(port),
            '--dir=' + str(directory), '--temp-dir=' + str(directory), '--verbosity=0'], capture_output=True)
    for _ in range(30):
        running = docker(['inspect', '--format', '{{.State.Running}}', name], capture_output=True, text=True)
        if running.stdout.strip() != 'true':
            raise RuntimeError('The managed local Telegram API container stopped before authorization.')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError('The managed local Telegram API did not open its loopback listener.')
    if cloud:
        if migration is None:
            verify_bot(config)
            migration = {'phase': 'logout-requested', 'bot': config['telegram_username'],
                         'api_url': endpoint, 'directory': str(directory),
                         'env_file': str(environment), 'host_directory': host_directory}
            private_json(checkpoint, migration)
            if telegram(config, 'logOut', {}) is not True:
                raise RuntimeError('Cloud Telegram logout was not confirmed; do not repeat it blindly.')
            private_json(checkpoint, {**migration, 'phase': 'logged-out'})
        # A checkpoint prevents repeating an uncertain or completed external action.
        # Local identity verification below safely resolves an interrupted migration.
    for _ in range(30):
        try:
            verify_bot(selected)
            break
        except RuntimeError:
            time.sleep(1)
    else:
        raise RuntimeError('Local Telegram API is not ready; check Docker, credentials and MTProto connectivity. '
                           'Preserve the migration checkpoint; cloud logout must not be repeated blindly.')
    if docker(['inspect', '--format', '{{.State.Running}}', name], capture_output=True, text=True).stdout.strip() != 'true':
        raise RuntimeError('The managed local Telegram API stopped during identity verification.')
    config.update(telegram_api_url=endpoint, telegram_api_directory=str(directory))
    private_json(target, config)


def status(target):
    output = run([PYTHON, '-m', 'oak', 'service', 'status', '--config', target],
                 capture_output=True, text=True)
    return json.loads(output.stdout)


def verify_features(broker_browser=False):
    command = [PYTHON, 'scripts/verify-bootstrap-features.py']
    if broker_browser:
        command.append('--broker-browser')
    try:
        result = json.loads(run(command, capture_output=True, text=True, timeout=240, cleanup_timeout=100).stdout)
        if (result.get('status') != 'verified' or result.get('protocol', {}).get('verified') is not True
                or not matches_manifest(result.get('features'), feature_manifest())
                or (broker_browser and (result.get('browser', {}).get('verified') is not True
                    or result.get('browser', {}).get('cleanup_verified') is not True))):
            raise ValueError
    except Exception:
        raise RuntimeError('Local feature verification failed; no account or gateway was started.') from None
    return result


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
        headers = {'Origin': origin, 'Authorization': 'Bearer ' + session['access_token']}
        bootstrap = request_json(origin + '/api/bootstrap', headers=headers)
        if (not matches_manifest(bootstrap.get('features'), feature_manifest())
                or not re.fullmatch(r'[a-f0-9]{32}', bootstrap.get('runtime_epoch', ''))):
            raise RuntimeError('The running gateway does not match this feature build')
        panel = request_json(origin + '/api/panel', headers={
            'Origin': origin, 'Authorization': 'Bearer ' + session['access_token']})
        if panel['settings']['auth'] != 'chatgpt' or not panel['computer']['configured']:
            raise RuntimeError('Runtime or computer is unavailable')
        if not {'input_requests', 'input_request_attention'} <= panel.get('session', {}).keys():
            raise RuntimeError('The running panel lacks contextual input')
        remote = request_json(origin + '/api/remote', headers={
            'Origin': origin, 'Authorization': 'Bearer ' + session['access_token']})
        if remote.get('configured') is not True:
            raise RuntimeError('Manual remote desktop is unavailable')
    except Exception:
        raise RuntimeError('Public panel verification failed; inspect HTTPS routing and the private log.') from None
    return {'status': 'ready', 'bot': config['telegram_username'], 'public_url': origin,
            'model': ready['model'], 'computer_enabled': panel['computer']['enabled'],
            'remote_configured': remote['configured'],
            'features': bootstrap['features'], 'runtime_epoch': bootstrap['runtime_epoch'],
            'local_feature_checks': None, 'credential_isolation_verified': False,
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
    parser.add_argument('--telegram-api-env-file', help='Private Telegram application credentials file')
    parser.add_argument('--telegram-api-port', type=int, help='Local Bot API port (default 8081)')
    parser.add_argument('--telegram-api-host-directory', help='Bind source on the Docker host, if different')
    parser.add_argument('--display')
    parser.add_argument('--port', type=int)
    parser.add_argument('--timezone')
    parser.add_argument('--public-url')
    for flag in ('prepare-only', 'skip-system', 'skip-voice', 'skip-autostart', 'migrate-telegram-api', 'verify-broker'):
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
    if args.telegram_api_port is not None and not 1024 <= args.telegram_api_port <= 65535:
        raise ValueError('Use a nonprivileged Telegram API port (1024..65535).')
    if args.public_url:
        url = urlsplit(args.public_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('Use a public HTTPS origin without a path, query or credentials.')
    target, config = settings(args)
    state = Path(config['state_dir'])
    cloud = config.get('telegram_api_url', 'https://api.telegram.org').rstrip('/') == 'https://api.telegram.org'
    if locked(state / 'gateway.lock') or locked(state / 'oak-service.lock'):
        if cloud:
            raise ValueError('This running deployment still uses the 20 MB cloud API. Drain pending work, '
                             'stop Oak and back up its state; then rerun with --migrate-telegram-api.')
        print(json.dumps(verify_live(target, config)))
        print('Existing deployment is running; dependencies and private state were left unchanged.')
        return
    if cloud and (state / 'state.sqlite').exists() and not (args.migrate_telegram_api or args.prepare_only):
        raise ValueError('Back up this stopped deployment, then rerun with --migrate-telegram-api '
                         'to replace the 20 MB cloud API without resetting its state.')
    if not args.skip_system:
        run(['sudo', 'apt-get', 'update'])
        packages = PACKAGES + ([] if shutil.which('docker') else ['docker.io'])
        run(['sudo', 'env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', *packages])
        if Path('/run/systemd/system').is_dir():
            run(['sudo', 'systemctl', 'enable', '--now', 'docker'])
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
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    docker, telegram_image = prepare_telegram_image(target)
    if not args.skip_voice:
        result = run([PYTHON, 'scripts/setup-voice.py', '--directory', target.parent / 'models'],
                     capture_output=True, text=True)
        models = json.loads(result.stdout)
        for key, value in models.items():
            if not config.get(key):
                config[key] = value
    private_json(target, config)
    run([PYTHON, '-m', 'oak.desktop', '--config', target, '--check'])
    local_features = verify_features(args.verify_broker)
    if args.prepare_only:
        print(json.dumps({'status': 'prepared', 'config': str(target), 'gateway_started': False,
                          'telegram_api_image_prepared': True,
                          'local_feature_checks': local_features,
                          'requires': ['ChatGPT login', 'Telegram bot token, username and owner ID',
                                       'Private Telegram application API ID and hash']}))
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
    # Resume an interrupted migration against its local endpoint, never cloud.
    if cloud and not (target.parent / 'telegram-api-migration.json').exists():
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
    setup_telegram_api(args, target, config, docker, telegram_image)
    if not args.skip_autostart:
        if not Path('/run/systemd/system').is_dir():
            raise ValueError('Automatic boot requires systemd/cron. For container-only checks use --skip-autostart.')
        run(['sudo', 'systemctl', 'enable', '--now', 'cron'])
        run([PYTHON, '-m', 'oak', 'service', 'install-autostart', '--config', target])
    run([PYTHON, '-m', 'oak', 'service', 'start', '--config', target])
    result = verify_live(target, config)
    result['local_feature_checks'] = local_features
    result['autostart_installed'] = not args.skip_autostart
    print(json.dumps(result))
    print('Open the Oak menu in Telegram and send a task to verify your actual client and chat delivery.')


if __name__ == '__main__':
    signalled = False
    def interrupt(_signal, _frame):
        global signalled
        if not signalled:
            signalled = True
            raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        main()
    except (KeyboardInterrupt, Exception) as error:
        # Never print HTTP exceptions (URLs may contain the bot token), captured
        # process output, private config or command environments.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print('Bootstrap stopped: ' + message, file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        signal.signal(signal.SIGTERM, previous)
