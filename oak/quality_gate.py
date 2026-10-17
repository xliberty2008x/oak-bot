"""Conversation-scoped visual review of the exact bytes offered for delivery."""

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path


VISUAL_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.mp4', '.mov', '.webm', '.mkv', '.avi'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
        self.db.commit()

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
        if not turn:
            raise ValueError('Review requires an active task.')
        self.specifications = {k: v for k, v in self.specifications.items() if c.active.get(k[0]) == k[1]}
        self.specifications[key] = (requirements, reference_paths)
        # These owner instructions come from dispatch, not from the producer's claimed verdict.
        async with self.lock:
            if c.active.get(chat_id) != turn or c.sessions.deleted(chat_id):
                raise ValueError('Task stopped during review; nothing was delivered.')
            owner_brief = getattr(c, 'task_briefs', {}).get(chat_id, '')[-16000:]
            brief = ('Owner task (authoritative):\n' + owner_brief +
                     '\nProducer description of requirements (check against owner task):\n' + requirements)
            snapshot, inspected = await self.inspect(path, snapshot)
            sha = inspected['sha256']
            references = []
            evidence = [{'label': 'OUTPUT: ' + item['label'], 'image_url': item['image_url']}
                        for item in inspected.pop('evidence', [])]
            output_valid = inspected.get('decode_status') == 'complete' and bool(evidence)
            references_valid = True
            for reference in reference_paths:
                _, data = await self.inspect(reference)
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
