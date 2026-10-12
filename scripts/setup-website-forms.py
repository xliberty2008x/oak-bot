#!/usr/bin/env python3
"""Render a private website-form service; --apply installs the reviewed bundle.

TLS/reverse proxy belongs to the deployment. No account state is accepted here.
"""
import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak.executor import immutable, load_profile
from oak.signin_broker import origin
from oak.website_resources import validate_static_resources
from oak.website_auth_hosts import validate_auth_hosts


def trusted_tree(path, visited=None):
    """Executable dependencies, including symlink parents, must be root-controlled."""
    path = Path(path)
    visited = set() if visited is None else visited
    for entry in (path, *path.parents):
        info = entry.lstat()
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            raise ValueError('Dependency is writable outside root')
        if entry.is_symlink() and not immutable(entry.resolve()):
            raise ValueError('Untrusted dependency symlink')
    if path.is_dir():
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        for entry in path.iterdir():
            trusted_tree(entry, visited)


def render(spec, output):
    keys = {'origin', 'parent_origin', 'gateway_user', 'broker_user', 'broker_uid', 'broker_gid',
            'python', 'code_root', 'browsers', 'execution'}
    if (not isinstance(spec, dict) or not keys <= set(spec)
            or set(spec) - keys - {'composer', 'tiktok_static_resources', 'static_resources', 'auth_hosts', 'anonymous_static_resources'}
            or type(spec.get('tiktok_static_resources', False)) is not bool
            or type(spec.get('anonymous_static_resources', False)) is not bool):
        raise ValueError('Invalid website-form specification')
    resource_policy = validate_static_resources(spec.get('static_resources', {}))
    auth_policy = validate_auth_hosts(spec.get('auth_hosts', {}))
    validate_static_resources(resource_policy, tiktok=spec.get('tiktok_static_resources', False))
    for name in ('broker_user', 'gateway_user'):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', spec[name]):
            raise ValueError('Invalid account')
    if spec['broker_user'] == spec['gateway_user']:
        raise ValueError('Separate broker and gateway accounts are required')
    for name in ('broker_uid', 'broker_gid'):
        if type(spec[name]) is not int or not 100 <= spec[name] <= 60000:
            raise ValueError('Invalid broker identity')
    for name in ('python', 'code_root', 'browsers'):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', spec[name]) or '..' in Path(spec[name]).parts:
            raise ValueError('Expected a safe absolute installation path')
    human, parent = origin(spec['origin']), origin(spec['parent_origin'])
    if human == parent:
        raise ValueError('Separate HTTPS origins are required')
    gateway = pwd.getpwnam(spec['gateway_user'])
    if gateway.pw_uid in (0, spec['broker_uid']):
        raise ValueError('Invalid gateway identity')
    config = {'origin': human, 'parent_origin': parent, 'gateway_uid': gateway.pw_uid,
              'broker_uid': spec['broker_uid'], 'socket': '/run/oak-website-forms/control.sock',
              'port': 18766, 'execution': spec['execution']}
    if 'composer' in spec:
        from oak.website_composer import validate_config
        config['composer'] = validate_config(spec['composer'])
    if 'tiktok_static_resources' in spec:
        config['tiktok_static_resources'] = spec['tiktok_static_resources']
    if 'anonymous_static_resources' in spec:
        config['anonymous_static_resources'] = spec['anonymous_static_resources']
    if 'static_resources' in spec:
        config['static_resources'] = resource_policy
    if 'auth_hosts' in spec:
        config['auth_hosts'] = auth_policy
    unit = f'''[Unit]
Description=Oak private dynamic website forms
After=network-online.target oak-executor-attest.service
Wants=network-online.target oak-executor-attest.service

[Service]
User={spec['broker_user']}
Group={gateway.pw_gid}
WorkingDirectory={spec['code_root']}
ExecStart={spec['python']} -m oak.login_broker --config /etc/oak-website-forms/config.json
Environment=HOME=/var/lib/oak-website-forms
Environment=PLAYWRIGHT_BROWSERS_PATH={spec['browsers']}
Environment=PYTHONNOUSERSITE=1
Environment=LANG=C.UTF-8
UnsetEnvironment=DISPLAY XAUTHORITY OPENAI_API_KEY CODEX_API_KEY CODEX_HOME
RuntimeDirectory=oak-website-forms
RuntimeDirectoryMode=0750
UMask=0077
LimitCORE=0
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectProc=invisible
ReadWritePaths=/var/lib/oak-website-forms /run/oak-website-forms
CapabilityBoundingSet=
AmbientCapabilities=
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
StandardOutput=null
StandardError=null
KillMode=control-group
TimeoutStopSec=20
Restart=on-failure
RestartSec=3
MemoryMax=1500M
TasksMax=256

[Install]
WantedBy=multi-user.target
'''
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Use an empty output directory')
    (output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    (output / 'oak-website-forms.service').write_text(unit)
    (output / 'specification.json').write_text(json.dumps(spec, indent=2) + '\n')


def apply(output):
    if os.geteuid() != 0:
        raise ValueError('Installation requires root')
    output = Path(output)
    spec = json.loads((output / 'specification.json').read_text())
    profile = load_profile(spec['execution'])
    if profile['executor_uid'] == spec['broker_uid']:
        raise ValueError('The executor cannot own the private broker')
    if (spec['code_root'] != profile['code_root'] or spec['python'] != profile['python']
            or spec['browsers'] != profile.get('playwright_browsers_path')):
        raise ValueError('Broker dependencies must match the reviewed execution profile')
    for name in ('login_broker.py', 'signin_providers.py', 'website_forms.py', 'connected_browser.py',
                 'website_ui.py', 'website_planner.py', 'website_composer.py', 'website_resources.py',
                 'website_agent.py', 'website_scene.py', 'website_scene_dom.py',
                 'website_navigation_transport.py',
                 'website_scene_planner.py', 'website_scene_sanitize.py', 'website_auth_hosts.py',
                 'login_web/index.html', 'login_web/login.js', 'login_web/login.css', 'web/form-controls.js'):
        if str(Path(spec['code_root']) / 'oak' / name) not in profile['manifest']:
            raise ValueError('Private broker code is absent from the reviewed manifest')
    python = Path(spec['python'])
    for path in (Path(spec['code_root']), python, python.resolve().parent.parent,
                 python.parent.parent, Path(spec['browsers'])):
        trusted_tree(path)
    try:
        account = pwd.getpwnam(spec['broker_user'])
    except KeyError:
        for lookup, value in ((pwd.getpwuid, spec['broker_uid']), (grp.getgrgid, spec['broker_gid'])):
            try:
                lookup(value)
            except KeyError:
                continue
            raise ValueError('Broker identity is already allocated')
        subprocess.run(['/usr/sbin/groupadd', '--system', '--gid', str(spec['broker_gid']), spec['broker_user']], check=True)
        subprocess.run(['/usr/sbin/useradd', '--system', '--uid', str(spec['broker_uid']), '--gid', str(spec['broker_gid']),
                        '--no-create-home', '--home-dir', '/var/lib/oak-website-forms', '--shell', '/usr/sbin/nologin', spec['broker_user']], check=True)
        account = pwd.getpwnam(spec['broker_user'])
    if (account.pw_uid, account.pw_gid) != (spec['broker_uid'], spec['broker_gid']):
        raise ValueError('Broker identity does not match')
    home = Path('/var/lib/oak-website-forms')
    home.mkdir(mode=0o700, exist_ok=True)
    os.chown(home, account.pw_uid, account.pw_gid)
    home.chmod(0o700)
    config_dir = Path('/etc/oak-website-forms')
    config_dir.mkdir(mode=0o755, exist_ok=True)
    for source, target in ((output / 'config.json', config_dir / 'config.json'),
                           (output / 'oak-website-forms.service', Path('/etc/systemd/system/oak-website-forms.service'))):
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise ValueError('Installation symlinks are not accepted')
        target.write_bytes(source.read_bytes())
        os.chown(target, 0, 0)
        target.chmod(0o644)
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True)
    subprocess.run(['/usr/bin/systemctl', 'enable', '--now', 'oak-website-forms.service'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    render(json.loads(Path(args.spec).read_text()), args.output)
    if args.apply:
        apply(args.output)
    print(json.dumps({'rendered': True, 'installed': args.apply, 'provider_login_verified': False}))
