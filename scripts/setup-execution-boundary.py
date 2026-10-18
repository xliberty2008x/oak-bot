#!/usr/bin/env python3
"""Render by default; provision only with explicit --apply as root.

The specification contains paths and policy, never passwords or account data.
Provisioning preserves workspace/runtime bytes and grants only targeted ACLs.
"""

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import grp
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak.executor import VERSION, decode, encode, load_profile, policy_digest

ROOT = Path('/opt/oak-executor')
PROFILE = Path('/etc/oak-executor/profile.json')
RUN = Path('/run/oak-executor')
SYSTEMD = Path('/etc/systemd/system')
TABLE = 'oak_executor'
DENY4 = ('0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
         '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
         '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24',
         '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4')
DENY6 = ('::/128', '::1/128', '::ffff:0:0/96', '64:ff9b::/96',
         '64:ff9b:1::/48', '100::/64', '2001:db8::/32', '2002::/16',
         'fc00::/7', 'fe80::/10', 'ff00::/8')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, **kwargs).stdout


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or any(c in str(path) for c in '\n\r\x00%'):
        raise ValueError('Expected absolute, systemd-safe path')
    return str(path)


def overlap(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def validate_spec(spec):
    required = {'source', 'executor_user', 'executor_uid', 'executor_gid', 'gateway_user',
                'workspace', 'runtime_home', 'desktop_state', 'python', 'codex',
                'readonly', 'dns_addresses', 'host_addresses', 'forbidden'}
    if not required <= spec.keys():
        raise ValueError('Missing execution specification fields')
    import re
    for key in ('executor_user', 'gateway_user'):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', spec[key]):
            raise ValueError('Invalid account name')
    if spec['executor_user'] == spec['gateway_user']:
        raise ValueError('Execution requires a separate account')
    for key in ('executor_uid', 'executor_gid'):
        if type(spec[key]) is not int or not 100 <= spec[key] <= 60000:
            raise ValueError('Invalid execution identity')
    for key in ('source', 'workspace', 'runtime_home', 'desktop_state', 'python', 'codex'):
        absolute(spec[key])
    if spec.get('migrate_desktop_from'):
        migration = Path(absolute(spec['migrate_desktop_from']))
        if migration.name != 'desktop' or not any(migration.is_relative_to(path) for path in spec['forbidden']):
            raise ValueError('Desktop migration must identify this deployment trusted state/desktop')
        if overlap(migration, spec['desktop_state']):
            raise ValueError('Desktop migration source and destination must be separate')
    if 'migrate_data_ownership' in spec and type(spec['migrate_data_ownership']) is not bool:
        raise ValueError('Data ownership migration must be explicit')
    if spec.get('migrate_data_ownership'):
        gateway_state = absolute(spec['gateway_state'])
        if gateway_state not in spec['forbidden']:
            raise ValueError('Gateway state must be explicitly excluded from execution')
        if spec.get('migrate_desktop_from') and Path(spec['migrate_desktop_from']).parent != Path(gateway_state):
            raise ValueError('Desktop must belong to the same gateway state')
    writes = [spec[name] for name in ('workspace', 'runtime_home', 'desktop_state')]
    if any(overlap(a, b) for i, a in enumerate(writes) for b in writes[i + 1:]):
        raise ValueError('Writable roots must be separate')
    if not spec['forbidden']:
        raise ValueError('Explicit trusted state and broker exclusions required')
    for path in [*spec['readonly'], *spec['forbidden']]:
        absolute(path)
    dangerous = {'/', '/home', '/root', '/run', '/tmp', '/proc', '/sys', '/dev', '/etc', '/var'}
    for path in [*writes, *spec['readonly']]:
        if (path in dangerous or any(Path(path).is_relative_to(root) for root in
                ('/proc', '/sys', '/run', '/tmp', '/dev'))
                or overlap(path, str(ROOT)) or overlap(path, str(PROFILE.parent))):
            raise ValueError('Overbroad or overlapping execution mount')
        if any(overlap(path, denied) for denied in spec['forbidden']):
            raise ValueError('Mount includes trusted state')
    if any(overlap(a, b) for a in writes for b in spec['readonly']):
        raise ValueError('Writable/read-only mounts overlap')
    for key in ('dns_addresses', 'host_addresses'):
        if not isinstance(spec[key], list) or not spec[key]:
            raise ValueError('Explicit resolver and host addresses required')
        spec[key] = [str(ipaddress.ip_address(value)) for value in spec[key]]
    # Public NAT addresses must be supplied explicitly: a local FIB check cannot discover them.
    for key in ('python', 'codex'):
        if not any(Path(spec[key]).is_relative_to(path) for path in spec['readonly']):
            raise ValueError('Executable missing a read-only mount')
    if not any(path == '/usr' for path in spec['readonly']):
        raise ValueError('The read-only system toolchain is required')
    for component in spec.get('path', '/usr/local/bin:/usr/bin:/bin').split(':'):
        absolute(component)
        if not any(Path(component).is_relative_to(path) for path in spec['readonly']):
            raise ValueError('PATH entries require read-only mounts')
    return spec


def firewall(spec):
    lines = [f'add table inet {TABLE}', f'flush table inet {TABLE}',
             f'table inet {TABLE} {{', ' chain output {',
             '  type filter hook output priority -10; policy accept;',
             # A missing socket UID breaks a meta expression's current rule;
             # a negative guard would fall through into the deny rules below.
             # Dispatch positively so kernel ACK/FIN/ICMP without socket
             # credentials, and every other UID, bypass this private chain.
             f'  meta skuid {spec["executor_uid"]} jump executor',
             ' }', ' chain executor {']
    for value in spec['dns_addresses']:
        family = 'ip6' if ':' in value else 'ip'
        lines += [f'  {family} daddr {value} udp dport 53 accept',
                  f'  {family} daddr {value} tcp dport 53 accept']
    lines += ['  fib daddr type local reject']
    for values, family in ((DENY4, 'ip'), (DENY6, 'ip6')):
        hosts = [value for value in spec['host_addresses'] if (':' in value) == (family == 'ip6')]
        lines += [f'  {family} daddr {{ {", ".join((*values, *hosts))} }} reject']
    # Currently globally routable IPv6 unicast is in 2000::/3. Fail closed for
    # other scopes (including deprecated site-local) instead of enumerating them.
    lines += ['  meta nfproto ipv6 ip6 daddr != 2000::/3 reject']
    return '\n'.join([*lines, ' }', '}', ''])


def units(spec, launcher, setup):
    result = {}
    for mode in ('native', 'worker'):
        name = f'oak-executor-{mode}'
        result[f'{name}.socket'] = f'''[Unit]
Description=Oak {mode} execution socket
Requires=oak-executor-firewall.service
After=oak-executor-firewall.service
BindsTo=oak-executor-firewall.service

[Socket]
ListenStream={RUN}/{mode}.sock
Accept=yes
SocketUser={spec['gateway_user']}
SocketMode=0600
DirectoryMode=0711
MaxConnections=8
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
'''
        result[f'{name}@.service'] = f'''[Unit]
Description=Oak isolated {mode} connection
Requires=oak-executor-firewall.service
After=oak-executor-firewall.service
BindsTo=oak-executor-firewall.service

[Service]
User={spec['executor_user']}
Group={spec['executor_gid']}
ExecStart=/usr/bin/python3 -I {launcher} serve-{mode} --profile {PROFILE}
StandardInput=socket
StandardOutput=inherit
StandardError=null
NoNewPrivileges=yes
UMask=0077
KillMode=control-group
TimeoutStopSec=10
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
CapabilityBoundingSet=
AmbientCapabilities=
'''
    result['oak-executor-firewall.service'] = f'''[Unit]
Description=Oak execution UID egress policy
Before=oak-executor-native.socket oak-executor-worker.socket

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/oak-executor/egress.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
'''
    result['oak-executor-attest.service'] = f'''[Unit]
Description=Oak current-boot execution boundary acceptance
Requires=oak-executor-native.socket oak-executor-worker.socket
After=oak-executor-native.socket oak-executor-worker.socket

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I {setup} --attest {PROFILE}
StandardOutput=null
StandardError=null
UMask=0077

[Install]
WantedBy=multi-user.target
'''
    return result


def render(spec, destination):
    spec = validate_spec(dict(spec))
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Bundle directory must be empty')
    destination.mkdir(parents=True, exist_ok=True)
    files = {}
    source = Path(spec['source'])
    for path in sorted((source / 'oak').rglob('*')):
        if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        if path.is_symlink():
            raise ValueError('Source symlinks are not accepted')
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError('Only regular reviewed source files are accepted')
        files[path.relative_to(source).as_posix()] = path.read_bytes()
    if not {'oak/executor.py', 'oak/executor_worker.py'} <= files.keys():
        raise ValueError('Execution implementation is incomplete')
    files['scripts/setup-execution-boundary.py'] = Path(__file__).read_bytes()
    release_digest = hashlib.sha256(b''.join(name.encode() + files[name] for name in sorted(files))).hexdigest()[:20]
    release = ROOT / 'releases' / release_digest
    installed = {str(release / name): value for name, value in files.items()}
    installed['/etc/oak-executor/egress.nft'] = firewall(spec).encode()
    for name, value in units(spec, release / 'oak/executor.py', release / 'scripts/setup-execution-boundary.py').items():
        installed[str(SYSTEMD / name)] = value.encode()
    profile = {key: spec[key] for key in ('executor_uid', 'executor_gid', 'workspace',
               'runtime_home', 'desktop_state', 'python', 'codex', 'readonly')}
    for key in ('models', 'display', 'width', 'height', 'playwright_browsers_path', 'search_url'):
        if key in spec:
            profile[key] = spec[key]
    profile.update(version=VERSION, code_root=str(release),
                   path=spec.get('path', '/usr/local/bin:/usr/bin:/bin'),
                   worker_socket=str(RUN / 'worker.sock'), native_socket=str(RUN / 'native.sock'),
                   receipt=str(RUN / 'acceptance.json'), host_addresses=spec['host_addresses'],
                   manifest={name: hashlib.sha256(value).hexdigest() for name, value in sorted(installed.items())})
    profile['policy_digest'] = policy_digest(profile)
    installed[str(PROFILE)] = encode(profile)
    for name, value in installed.items():
        target = destination / name.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        target.chmod(0o644)
    (destination / 'specification.json').write_bytes(encode(spec))
    return profile


def _acl(path, user, permissions, recursive=False):
    path = Path(path)
    # Traversal only on existing private parents. No chmod or recursive parent grant.
    for parent in reversed(path.parents):
        if parent != Path('/'):
            run('/usr/bin/setfacl', '-m', f'u:{user}:--x', str(parent))
    run('/usr/bin/setfacl', *(('-R', '-P') if recursive else ()),
        '-m', f'u:{user}:{permissions}', str(path))


def validate_data_tree(root):
    """Reject links to outside inodes and IPC nodes before granting any access."""
    root = Path(root)
    if root.resolve() != root or not root.is_dir():
        raise ValueError('Data root must be an existing non-symlink directory')
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            info = (Path(directory) / name).lstat()
            if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                     or stat.S_ISREG(info.st_mode))
                    or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise ValueError('Unsafe data file type or hardlink')


def _chown_tree(root, uid, gid):
    # Do not follow profile/cache symlinks to toolchains or other account data.
    for directory, dirs, files in os.walk(root, followlinks=False):
        for path in [Path(directory), *(Path(directory) / name for name in dirs + files)]:
            os.chown(path, uid, gid, follow_symlinks=False)


def migrate_data_ownership(spec):
    """Codex chmods its data; ACL writes alone are insufficient for a new UID."""
    if not spec.get('migrate_data_ownership'):
        return
    state = Path(spec['gateway_state'])
    if state.resolve() != state:
        raise ValueError('Gateway state must not use symlink ancestors')
    with contextlib.ExitStack() as locks:
        lock_paths = [state / 'gateway.lock']
        if spec.get('migrate_desktop_from'):
            source = Path(spec['migrate_desktop_from'])
            target = Path(spec['desktop_state']) / 'desktop'
            if source.resolve() != source or not source.is_dir() or target.exists():
                raise ValueError('Desktop migration requires an existing source and absent destination')
            lock_paths.append(source / 'desktop.lock')
        for path in lock_paths:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            locks.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Complete every validation before changing any inode owner.
        roots = [spec['workspace'], spec['runtime_home']]
        if spec.get('migrate_desktop_from'):
            roots.append(spec['migrate_desktop_from'])
        for root in roots:
            validate_data_tree(root)
        for root in (spec['workspace'], spec['runtime_home']):
            _chown_tree(root, spec['executor_uid'], spec['executor_gid'])


def migrate_desktop(spec):
    """Move only this deployment's stopped desktop, preserving profile bytes."""
    if not spec.get('migrate_desktop_from'):
        return
    source = Path(spec['migrate_desktop_from'])
    target = Path(spec['desktop_state']) / 'desktop'
    if source.resolve() != source or not source.is_dir() or target.exists():
        raise ValueError('Desktop migration requires an existing source and absent destination')
    # Both locks are normally held by the gateway. Never move a live profile.
    with contextlib.ExitStack() as locks:
        for path in (source.parent / 'gateway.lock', source / 'desktop.lock'):
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            locks.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_data_tree(source)
        os.rename(source, target)  # Same-filesystem move; EXDEV fails without copying.
        # Existing browser locks/cookies/cache are neither opened nor printed.
        for directory, dirs, files in os.walk(target, followlinks=False):
            for path in [Path(directory), *(Path(directory) / name for name in dirs + files)]:
                info = path.lstat()
                os.chown(path, spec['executor_uid'], spec['executor_gid'], follow_symlinks=False)
                if not stat.S_ISLNK(info.st_mode):
                    path.chmod(stat.S_IMODE(info.st_mode) & ~0o077)
        run('/usr/bin/setfacl', '-R', '-P', '-b', '-k', str(target))


def apply_bundle(destination):
    if os.geteuid() != 0:
        raise PermissionError('Provisioning requires root')
    destination = Path(destination)
    spec = validate_spec(decode((destination / 'specification.json').read_bytes()))
    for executable in ('/usr/bin/bwrap', '/usr/sbin/nft', '/usr/bin/setfacl', '/usr/bin/systemctl'):
        if not os.access(executable, os.X_OK):
            raise ValueError('Required system dependency is missing')
    # Complete non-mutating dependency checks before changing identities or data.
    for name in ('workspace', 'runtime_home'):
        path = Path(spec[name])
        if not path.is_dir() or path.resolve() != path:
            raise ValueError('Existing non-symlink data roots are required')
        validate_data_tree(path)
    if spec.get('migrate_desktop_from'):
        validate_data_tree(spec['migrate_desktop_from'])
    for path in spec['readonly']:
        if not Path(path).exists():
            raise ValueError('Read-only dependency is missing')
    for name in ('python', 'codex'):
        if not os.access(spec[name], os.X_OK):
            raise ValueError('Execution dependency is not executable')
    run('/usr/sbin/nft', '--check', '--file',
        str(destination / 'etc/oak-executor/egress.nft'))
    gateway = pwd.getpwnam(spec['gateway_user'])
    if gateway.pw_uid == spec['executor_uid']:
        raise ValueError('Execution identity overlaps gateway')
    try:
        account = pwd.getpwnam(spec['executor_user'])
    except KeyError:
        # Never adopt an unrelated existing UID or primary group.
        for lookup, identity in ((pwd.getpwuid, spec['executor_uid']),
                                 (grp.getgrgid, spec['executor_gid'])):
            try:
                lookup(identity)
            except KeyError:
                continue
            raise ValueError('Execution identity is already allocated')
        run('/usr/sbin/groupadd', '--system', '--gid', str(spec['executor_gid']), spec['executor_user'])
        run('/usr/sbin/useradd', '--system', '--uid', str(spec['executor_uid']),
            '--gid', str(spec['executor_gid']), '--no-create-home', '--home-dir', '/nonexistent',
            '--shell', '/usr/sbin/nologin', spec['executor_user'])
        account = pwd.getpwnam(spec['executor_user'])
    if (account.pw_uid, account.pw_gid) != (spec['executor_uid'], spec['executor_gid']):
        raise ValueError('Execution account does not match specification')
    if os.getgrouplist(account.pw_name, account.pw_gid) != [account.pw_gid]:
        raise ValueError('Execution account has supplementary groups')
    for entry in Path('/proc').iterdir():
        try:
            if entry.name.isdigit() and entry.stat().st_uid == account.pw_uid:
                raise ValueError('Stop execution connections before changing policy')
        except FileNotFoundError:
            pass
    # No broad copies: reviewed code/config only. Runtime and workspace remain in place.
    for prefix in ('opt', 'etc'):
        for source in sorted((destination / prefix).rglob('*')):
            if source.is_symlink():
                raise ValueError('Bundle symlinks are not accepted')
            target = Path('/') / source.relative_to(destination)
            if target.is_symlink():
                raise ValueError('Installed symlinks are not accepted')
            for ancestor in target.parents:
                if ancestor.is_symlink():
                    raise ValueError('Installed symlink ancestors are not accepted')
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
                os.chown(target, 0, 0)
                target.chmod(0o755)
            else:
                if target.is_symlink():
                    raise ValueError('Installed symlinks are not accepted')
                target.write_bytes(source.read_bytes())
                os.chown(target, 0, 0)
                target.chmod(0o644)
    migrate_data_ownership(spec)
    for name in ('workspace', 'runtime_home', 'desktop_state'):
        path = Path(spec[name])
        if path.resolve() != path:
            raise ValueError('Writable root ancestors must not be symlinks')
        if not path.exists():
            if name != 'desktop_state':
                raise ValueError('Existing runtime and workspace are required')
            path.mkdir(parents=True, mode=0o700)
            os.chown(path, gateway.pw_uid, gateway.pw_gid)
        if path.is_symlink():
            raise ValueError('Writable root must not be a symlink')
        if name == 'desktop_state':
            os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o700)
            migrate_desktop(spec)
            run('/usr/bin/setfacl', '-R', '-P', '-b', '-k', str(path))
        _acl(path, account.pw_uid, 'rwX', recursive=True)
        # Newly created descendants remain accessible to the trusted gateway.
        for directory, dirs, _ in (os.walk(path, followlinks=False) if name != 'desktop_state' else ()):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            run('/usr/bin/setfacl', '-m', f'd:u:{account.pw_uid}:rwx,d:u:{gateway.pw_uid}:rwx', directory)
    for path in spec['readonly']:
        _acl(path, account.pw_uid, 'rX', recursive=path.startswith('/home/'))
    RUN.mkdir(mode=0o711, exist_ok=True)
    os.chown(RUN, 0, 0)
    RUN.chmod(0o711)
    (RUN / 'acceptance.json').unlink(missing_ok=True)
    run('/usr/bin/systemctl', 'daemon-reload')
    # No executor processes are running (checked above). Reapply the own-table
    # transaction before enabling sockets, including on an existing installation.
    run('/usr/bin/systemctl', 'restart', 'oak-executor-firewall.service')
    run('/usr/bin/systemctl', 'enable', '--now', 'oak-executor-firewall.service',
        'oak-executor-worker.socket', 'oak-executor-native.socket')
    run('/usr/bin/systemctl', 'enable', 'oak-executor-attest.service')
    run('/usr/bin/systemctl', 'start', 'oak-executor-attest.service')


def _exchange(path, message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect(path)
        connection.sendall(encode(message))
        response = connection.makefile('rb').readline(1024 * 1024)
        return decode(response)


def attest(profile_path):
    if os.geteuid() != 0:
        raise PermissionError('Attestation requires root')
    profile = load_profile({'profile': profile_path})
    receipt_path = Path(profile['receipt'])
    receipt_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='probe-', dir=RUN) as temporary:
        marker = Path(temporary) / 'trusted-marker'
        marker.write_bytes(b'Oak execution boundary dummy marker\n')
        marker.chmod(0o600)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as private, \
                socket.socket(socket.AF_INET, socket.SOCK_STREAM) as local:
            private_path = str(Path(temporary) / 'control.sock')
            private.bind(private_path)
            private.listen(8)
            local.bind(('0.0.0.0', 0))
            local.listen(8)
            port = local.getsockname()[1]
            hosts = [['127.0.0.1', port]]
            addresses = json.loads(run('/usr/sbin/ip', '-j', 'address', 'show'))
            local_addresses = {item['local'] for interface in addresses
                               for item in interface.get('addr_info', [])
                               if item.get('family') == 'inet'}
            for host in sorted(local_addresses - {'127.0.0.1'}):
                if len(hosts) < 8:
                    hosts.append([host, port])
            # A failed probe must mean denial, rather than a listener that never existed.
            for host in hosts:
                with socket.create_connection(tuple(host), timeout=1):
                    pass
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as baseline:
                baseline.connect(private_path)
            args = {'files': [str(marker)], 'sockets': [private_path], 'hosts': hosts,
                    'host_pid': os.getpid(), 'host_pid_namespace': os.readlink('/proc/self/ns/pid')}
            worker = _exchange(profile['worker_socket'], {'id': 'root-probe', 'op': 'probe', 'args': args})
            native = _exchange(profile['native_socket'], {'probe': args})
            checks = worker.get('result', {}) if worker.get('ok') is True else {}
            native_checks = native.get('probe', {}) if native.get('ready') is True else {}
            checks['native_policy'] = (set(native_checks) == set(checks) and bool(checks)
                and all(value is True for value in native_checks.values())
                and native.get('uid') == profile['executor_uid']
                and native.get('policy_digest') == profile['policy_digest'])
            checks['firewall_active'] = (run('/usr/bin/systemctl', 'is-active',
                'oak-executor-firewall.service').strip() == b'active'
                and bool(run('/usr/sbin/nft', 'list', 'table', 'inet', TABLE)))
            if len(checks) != 10 or not all(value is True for value in checks.values()):
                raise RuntimeError('Execution boundary acceptance failed')
            receipt = {'version': VERSION, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       'policy_digest': profile['policy_digest'], 'executor_uid': profile['executor_uid'],
                       'checks': checks, 'accepted_at': int(time.time())}
            receipt_path.write_bytes(encode(receipt))
            receipt_path.chmod(0o444)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec')
    parser.add_argument('--output')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--attest')
    args = parser.parse_args()
    try:
        if args.attest:
            attest(args.attest)
        elif args.spec and args.output:
            render(decode(Path(args.spec).read_bytes().rstrip(b'\n') + b'\n'), args.output)
            if args.apply:
                apply_bundle(args.output)
            print('Execution bundle provisioned' if args.apply else 'Execution bundle rendered; host unchanged')
        else:
            parser.error('Use --spec and --output, or --attest')
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
