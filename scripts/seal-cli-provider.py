#!/usr/bin/env python3
"""Record a reviewed immutable Colab installation; never starts authentication."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oak.cli_provider import immutable, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('Sealing the provider requires root.')
    output = Path(args.output)
    immutable(output.parent)
    value = manifest(args.python)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({'sealed': True, 'upstream_commit': value['upstream_commit'],
                      'files': len(value['files']), 'authentication_started': False}))


if __name__ == '__main__':
    main()
