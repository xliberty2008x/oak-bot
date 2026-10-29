#!/usr/bin/python3
"""Import the owner's existing OpenAI key through a private SSH terminal."""
import getpass
import os
from pathlib import Path
import re
import secrets
import stat
import sys

BASE = Path('/home/ubuntu/oak-stt-openai')
TARGET = BASE / 'secrets/openai.env'


def checked_directory(path):
    if path.resolve() != path:
        raise ValueError('Symlink directories are forbidden')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Private directory must belong to the current user and have mode 0700')


def write_key(target, value):
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,509}', value):
        raise ValueError('Invalid OpenAI API key format; its value was discarded')
    checked_directory(target.parent)
    original = ''
    if target.exists() or target.is_symlink():
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 65536):
                raise ValueError('Existing env file has unsafe permissions or type')
            with os.fdopen(fd, 'r', closefd=False) as stream:
                original = stream.read()
        finally:
            os.close(fd)
    lines = []
    updated = False
    for line in original.splitlines():
        if re.match(r'^\s*(?:export\s+)?OPENAI_API_KEY\s*=', line):
            if not updated:
                lines.append('OPENAI_API_KEY=' + value)
                updated = True
        else:
            lines.append(line)
    if not updated:
        lines.append('OPENAI_API_KEY=' + value)
    temporary = target.with_name('.openai-import-' + secrets.token_hex(12) + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('Run this command directly in your SSH terminal; redirected input is refused')
    checked_directory(BASE)
    print('Збереження OPENAI_API_KEY у ' + str(TARGET) + ' з правами 0600.')
    print('Ключ використовуватиметься лише брокером STT для Oak. Ctrl+C — скасувати.')
    value = getpass.getpass('Вставте вже створений ключ і натисніть Enter (ввід приховано): ')
    try:
        write_key(TARGET, value)
    finally:
        value = ''
    print('Ключ збережено. Повідомте в чаті лише: «готово».')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nСкасовано; ключ не збережено.', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError):
        print('Ключ не збережено: перевірте SSH-термінал, формат ключа та права приватного каталогу.', file=sys.stderr)
        sys.exit(1)
