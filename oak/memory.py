"""Private, conversation-scoped preferences and reusable working experience."""

import hashlib
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
            CREATE TABLE IF NOT EXISTS web_memory_operations (
                chat_id INTEGER NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(chat_id,operation_id));
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
                id UNINDEXED, chat_id UNINDEXED, text, tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS experience_notes (
                id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, key TEXT NOT NULL,
                title TEXT NOT NULL, when_to_use TEXT NOT NULL, procedure TEXT NOT NULL,
                limitations TEXT NOT NULL, status TEXT NOT NULL,
                evidence TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                revision INTEGER NOT NULL, UNIQUE(chat_id,key));
            CREATE VIRTUAL TABLE IF NOT EXISTS experience_search USING fts5(
                id UNINDEXED, chat_id UNINDEXED, title, when_to_use, procedure,
                limitations, tokenize='unicode61');
        ''')

    def save_experience(self, chat_id, key, title, when_to_use, procedure, *,
                        limitations='', evidence=None, verified=False):
        """Upsert a scoped lesson; only a trusted review caller may verify it.

        ``verified`` is deliberately separate from model-supplied lesson text.
        The caller must check the review receipt and artifact before passing
        True. ``proven`` means this revision has passing artifact evidence;
        it does not certify every claim in the narrated procedure or guarantee
        a future result. Changed instructions without a new verified receipt
        lose that status, even if an earlier revision worked.
        """
        limits = {'key': 160, 'title': 240, 'when_to_use': 2000,
                  'procedure': 16000, 'limitations': 4000}
        values = {'key': key, 'title': title, 'when_to_use': when_to_use,
                  'procedure': procedure, 'limitations': limitations}
        for name, value in values.items():
            if not isinstance(value, str):
                raise ValueError('Experience fields must be text.')
            values[name] = value.strip()
            if len(values[name]) > limits[name] or (name != 'limitations' and not values[name]):
                raise ValueError('Experience field is empty or exceeds its size limit: ' + name)
        values['key'] = ' '.join(values['key'].casefold().split())
        if type(verified) is not bool:
            raise ValueError('Experience verification must be boolean.')
        if evidence is None:
            evidence = []
        # Keep local receipts as references, never an unbounded copy of model
        # transcripts or media. The review layer validates their provenance.
        allowed = {'review_id', 'artifact_sha256', 'path', 'outcome', 'note', 'kind', 'reference'}
        if (not isinstance(evidence, list) or len(evidence) > 12
                or any(not isinstance(item, dict) or not item or set(item) - allowed
                       or any(not isinstance(value, str) or not value.strip() or len(value) > 2000
                              for value in item.values()) for item in evidence)):
            raise ValueError('Experience evidence must contain bounded review references.')
        if verified and not evidence:
            raise ValueError('Proven experience requires a verified evidence reference.')
        serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(serialized) > 12000:
            raise ValueError('Experience evidence exceeds its size limit.')
        status = 'proven' if verified else 'unverified'
        now = time.time()
        with self.db:
            previous = self.db.execute(
                'SELECT id,revision FROM experience_notes WHERE chat_id=? AND key=?',
                (chat_id, values['key'])).fetchone()
            note_id, revision = (previous[0], previous[1] + 1) if previous else (uuid.uuid4().hex[:12], 1)
            if previous:
                self.db.execute('''UPDATE experience_notes SET title=?,when_to_use=?,procedure=?,
                    limitations=?,status=?,evidence=?,updated=?,revision=? WHERE id=? AND chat_id=?''',
                    (values['title'], values['when_to_use'], values['procedure'], values['limitations'],
                     status, serialized, now, revision, note_id, chat_id))
                self.db.execute('DELETE FROM experience_search WHERE id=? AND chat_id=?', (note_id, chat_id))
            else:
                self.db.execute('INSERT INTO experience_notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (note_id, chat_id, values['key'], values['title'], values['when_to_use'],
                     values['procedure'], values['limitations'], status, serialized, now, now, revision))
            self.db.execute('INSERT INTO experience_search VALUES (?,?,?,?,?,?)',
                (note_id, chat_id, values['title'], values['when_to_use'], values['procedure'], values['limitations']))
        return {'id': note_id, 'key': values['key'], 'status': status, 'revision': revision}

    def experience_search(self, chat_id, query='', limit=5):
        """Retrieve procedures only from the selected conversation, after restart too."""
        if not isinstance(query, str) or len(query) > 8000:
            raise ValueError('Experience query must be text of at most 8000 characters.')
        limit = max(1, min(int(limit), 20))
        columns = ('id', 'key', 'title', 'when_to_use', 'procedure', 'limitations',
                   'status', 'evidence', 'created', 'updated', 'revision')
        selection = ','.join('n.' + column for column in columns)
        words = re.findall(r'[^\W_]+', query, re.UNICODE)[:80]
        if words:
            rows = self.db.execute('SELECT ' + selection + ''' FROM experience_search s
                JOIN experience_notes n ON n.id=s.id WHERE experience_search MATCH ?
                AND n.chat_id=? ORDER BY bm25(experience_search,0,0,4,3,1,1),n.updated DESC LIMIT ?''',
                (' OR '.join('"' + word + '"' for word in words), chat_id, limit))
        else:
            rows = self.db.execute('SELECT ' + selection + ''' FROM experience_notes n
                WHERE n.chat_id=? ORDER BY n.updated DESC LIMIT ?''', (chat_id, limit))
        result = []
        for row in rows:
            note = dict(zip(columns, row))
            note['evidence'] = json.loads(note['evidence'])
            result.append(note)
        return result

    def forget_experience(self, chat_id, note_id):
        with self.db:
            found = self.db.execute('DELETE FROM experience_notes WHERE id=? AND chat_id=?',
                                    (note_id, chat_id)).rowcount
            if found:
                self.db.execute('DELETE FROM experience_search WHERE id=? AND chat_id=?', (note_id, chat_id))
        return bool(found)

    def remember(self, chat_id, text, source='owner'):
        with self.db:
            return self._remember(chat_id, text, source)

    def _remember(self, chat_id, text, source):
        text = text.strip()
        if not text or len(text) > 32000:
            raise ValueError('Нотатка має містити від 1 до 32000 символів.')
        note_id = uuid.uuid4().hex[:12]
        self.db.execute('INSERT INTO memory_notes VALUES (?,?,?,?,?)',
                        (note_id, chat_id, text, source, time.time()))
        self.db.execute('INSERT INTO memory_search VALUES (?,?,?)', (note_id, chat_id, text))
        return note_id

    def search(self, chat_id, query='', limit=10, offset=0):
        limit = max(1, min(int(limit), 50))
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Invalid memory offset.')
        words = re.findall(r'[^\W_]+', query, re.UNICODE)
        if words:
            rows = self.db.execute('''SELECT n.id,n.text,n.source FROM memory_search s
                JOIN memory_notes n ON n.id=s.id WHERE memory_search MATCH ?
                AND n.chat_id=? ORDER BY rank,n.created DESC,n.id LIMIT ? OFFSET ?''',
                (' OR '.join('"' + w + '"' for w in words), chat_id, limit, offset))
        else:
            rows = self.db.execute('SELECT id,text,source FROM memory_notes WHERE chat_id=? '
                                   'ORDER BY created DESC,id LIMIT ? OFFSET ?', (chat_id, limit, offset))
        return [dict(zip(('id', 'text', 'source'), row)) for row in rows]

    def forget(self, chat_id, note_id):
        with self.db:
            return self._forget(chat_id, note_id)

    def _forget(self, chat_id, note_id):
        found = self.db.execute('DELETE FROM memory_notes WHERE id=? AND chat_id=?',
                                (note_id, chat_id)).rowcount
        if found:
            self.db.execute('DELETE FROM memory_search WHERE id=? AND chat_id=?', (note_id, chat_id))
        return bool(found)

    def change(self, chat_id, operation_id, action, value):
        """Commit a panel mutation and its retry receipt in the same transaction."""
        if not isinstance(operation_id, str) or not re.fullmatch(r'[a-f0-9]{32}', operation_id):
            raise ValueError('Invalid operation identity.')
        if action not in {'add', 'delete'} or not isinstance(value, str):
            raise ValueError('Invalid memory operation.')
        fingerprint = hashlib.sha256(json.dumps([action, value], ensure_ascii=False).encode()).hexdigest()
        with self.db:
            row = self.db.execute('SELECT fingerprint,result FROM web_memory_operations WHERE chat_id=? AND operation_id=?',
                                  (chat_id, operation_id)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError('Operation identity already used.')
                return json.loads(row[1])
            if action == 'add':
                result = {'id': self._remember(chat_id, value, 'owner'), 'created': True}
            else:
                if not self._forget(chat_id, value):
                    raise LookupError('Memory note not found.')
                result = {'id': value, 'deleted': True}
            self.db.execute('INSERT INTO web_memory_operations VALUES (?,?,?,?)',
                            (chat_id, operation_id, fingerprint, json.dumps(result)))
            return result

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
