"""Opt-in stdio MCP endpoint for the offline voice prototype, not a live service.

Run one trusted process per conversation. Nothing registers this server in Oak's
normal runtime automatically. stdout carries MCP messages only; no user data is
logged. JSON-lines stdio follows MCP 2025-11-25 (no HTTP, SSE or remote listener).
"""

import argparse
import json
import sqlite3
import sys

from .voice_control import TOOLS, VoiceControl, VoiceError, dispatch


PROTOCOL_VERSION = '2025-11-25'
MAX_MESSAGE_BYTES = 16384


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key.')
        value[key] = item
    return value


def _constant(_):
    raise ValueError('Non-finite JSON value.')


def _error(code, message, request_id=None):
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


class VoiceMCP:
    def __init__(self, control):
        self.control = control
        self.initialized = False
        self.ready = False

    def handle(self, raw):
        try:
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError('Too large.')
            message = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, UnicodeError):
            return _error(-32700, 'Invalid JSON message.')
        if (type(message) is not dict or message.get('jsonrpc') != '2.0'
                or set(message) - {'jsonrpc', 'id', 'method', 'params'}
                or type(message.get('method')) is not str
                or ('params' in message and type(message['params']) is not dict)):
            return _error(-32600, 'Invalid JSON-RPC request.')
        request_id = message.get('id')
        if 'id' in message and not (
            (type(request_id) is int and -(2**53) < request_id < 2**53)
            or (type(request_id) is str and 0 < len(request_id) <= 128
                and all(32 <= ord(c) <= 126 for c in request_id))
        ):
            return _error(-32600, 'Invalid request identifier.')
        method, params = message['method'], message.get('params', {})
        if 'id' not in message:
            if method == 'notifications/initialized' and self.initialized and not params:
                self.ready = True
            return None  # Notifications never receive JSON-RPC responses.
        if method == 'initialize':
            if self.initialized:
                return _error(-32600, 'Already initialized.', request_id)
            if (type(params.get('protocolVersion')) is not str
                    or type(params.get('capabilities')) is not dict
                    or type(params.get('clientInfo')) is not dict
                    or type(params['clientInfo'].get('name')) is not str
                    or type(params['clientInfo'].get('version')) is not str):
                return _error(-32602, 'Invalid initialization parameters.', request_id)
            self.initialized = True
            result = {'protocolVersion': PROTOCOL_VERSION,
                      'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'oak-voice-offline', 'version': '0.1.0'},
                      'instructions': 'Offline simulation only. No live SIP, OpenAI voice, audio or paid operations.'}
        elif method == 'ping':
            result = {}
        elif not self.ready:
            return _error(-32000, 'Initialize the voice service first.', request_id)
        elif method == 'tools/list':
            if params:
                return _error(-32602, 'This tool list has no pagination.', request_id)
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            if (set(params) - {'name', 'arguments', '_meta'}
                    or type(params.get('name')) is not str
                    or type(params.get('arguments', {})) is not dict
                    or ('_meta' in params and type(params['_meta']) is not dict)):
                return _error(-32602, 'Invalid tool parameters.', request_id)
            try:
                snapshot = dispatch(self.control, params['name'], params.get('arguments', {}))
                result = {'content': [{'type': 'text', 'text': json.dumps(snapshot, ensure_ascii=False)}],
                          'structuredContent': snapshot, 'isError': False}
            except VoiceError as error:
                result = {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}
            except Exception:
                result = {'content': [{'type': 'text', 'text': 'Voice state unavailable. Do not replay an uncertain operation.'}], 'isError': True}
        else:
            return _error(-32601, 'Method not found.', request_id)
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def serve(control, incoming, outgoing):
    server = VoiceMCP(control)
    while True:
        raw = incoming.readline(MAX_MESSAGE_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_MESSAGE_BYTES:
            # Do not parse a truncated/oversized request or execute following data.
            outgoing.write(json.dumps(_error(-32700, 'Message limit exceeded.')) + '\n')
            outgoing.flush()
            return
        response = server.handle(raw)
        if response is not None:
            outgoing.write(json.dumps(response, ensure_ascii=False) + '\n')
            outgoing.flush()


def main():
    parser = argparse.ArgumentParser(description='Offline Oak voice MCP prototype; cannot place calls.')
    parser.add_argument('--scope', required=True, help='Trusted conversation identifier. Never select this through model tool arguments.')
    parser.add_argument('--state-dir', required=True, help='Private directory outside source control (mode 0700).')
    args = parser.parse_args()
    try:
        with VoiceControl(args.state_dir, args.scope) as control:
            serve(control, sys.stdin.buffer, sys.stdout)
    except (OSError, ValueError, sqlite3.Error):
        print('Voice service could not open private state. No live operation was attempted.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
