"""Offline voice-control prototype. No SIP, audio, credentials or paid API client.

One trusted launcher binds a store to a conversation. Tool arguments cannot
select that scope. All provider operations below are local deterministic mocks.
"""

import hashlib
import os
import re
import sqlite3
import stat
import uuid
from pathlib import Path

import fcntl


class VoiceError(ValueError):
    """Safe, fixed error text suitable for a tool response."""


def _alias(value):
    return type(value) is str and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}', value)


def _call_id(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{32}', value)


def capabilities():
    return {
        'mode': 'offline_simulation',
        'live_calls_enabled': False,
        'network_access': False,
        'audio_bridge_implemented': False,
        'sip_provider': 'not_configured',
        'voice_provider': 'openai_candidate_unverified',
        'language': 'uk-UA',
        'incremental_provider_charge': 0,
        'readiness': 'not_ready_for_live_calls',
        'missing': ['operator_trunk_contract_and_credentials',
                    'verified_sip_and_media_interoperability',
                    'separately_configured_voice_backend_and_cost_limits',
                    'authenticated_owner_call_authorization',
                    'live_adapter_and_audio_bridge',
                    'controlled_owner_test'],
    }


class MockVoiceAdapter:
    """No external side effects. These outcomes are fixtures, not provider data."""

    def simulate(self, outcome):
        return {
            'connected': 'simulated_connected',
            'completed': 'simulated_ended',
            'failed': 'simulated_failed',
            'uncertain': 'uncertain',
        }[outcome]


class VoiceControl:
    """Durable, scope-bound dry-run lifecycle; only one process per scope.

    A consumed simulation is never replayed, even after a crash or cancellation.
    An uncertain outcome stays uncertain. No caller-supplied approval flag or
    environment variable can enable a real call in this implementation.
    """

    def __init__(self, state_dir, scope, *, adapter=None):
        if (type(scope) is not str
                or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}', scope)):
            raise VoiceError('A trusted conversation scope is required.')
        root = Path(state_dir)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or root.is_symlink()
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise VoiceError('State directory must be private and owner-controlled.')
        stem = hashlib.sha256(scope.encode('utf-8')).hexdigest()
        self._lock = None
        self._db = None
        try:
            self._lock = self._private_file(root / (stem + '.lock'))
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise VoiceError('This conversation already has a voice service.') from None
            path = root / (stem + '.sqlite3')
            fd = self._private_file(path)
            os.close(fd)
            self._db = sqlite3.connect(path)
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA journal_mode=DELETE')
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('''CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                destination_ref TEXT NOT NULL, max_duration_seconds INTEGER NOT NULL,
                state TEXT NOT NULL, attempted INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0)''')
            # The process lock proves the previous owner is no longer running.
            with self._db:
                self._db.execute("UPDATE calls SET state='uncertain' WHERE state='simulating'")
            self._adapter = adapter or MockVoiceAdapter()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _private_file(path):
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            os.close(fd)
            raise VoiceError('State files must be private regular files.')
        return fd

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _row(self, call_id):
        if not _call_id(call_id):
            raise VoiceError('Invalid call reference.')
        row = self._db.execute('SELECT * FROM calls WHERE call_id=?', (call_id,)).fetchone()
        if row is None:
            raise VoiceError('Call was not found in this conversation.')
        return row

    @staticmethod
    def _snapshot(row):
        return {'call_id': row['call_id'], 'mode': 'offline_simulation',
                'state': row['state'], 'destination_ref': row['destination_ref'],
                'language': 'uk-UA', 'max_duration_seconds': row['max_duration_seconds'],
                'cancel_requested': bool(row['cancel_requested']),
                'simulation_consumed': bool(row['attempted']),
                'live_call_started': False, 'incremental_provider_charge': 0}

    def prepare(self, request_key, destination_ref, max_duration_seconds=60):
        if not _alias(request_key) or not _alias(destination_ref):
            raise VoiceError('Use opaque aliases, not phone numbers or credentials.')
        if type(max_duration_seconds) is not int or not 15 <= max_duration_seconds <= 600:
            raise VoiceError('Duration must be an integer between 15 and 600 seconds.')
        row = self._db.execute('SELECT * FROM calls WHERE request_key=?', (request_key,)).fetchone()
        if row is not None:
            if (row['destination_ref'], row['max_duration_seconds']) != (destination_ref, max_duration_seconds):
                raise VoiceError('Request key already belongs to different call details.')
            return self._snapshot(row)
        call_id = uuid.uuid4().hex
        with self._db:
            self._db.execute('''INSERT INTO calls
                (call_id, request_key, destination_ref, max_duration_seconds, state)
                VALUES (?, ?, ?, ?, 'prepared')''',
                (call_id, request_key, destination_ref, max_duration_seconds))
        return self.status(call_id)

    def simulate(self, call_id, outcome='completed'):
        if type(outcome) is not str or outcome not in {'connected', 'completed', 'failed', 'uncertain'}:
            raise VoiceError('Unknown simulated outcome.')
        row = self._row(call_id)
        if row['attempted']:
            return self._snapshot(row)
        if row['state'] != 'prepared':
            raise VoiceError('Only a prepared call can be simulated.')
        # Commit before touching the adapter. Interruption cannot lead to replay.
        with self._db:
            self._db.execute("UPDATE calls SET state='simulating', attempted=1 WHERE call_id=?", (call_id,))
        try:
            state = self._adapter.simulate(outcome)
            if state not in {'simulated_connected', 'simulated_ended', 'simulated_failed', 'uncertain'}:
                state = 'uncertain'
        except Exception:
            # Never include adapter errors, credentials or account data in results.
            state = 'uncertain'
        with self._db:
            self._db.execute('UPDATE calls SET state=? WHERE call_id=?', (state, call_id))
        return self.status(call_id)

    def status(self, call_id):
        return self._snapshot(self._row(call_id))

    def cancel(self, call_id):
        row = self._row(call_id)
        state = {'prepared': 'cancelled', 'simulated_connected': 'simulated_ended'}.get(row['state'], row['state'])
        with self._db:
            self._db.execute('UPDATE calls SET state=?, cancel_requested=1 WHERE call_id=?', (state, call_id))
        return self.status(call_id)


def _tool(name, description, properties, required=(), *, read_only=False):
    return {'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties,
                            'required': list(required), 'additionalProperties': False},
            'annotations': {'readOnlyHint': read_only, 'destructiveHint': False,
                            'idempotentHint': True, 'openWorldHint': False}}


_REFERENCE = {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}
_ALIAS = {'type': 'string', 'pattern': '^[a-zA-Z][a-zA-Z0-9_-]{0,63}$'}
TOOLS = [
    _tool('voice_capabilities', 'Read offline prototype readiness. No live SIP or voice backend exists.', {}, read_only=True),
    _tool('voice_prepare_call', 'Prepare an offline Ukrainian call fixture. Use opaque aliases only; never send phone numbers, prompts, memory or credentials.',
          {'request_key': _ALIAS, 'destination_ref': _ALIAS,
           'max_duration_seconds': {'type': 'integer', 'minimum': 15, 'maximum': 600, 'default': 60}},
          ('request_key', 'destination_ref')),
    _tool('voice_simulate_call', 'Consume one offline call simulation. Does not call anyone, start voice or spend money. Repeated calls return the prior state.',
          {'call_id': _REFERENCE, 'outcome': {'type': 'string', 'enum': ['connected', 'completed', 'failed', 'uncertain'], 'default': 'completed'}}, ('call_id',)),
    _tool('voice_call_status', 'Read a fixture in this conversation, never another conversation.',
          {'call_id': _REFERENCE}, ('call_id',), read_only=True),
    _tool('voice_cancel_call', 'Cancel a prepared fixture or end simulated audio. An uncertain result remains uncertain; no real hangup is performed.',
          {'call_id': _REFERENCE}, ('call_id',)),
]


def dispatch(control, name, arguments):
    if type(name) is not str or type(arguments) is not dict:
        raise VoiceError('Invalid tool call.')
    spec = next((tool for tool in TOOLS if tool['name'] == name), None)
    if spec is None:
        raise VoiceError('Unknown voice tool. Live call tools are unavailable.')
    schema = spec['inputSchema']
    if set(arguments) - set(schema['properties']) or not set(schema['required']) <= set(arguments):
        raise VoiceError('Unexpected or missing tool arguments.')
    handlers = {'voice_capabilities': capabilities, 'voice_prepare_call': control.prepare,
                'voice_simulate_call': control.simulate, 'voice_call_status': control.status,
                'voice_cancel_call': control.cancel}
    return handlers[name](**arguments)
