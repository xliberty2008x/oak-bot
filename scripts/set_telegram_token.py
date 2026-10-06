#!/usr/bin/env python3
"""Read a Telegram token without echo and save it outside the repository."""

import getpass
import argparse
import os
from pathlib import Path
import re
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='~/.config/oak-bot/telegram-token')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('Run this script in an interactive terminal.')
    os.umask(0o077)
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = getpass.getpass('Telegram bot token (hidden input): ').strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        raise SystemExit('Invalid Telegram token format; nothing saved.')
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(token + '\n')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Token saved privately. Its value was not printed.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('\nCancelled; nothing saved.') from None
