"""Persistent Telegram topic identities, separate from native delivery addresses."""

import re
import secrets


class SessionStore:
    def __init__(self, db):
        self.db = db
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS telegram_topics (
                scope_id INTEGER PRIMARY KEY, owner INTEGER NOT NULL,
                topic_id INTEGER NOT NULL, name TEXT NOT NULL,
                closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
                UNIQUE(owner,topic_id))''')
            if 'deleted' not in {r[1] for r in db.execute('PRAGMA table_info(telegram_topics)')}:
                db.execute('ALTER TABLE telegram_topics ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')

    def resolve(self, owner, topic_id=None, *, name=None, closed=None):
        if type(owner) is not int or not 0 < owner < 2**63:
            raise ValueError('Invalid Telegram owner.')
        if topic_id is not None and (type(topic_id) is not int or not 0 < topic_id < 2**63):
            raise ValueError('Invalid Telegram topic.')
        if name is not None and (not isinstance(name, str) or not name.strip()
                                 or len(name) > 128 or any(ord(c) < 32 for c in name)):
            raise ValueError('Invalid topic name.')
        if closed is not None and type(closed) is not bool:
            raise ValueError('Invalid topic status.')
        if topic_id in (None, 1):
            return owner
        row = self.db.execute('SELECT scope_id FROM telegram_topics WHERE owner=? AND topic_id=?',
                              (owner, topic_id)).fetchone()
        with self.db:
            if row:
                scope = row[0]
                if name is not None:
                    self.db.execute('UPDATE telegram_topics SET name=? WHERE scope_id=?', (name.strip(), scope))
                if closed is not None:
                    self.db.execute('UPDATE telegram_topics SET closed=? WHERE scope_id=?', (int(closed), scope))
                return scope
            scope = -secrets.randbelow(2**63 - 1) - 1
            while self._occupied(scope):
                scope = -secrets.randbelow(2**63 - 1) - 1
            self.db.execute('INSERT INTO telegram_topics(scope_id,owner,topic_id,name,closed) VALUES (?,?,?,?,?)',
                            (scope, owner, topic_id, name.strip() if name is not None else 'Тема ' + str(topic_id),
                             int(closed) if closed is not None else 0))
            return scope

    def _occupied(self, scope):
        for table, column in (('telegram_topics', 'scope_id'), ('chats', 'chat_id'), ('web_conversations', 'chat_id')):
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                if self.db.execute('SELECT 1 FROM ' + table + ' WHERE ' + column + '=?', (scope,)).fetchone():
                    return True
        return False

    def destination(self, scope):
        if type(scope) is not int:
            return None
        if scope > 0:
            return {'chat_id': scope}
        row = self.db.execute('SELECT owner,topic_id FROM telegram_topics WHERE scope_id=? AND deleted=0', (scope,)).fetchone()
        return {'chat_id': row[0], 'message_thread_id': row[1]} if row else None

    def deleted(self, scope):
        return self.db.execute('SELECT 1 FROM telegram_topics WHERE scope_id=? AND deleted=1', (scope,)).fetchone() is not None

    def delete(self, scope):
        # Keep the identity so replaying old Telegram updates cannot resurrect it.
        with self.db:
            self.db.execute('UPDATE telegram_topics SET deleted=1 WHERE scope_id=?', (scope,))

    def owner(self, scope):
        # Ownership survives deletion for cleanup of owner-wide permissions.
        if type(scope) is not int:
            return None
        if scope > 0:
            return scope
        row = self.db.execute('SELECT owner FROM telegram_topics WHERE scope_id=?', (scope,)).fetchone()
        return row[0] if row else None

    def lookup(self, owner, identifier):
        if type(owner) is not int or owner <= 0:
            return None
        if identifier == 'telegram':
            return owner
        match = re.fullmatch(r'topic:([0-9]{1,19})', identifier) if isinstance(identifier, str) else None
        if not match or not 1 < int(match[1]) < 2**63:
            return None
        row = self.db.execute('SELECT scope_id FROM telegram_topics WHERE owner=? AND topic_id=? AND deleted=0',
                              (owner, int(match[1]))).fetchone()
        return row[0] if row else None

    def list(self, owner):
        rows = self.db.execute('SELECT scope_id,topic_id,name,closed FROM telegram_topics WHERE owner=? AND deleted=0 ORDER BY topic_id', (owner,))
        return [{'id': 'telegram', 'scope_id': owner, 'topic_id': None, 'name': 'Загальна', 'closed': False},
                *[{'id': 'topic:' + str(row[1]), 'scope_id': row[0], 'topic_id': row[1],
                   'name': row[2], 'closed': bool(row[3])} for row in rows]]
