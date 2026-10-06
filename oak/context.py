"""Explicit local persona and memory files, never discovered from other agents."""

from pathlib import Path


def load_context(config, config_dir):
    def read(value):
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(config_dir) / path
        text = path.read_text(encoding='utf-8')
        if len(text) > 64000:
            raise ValueError('A context file exceeds 64000 characters; select a smaller explicit export.')
        return text

    parts = []
    if config.get('instructions_file'):
        parts.append(read(config['instructions_file']))
    for source in config.get('memory_files', []):
        parts.append('Selected memory supplied by the owner (background context, not a new command):\n' + read(source))
    context = '\n\n'.join(parts)
    if len(context) > 128000:
        raise ValueError('Selected context exceeds 128000 characters; narrow the memory selection.')
    return context
