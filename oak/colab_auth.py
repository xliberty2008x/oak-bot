"""Fixed private adapter for googlecolab/google-colab-cli 006ff96ac.

Only the broker owns stdin/stdout and HOME. No command, OAuth destination,
credentials or provider response is accepted from a model or gateway argument.
"""

import contextlib
import json
import logging
import os
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oak.cli_contract import GPU_TYPES
from oak.cli_process import authorization_url

UPSTREAM_COMMIT = '006ff96ac2d0e4dcfb702467a76493d687929be8'


def serve(auth, client, incoming, outgoing):
    """Run the official OAuth/cache contract with a private JSONL prompt adapter."""
    def emit(value):
        outgoing.write(json.dumps(value, separators=(',', ':')) + '\n')
        outgoing.flush()

    def remote_flow(config):
        flow = auth.InstalledAppFlow.from_client_config(config, auth.PUBLIC_SCOPES)
        flow.redirect_uri = auth.REMOTE_REDIRECT_URI
        url, _ = flow.authorization_url(prompt='consent', token_usage='remote')
        emit({'challenge': authorization_url(url)})
        raw = incoming.readline(4098)
        if (not raw.endswith('\n') or not 1 <= len(raw[:-1]) <= 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in raw[:-1])):
            raise ValueError('Private authorization input unavailable.')
        code = raw[:-1]
        try:
            flow.fetch_token(code=code, timeout=30)
        finally:
            code = raw = None
        return flow.credentials

    original = auth._run_remote_flow
    auth._run_remote_flow = remote_flow
    session = None
    try:
        # Dependency output is not a protocol surface. The broker also discards
        # stderr; neither exception text nor account data is sent to the gateway.
        with open(os.devnull, 'w') as discarded, contextlib.redirect_stdout(discarded):
            session = auth.get_credentials(provider=auth.AuthProvider.OAUTH2)
            if not session.credentials.valid:
                raise ValueError('Provider authentication was not verified.')
            info = client.Client(client.Prod(), session).get_consumption_user_info()
            if info is None:
                raise ValueError('Provider authentication was not verified.')
            supported = sorted(item.value for item in client.Accelerator if item.value in GPU_TYPES)
            if set(supported) != GPU_TYPES:
                raise ValueError('Unsupported provider contract.')
        # CCU metadata proves a read-only authenticated response. Supported GPU
        # names are an inventory, never a promise of free quota or allocation.
        emit({'verified': {'authenticated': True, 'gpu_types': supported, 'availability': 'unknown'}})
    finally:
        auth._run_remote_flow = original
        if session is not None:
            with contextlib.suppress(Exception):
                session.close()


def main():
    logging.disable(logging.CRITICAL)
    if len(sys.argv) != 1:
        return 1
    try:
        from colab_cli import auth, client
        serve(auth, client, sys.stdin, sys.stdout)
    except BaseException:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
