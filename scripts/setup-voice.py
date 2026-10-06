#!/usr/bin/env python3
"""Install public Ukrainian voice models outside the Oak repository."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile


PIPER_NAME = 'uk_UA-ukrainian_tts-medium.onnx'
PIPER_REVISION = 'c10ece1aade47bb51c153c893d14e5bf8e5b7117'
PIPER_BASE = ('https://huggingface.co/rhasspy/piper-voices/resolve/' + PIPER_REVISION +
              '/uk/uk_UA/ukrainian_tts/medium/')
PIPER_SHA256 = '7920419ac5f6fd8b6450520f24b52ed5a319cb53dd018fbcd71c9e079cbac84f'
VOSK_NAME = 'vosk-model-small-uk-v3-nano'
VOSK_URL = 'https://alphacephei.com/vosk/models/' + VOSK_NAME + '.zip'
MIB = 1024 * 1024


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(MIB):
            value.update(chunk)
    return value.hexdigest()


def download(url, target, maximum, expected_hash=None):
    if target.exists():
        if target.is_symlink() or not target.is_file() or not 0 < target.stat().st_size <= maximum:
            raise ValueError('An existing model file is invalid; inspect the model directory.')
        if expected_hash and digest(target) != expected_hash:
            raise ValueError('Existing Piper model checksum differs from the pinned public model.')
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            request = Request(url, headers={'User-Agent': 'Oak-model-setup/0.1'})
            with urlopen(request, timeout=60) as source:
                length = source.headers.get('Content-Length')
                if length and int(length) > maximum:
                    raise ValueError('Model download exceeds the expected size limit.')
                total = 0
                while chunk := source.read(MIB):
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError('Model download exceeds the expected size limit.')
                    output.write(chunk)
                if not total or (length and total != int(length)):
                    raise ValueError('Model download was incomplete.')
            output.flush()
            os.fsync(output.fileno())
        if expected_hash and digest(temporary) != expected_hash:
            raise ValueError('Downloaded Piper model checksum does not match the public model.')
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def safe_extract(archive_path, destination):
    """Validate every ZIP member before extracting into a fresh directory."""
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(info.file_size for info in members) > 512 * MIB:
            raise ValueError('Voice archive exceeds the extraction limits.')
        seen = set()
        for info in members:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            target = (destination / path).resolve()
            if (not path.parts or path.is_absolute() or path.parts[0] != VOSK_NAME
                    or any(part in {'.', '..'} for part in info.filename.split('/'))
                    or '\\' in info.filename or info.flag_bits & 1
                    or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or not target.is_relative_to(destination)
                    or info.file_size > 256 * MIB or target in seen):
                raise ValueError('Voice archive contains an unsafe or duplicate path.')
            seen.add(target)
        for info in members:
            target = destination / PurePosixPath(info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, MIB)


def validate_vosk(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Vosk model must be an extracted local directory.')
    for name in ('am/final.mdl', 'conf/mfcc.conf', 'conf/model.conf'):
        item = path / name
        if item.is_symlink() or not item.is_file() or not item.stat().st_size:
            raise ValueError('Vosk model is incomplete; inspect the model directory.')


def install(directory):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    piper = directory / PIPER_NAME
    download(PIPER_BASE + PIPER_NAME, piper, 100 * MIB, PIPER_SHA256)
    config = directory / (PIPER_NAME + '.json')
    download(PIPER_BASE + config.name, config, MIB)
    metadata = json.loads(config.read_text())
    if metadata.get('language', {}).get('code') != 'uk_UA':
        raise ValueError('The downloaded voice configuration is not Ukrainian.')
    download(PIPER_BASE + 'MODEL_CARD', directory / 'PIPER-MODEL-CARD.txt', MIB)
    vosk = directory / VOSK_NAME
    if not vosk.exists():
        with tempfile.TemporaryDirectory(prefix='.vosk-setup-', dir=directory) as temporary:
            staging = Path(temporary)
            archive = staging / 'voice.zip'
            download(VOSK_URL, archive, 100 * MIB)
            extracted = staging / 'extracted'
            extracted.mkdir()
            safe_extract(archive, extracted)
            validate_vosk(extracted / VOSK_NAME)
            (extracted / VOSK_NAME).rename(vosk)
    validate_vosk(vosk)
    return {'piper_model': str(piper), 'vosk_model': str(vosk)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='~/.local/share/oak-bot/models',
                        help='Private model directory (default: %(default)s)')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        snippet = install(args.directory)
    except (OSError, ValueError, zipfile.BadZipFile):
        print('Voice setup failed. Check network access, free space and the model directory; existing files were preserved.', file=sys.stderr)
        raise SystemExit(1) from None
    print('Add these paths to your private Oak configuration:', file=sys.stderr)
    print(json.dumps(snippet, indent=2))


if __name__ == '__main__':
    main()
