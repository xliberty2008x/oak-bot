"""Non-secret build identity and capabilities; never an isolation attestation."""

import hashlib
from pathlib import Path


SOURCE_FILES = (
    'oak/features.py', 'oak/controller.py', 'oak/requests.py', 'oak/runtime.py', 'oak/tools.py',
    'oak/interaction.py', 'oak/sessions.py', 'oak/bus.py', 'oak/telegram.py',
    'oak/a2ui.py', 'oak/web.py', 'oak/panel.py', 'oak/signin.py',
    'oak/signin_broker.py', 'oak/signin_fixture.py', 'oak/web/requests.js', 'oak/web/a2ui.js',
    'oak/web/app.js', 'oak/web/index.html', 'oak/web/style.css',
    'oak/web/a2ui-catalog.json', 'oak/broker_web/human.js',
    'oak/broker_web/index.html',
)


def feature_manifest(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        content = (root / name).read_bytes()
        digest.update(name.encode() + b'\0' + str(len(content)).encode() + b'\0' + content)
    return {
        'manifest_version': 1,
        'code_digest': digest.hexdigest(),
        'contextual_input': 1,
        'a2ui_version': 'v0.9.1',
        'a2ui_catalog': 'urn:oak:a2ui:canonical:v1',
        'synthetic_broker': 1,
        'instagram_enabled': False,
        'credential_isolation_verified': False,
    }


def matches_manifest(value, expected):
    """Require every bounded capability and the exact loaded feature build."""
    return isinstance(value, dict) and all(
        type(value.get(key)) is type(item) and value.get(key) == item
        for key, item in expected.items())
