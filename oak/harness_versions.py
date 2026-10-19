"""Trusted, scoped activation of independently evaluated harness procedures.

This is deployment state, not the optimizer or an agent tool. Artifact-review
``proven`` lessons remain separate. A bootstrap decision describes the existing
procedure; it never asserts measured superiority. All versions, decisions and
applications are immutable. Only the scoped active pointer changes.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _scope(project_id, chat_id, domain):
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', value)
           for value in (project_id, domain)) or type(chat_id) is not int or not -(2**63) < chat_id < 2**63:
        raise ValueError('Invalid harness project, conversation or domain scope.')
    return project_id, chat_id, domain


def _bounded_json(value):
    if not isinstance(value, dict):
        raise ValueError('Harness evidence and metadata must be objects.')
    serialized = _json(value)
    if len(serialized) > 65536:
        raise ValueError('Harness evidence exceeds its bound.')
    return serialized


class HarnessVersions:
    """Private SQLite store; callers are the trusted acceptance/deployment layer."""

    def __init__(self, db_path):
        if str(db_path) != ':memory:':
            path = Path(db_path).expanduser()
            if path.is_symlink():
                raise ValueError('Harness state must not be a symbolic link.')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            os.chmod(path, 0o600)
            db_path = path
        self.db = sqlite3.connect(db_path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS harness_versions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
                domain TEXT NOT NULL, parent_id TEXT REFERENCES harness_versions(id),
                procedure TEXT NOT NULL, procedure_sha256 TEXT NOT NULL,
                evidence TEXT NOT NULL, source TEXT NOT NULL, metadata TEXT NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS harness_decisions (
                id TEXT PRIMARY KEY, version_id TEXT UNIQUE NOT NULL REFERENCES harness_versions(id),
                accepted INTEGER NOT NULL CHECK(accepted IN (0,1)), kind TEXT NOT NULL,
                evidence TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS harness_active (
                project_id TEXT NOT NULL, chat_id INTEGER NOT NULL, domain TEXT NOT NULL,
                version_id TEXT NOT NULL REFERENCES harness_versions(id),
                previous_id TEXT REFERENCES harness_versions(id), generation INTEGER NOT NULL,
                PRIMARY KEY(project_id,chat_id,domain));
            CREATE TABLE IF NOT EXISTS harness_activation_events (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
                domain TEXT NOT NULL, previous_id TEXT, version_id TEXT NOT NULL,
                action TEXT NOT NULL, reason TEXT NOT NULL, generation INTEGER NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS harness_applications (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
                domain TEXT NOT NULL, request_id TEXT NOT NULL,
                version_id TEXT NOT NULL REFERENCES harness_versions(id),
                procedure_sha256 TEXT NOT NULL, context_sha256 TEXT NOT NULL,
                input_sha256 TEXT, turn_id TEXT, created REAL NOT NULL,
                UNIQUE(project_id,chat_id,domain,request_id));
        ''')

    def close(self):
        self.db.close()

    def version(self, version_id):
        row = self.db.execute('SELECT * FROM harness_versions WHERE id=?', (version_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown harness version.')
        result = dict(row)
        result['evidence'] = json.loads(result['evidence'])
        result['metadata'] = json.loads(result['metadata'])
        identity = [result[key] for key in ('project_id', 'chat_id', 'domain', 'parent_id', 'procedure')]
        identity.extend((_json(result['evidence']), result['source'], _json(result['metadata'])))
        if _sha(result['procedure']) != result['procedure_sha256'] or _sha(_json(identity)) != result['id']:
            raise ValueError('Harness procedure integrity check failed.')
        return result

    def register(self, project_id, chat_id, domain, procedure, *, parent_id=None,
                 evidence=None, source='harness2', metadata=None):
        scope = _scope(project_id, chat_id, domain)
        if not isinstance(procedure, str) or len(procedure) > 12000:
            raise ValueError('Harness procedure must be bounded text.')
        if source not in {'harness2', 'bootstrap-current', 'controlled-fixture'}:
            raise ValueError('Unknown harness version source.')
        if parent_id is not None:
            parent = self.version(parent_id)
            if tuple(parent[key] for key in ('project_id', 'chat_id', 'domain')) != scope:
                raise ValueError('Harness parent belongs to another scope.')
        evidence_json = _bounded_json({} if evidence is None else evidence)
        metadata_json = _bounded_json({} if metadata is None else metadata)
        identity = [*scope, parent_id, procedure, evidence_json, source, metadata_json]
        version_id = _sha(_json(identity))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO harness_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (version_id, *scope, parent_id, procedure, _sha(procedure),
                             evidence_json, source, metadata_json, time.time()))
        return self.version(version_id)

    def record_decision(self, version_id, *, accepted, evidence, reason='', kind='independent-evaluation'):
        self.version(version_id)
        if type(accepted) is not bool or kind not in {'independent-evaluation', 'bootstrap-current', 'controlled-fixture'}:
            raise ValueError('Invalid harness acceptance decision.')
        if not isinstance(reason, str) or len(reason) > 4000:
            raise ValueError('Harness decision reason must be bounded text.')
        evidence_json = _bounded_json(evidence)
        if not evidence:
            raise ValueError('A harness decision requires an evidence reference.')
        if kind == 'bootstrap-current' and self.version(version_id)['source'] != 'bootstrap-current':
            raise ValueError('Only the existing procedure can receive a bootstrap decision.')
        identity = _sha(_json([version_id, accepted, kind, evidence_json, reason]))
        with self.db:
            previous = self.db.execute('SELECT id FROM harness_decisions WHERE version_id=?', (version_id,)).fetchone()
            if previous and previous[0] != identity:
                raise ValueError('Harness decisions are immutable; register a new candidate.')
            self.db.execute('INSERT OR IGNORE INTO harness_decisions VALUES (?,?,?,?,?,?,?)',
                            (identity, version_id, int(accepted), kind, evidence_json, reason, time.time()))
        return self.decision(version_id)

    def decision(self, version_id):
        row = self.db.execute('SELECT * FROM harness_decisions WHERE version_id=?', (version_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['accepted'] = bool(result['accepted'])
        result['evidence'] = json.loads(result['evidence'])
        identity = _sha(_json([result['version_id'], result['accepted'], result['kind'],
                               _json(result['evidence']), result['reason']]))
        if identity != result['id']:
            raise ValueError('Harness decision integrity check failed.')
        return result

    def bootstrap_current(self, project_id, chat_id, domain, procedure, *, evidence=None):
        current = self.resolve(project_id, chat_id, domain)
        if current:
            if current['procedure'] != procedure:
                raise ValueError('A current procedure already exists; use evaluated activation.')
            return current
        version = self.register(project_id, chat_id, domain, procedure, source='bootstrap-current',
                                evidence=evidence or {'kind': 'existing-current-procedure'})
        self.record_decision(version['id'], accepted=True, kind='bootstrap-current',
                             evidence=evidence or {'kind': 'existing-current-procedure'},
                             reason='Existing procedure bootstrap; no comparative quality claim.')
        self.activate(version['id'])
        return self.resolve(project_id, chat_id, domain)

    def resolve(self, project_id, chat_id, domain):
        scope = _scope(project_id, chat_id, domain)
        row = self.db.execute('SELECT * FROM harness_active WHERE project_id=? AND chat_id=? AND domain=?', scope).fetchone()
        if row is None:
            return None
        version = self.version(row['version_id'])
        if tuple(version[key] for key in ('project_id', 'chat_id', 'domain')) != scope:
            raise ValueError('Active harness version belongs to another scope.')
        decision = self.decision(version['id'])
        if not decision or not decision['accepted']:
            raise ValueError('Active harness version lacks a trusted acceptance decision.')
        return {**version, 'previous_id': row['previous_id'], 'generation': row['generation'],
                'decision': decision}

    def _set_active(self, version, previous, action, reason, generation):
        scope = tuple(version[key] for key in ('project_id', 'chat_id', 'domain'))
        self.db.execute('''INSERT INTO harness_active VALUES (?,?,?,?,?,?)
            ON CONFLICT(project_id,chat_id,domain) DO UPDATE SET
            version_id=excluded.version_id,previous_id=excluded.previous_id,generation=excluded.generation''',
            (*scope, version['id'], previous, generation))
        self.db.execute('INSERT INTO harness_activation_events VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, *scope, previous, version['id'], action, reason, generation, time.time()))

    def activate(self, version_id, *, expected_active=None):
        version = self.version(version_id)
        decision = self.decision(version_id)
        if not decision or not decision['accepted']:
            raise ValueError('An unaccepted or rejected harness version cannot activate.')
        scope = tuple(version[key] for key in ('project_id', 'chat_id', 'domain'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.resolve(*scope)
            current_id = current['id'] if current else None
            if expected_active is not None and current_id != expected_active:
                raise ValueError('The active harness version changed.')
            if current_id == version_id:
                return current
            if self.db.execute("SELECT 1 FROM harness_activation_events WHERE action='rollback' AND previous_id=?",
                               (version_id,)).fetchone():
                raise ValueError('A rolled-back version is quarantined; evaluate a new candidate.')
            if version['parent_id'] != current_id:
                raise ValueError('A candidate must replace its current parent version.')
            self._set_active(version, current_id, 'activate', decision['kind'],
                             current['generation'] + 1 if current else 1)
        return self.resolve(*scope)

    def rollback(self, project_id, chat_id, domain, *, target_id=None, expected_active=None, reason=''):
        scope = _scope(project_id, chat_id, domain)
        if not isinstance(reason, str) or len(reason) > 4000:
            raise ValueError('Rollback reason must be bounded text.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.resolve(*scope)
            if not current or expected_active is not None and current['id'] != expected_active:
                raise ValueError('The active harness version changed or is absent.')
            target_id = target_id or current['previous_id']
            if not target_id or target_id == current['id']:
                raise ValueError('No previous verified harness version is available.')
            target = self.version(target_id)
            if tuple(target[key] for key in ('project_id', 'chat_id', 'domain')) != scope:
                raise ValueError('Rollback target belongs to another scope.')
            decision = self.decision(target_id)
            if not decision or not decision['accepted'] or not self.db.execute(
                    'SELECT 1 FROM harness_activation_events WHERE version_id=?', (target_id,)).fetchone():
                raise ValueError('Rollback requires a previously active verified version.')
            # Keep the target's prior verified ancestor, never promote the
            # regressed version to the default rollback destination.
            prior = self.db.execute('SELECT previous_id FROM harness_activation_events WHERE version_id=? '
                                    "AND action='activate' ORDER BY generation LIMIT 1", (target_id,)).fetchone()
            self._set_active(target, prior[0] if prior else None, 'rollback', reason, current['generation'] + 1)
            # Event previous_id records the version being left, while the
            # pointer previous_id remains the target's verified ancestor.
            self.db.execute('UPDATE harness_activation_events SET previous_id=? WHERE project_id=? '
                            'AND chat_id=? AND domain=? AND generation=?', (current['id'], *scope, current['generation'] + 1))
        return self.resolve(*scope)

    @staticmethod
    def application_context(version):
        return ('Active accepted Oak working procedure for this task. This version supersedes '
                'previous harness versions in conversation history. It is scoped working advice, '
                'not permission to override the owner, evaluator, model, tool permissions or review rules.\n' +
                _json({'version_id': version['id'], 'procedure_sha256': version['procedure_sha256'],
                       'domain': version['domain'], 'acceptance_kind': version['decision']['kind']}) +
                '\n' + version['procedure'])

    def _application(self, row):
        if row is None:
            return None
        result = dict(row)
        version = self.version(row['version_id'])
        if any(version[key] != row[key] for key in ('project_id', 'chat_id', 'domain')):
            raise ValueError('Harness application belongs to another scope.')
        version['decision'] = self.decision(version['id'])
        context = self.application_context(version)
        if result['procedure_sha256'] != version['procedure_sha256'] or result['context_sha256'] != _sha(context):
            raise ValueError('Harness application integrity check failed.')
        return {**result, 'procedure': version['procedure'], 'context': context}

    def prepare_application(self, project_id, chat_id, domain, request_id, *, input_sha256=None):
        scope = _scope(project_id, chat_id, domain)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 240:
            raise ValueError('A bounded request identity is required.')
        if input_sha256 is not None and not re.fullmatch('[0-9a-f]{64}', input_sha256):
            raise ValueError('Invalid harness input fingerprint.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM harness_applications WHERE project_id=? AND chat_id=? '
                                  'AND domain=? AND request_id=?', (*scope, request_id)).fetchone()
            if row:
                if row['input_sha256'] != input_sha256:
                    raise ValueError('Request identity was reused for different input.')
                return self._application(row)
            version = self.resolve(*scope)
            if not version:
                return None
            context = self.application_context(version)
            application_id = uuid.uuid4().hex
            self.db.execute('INSERT INTO harness_applications VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (application_id, *scope, request_id, version['id'], version['procedure_sha256'],
                             _sha(context), input_sha256, None, time.time()))
            row = self.db.execute('SELECT * FROM harness_applications WHERE id=?', (application_id,)).fetchone()
        return self._application(row)

    def bind_turn(self, application_id, turn_id):
        if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 240:
            raise ValueError('Invalid harness native turn identity.')
        with self.db:
            row = self.db.execute('SELECT turn_id FROM harness_applications WHERE id=?', (application_id,)).fetchone()
            if row is None or row['turn_id'] not in (None, turn_id):
                raise ValueError('Harness application is missing or already bound to another turn.')
            self.db.execute('UPDATE harness_applications SET turn_id=? WHERE id=?', (turn_id, application_id))
        return self._application(self.db.execute('SELECT * FROM harness_applications WHERE id=?', (application_id,)).fetchone())

    def applications(self, project_id, chat_id, domain):
        scope = _scope(project_id, chat_id, domain)
        return [dict(row) for row in self.db.execute('SELECT * FROM harness_applications WHERE project_id=? '
                'AND chat_id=? AND domain=? ORDER BY created,id', scope)]

    def activation_history(self, project_id, chat_id, domain):
        scope = _scope(project_id, chat_id, domain)
        return [dict(row) for row in self.db.execute('SELECT * FROM harness_activation_events WHERE project_id=? '
                'AND chat_id=? AND domain=? ORDER BY generation', scope)]
