"""Gateway-only MCP client for one existing private website lease.

The native model supplies a website URL. Gateway-owned binding and MCP session
headers never enter model arguments, ordinary forms or persisted chat events.
"""

import json

from .website_task import validate_task_snapshot


PROTOCOL = '2025-11-25'
TOOL_NAMES = {'website_sign_in', 'website_task_status', 'website_task_cancel'}


class WebsiteTaskClient:
    def __init__(self, client, scope):
        self.client, self.scope = client, dict(scope)
        self.session = None
        self.sequence = 0

    async def rpc(self, method, params=None, *, notification=False):
        self.sequence += 1
        message = {'jsonrpc': '2.0', 'method': method}
        if not notification:
            message['id'] = self.sequence
        if params is not None:
            message['params'] = params
        headers = {'Accept': 'application/json, text/event-stream'}
        if self.session:
            headers.update({'MCP-Session-Id': self.session, 'MCP-Protocol-Version': PROTOCOL})
        async with self.client.post('http://login/mcp', json=message, headers=headers,
                                    allow_redirects=False) as response:
            if notification:
                if response.status != 202:
                    raise ValueError('Website task notification failed.')
                return
            if response.status != 200 or response.content_type != 'application/json':
                raise ValueError('Website task transport failed.')
            raw = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > 32768:
                    raise ValueError('Website task response exceeded its bound.')
            value = json.loads(raw)
            if (type(value) is not dict or set(value) != {'jsonrpc', 'id', 'result'}
                    or value['jsonrpc'] != '2.0' or value['id'] != self.sequence
                    or type(value['result']) is not dict):
                raise ValueError('Website task response is invalid.')
            if method == 'initialize':
                import re
                session = response.headers.get('MCP-Session-Id', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', session):
                    raise ValueError('Website task session is invalid.')
                self.session = session
            return value['result']

    async def initialize(self):
        result = await self.rpc('initialize', {
            'protocolVersion': PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'oak-website-gateway', 'version': '1.0.0'},
            '_meta': {'oak/lease': self.scope}})
        if (result.get('protocolVersion') != PROTOCOL or type(result.get('capabilities')) is not dict
                or result['capabilities'].get('tools') not in ({}, {'listChanged': False})):
            raise ValueError('Website task protocol is unavailable.')
        await self.rpc('notifications/initialized', notification=True)
        catalog = await self.rpc('tools/list')
        if (type(catalog.get('tools')) is not list
                or {tool.get('name') for tool in catalog['tools']} != TOOL_NAMES):
            raise ValueError('Website task catalog is unavailable.')

    async def call(self, name, *, goal=None):
        if name not in TOOL_NAMES:
            raise ValueError('Unknown website task tool.')
        arguments = {}
        if goal is not None:
            from .website_goal import validate_task_goal
            if name != 'website_sign_in':
                raise ValueError('A task objective belongs only to task start.')
            arguments['goal'] = validate_task_goal(goal)
        result = await self.rpc('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError', False) is not False:
            raise ValueError('Website task operation failed.')
        return validate_task_snapshot(result.get('structuredContent'))

    async def close(self):
        if self.session is None:
            return
        headers = {'Accept': 'application/json, text/event-stream',
                   'MCP-Session-Id': self.session, 'MCP-Protocol-Version': PROTOCOL}
        async with self.client.delete('http://login/mcp', headers=headers, allow_redirects=False) as response:
            # A terminal/revoked browser lease has already stopped its task.
            if response.status not in {200, 202, 204, 404}:
                raise ValueError('Website task cleanup is unconfirmed.')
        self.session = None
