"""Bounded owner-facing projections; never expose stored event payloads or paths."""

from pathlib import Path


def require_scope(controller, owner, scope):
    """Validate an already selected conversation, including private web sessions."""
    if type(owner) is not int or type(scope) is not int or controller.sessions.deleted(scope):
        raise LookupError('Розмову не знайдено.')
    if controller.sessions.owner(scope) == owner:
        return
    db = controller.db
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_conversations'").fetchone():
        if db.execute('SELECT 1 FROM web_conversations WHERE chat_id=? AND owner=?', (scope, owner)).fetchone():
            return
    raise LookupError('Розмову не знайдено.')


def page_bounds(before, limit):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Розмір сторінки має бути від 1 до 50.')
    if before is not None and (type(before) is not int or not 0 < before < 2**63):
        raise ValueError('Некоректна сторінка.')
    return before, limit


def preview(text, limit=80):
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


class OwnerViews:
    def __init__(self, controller):
        self.controller, self.db = controller, controller.db

    def artifacts(self, owner, scope, *, before=None, limit=30):
        require_scope(self.controller, owner, scope)
        page_bounds(before, limit)
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone():
            return {'items': [], 'next_cursor': None}
        rows = self.db.execute('SELECT rowid,* FROM artifacts WHERE chat_id=? '
                               + ('AND rowid<? ' if before is not None else '')
                               + 'ORDER BY rowid DESC LIMIT ?',
                               (scope, before, limit + 1) if before is not None else (scope, limit + 1)).fetchall()
        items = []
        media = {'image': {'image/png', 'image/jpeg', 'image/gif', 'image/webp'},
                 'video': {'video/mp4', 'video/webm'},
                 'audio': {'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/wav', 'audio/x-wav'}}
        for row in rows[:limit]:
            name = str(row['name'] or '').replace('\\', '/').rsplit('/', 1)[-1]
            name = preview(''.join(char for char in name if ord(char) >= 32 and not 127 <= ord(char) <= 159), 120)
            name = name or 'Файл'
            mime = row['mime'] if row['mime'] in {'application/pdf', 'text/plain', 'application/zip'} \
                or any(row['mime'] in types for types in media.values()) else 'application/octet-stream'
            kind = next((kind for kind, types in media.items() if mime in types), 'document')
            size = None
            try:
                path = Path(row['path'])
                if path.is_file():
                    size = path.stat().st_size
            except (OSError, TypeError, ValueError):
                pass
            items.append({'id': row['id'], 'name': name, 'mime': mime, 'kind': kind,
                          'size_bytes': size, 'available': size is not None,
                          'url': '/api/artifacts/' + row['id'] if size is not None else None})
        return {'items': items, 'next_cursor': rows[limit - 1]['rowid'] if len(rows) > limit else None}
