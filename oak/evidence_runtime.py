"""Isolated, tool-disabled subscription runtime for evidence review and alert composition."""

import asyncio
import contextlib
import re

from .runtime import MODEL, RuntimeClient


# These are features exposed by the supported native runtime. Read-only sandbox
# alone would still allow shell reads and is not a substitute for disabling tools.
_DISABLED_FEATURES = (
    'shell_tool', 'unified_exec', 'apps', 'plugins', 'remote_plugin', 'multi_agent',
    'multi_agent_v2', 'browser_use', 'browser_use_external', 'computer_use',
    'view_image', 'image_generation', 'workspace_dependencies', 'skill_search',
    'skill_mcp_dependency_install', 'sleep_tool', 'goals', 'code_mode',
    'code_mode_only', 'code_mode_host', 'tool_suggest', 'hooks', 'plugin_hooks',
    'memories',
)
_NON_TOOL_ITEMS = frozenset({'userMessage', 'agentMessage', 'reasoning',
                           'plan', 'contextCompaction'})


class EvidenceTurnError(RuntimeError):
    """A sanitized failure code, safe to report to the caller."""


async def evidence_turn(deployment, instructions, inputs, *, model=MODEL, effort='low',
                        schema=None, client_factory=RuntimeClient):
    """Run one ephemeral native turn with only the evidence supplied by the caller."""
    config = {'approval_policy': 'on-request', 'web_search': 'disabled',
              'model_reasoning_effort': effort, 'project_doc_max_bytes': 0,
              'developer_instructions': '',
              **{f'features.{name}': False for name in _DISABLED_FEATURES}}
    client = client_factory(cwd=deployment.get('workspace'), sandbox='read-only', config=config,
                            home=deployment.get('runtime_home'), execution=deployment.get('execution'))
    thread_id = None
    turn_id = None
    async with client:
        # A home/project configuration can contain local MCP servers even
        # with apps/plugins disabled. Disable every effective server before
        # creating a thread. Inspect names only; never persist this response.
        effective = await client.request('config/read', {
            'cwd': deployment.get('workspace'), 'includeLayers': False})
        servers = effective.get('config', {}).get('mcp_servers') or {}
        if (not isinstance(servers, dict) or any(
                not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name)
                for name in servers)):
            raise EvidenceTurnError('tool_configuration_invalid')
        overrides = {f'mcp_servers.{name}.enabled': False for name in servers}
        # Explicit base instructions remove the producer/coding-agent role;
        # project docs, memory and skills are not reviewer evidence.
        thread = await client.request('thread/start', {
            'cwd': deployment.get('workspace'), 'ephemeral': True,
            'model': model, 'dynamicTools': [], 'environments': [],
            'config': overrides, 'baseInstructions': instructions,
            'developerInstructions': instructions,
        })
        if thread.get('model') != model or thread.get('modelProvider') != 'openai':
            raise EvidenceTurnError('model_mismatch')
        thread_id = thread['thread']['id']
        try:
            turn_params = {'threadId': thread_id, 'model': model, 'effort': effort,
                           'input': inputs, 'environments': []}
            if schema is not None:
                turn_params['outputSchema'] = schema
            turn = await client.request('turn/start', turn_params)
            turn_id = turn['turn']['id']
            final = None
            while True:
                event = await client.events.get()
                method = event.get('method')
                params = event.get('params') or {}
                if method == 'client/disconnected':
                    raise EvidenceTurnError('runtime_disconnected')
                if params.get('threadId') != thread_id:
                    continue
                if params.get('turnId') not in {None, turn_id}:
                    continue
                if method in {'item/started', 'item/completed'}:
                    item = params.get('item') or {}
                    if item.get('type') not in _NON_TOOL_ITEMS:
                        raise EvidenceTurnError('attempted_tool_use')
                    if method == 'item/completed' and item.get('type') == 'agentMessage':
                        if item.get('phase') in {None, 'final_answer'}:
                            final = item.get('text')
                elif method == 'client/requestDenied':
                    raise EvidenceTurnError('attempted_tool_use')
                elif method == 'error':
                    raise EvidenceTurnError('runtime_error')
                elif method == 'turn/completed':
                    completed = params.get('turn') or {}
                    if completed.get('id') != turn_id or completed.get('status') != 'completed':
                        raise EvidenceTurnError('turn_incomplete')
                    turn_id = None
                    return final
        finally:
            if turn_id:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.request('turn/interrupt', {
                        'threadId': thread_id, 'turnId': turn_id}), 3)
            # Ephemeral threads cannot be archived; closing this dedicated
            # client discards their in-memory context without a rollout file.
