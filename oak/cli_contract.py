"""Public CLI-authentication metadata. Never accept a native PTY or command."""

import math
import re

PROVIDER = 'colab'
PATH = '/cli/'
STATES = frozenset({'waiting', 'active', 'authenticated', 'cancelled', 'expired', 'failed', 'uncertain'})
TERMINAL = frozenset({'cancelled', 'expired', 'failed', 'uncertain'})
# googlecolab/google-colab-cli 006ff96, commands/session.py's GPU enum.
GPU_TYPES = frozenset({'T4', 'L4', 'G4', 'H100', 'A100'})


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) is not None


def capability(ready=False):
    return {'provider': PROVIDER, 'available': ready is True,
            'reason': 'private_cli_available' if ready is True else 'private_cli_unavailable',
            'native_session_attach': False}


def metadata(value):
    """Allowlisted verification only; arbitrary provider output is never public."""
    if (not isinstance(value, dict) or set(value) != {'authenticated', 'gpu_types', 'availability'}
            or value['authenticated'] is not True or not isinstance(value['gpu_types'], list)
            or value['availability'] != 'unknown'
            or len(value['gpu_types']) > 32
            or any(not isinstance(item, str) or item not in GPU_TYPES for item in value['gpu_types'])
            or len(set(value['gpu_types'])) != len(value['gpu_types'])):
        raise ValueError('Invalid CLI verification metadata.')
    return {'authenticated': True, 'gpu_types': list(value['gpu_types']), 'availability': 'unknown'}


def status(value):
    fields = {'request_id', 'session_id', 'runtime_epoch', 'provider', 'state', 'cleanup', 'session_expires'}
    if (not isinstance(value, dict) or set(value) != fields
            or not identifier(value['request_id']) or not identifier(value['session_id'])
            or not isinstance(value['runtime_epoch'], str) or not 0 < len(value['runtime_epoch']) <= 200
            or value['provider'] != PROVIDER or value['state'] not in STATES
            or value['cleanup'] not in {'waiting', 'retained', 'confirmed', 'uncertain'}
            or type(value['session_expires']) not in (int, float)
            or not math.isfinite(value['session_expires']) or value['session_expires'] < 0):
        raise ValueError('Invalid CLI authentication status.')
    return dict(value)
