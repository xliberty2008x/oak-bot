"""Bounded owner-facing projections; never expose stored event payloads or paths."""

from pathlib import Path
import hashlib

from .strings import text


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


ARTIFACT_MEDIA = {'image': {'image/png', 'image/jpeg', 'image/gif', 'image/webp'},
                  'video': {'video/mp4', 'video/webm'},
                  'audio': {'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/wav', 'audio/x-wav'}}


def artifact_mime(mime):
    return mime if mime in {'application/pdf', 'text/plain', 'application/zip'} \
        or any(mime in types for types in ARTIFACT_MEDIA.values()) else 'application/octet-stream'


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
        for row in rows[:limit]:
            name = str(row['name'] or '').replace('\\', '/').rsplit('/', 1)[-1]
            name = preview(''.join(char for char in name if ord(char) >= 32 and not 127 <= ord(char) <= 159), 120)
            name = name or text('common.file', self.controller.config.get('locale'))
            mime = artifact_mime(row['mime'])
            kind = next((kind for kind, types in ARTIFACT_MEDIA.items() if mime in types), 'document')
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

    def activity(self, owner, scope, *, before=None, limit=30):
        from .activity import tool_label

        require_scope(self.controller, owner, scope)
        page_bounds(before, limit)
        rows = self.db.execute('SELECT rowid,turn_id,status FROM turns WHERE chat_id=? '
                               + ('AND rowid<? ' if before is not None else '')
                               + 'ORDER BY rowid DESC LIMIT ?',
                               (scope, before, limit + 1) if before is not None else (scope, limit + 1)).fetchall()
        events_exist = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_events'").fetchone()
        items = []
        for row in rows[:limit]:
            status = row['status'] if row['status'] in {'inProgress', 'completed', 'failed', 'interrupted', 'uncertain'} else 'unknown'
            started, finished, count, labels, truncated = None, None, 0, [], False
            if events_exist:
                # Select only bounded metadata. Message bodies, tool arguments,
                # results, native errors and task-outcome claims stay private.
                stats = self.db.execute('''SELECT
                    MIN(CASE WHEN json_extract(event,'$.type')='RUN_STARTED'
                             AND json_type(event,'$.timestamp')='integer' THEN json_extract(event,'$.timestamp') END),
                    MAX(CASE WHEN json_extract(event,'$.type') IN ('RUN_FINISHED','RUN_ERROR')
                             AND json_type(event,'$.timestamp')='integer' THEN json_extract(event,'$.timestamp') END),
                    COUNT(DISTINCT CASE WHEN json_extract(event,'$.type')='TOOL_CALL_START'
                             THEN json_extract(event,'$.toolCallId') END)
                    FROM ui_events WHERE chat_id=? AND run_id=? AND json_valid(event)''', (scope, row['turn_id'])).fetchone()
                started, finished, count = stats
                names = self.db.execute('''SELECT DISTINCT json_extract(event,'$.toolCallName')
                    FROM ui_events WHERE chat_id=? AND run_id=? AND json_valid(event)
                    AND json_extract(event,'$.type')='TOOL_CALL_START' LIMIT 201''', (scope, row['turn_id'])).fetchall()
                labels = sorted({tool_label(name[0]) for name in names[:200]})
                truncated = len(names) > 200
            started = started / 1000 if type(started) is int and 0 <= started <= 8640000000000000 else None
            finished = finished / 1000 if type(finished) is int and 0 <= finished <= 8640000000000000 else None
            if status == 'inProgress':
                finished = None
            duration = finished - started if started is not None and finished is not None and finished >= started else None
            items.append({'id': hashlib.sha256(row['turn_id'].encode()).hexdigest()[:24], 'status': status,
                          'started_at': started, 'finished_at': finished, 'duration_seconds': duration,
                          'tool_labels': labels, 'tool_count': count, 'tool_labels_truncated': truncated,
                          'task_outcome': 'unverified'})
        return {'items': items, 'next_cursor': rows[limit - 1]['rowid'] if len(rows) > limit else None}
