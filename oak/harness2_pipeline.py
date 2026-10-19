"""Durable Oak admission around the genuine, pinned Harness² search.

The upstream engine owns proposal, probes, composition and its persistent library.
This module owns scoped cases, scheduling, budgets and independent promotion.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from datetime import datetime, timezone

MODEL = 'gpt-6.1-sol'
UPSTREAM_COMMIT = '1cfe254e8d25b15f37048c3730134686abe60335'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def input_identity(row):
    inputs = row['commitment']['inputs']
    pixels = [value for name, value in inputs.items() if name.endswith('.png') and not name.startswith('broken-')]
    return digest(sorted(pixels or inputs.values()))


class BudgetStopped(RuntimeError):
    pass


class PipelineStore:
    """One private SQLite journal; every query carries the complete owner scope."""

    def __init__(self, root, project_id, chat_id, domain='media-depth'):
        for value in (project_id, domain):
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', str(value)):
                raise ValueError('Use a bounded explicit project and domain identifier.')
        if type(chat_id) is not int:
            raise ValueError('chat_id must be the exact integer conversation scope.')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.scope = (str(project_id), chat_id, str(domain))
        self.lock = threading.RLock()
        self.path = self.root / 'pipeline.sqlite3'
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.path.chmod(0o600)
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (scope TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, signature TEXT NOT NULL,
                approved INTEGER NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                scope TEXT NOT NULL, value TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS queue (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, signature TEXT NOT NULL,
                priority INTEGER NOT NULL, state TEXT NOT NULL, updated TEXT NOT NULL,
                UNIQUE(scope,signature));
            CREATE TABLE IF NOT EXISTS cycles (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, ordinal INTEGER NOT NULL,
                queue_id TEXT NOT NULL REFERENCES queue(id), phase TEXT NOT NULL,
                config TEXT NOT NULL, manifest TEXT NOT NULL, frozen TEXT,
                search TEXT, decision TEXT, created TEXT NOT NULL, updated TEXT NOT NULL,
                UNIQUE(scope,ordinal));
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, cycle_id TEXT NOT NULL REFERENCES cycles(id),
                kind TEXT NOT NULL, identity TEXT NOT NULL, state TEXT NOT NULL,
                started TEXT NOT NULL, ended TEXT, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exposure (
                identity TEXT NOT NULL, scope TEXT NOT NULL, cycle_id TEXT NOT NULL,
                split TEXT NOT NULL, opened TEXT, PRIMARY KEY(scope,identity));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, cycle_id TEXT,
                kind TEXT NOT NULL, value TEXT NOT NULL, created TEXT NOT NULL);
        ''')
        self.db.commit()

    @property
    def key(self):
        return canonical(self.scope)

    def close(self):
        self.db.close()

    def event(self, kind, value, cycle_id=None):
        self.db.execute('INSERT INTO events(scope,cycle_id,kind,value,created) VALUES(?,?,?,?,?)',
                        (self.key, cycle_id, kind, canonical(value), now()))

    def setting(self, name, value=None):
        key = self.key + ':' + name
        with self.lock, self.db:
            if value is not None:
                self.db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET value=excluded.value',
                                (key, canonical(value)))
            row = self.db.execute('SELECT value FROM settings WHERE scope=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def record_case(self, payload, *, signature, approved=False, priority=10):
        """Private references are retained but cannot enter optimization without consent."""
        if not isinstance(payload, dict) or not signature or len(signature) > 160:
            raise ValueError('A case needs structured evidence and a bounded failure signature.')
        if approved and payload.get('visibility') not in {'public_fixture', 'explicit_private'}:
            raise ValueError('Optimizer material requires an explicit source permission.')
        case_id = digest({'scope': self.scope, 'signature': signature, 'payload': payload})
        group = digest({'scope': self.scope, 'signature': signature})
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO cases VALUES(?,?,?,?,?,?)',
                            (case_id, self.key, signature, int(approved), canonical(payload), now()))
            if approved:
                self.db.execute('UPDATE cases SET approved=1 WHERE id=? AND scope=?', (case_id, self.key))
            self.db.execute('INSERT INTO queue VALUES(?,?,?,?,?,?) ON CONFLICT(scope,signature) DO UPDATE SET '
                            'priority=MAX(priority,excluded.priority),updated=excluded.updated,state=CASE WHEN queue.state="completed" THEN "pending" ELSE queue.state END',
                            (group, self.key, signature, priority, 'pending', now()))
        return case_id

    def add_feedback(self, case_id, value):
        if not self.db.execute('SELECT 1 FROM cases WHERE id=? AND scope=?', (case_id, self.key)).fetchone():
            raise ValueError('Unknown case in this project and conversation.')
        ident = digest({'scope': self.scope, 'case': case_id, 'feedback': value})
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO feedback VALUES(?,?,?,?,?)',
                            (ident, case_id, self.key, canonical(value), now()))
            self.event('user_feedback', {'case_id': case_id, 'feedback_id': ident})
            self.db.execute('UPDATE queue SET priority=MAX(priority,100),state=CASE WHEN state="completed" THEN "pending" ELSE state END,updated=? '
                            'WHERE scope=? AND signature=(SELECT signature FROM cases WHERE id=?)',
                            (now(), self.key, case_id))
        return ident

    def cases(self, *, approved_only=False, signature=None):
        query, args = 'SELECT * FROM cases WHERE scope=?', [self.key]
        if approved_only:
            query += ' AND approved=1'
        if signature:
            query += ' AND signature=?'; args.append(signature)
        rows = self.db.execute(query + ' ORDER BY created,id', args).fetchall()
        return [dict(r) | {'payload': json.loads(r['payload'])} for r in rows]

    def cycle(self, cycle_id):
        row = self.db.execute('SELECT * FROM cycles WHERE id=? AND scope=?', (cycle_id, self.key)).fetchone()
        if not row:
            raise ValueError('Unknown cycle in this project and conversation.')
        value = dict(row)
        for key in ('config', 'manifest', 'frozen', 'search', 'decision'):
            value[key] = json.loads(value[key]) if value[key] else None
        return value

    def cycles(self):
        return [self.cycle(r[0]) for r in self.db.execute('SELECT id FROM cycles WHERE scope=? ORDER BY ordinal', (self.key,))]

    def create_cycle(self, signature, manifest, config):
        """Seal the policy and fresh input commitments before upstream sees DEV."""
        group = self.db.execute('SELECT * FROM queue WHERE scope=? AND signature=?', (self.key, signature)).fetchone()
        if not group or not self.cases(approved_only=True, signature=signature):
            raise ValueError('A queue group needs at least one optimizer-approved case.')
        if config.get('model') != MODEL or config.get('effort') != 'low':
            raise ValueError('This adapter fixes the existing subscription model and effort.')
        if config.get('upstream_commit') != UPSTREAM_COMMIT:
            raise ValueError('Unpinned upstream version.')
        for name in ('max_producers', 'max_roles', 'max_role_tools', 'producer_timeout', 'role_timeout'):
            if type(config.get(name)) is not int or config[name] < 1:
                raise ValueError('Finite positive call, tool and time budgets are required.')
        if not manifest.get('dev') or not manifest.get('regression') or not manifest.get('holdout'):
            raise ValueError('DEV, regression and fresh holdout sets are mandatory.')
        identities = [input_identity(r) for r in manifest['holdout']]
        known = [input_identity(r)
                 for split in ('dev', 'regression') for r in manifest[split]]
        if set(known) & set(identities):
            raise ValueError('Fresh holdout must be disjoint from DEV and regression.')
        if len(set(identities)) != len(identities):
            raise ValueError('Fresh holdout inputs cannot duplicate each other.')
        with self.lock, self.db:
            ordinal = self.db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM cycles WHERE scope=?', (self.key,)).fetchone()[0]
            ident = digest({'scope': self.scope, 'ordinal': ordinal, 'manifest': manifest, 'config': config})
            for identity in identities:
                if self.db.execute('SELECT 1 FROM exposure WHERE identity=? AND scope=?', (identity, self.key)).fetchone():
                    raise ValueError('An already reserved or disclosed example cannot be fresh holdout.')
                self.db.execute('INSERT INTO exposure VALUES(?,?,?,?,NULL)', (identity, self.key, ident, 'holdout'))
            for identity in known:
                self.db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?,?,?,?)',
                                (identity, self.key, ident, 'development_or_regression', now()))
            self.db.execute('INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (ident, self.key, ordinal, group['id'], 'queued', canonical(config), canonical(manifest),
                             None, None, None, now(), now()))
            self.db.execute('UPDATE queue SET state=?,updated=? WHERE id=?', ('scheduled', now(), group['id']))
            self.event('cycle_sealed', {'config_sha256': digest(config), 'manifest_sha256': digest(manifest)}, ident)
        return self.cycle(ident)

    def update(self, cycle_id, *, phase=None, **fields):
        previous = self.cycle(cycle_id)
        if set(fields) - {'search', 'frozen', 'decision'}:
            raise ValueError('Immutable cycle inputs and policy cannot be edited.')
        for key, value in fields.items():
            if previous[key] is not None and previous[key] != value:
                raise ValueError('Recorded '+key+' is immutable.')
        values = {k: canonical(v) for k, v in fields.items()}
        if phase:
            values['phase'] = phase
        values['updated'] = now()
        with self.lock, self.db:
            self.db.execute('UPDATE cycles SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=? AND scope=?',
                            [*values.values(), cycle_id, self.key])

    def open_holdout(self, cycle_id):
        cycle = self.cycle(cycle_id)
        if not cycle['frozen']:
            raise ValueError('Holdout remains closed until candidate and policy are frozen.')
        with self.lock, self.db:
            self.db.execute('UPDATE exposure SET opened=COALESCE(opened,?) WHERE cycle_id=? AND scope=?',
                            (now(), cycle_id, self.key))
            self.event('holdout_opened', {'frozen_sha256': digest(cycle['frozen'])}, cycle_id)

    def budget_hook(self, cycle_id):
        def reserve(kind, identity):
            if self.setting('stopped'):
                raise BudgetStopped('Pipeline stopped; resume explicitly before another native call.')
            cycle = self.cycle(cycle_id)
            limit_key = {'producer': 'max_producers', 'role': 'max_roles', 'role_tool': 'max_role_tools'}.get(kind)
            if not limit_key:
                raise ValueError('Unknown budget category.')
            with self.lock, self.db:
                used = self.db.execute('SELECT COUNT(*) FROM operations WHERE scope=? AND cycle_id=? AND kind=?',
                                       (self.key, cycle_id, kind)).fetchone()[0]
                if used >= cycle['config'][limit_key]:
                    raise BudgetStopped('The sealed '+kind+' budget is exhausted.')
                self.db.execute('INSERT INTO operations(scope,cycle_id,kind,identity,state,started,details) VALUES(?,?,?,?,?,?,?)',
                                (self.key, cycle_id, kind, canonical(identity), 'started', now(), '{}'))
            if kind != 'role_tool':
                print(canonical({'event': 'native_intent', 'cycle': cycle['ordinal'], 'kind': kind, 'attempt_ordinal': used+1}), flush=True)
            return used + 1
        return reserve

    def reconcile(self, cycle_id, kind, details):
        """Phase receipts bind outcomes; raw upstream/adapter caches remain authoritative."""
        with self.lock, self.db:
            self.db.execute('UPDATE operations SET state=?,ended=?,details=? WHERE cycle_id=? AND scope=? AND kind=? AND state=?',
                            ('phase_recorded', now(), canonical(details), cycle_id, self.key, kind, 'started'))

    def recover(self):
        """Called only after acquiring the exclusive worker flock, never on a live worker."""
        with self.lock, self.db:
            count = self.db.execute('SELECT COUNT(*) FROM operations WHERE scope=? AND state=?', (self.key, 'started')).fetchone()[0]
            self.db.execute('UPDATE operations SET state=?,ended=? WHERE scope=? AND state=?',
                            ('interrupted_unknown', now(), self.key, 'started'))
            if count:
                self.event('worker_restart', {'unreconciled_operation_intents': count,
                    'limitation': 'Native receipt caches determine completion; these intents include tools.'})

    @contextlib.contextmanager
    def worker(self):
        with (self.root / 'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Another worker owns this pipeline root.') from exc
            self.recover()
            yield

    def usage(self, cycle_id):
        self.cycle(cycle_id)
        return [dict(row) for row in self.db.execute('SELECT kind,state,COUNT(*) AS count FROM operations WHERE scope=? AND cycle_id=? GROUP BY kind,state', (self.key, cycle_id))]


def acceptance(regression, holdout, *, policy):
    """Fixed independent admission, separate from the upstream finals-only selector."""
    expected = policy['regression_tasks'] + policy['holdout_tasks']
    pairs = [*regression, *holdout]
    reasons = []
    if (len(pairs) != expected or len(regression) != policy['regression_tasks']
            or len(holdout) != policy['holdout_tasks']):
        reasons.append('incomplete_planned_comparison')
    identities = [p.get('task_id') for p in pairs]
    if any(x is None for x in identities) or len(set(identities)) != len(identities):
        reasons.append('duplicate_or_missing_task_identity')
    for pair in pairs:
        if not {'baseline', 'candidate'} <= set(pair):
            reasons.append('missing_arm'); continue
        for arm in ('baseline', 'candidate'):
            result = pair[arm]
            if result.get('status') in {'interrupted', 'interrupted_unknown', 'running', None}:
                reasons.append('unknown_execution_'+arm)
            if arm == 'candidate' and (result.get('status') != 'completed' or result.get('unverified')):
                reasons.append('incomplete_or_unverified_'+arm)
            if not result.get('source_immutable'):
                reasons.append('source_changed_'+arm)
            if arm == 'candidate' and not result.get('delivered'):
                reasons.append('delivery_missing_'+arm)
            if arm == 'candidate' and result.get('material_defect'):
                reasons.append('material_visual_defect_'+arm)
            elapsed = result.get('elapsed_seconds')
            tools = result.get('tool_calls')
            if (type(elapsed) not in (int, float) or not math.isfinite(elapsed)
                    or not 0 <= elapsed <= policy['max_elapsed_seconds']):
                reasons.append('time_budget_'+arm)
            if type(tools) is not int or not 0 <= tools <= policy['max_tool_calls']:
                reasons.append('tool_budget_'+arm)
            if result.get('joint_passed') and (not result.get('artifact_sha256')
                    or result.get('status') != 'completed'
                    or result.get('unverified') or result.get('material_defect')
                    or not all(result.get(x) for x in ('delivered', 'visual_passed', 'mechanical_passed'))):
                reasons.append('invalid_joint_sha_binding_'+arm)
            if not result.get('model_valid', True) or not result.get('budget_valid', True):
                reasons.append('invalid_conditions_'+arm)
        if pair['baseline'].get('joint_passed') and not pair['candidate'].get('joint_passed'):
            if pair.get('split') == 'regression':
                reasons.append('regression_loss')
    base = sum(bool(p['baseline'].get('joint_passed')) for p in holdout if 'baseline' in p)
    cand = sum(bool(p['candidate'].get('joint_passed')) for p in holdout if 'candidate' in p)
    if cand <= base:
        reasons.append('no_strict_fresh_holdout_gain')
    candidate_missing = any(not p.get('candidate', {}).get('delivered') for p in pairs)
    if candidate_missing:
        reasons.append('candidate_delivery_missing')
    return {'accepted': not reasons, 'reasons': sorted(set(reasons)),
            'baseline_holdout_joint': base, 'candidate_holdout_joint': cand,
            'holdout_tasks': len(holdout), 'regression_tasks': len(regression),
            'policy_sha256': digest(policy), 'scope': 'narrow_synthetic_media_depth',
            'broad_quality_improvement_claim': False}


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError('Immutable evidence already exists with different content: '+path.name)
        return
    temporary = path.with_suffix('.tmp')
    temporary.write_text(canonical(value)+'\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def implementation():
    root = Path(__file__).resolve().parent
    names = ('harness2_pipeline.py', 'harness2_upstream.py', 'harness_versions.py',
             'controller.py', 'runtime.py', 'tools.py')
    from .harness2_media import evaluator_fingerprint
    files = {**evaluator_fingerprint()['files'], **{'oak/'+n: file_sha(root/n) for n in names}}
    return {'sha256': digest(files), 'files': files}


def default_config():
    return {'model': MODEL, 'effort': 'low', 'upstream_commit': UPSTREAM_COMMIT,
            'max_producers': 16, 'max_roles': 8, 'max_role_tools': 512,
            'max_parallel_native': 1,
            'producer_timeout': 600, 'role_timeout': 300, 'k': 1,
            'implementation': implementation(),
            'policy': {'regression_tasks': 2, 'holdout_tasks': 2,
                       'max_elapsed_seconds': 600, 'max_tool_calls': 64,
                       'strict_fresh_gain': True, 'zero_regression_losses': True,
                       'candidate_all_delivered': True,
                       'infra_rule': 'A missing terminal receipt is unknown, retained and retried only within the sealed total call budget. A terminal failed/refused/unverified attempt is a failure and never retried.',
                       'scope': 'synthetic source-preserving layered media only'}}


def _versions(store):
    from .harness_versions import HarnessVersions
    return HarnessVersions(store.root / 'versions.sqlite3')


def _scope_root(store):
    path = store.root / 'scopes' / digest(store.scope)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def import_pilot_cases(store):
    """Only explicitly public, already published synthetic evidence is imported."""
    repo = Path(__file__).resolve().parent.parent
    relative = 'docs/evaluations/hard-media-harness-pilot-2026-10-19/complex-results.json'
    path = repo / relative
    source = json.loads(path.read_text())
    ids = []
    for task in source['tasks']:
        payload = {'visibility': 'public_fixture', 'task_id': task['id'],
                   'artifact_reference': relative, 'evidence_sha256': file_sha(path),
                   'source': 'completed-public-pilot-104',
                   'procedure_version': source['source_commit'],
                   'result': 'historical_evidence_only',
                   'failure_reason': ('source fidelity and layered occlusion' if 'depth' in task['id']
                                      else 'diagnostic only; ambiguous mechanism scorer excluded'),
                   'eligible_primary': 'depth' in task['id']}
        ids.append(store.record_case(payload, signature='source-preserving-depth', approved=True, priority=80))
    return ids


def _seal_descriptor(store, descriptor):
    from .harness2_media import materialize_descriptor, source_commitment
    destination = _scope_root(store) / 'sealed-inputs' / digest(descriptor)
    marker = destination / 'materialized.json'
    if marker.exists():
        task = json.loads(marker.read_text())
    else:
        task = materialize_descriptor(descriptor, destination)
        write_once(marker, task)
    return {'descriptor': descriptor, 'commitment': source_commitment(descriptor, task)}


def _prior_dev_attempt_history(store, cycle):
    """Verbatim earlier DEV-role observations, distinct from upstream Library.

    A genuine NO_ENTRY remains a NO_ENTRY. This adapter record stores neither
    new lessons nor independent acceptance feedback. It reads only role audit
    paths in completed earlier cycles of this exact project/chat/domain scope.
    """
    scope_root = _scope_root(store)
    previous = store.db.execute(
        'SELECT id,ordinal FROM cycles WHERE scope=? AND phase="completed" AND ordinal<? '
        'ORDER BY ordinal DESC LIMIT 4', (store.key, cycle['ordinal'])).fetchall()

    def read(path, root, maximum):
        path, root = Path(path).absolute(), Path(root).absolute()
        if '..' in path.parts or not path.is_relative_to(root):
            raise ValueError('Prior DEV role evidence escaped its selected cycle.')
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('Prior DEV role evidence cannot contain links.')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > maximum):
                raise ValueError('Prior DEV role evidence exceeds its fixed boundary.')
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (len(data) != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise ValueError('Prior DEV role evidence changed while being read.')
        return data

    candidates = []
    for prior in previous:
        root = scope_root / 'cycles' / prior['id'] / 'roles'
        for path in sorted(root.glob('*/attempt-*/receipt.json')):
            raw = read(path, root, 65536)
            receipt = json.loads(raw)
            role, status = receipt.get('role'), receipt.get('status')
            if (receipt.get('model') != MODEL or receipt.get('effort') != 'low'
                    or receipt.get('channel') != 'oak_chatgpt_subscription'
                    or not isinstance(role, str)
                    or not re.fullmatch(r'(?:inv|dom|c)\d+\.a\d+|(?:experience|select)\.seq\d+', role)
                    or type(receipt.get('attempt')) is not int or receipt['attempt'] < 1):
                raise ValueError('Prior DEV role evidence has an unexpected native identity.')
            entry = {'cycle_id': prior['id'], 'ordinal': prior['ordinal'], 'role': role,
                     'attempt': receipt['attempt'], 'status': status,
                     'receipt_sha256': hashlib.sha256(raw).hexdigest()}
            if status == 'completed':
                response = read(path.parent / 'response.txt', root, 32768)
                actual = hashlib.sha256(response).hexdigest()
                if receipt.get('response_sha256') != actual:
                    raise ValueError('Prior DEV role response differs from its native receipt.')
                entry.update(response_sha256=actual, response=response.decode('utf-8'))
            elif status in {'failed', 'interrupted_unknown', 'interrupted_before_native',
                            'not_started_budget_blocked', 'running', 'pending_budget'}:
                # Failed/interrupted calls may have no final message. The
                # status and original receipt are evidence; no lesson is added.
                entry['response_available'] = False
            else:
                raise ValueError('Prior DEV role evidence has an unknown status.')
            priority = 0 if role.startswith('experience.') else 2 if status == 'completed' else 1
            candidates.append((prior['ordinal'], priority, role, receipt['attempt'], entry))
    history = {'source': 'Oak adapter attempt_history: verbatim earlier DEV role responses, not upstream Library entries',
               'policy': 'No independent grades, holdout material, acceptance decisions or authored lessons are included. NO_ENTRY is preserved verbatim.',
               'max_prior_cycles': 4, 'max_bytes': 16384, 'attempts': [], 'omitted_attempts': 0}
    # Prefer the latest real reflection, then its candidate observations. This
    # is evidence retention, not a substitute reflection or a selected lesson.
    for _, _, _, _, entry in sorted(candidates, key=lambda row: (row[1], -row[0], row[2], row[3])):
        proposed = {**history, 'attempts': [*history['attempts'], entry]}
        if len(canonical(proposed).encode()) <= 16200:
            history['attempts'].append(entry)
        else:
            history['omitted_attempts'] += 1
    history['content_sha256'] = digest(history['attempts'])
    if len(canonical(history).encode()) > 16384:
        raise ValueError('Prior DEV role evidence exceeds its total fixed budget.')
    return history


def approved_development_evidence(store, cycle):
    """Only consented current DEV corrections enter the scoped optimizer context.

    Independent grades, regression and unopened holdout remain outside. The
    genuine upstream library separately supplies prior probe/reflection history.
    """
    ids = {row['descriptor']['id'] for row in cycle['manifest']['dev']}
    cases = []
    signature = store.db.execute('SELECT signature FROM queue WHERE id=? AND scope=?',
                                 (cycle['queue_id'], store.key)).fetchone()[0]
    for case in store.cases(approved_only=True, signature=signature):
        payload = case['payload']
        if payload.get('descriptor', {}).get('id') not in ids:
            continue
        feedback = [json.loads(r[0]) for r in store.db.execute(
            'SELECT value FROM feedback WHERE case_id=? AND scope=? ORDER BY created,id', (case['id'], store.key))]
        cases.append({'case_id': case['id'], 'development_case': payload, 'user_feedback': feedback})
    value = {'scope': store.scope, 'cases': cases, 'source': 'explicitly approved current DEV cases only',
             'attempt_history': _prior_dev_attempt_history(store, cycle)}
    if len(canonical(value).encode()) > 64000:
        raise ValueError('Approved DEV evidence exceeds its bounded optimizer context.')
    return value


def schedule_demo(store):
    """Reusable queue recipe, not a handwritten optimizer. One new DEV per cycle."""
    from .harness2_media import make_descriptor, exposed_regression, preflight_descriptors
    previous = store.cycles()
    if previous and previous[-1]['phase'] != 'completed':
        return previous[-1]
    ordinal = len(previous)+1
    if ordinal > 2:
        return None
    namespace = store.setting('namespace')
    if namespace is None:
        import uuid
        namespace = 'oak-native-' + uuid.uuid4().hex
        store.setting('namespace', namespace)
    dev = make_descriptor(namespace, ordinal*10, kind='creation' if ordinal == 1 else 'repair')
    if previous:
        regression = [{**row['descriptor'], 'split': 'regression'} for row in previous[-1]['manifest']['holdout']]
    else:
        regression = [exposed_regression('holdout-create-reeds309-depth'),
                      exposed_regression('holdout-repair-reeds310-depth')]
    holds = [make_descriptor(namespace, ordinal*10+i, split='holdout',
                             kind='creation' if i == 1 else 'repair') for i in (1, 2)]
    store.record_case({'visibility': 'public_fixture', 'descriptor': dev,
                       'source': 'fresh-cycle-development', 'result': 'pending',
                       'failure_reason': 'source-preserving occlusion refinement'},
                      signature='source-preserving-depth', approved=True, priority=100)
    manifest = {split: [_seal_descriptor(store, d) for d in descriptors]
                for split, descriptors in (('dev', [dev]), ('regression', regression), ('holdout', holds))}
    controls = preflight_descriptors([dev, holds[0] if ordinal == 2 else holds[1]],
                                    _scope_root(store) / 'construction-controls' / str(ordinal))
    if controls['status'] != 'pass':
        raise ValueError('Authored construction controls failed; native search remains closed.')
    config = default_config()
    config['construction_controls_sha256'] = digest(controls)
    return store.create_cycle('source-preserving-depth', manifest, config)


def schedule_next(store):
    """Consume the highest-priority approved new DEV case using the same recipe.

    The initial supported deployment scope is layered choreography. A new family
    needs independent construction controls and a declared regression recipe.
    """
    from .harness2_media import make_descriptor, exposed_regression, preflight_descriptors
    cycles = store.cycles()
    used = {row['descriptor']['id'] for c in cycles for row in c['manifest']['dev']}
    groups = store.db.execute('SELECT signature FROM queue WHERE scope=? AND state="pending" ORDER BY priority DESC,updated', (store.key,)).fetchall()
    for group in groups:
        for case in store.cases(approved_only=True, signature=group['signature']):
            dev = case['payload'].get('descriptor')
            if not dev or dev.get('id') in used:
                continue
            if dev.get('family') != 'choreography' or dev.get('split') != 'dev':
                raise ValueError('Automatic queue scheduling currently supports only approved layered choreography DEV descriptors.')
            import uuid
            namespace = 'queued-'+uuid.uuid4().hex
            prior = next((c for c in reversed(cycles) if c['phase'] == 'completed'), None)
            reg = ([{**r['descriptor'], 'split': 'regression'} for r in prior['manifest']['holdout']]
                   if prior else [exposed_regression('holdout-create-reeds309-depth'), exposed_regression('holdout-repair-reeds310-depth')])
            hold = [make_descriptor(namespace, i, split='holdout', kind=kind)
                    for i,kind in enumerate(('creation', 'repair'))]
            manifest = {split: [_seal_descriptor(store, d) for d in ds]
                        for split,ds in (('dev',[dev]), ('regression',reg), ('holdout',hold))}
            controls = preflight_descriptors([dev,hold[1] if dev['kind'] == 'creation' else hold[0]],
                                            _scope_root(store) / 'construction-controls' / namespace)
            if controls['status'] != 'pass':
                raise ValueError('Authored construction controls failed; native search remains closed.')
            config = default_config()
            config['construction_controls_sha256'] = digest(controls)
            return store.create_cycle(group['signature'], manifest, config)
    return None


def run_cycle(store, cycle_id, *, runtime_home=None):
    """Resume genuine upstream search, then independently admit its frozen output."""
    from .harness2_upstream import SubscriptionRole, NativeRoleSpec, run_development
    from .harness2_media import MediaBenchmarkAdapter, source_commitment
    cycle = store.cycle(cycle_id)
    if cycle['phase'] == 'completed':
        return cycle
    config = cycle['config']
    if config['implementation'] != implementation():
        raise ValueError('Implementation changed after sealing; completed evidence is preserved. Start a new cycle under the changed implementation.')
    root = _scope_root(store) / 'cycles' / cycle_id
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    versions = _versions(store)
    try:
        active = versions.resolve(*store.scope)
        if active is None:
            active = versions.bootstrap_current(*store.scope, '', evidence={'source': 'existing Oak Controller instructions', 'comparative_gain_claim': False})
        parent_path = root / 'parent.json'
        if parent_path.exists():
            parent = versions.version(json.loads(parent_path.read_text())['id'])
        else:
            parent = active
            write_once(parent_path, {'id': parent['id'], 'procedure_sha256': parent['procedure_sha256']})
        if cycle['frozen'] is None:
            store.update(cycle_id, phase='searching')
            role = SubscriptionRole(root / 'roles', runtime_home=runtime_home,
                timeout=config['role_timeout'], budget_hook=store.budget_hook(cycle_id), native_lock_path=root / 'native.lock')
            channels = {'proposer': NativeRoleSpec(timeout=config['role_timeout']),
                        'selector': NativeRoleSpec(timeout=config['role_timeout'])}
            dev = [row['descriptor'] for row in cycle['manifest']['dev']]
            adapter = MediaBenchmarkAdapter(root / 'development', dev, channels=channels,
                runtime_home=runtime_home, timeout=config['producer_timeout'],
                baseline_procedure=parent['procedure'], budget_hook=store.budget_hook(cycle_id), native_lock_path=root / 'native.lock')
            for row in cycle['manifest']['dev']:
                if source_commitment(row['descriptor'], adapter._task(row['descriptor']['id'])) != row['commitment']:
                    raise ValueError('DEV material differs from its sealed source.')
            if len(dev) != 1:
                raise ValueError('This bounded sequential recipe requires one new DEV task per cycle.')
            context_path = root / 'approved-development-evidence.json'
            if context_path.exists():
                context = json.loads(context_path.read_text())
            else:
                context = approved_development_evidence(store, cycle)
                write_once(context_path, context)
            write_once(adapter.task_files(dev[0]['id']) / 'OAK_CASE_EVIDENCE.json', context)
            search_path = root / 'upstream-result.json'
            if search_path.exists():
                search = json.loads(search_path.read_text())
            else:
                result = run_development(dev[0]['id'], adapter=adapter, role=role,
                    work_dir=root / 'upstream', library_dir=_scope_root(store) / 'upstream-library', k=config['k'])
                search = {'upstream': result.to_dict(), 'provenance': result.oak_provenance}
                write_once(search_path, search)
            chosen = Path(search['provenance']['chosen_harness']).resolve(strict=True)
            if not chosen.is_relative_to(root / 'upstream'):
                raise ValueError('The chosen upstream harness is outside this cycle.')
            procedure = adapter._procedure(chosen)
            candidate = versions.register(*store.scope, procedure, parent_id=parent['id'],
                evidence={'cycle_id': cycle_id, 'search_sha256': digest(search), 'upstream_commit': UPSTREAM_COMMIT},
                metadata={'domain': 'choreography', 'selection': 'upstream seq final; not independently admitted'})
            frozen = {'candidate_id': candidate['id'], 'candidate_sha256': candidate['procedure_sha256'],
                      'parent_id': parent['id'], 'parent_sha256': parent['procedure_sha256'],
                      'manifest_sha256': digest(cycle['manifest']), 'config_sha256': digest(config),
                      'search_sha256': digest(search), 'frozen_at': now()}
            if (root / 'freeze.json').exists():
                prior_freeze = json.loads((root / 'freeze.json').read_text())
                if {k:v for k,v in prior_freeze.items() if k != 'frozen_at'} != {k:v for k,v in frozen.items() if k != 'frozen_at'}:
                    raise ValueError('Persisted candidate freeze differs from the recovered search.')
                frozen = prior_freeze
            write_once(root / 'freeze.json', frozen)
            store.update(cycle_id, search=search, frozen=frozen, phase='frozen')
            for kind in ('producer', 'role', 'role_tool'):
                store.reconcile(cycle_id, kind, {'phase_association': 'upstream search returned', 'search_sha256': digest(search)})
            print(canonical({'event': 'candidate_frozen', 'cycle': cycle['ordinal'], 'candidate': candidate['id']}), flush=True)
        cycle = store.cycle(cycle_id)
        frozen = cycle['frozen']
        candidate = versions.version(frozen['candidate_id'])
        freeze_sha = digest(frozen)
        if candidate['procedure_sha256'] != frozen['candidate_sha256']:
            raise ValueError('Frozen procedure changed.')
        candidate_dir = root / 'frozen-candidate'
        candidate_dir.mkdir(exist_ok=True)
        candidate_path = candidate_dir / 'AGENTS.md'
        if candidate_path.exists() and candidate_path.read_text() != candidate['procedure']:
            raise ValueError('Frozen candidate tree changed.')
        candidate_path.write_text(candidate['procedure'])
        store.open_holdout(cycle_id)
        store.update(cycle_id, phase='evaluating')
        rows = [*cycle['manifest']['regression'], *cycle['manifest']['holdout']]
        outer = MediaBenchmarkAdapter(root / 'independent', [r['descriptor'] for r in rows],
            runtime_home=runtime_home, timeout=config['producer_timeout'], baseline_procedure=parent['procedure'],
            budget_hook=store.budget_hook(cycle_id), optimizer=False, freeze_sha256=freeze_sha, native_lock_path=root / 'native.lock')
        pairs = []
        for index, row in enumerate(rows):
            descriptor = row['descriptor']
            if source_commitment(descriptor, outer._task(descriptor['id'])) != row['commitment']:
                raise ValueError('Independent material differs from the sealed source.')
            pair = {'task_id': descriptor['id'], 'split': descriptor['split'], 'input_sha256': row['commitment']['sha256']}
            arms = ('baseline', 'candidate') if index % 2 == 0 else ('candidate', 'baseline')
            for arm in arms:
                print(canonical({'event': 'native_comparison', 'cycle': cycle['ordinal'], 'task': descriptor['id'], 'arm': arm}), flush=True)
                run = outer.execute(descriptor['id'], None if arm == 'baseline' else candidate_dir,
                                    descriptor['id']+'-'+arm)
                result = outer.score(run, descriptor['id'])
                write_once(root / 'evaluations' / (descriptor['id']+'-'+arm+'.json'), result)
                pair[arm] = result
                store.record_case({'visibility': 'public_fixture', 'task_id': descriptor['id'],
                    'procedure_version': parent['id'] if arm == 'baseline' else candidate['id'],
                    'result': result, 'artifact_reference': str(run / 'receipt.json'),
                    'artifact_sha256': result['artifact_sha256'],
                    'failure_reason': None if result['joint_passed'] else 'independent joint delivery failure'},
                    signature='source-preserving-depth', approved=False, priority=100)
            pairs.append(pair)
        evaluation = {'freeze_sha256': freeze_sha, 'pairs': pairs}
        write_once(root / 'evaluation.json', evaluation)
        decision = acceptance([p for p in pairs if p['split'] == 'regression'],
                              [p for p in pairs if p['split'] == 'holdout'], policy=config['policy'])
        evidence = {'cycle_id': cycle_id, 'evaluation_sha256': digest(evaluation),
                    'freeze_sha256': freeze_sha, 'policy_sha256': digest(config['policy'])}
        receipt = versions.record_decision(candidate['id'], accepted=decision['accepted'], evidence=evidence,
                                          reason='; '.join(decision['reasons']) or 'Strict fresh gain without regression.')
        if decision['accepted']:
            current = versions.resolve(*store.scope)
            if current['id'] != candidate['id']:
                versions.activate(candidate['id'], expected_active=parent['id'])
        decision.update(version_decision_id=receipt['id'], candidate_id=candidate['id'],
                        active_id=versions.resolve(*store.scope)['id'], evidence=evidence)
        write_once(root / 'decision.json', decision)
        store.reconcile(cycle_id, 'producer', {'phase_association': 'independent comparison returned', 'evaluation_sha256': digest(evaluation)})
        with store.lock, store.db:
            store.db.execute('UPDATE cycles SET decision=?,phase=?,updated=? WHERE id=? AND scope=?',
                             (canonical(decision), 'completed', now(), cycle_id, store.key))
            store.db.execute('UPDATE queue SET state=?,updated=? WHERE id=? AND scope=?',
                             ('completed', now(), cycle['queue_id'], store.key))
            store.event('admission_decision', decision, cycle_id)
        print(canonical({'event': 'cycle_completed', 'cycle': cycle['ordinal'], 'decision': decision}), flush=True)
        return store.cycle(cycle_id)
    finally:
        versions.close()


def work(store, *, runtime_home=None, max_cycles=1, demo=False):
    if type(max_cycles) is not int or not 1 <= max_cycles <= 20:
        raise ValueError('A finite cycle limit from 1 to 20 is required.')
    with store.worker():
        for _ in range(max_cycles):
            if store.setting('stopped'):
                raise BudgetStopped('Pipeline is stopped.')
            priorities = {r['id']: r['priority'] for r in store.db.execute('SELECT id,priority FROM queue WHERE scope=?', (store.key,))}
            pending = next(iter(sorted((c for c in store.cycles() if c['phase'] != 'completed'),
                                      key=lambda c: (-priorities[c['queue_id']], c['ordinal']))), None)
            if pending is None and demo:
                pending = schedule_demo(store)
            elif pending is None:
                pending = schedule_next(store)
            if pending is None:
                break
            run_cycle(store, pending['id'], runtime_home=runtime_home)


def status(store):
    versions = _versions(store)
    try:
        active = versions.resolve(*store.scope)
        return {'scope': store.scope, 'stopped': bool(store.setting('stopped')),
                'case_count': len(store.cases()), 'approved_cases': len(store.cases(approved_only=True)),
                'queue': [dict(r) for r in store.db.execute('SELECT * FROM queue WHERE scope=? ORDER BY priority DESC', (store.key,))],
                'active_id': active['id'] if active else None,
                'cycles': [{'id': c['id'], 'ordinal': c['ordinal'], 'phase': c['phase'],
                            'decision': c['decision'], 'usage': store.usage(c['id'])} for c in store.cycles()]}
    finally:
        versions.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--chat', type=int, required=True)
    parser.add_argument('--domain', default='media-depth')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init-demo')
    commands.add_parser('status')
    commands.add_parser('stop')
    commands.add_parser('resume')
    runner = commands.add_parser('work')
    runner.add_argument('--runtime-home')
    runner.add_argument('--max-cycles', type=int, default=1)
    runner.add_argument('--demo', action='store_true')
    add = commands.add_parser('case-add')
    add.add_argument('--json', required=True)
    add.add_argument('--signature', required=True)
    add.add_argument('--priority', type=int, default=10)
    add.add_argument('--optimizer-approved', action='store_true')
    feedback = commands.add_parser('feedback')
    feedback.add_argument('--case-id', required=True)
    feedback.add_argument('--json', required=True)
    schedule = commands.add_parser('schedule')
    schedule.add_argument('--signature', required=True)
    schedule.add_argument('--manifest', required=True)
    args = parser.parse_args(argv)
    store = PipelineStore(args.root, args.project, args.chat, args.domain)
    try:
        if args.command == 'init-demo':
            import_pilot_cases(store)
            cycle = schedule_demo(store)
            print(canonical({'cycle_id': cycle['id'] if cycle else None}))
        elif args.command == 'work':
            work(store, runtime_home=args.runtime_home, max_cycles=args.max_cycles, demo=args.demo)
        elif args.command in {'stop', 'resume'}:
            store.setting('stopped', args.command == 'stop')
        elif args.command == 'case-add':
            print(store.record_case(json.loads(Path(args.json).read_text()), signature=args.signature,
                                    approved=args.optimizer_approved, priority=args.priority))
        elif args.command == 'feedback':
            print(store.add_feedback(args.case_id, json.loads(Path(args.json).read_text())))
        elif args.command == 'schedule':
            from .harness2_media import preflight_descriptors
            manifest = json.loads(Path(args.manifest).read_text())
            controls = preflight_descriptors([r['descriptor'] for rows in manifest.values() for r in rows],
                                            _scope_root(store) / 'construction-controls' / digest(manifest))
            if controls['status'] != 'pass':
                raise ValueError('Authored construction controls failed.')
            config = default_config()
            config['construction_controls_sha256'] = digest(controls)
            print(store.create_cycle(args.signature, manifest, config)['id'])
        else:
            print(json.dumps(status(store), ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
