"""Owner review records are separate from uncertain execution and delivery state."""

import json
import time
import uuid

from .owner_views import page_bounds, preview, require_scope


SOURCES = {
    'input': ('inputs', 'update_id', "status='uncertain'", 'Запит до Oak'),
    'job': ('jobs', 'id', "status='uncertain'", 'Запланована задача'),
    'turn': ('turns', 'turn_id', "status='uncertain'", 'Виконання задачі'),
    'native_request': ('input_requests', 'id', "delivery='uncertain'", 'Передавання відповіді на запит'),
    'telegram_intake': ('intake', 'update_id', "status='uncertain'", 'Отримання повідомлення Telegram'),
    'telegram_delivery': ('deliveries', 'id', "status='uncertain'", 'Доставлення повідомлення або файлу'),
    'telegram_reply': ('replies', 'run_id', "status='uncertain'", 'Доставлення відповіді Oak'),
}


class RecoveryStore:
    def __init__(self, controller):
        self.controller, self.db = controller, controller.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS owner_recovery (
            id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, kind TEXT NOT NULL,
            source_id TEXT NOT NULL, observed REAL NOT NULL, reviewed REAL,
            UNIQUE(chat_id,kind,source_id))''')
        self.db.commit()

    def _database(self, kind):
        if kind.startswith('telegram_'):
            return getattr(getattr(self.controller, 'telegram', None), 'db', None)
        return self.db

    def _refresh(self, scope):
        # Persist opaque references only. Original private payloads stay in their
        # existing stores; reviewing a reference never resolves its execution.
        with self.db:
            for kind, (table, key, condition, _) in SOURCES.items():
                database = self._database(kind)
                if database is None:
                    continue
                rows = database.execute(f'SELECT {key} FROM {table} WHERE chat_id=? AND {condition}', (scope,))
                for row in rows:
                    self.db.execute('INSERT OR IGNORE INTO owner_recovery VALUES (?,?,?,?,?,NULL)',
                                    (uuid.uuid4().hex, scope, kind, str(row[0]), time.time()))

    def _source(self, reference):
        kind = reference['kind']
        table, key, condition, _ = SOURCES[kind]
        database = self._database(kind)
        if database is None:
            return None
        columns = 'text,attachments' if kind == 'input' else 'prompt,mode' if kind == 'job' else key
        return database.execute(f'SELECT {columns} FROM {table} WHERE {key}=? AND chat_id=? AND {condition}',
                                (reference['source_id'], reference['chat_id'])).fetchone()

    def _item(self, reference, source):
        kind = reference['kind']
        prompt, retryable = None, False
        if kind == 'input':
            prompt = source['text']
            try:
                retryable = json.loads(source['attachments']) == []
            except (TypeError, ValueError):
                pass
        elif kind == 'job':
            prompt, retryable = source['prompt'], source['mode'] == 'run'
        return {'id': reference['id'], 'kind': kind, 'label': SOURCES[kind][3],
                'preview': preview(prompt) if prompt else None, 'state': 'uncertain',
                'observed_at': reference['observed'], 'reviewed': reference['reviewed'] is not None,
                'reviewed_at': reference['reviewed'], 'can_retry': bool(retryable and prompt and len(prompt) <= 32000)}

    def _lookup(self, owner, scope, identifier):
        require_scope(self.controller, owner, scope)
        if not isinstance(identifier, str) or len(identifier) != 32:
            raise LookupError('Запис не знайдено.')
        reference = self.db.execute('SELECT * FROM owner_recovery WHERE id=? AND chat_id=?', (identifier, scope)).fetchone()
        source = self._source(reference) if reference else None
        if source is None:
            raise LookupError('Запис не знайдено або його стан уже змінився.')
        return reference, source

    def inventory(self, owner, scope, *, before=None, limit=30):
        require_scope(self.controller, owner, scope)
        page_bounds(before, limit)
        self._refresh(scope)
        items, last = [], before
        while len(items) <= limit:
            rows = self.db.execute('SELECT rowid,* FROM owner_recovery WHERE chat_id=? '
                                   + ('AND rowid<? ' if last is not None else '') + 'ORDER BY rowid DESC LIMIT 51',
                                   (scope, last) if last is not None else (scope,)).fetchall()
            if not rows:
                break
            for reference in rows:
                last = reference['rowid']
                source = self._source(reference)
                if source is not None:
                    items.append((last, self._item(reference, source)))
                    if len(items) > limit:
                        break
        return {'items': [item for _, item in items[:limit]],
                'next_cursor': items[limit - 1][0] if len(items) > limit else None}

    def review(self, owner, scope, identifier):
        reference, source = self._lookup(owner, scope, identifier)
        with self.db:
            self.db.execute('UPDATE owner_recovery SET reviewed=COALESCE(reviewed,?) WHERE id=?',
                            (time.time(), identifier))
        reference = self.db.execute('SELECT * FROM owner_recovery WHERE id=?', (identifier,)).fetchone()
        return self._item(reference, source)
