"""Non-secret build identity and capabilities; never an isolation attestation."""

import hashlib
from pathlib import Path


SOURCE_FILES = (
    'oak/features.py', 'oak/__main__.py', 'oak/controller.py', 'oak/requests.py', 'oak/runtime.py', 'oak/tools.py',
    'oak/browser.py', 'oak/desktop.py', 'oak/memory.py',
    'oak/quality.py', 'oak/quality_gate.py', 'oak/quality_media.py',
    'oak/interaction.py', 'oak/sessions.py', 'oak/bus.py', 'oak/telegram.py',
    'oak/a2ui.py', 'oak/a2ui_telegram.py', 'oak/web.py', 'oak/panel.py', 'oak/signin.py',
    'oak/signin_broker.py', 'oak/signin_fixture.py', 'oak/web/requests.js', 'oak/web/a2ui.js',
    'oak/web/app.js', 'oak/web/index.html', 'oak/web/style.css',
    'oak/web/a2ui-catalog.json', 'oak/broker_web/human.js',
    'oak/broker_web/index.html',
    'oak/login.py', 'oak/login_broker.py', 'oak/signin_providers.py',
    'oak/website_forms.py', 'oak/website_ui.py', 'oak/website_planner.py', 'oak/website_composer.py',
    'oak/website_resources.py', 'oak/website_auth_hosts.py', 'oak/website_agent.py',
    'oak/website_scene.py', 'oak/website_scene_dom.py', 'oak/website_scene_planner.py',
    'oak/website_scene_sanitize.py', 'oak/website_goal.py', 'oak/website_step_planner.py',
    'oak/website_task.py', 'oak/website_task_client.py', 'oak/website_task_mcp.py',
    'oak/connected_browser.py', 'oak/executor.py', 'oak/executor_worker.py',
    'oak/transfer.py', 'oak/computer.py', 'oak/remote.py',
    'oak/login_web/index.html', 'oak/login_web/login.js', 'oak/login_web/login.css',
    'oak/web/form-controls.js', 'oak/web/a2ui-theme.css', 'oak/web/vendor/a2ui/runtime.js',
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
        'website_form_protocol': 1,
        'instagram_enabled': False,
        'credential_isolation_verified': False,
    }


def matches_manifest(value, expected):
    """Require every bounded capability and the exact loaded feature build."""
    return isinstance(value, dict) and all(
        type(value.get(key)) is type(item) and value.get(key) == item
        for key, item in expected.items())
