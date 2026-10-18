"""Conversation-scoped visual review of the exact bytes offered for delivery."""

import asyncio
import hashlib
import json
import re
import time
import uuid
from pathlib import Path


VISUAL_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.mp4', '.mov', '.webm', '.mkv', '.avi'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def has_bound_bytes(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_source_hashes'").fetchone()
                and db.execute('SELECT 1 FROM quality_source_hashes LIMIT 1').fetchone())


def required_source(db, chat_id, path, sha256=None):
    """A generated candidate keeps its review requirement across turns/config changes."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_sources'").fetchone():
        return None
    rows = [db.execute('SELECT chat_id,source,source_sha256,criteria FROM quality_sources WHERE path=?',
                       (str(path),)).fetchone()]
    if sha256 is not None and has_bound_bytes(db):
        owned = db.execute('SELECT chat_id,source,source_sha256,criteria FROM quality_source_hashes WHERE sha256=? AND chat_id=?',
                           (sha256, chat_id)).fetchone()
        if owned is None and db.execute('SELECT 1 FROM quality_source_hashes WHERE sha256=?', (sha256,)).fetchone():
            raise ValueError('This review candidate belongs to another conversation.')
        rows.append(owned)
    rows = [tuple(row) for row in rows if row is not None]
    if not rows:
        return None
    if any(row[0] != chat_id for row in rows):
        raise ValueError('This review candidate belongs to another conversation.')
    if len({(row[0], row[2], row[3]) for row in rows}) != 1:
        raise ValueError('Artifact path and bytes have conflicting required sources.')
    row = rows[0]
    return {'path': row[1], 'sha256': row[2], 'criteria': row[3]}


class QualityGate:
    def __init__(self, tools, config):
        from .quality import QualityReviewer
        self.tools = tools
        self.controller = tools.controller
        self.db = self.controller.db
        self.reviewer = QualityReviewer(config)
        self.lock = asyncio.Lock()
        self.specifications = {}
        self.db.execute('''CREATE TABLE IF NOT EXISTS quality_reviews (
            id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, turn_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, sha256 TEXT NOT NULL, snapshot TEXT NOT NULL,
            verdict TEXT NOT NULL, report TEXT NOT NULL, created REAL NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS quality_sources (
            path TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, source TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, criteria TEXT NOT NULL)''')
        schema = '''CREATE TABLE IF NOT EXISTS quality_source_hashes (
            sha256 TEXT NOT NULL, chat_id INTEGER NOT NULL, source TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, criteria TEXT NOT NULL, PRIMARY KEY(sha256,chat_id))'''
        primary = [row[1] for row in self.db.execute('PRAGMA table_info(quality_source_hashes)') if row[5]]
        if primary == ['sha256']:
            # Atomic additive migration; preserve every existing owner/source receipt.
            with self.db:
                self.db.execute('SAVEPOINT quality_hash_scope')
                self.db.execute('ALTER TABLE quality_source_hashes RENAME TO quality_source_hashes_legacy')
                self.db.execute(schema)
                self.db.execute('INSERT INTO quality_source_hashes SELECT * FROM quality_source_hashes_legacy')
                self.db.execute('DROP TABLE quality_source_hashes_legacy')
                self.db.execute('RELEASE quality_hash_scope')
        else:
            self.db.execute(schema)
        self.db.commit()

    def require_source(self, chat_id, path, source, source_sha256, criteria, *, artifact_sha256):
        """Bind trusted generation evidence once; producer review args cannot replace it."""
        path, source = str(self.tools.file(path)), str(self.tools.file(source))
        if (path == source or not all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                                     for value in (source_sha256, artifact_sha256))
                or not isinstance(criteria, str) or not 1 <= len(criteria) <= 2000):
            raise ValueError('Invalid required artifact source.')
        existing = required_source(self.db, chat_id, path)
        desired = {'path': source, 'sha256': source_sha256, 'criteria': criteria}
        if existing is not None:
            if existing != desired:
                raise ValueError('Required artifact source and criteria cannot be replaced.')
        existing_hash = self.db.execute('SELECT source_sha256,criteria FROM quality_source_hashes WHERE sha256=? AND chat_id=?',
                                        (artifact_sha256, chat_id)).fetchone()
        if existing_hash and tuple(existing_hash) != (source_sha256, criteria):
            raise ValueError('Required artifact source and criteria cannot be replaced.')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO quality_sources VALUES (?,?,?,?,?)',
                            (path, chat_id, source, source_sha256, criteria))
            # Only trusted generation can register this scope. Review/delivery only reads.
            self.db.execute('''INSERT INTO quality_source_hashes VALUES (?,?,?,?,?)
                ON CONFLICT(sha256,chat_id) DO UPDATE SET source=excluded.source''',
                            (artifact_sha256, chat_id, source, source_sha256, criteria))

    def receipt(self, chat_id, review_id):
        row = self.db.execute('SELECT id,sha256,verdict FROM quality_reviews WHERE id=? AND chat_id=?',
                              (review_id, chat_id)).fetchone()
        if row is None:
            raise ValueError('Unknown quality review in this conversation.')
        return {'review_id': row[0], 'artifact_sha256': row[1], 'outcome': row[2]}

    async def inspect(self, path, snapshot=None):
        if self.tools.files is None or self.tools.executor is None:
            raise ValueError('Independent visual review requires the configured execution worker.')
        if snapshot is None:
            snapshot = await self.tools.files.snapshot(self.tools.file(path), limit=256 * 1024 * 1024)
        elif not self.tools.files.stored(snapshot) or Path(snapshot).stat().st_size > 256 * 1024 * 1024:
            raise ValueError('Invalid quality snapshot.')
        sha = await asyncio.to_thread(digest, snapshot)
        # The inspector copies a held descriptor and checks this digest before decoding.
        evidence = await self.tools.executor.call('quality.inspect', {'path': str(self.tools.file(path)), 'sha256': sha})
        if evidence.get('sha256') != sha:
            raise ValueError('Artifact changed during quality review; inspect the current file again.')
        return snapshot, evidence

    async def review(self, chat_id, path, *, requirements=None, reference_paths=None, snapshot=None):
        c = self.controller
        turn = c.active.get(chat_id)
        key = (chat_id, turn, str(self.tools.file(path)))
        saved = self.specifications.get(key, ('', ()))
        requirements = saved[0] if requirements is None else requirements
        reference_paths = saved[1] if reference_paths is None else reference_paths
        if not isinstance(requirements, str) or len(requirements) > 8000:
            raise ValueError('Review requirements must be a short description of the requested result.')
        if not isinstance(reference_paths, (list, tuple)) or len(reference_paths) > 3:
            raise ValueError('Use at most three actual source/reference files.')
        required_source(self.db, chat_id, key[2])
        if not turn:
            raise ValueError('Review requires an active task.')
        self.specifications = {k: v for k, v in self.specifications.items() if c.active.get(k[0]) == k[1]}
        self.specifications[key] = (requirements, reference_paths)
        # These owner instructions come from dispatch, not from the producer's claimed verdict.
        async with self.lock:
            if c.active.get(chat_id) != turn or c.sessions.deleted(chat_id):
                raise ValueError('Task stopped during review; nothing was delivered.')
            owner_brief = getattr(c, 'task_briefs', {}).get(chat_id, '')[-16000:]
            snapshot, inspected = await self.inspect(path, snapshot)
            sha = inspected['sha256']
            required = required_source(self.db, chat_id, key[2], sha)
            if required:
                reference_paths = list(dict.fromkeys([required['path'], *(str(self.tools.file(p)) for p in reference_paths)]))
                if len(reference_paths) > 3:
                    raise ValueError('Use at most three references including the required original source.')
            brief = ('Owner task (authoritative):\n' + owner_brief +
                     ('\nRequired artifact checks (fixed at generation):\n' + required['criteria'] if required else '') +
                     '\nProducer description of requirements (check against owner task):\n' + requirements)
            references = []
            evidence = [{'label': 'OUTPUT: ' + item['label'], 'image_url': item['image_url']}
                        for item in inspected.pop('evidence', [])]
            output_valid = inspected.get('decode_status') == 'complete' and bool(evidence)
            references_valid = True
            for reference in reference_paths:
                _, data = await self.inspect(reference)
                if required and str(reference) == required['path'] and data.get('sha256') != required['sha256']:
                    raise ValueError('Required original source changed; restore it or create a new candidate.')
                references.append({key: value for key, value in data.items() if key != 'evidence'})
                references_valid = references_valid and data.get('decode_status') == 'complete' and bool(data.get('evidence'))
                # A reference is context, never a replacement for output evidence.
                evidence.extend({'label': 'REFERENCE: ' + item['label'], 'image_url': item['image_url']}
                                for item in data.get('evidence', [])[:2])
            if c.active.get(chat_id) != turn or c.sessions.deleted(chat_id):
                raise ValueError('Task stopped during review; nothing was delivered.')
            if getattr(c, 'task_briefs', {}).get(chat_id, '')[-16000:] != owner_brief:
                raise ValueError('Task requirements changed during review; review against the current request.')
            fingerprint = hashlib.sha256(json.dumps([brief, sha, references], sort_keys=True).encode()).hexdigest()
            previous = self.db.execute('SELECT report,snapshot FROM quality_reviews '
                'WHERE chat_id=? AND turn_id=? AND fingerprint=? ORDER BY created DESC LIMIT 1',
                (chat_id, turn, fingerprint)).fetchone()
            if previous and json.loads(previous[0]).get('verdict') != 'unverified':
                return json.loads(previous[0]), Path(previous[1])
            attempts = self.db.execute('SELECT count(*) FROM quality_reviews WHERE chat_id=? AND turn_id=?',
                                       (chat_id, turn)).fetchone()[0]
            if attempts >= 4:
                return {'ok': False, 'verdict': 'unverified', 'error_code': 'review_budget_exhausted',
                        'summary': 'Four independent reviews were used. Stop and explain remaining limitations; do not send an unverified final.'}, snapshot
            if not output_valid or not references_valid:
                report = {'verdict': 'unverified', 'summary': inspected.get('coverage', 'This file has no supported visual evidence.'),
                          'error_code': 'media_' + inspected.get('decode_status', 'unsupported') if not output_valid else 'reference_decode_failed',
                          'issues': [], 'limitations': ['Visual review supports images and bounded videos.']}
            else:
                report = await self.reviewer.review(brief, evidence, {'output': inspected, 'references': references},
                    model=c.model_for(chat_id), effort=c.effort_for(chat_id) or 'high')
            if c.active.get(chat_id) != turn or c.sessions.deleted(chat_id):
                raise ValueError('Task stopped during review; nothing was delivered.')
            if getattr(c, 'task_briefs', {}).get(chat_id, '')[-16000:] != owner_brief:
                raise ValueError('Task requirements changed during review; review against the current request.')
            review_id = uuid.uuid4().hex
            report = {**report, 'review_id': review_id, 'sha256': sha,
                      'ok': report.get('verdict') == 'pass', 'queued_for_delivery': False}
            if report.get('verdict') == 'revise':
                report['next_action'] = 'Fix the observed defects in the artifact, then call oak_review_artifact again. Do not ask the owner to do the repair.'
            elif report.get('verdict') != 'pass':
                report['next_action'] = 'Review is incomplete. Resolve the stated blocker or report it honestly; this is not a passed final artifact.'
            with self.db:
                self.db.execute('INSERT INTO quality_reviews VALUES (?,?,?,?,?,?,?,?,?)',
                    (review_id, chat_id, turn, fingerprint, sha, str(snapshot), report.get('verdict', 'unverified'),
                     json.dumps(report, ensure_ascii=False), time.time()))
            return report, snapshot
