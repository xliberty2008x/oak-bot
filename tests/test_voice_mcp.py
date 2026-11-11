"""MCP stdio fixture checks. No OpenAI/SIP credentials or network."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from oak.voice_control import VoiceControl
from oak.voice_mcp import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, VoiceMCP, serve


def rpc(method, params=None, request_id=1):
    return json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': method,
                       'params': params or {}}).encode()


def initialize():
    return rpc('initialize', {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                              'clientInfo': {'name': 'test', 'version': '1'}})


INITIALIZED = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'


class VoiceMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = VoiceControl(self.temp.name, 'conversation-A')
        self.addCleanup(self.control.close)
        self.server = VoiceMCP(self.control)

    def ready(self):
        result = self.server.handle(initialize())['result']
        self.assertEqual(result['protocolVersion'], PROTOCOL_VERSION)
        self.assertIsNone(self.server.handle(INITIALIZED))

    def tool(self, name, arguments=None):
        return self.server.handle(rpc('tools/call', {'name': name, 'arguments': arguments or {}}))['result']

    def test_initialization_discovery_and_call_lifecycle(self):
        self.ready()
        tools = self.server.handle(rpc('tools/list'))['result']['tools']
        self.assertEqual(len(tools), 5)
        self.assertTrue(all(not t['inputSchema']['additionalProperties'] for t in tools))
        self.assertEqual(self.server.handle(rpc('ping'))['result'], {})
        result = self.tool('voice_prepare_call', {'request_key': 'test', 'destination_ref': 'owner'})
        self.assertFalse(result['isError'])
        self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
        call_id = result['structuredContent']['call_id']
        simulated = self.tool('voice_simulate_call', {'call_id': call_id})
        self.assertEqual(simulated['structuredContent']['state'], 'simulated_ended')
        self.assertEqual(self.tool('voice_call_status', {'call_id': call_id}), simulated)

    def test_cannot_use_tools_before_initialized_notification(self):
        for raw in (rpc('tools/list'), rpc('tools/call', {'name': 'voice_capabilities'})):
            self.assertEqual(self.server.handle(raw)['error']['code'], -32000)
        self.server.handle(initialize())
        self.assertEqual(self.server.handle(rpc('tools/list'))['error']['code'], -32000)
        self.server.handle(INITIALIZED)
        self.assertIn('result', self.server.handle(rpc('tools/list')))

    def test_bad_or_duplicate_initialize_and_version_negotiation(self):
        self.assertEqual(self.server.handle(rpc('initialize'))['error']['code'], -32602)
        request = json.loads(initialize())
        request['params']['protocolVersion'] = 'old-version'
        self.assertEqual(self.server.handle(json.dumps(request).encode())['result']['protocolVersion'], PROTOCOL_VERSION)
        self.assertIn('error', self.server.handle(initialize()))

    def test_bad_json_and_invalid_rpc(self):
        for raw in (b'nope', b'\xff', b'{"jsonrpc":"2.0","jsonrpc":"2.0"}', b'{"x":NaN}', b'x' * (MAX_MESSAGE_BYTES + 1)):
            self.assertEqual(self.server.handle(raw)['error']['code'], -32700)
        for value in ([], None, {}, {'jsonrpc': '2.0', 'id': True, 'method': 'ping'},
                      {'jsonrpc': '2.0', 'id': None, 'method': 'ping'},
                      {'jsonrpc': '2.0', 'id': 'line\nbreak', 'method': 'ping'},
                      {'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': []}):
            self.assertEqual(self.server.handle(json.dumps(value).encode())['error']['code'], -32600)

    def test_notifications_cannot_execute_tools(self):
        self.ready()
        raw = json.dumps({'jsonrpc': '2.0', 'method': 'tools/call', 'params': {
            'name': 'voice_prepare_call', 'arguments': {'request_key': 'test', 'destination_ref': 'owner'}}}).encode()
        with patch.object(self.control, 'prepare') as prepare:
            self.assertIsNone(self.server.handle(raw))
            prepare.assert_not_called()

    def test_scope_and_approval_injection_rejected(self):
        self.ready()
        for key in ('scope', 'approved', 'live', 'api_key', 'memory'):
            result = self.tool('voice_capabilities', {key: 'untrusted'})
            self.assertTrue(result['isError'])
            self.assertNotIn('untrusted', json.dumps(result))
        result = self.server.handle(rpc('tools/call', {'name': 'voice_capabilities', '_meta': {'scope': 'other'}}))
        self.assertFalse(result['result']['structuredContent']['live_calls_enabled'])

    def test_tool_errors_are_sanitized(self):
        self.ready()
        self.assertTrue(self.tool('live_call')['isError'])
        with patch.object(self.control, 'status', side_effect=OSError('private-secret-path')):
            result = self.tool('voice_call_status', {'call_id': 'a' * 32})
        self.assertTrue(result['isError'])
        self.assertNotIn('private-secret', json.dumps(result))

    def test_unknown_method_and_bad_params(self):
        self.ready()
        self.assertEqual(self.server.handle(rpc('resources/list'))['error']['code'], -32601)
        self.assertEqual(self.server.handle(rpc('tools/list', {'cursor': 'fake'}))['error']['code'], -32602)
        for params in ({}, {'name': 5}, {'name': 'voice_capabilities', 'arguments': []},
                       {'name': 'voice_capabilities', 'scope': 'other'}, {'name': 'voice_capabilities', '_meta': True}):
            self.assertEqual(self.server.handle(rpc('tools/call', params))['error']['code'], -32602)

    def test_stdio_framing_eof_and_oversize_shutdown(self):
        incoming = io.BytesIO(b'\n'.join([initialize(), INITIALIZED, rpc('tools/list')]) + b'\n')
        outgoing = io.StringIO()
        serve(self.control, incoming, outgoing)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertIn('tools', messages[1]['result'])
        outgoing = io.StringIO()
        serve(self.control, io.BytesIO(b'x' * (MAX_MESSAGE_BYTES + 1) + b'\n' + initialize()), outgoing)
        self.assertEqual(len(outgoing.getvalue().splitlines()), 1)

    def test_actual_cli_launch_has_only_json_rpc_stdout(self):
        self.control.close()
        data = b'\n'.join([initialize(), INITIALIZED, rpc('tools/call', {'name': 'voice_capabilities'})]) + b'\n'
        env = dict(os.environ, OPENAI_API_KEY='fixture-not-a-real-key', OAK_VOICE_LIVE='true')
        result = subprocess.run([sys.executable, '-m', 'oak.voice_mcp', '--scope', 'conversation-A',
                                 '--state-dir', self.temp.name], input=data, capture_output=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertFalse(messages[-1]['result']['structuredContent']['live_calls_enabled'])
        self.assertNotIn(b'fixture-not-a-real-key', result.stdout)

    def test_cli_state_failure_does_not_expose_private_paths(self):
        result = subprocess.run([sys.executable, '-m', 'oak.voice_mcp', '--scope', 'conversation-A',
                                 '--state-dir', self.temp.name], input=b'', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(self.temp.name.encode(), result.stderr)
        self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main()
