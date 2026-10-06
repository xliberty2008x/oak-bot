"""Private, chat-scoped long-term notes with local full-text retrieval."""

import json
import re
import time
import uuid
from pathlib import Path


class MemoryStore:
    def __init__(self, db):
        self.db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS memory_notes (
                id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, text TEXT NOT NULL,
                source TEXT NOT NULL, created REAL NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
                id UNINDEXED, chat_id UNINDEXED, text, tokenize='unicode61');
        ''')

    def remember(self, chat_id, text, source='owner'):
        text = text.strip()
        if not text or len(text) > 32000:
            raise ValueError('Нотатка має містити від 1 до 32000 символів.')
        note_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute('INSERT INTO memory_notes VALUES (?,?,?,?,?)',
                            (note_id, chat_id, text, source, time.time()))
            self.db.execute('INSERT INTO memory_search VALUES (?,?,?)', (note_id, chat_id, text))
        return note_id

    def search(self, chat_id, query='', limit=10):
        limit = max(1, min(int(limit), 50))
        words = re.findall(r'[^\W_]+', query, re.UNICODE)
        if words:
            rows = self.db.execute('''SELECT n.id,n.text,n.source FROM memory_search s
                JOIN memory_notes n ON n.id=s.id WHERE memory_search MATCH ?
                AND n.chat_id=? ORDER BY rank LIMIT ?''',
                (' OR '.join('"' + w + '"' for w in words), chat_id, limit))
        else:
            rows = self.db.execute('SELECT id,text,source FROM memory_notes WHERE chat_id=? '
                                   'ORDER BY created DESC LIMIT ?', (chat_id, limit))
        return [dict(zip(('id', 'text', 'source'), row)) for row in rows]

    def forget(self, chat_id, note_id):
        with self.db:
            found = self.db.execute('DELETE FROM memory_notes WHERE id=? AND chat_id=?',
                                    (note_id, chat_id)).rowcount
            if found:
                self.db.execute('DELETE FROM memory_search WHERE id=? AND chat_id=?', (note_id, chat_id))
        return bool(found)

    def import_file(self, chat_id, path):
        source = Path(path).resolve()
        if source.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Memory export exceeds 4 MiB.')
        if source.suffix.lower() == '.json':
            data = json.loads(source.read_text())
            if (not isinstance(data, dict) or set(data) - {'profile', 'logs'}
                    or not isinstance(data.get('profile', ''), str)):
                raise ValueError('Expected a selected memory folder with profile and logs only.')
            logs = data.get('logs', {})
            if not isinstance(logs, dict) or any(not isinstance(v, str) for v in logs.values()):
                raise ValueError('Memory logs must be a mapping of names to text.')
            notes = [data.get('profile', ''), *logs.values()]
        else:
            notes = [source.read_text()]
        # Validate before writing, then atomically import the selected folder.
        if any(len(n) > 32000 for n in notes):
            raise ValueError('Split memory entries larger than 32000 characters before importing.')
        ids = []
        with self.db:
            for text in notes:
                if text.strip():
                    note_id = uuid.uuid4().hex[:12]
                    self.db.execute('INSERT INTO memory_notes VALUES (?,?,?,?,?)',
                                    (note_id, chat_id, text.strip(), 'import:' + source.name, time.time()))
                    self.db.execute('INSERT INTO memory_search VALUES (?,?,?)',
                                    (note_id, chat_id, text.strip()))
                    ids.append(note_id)
        return ids
