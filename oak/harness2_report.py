"""Export completed public-fixture Harness² evidence without private account data.

This is a read-only evidence exporter, not an evaluator or a model client.
All selected cases must explicitly be public fixtures, and every selected cycle
must be completed before its manifest can be published. Original file hashes
refer to private receipts before portable path substitution.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import statistics


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _connection(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('An existing ordinary pipeline database is required.')
    result = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    result.row_factory = sqlite3.Row
    result.execute('PRAGMA query_only=ON')
    result.execute('BEGIN')
    return result


class _Evidence:
    def __init__(self, root, scope_root):
        self.root, self.scope_root = root, scope_root
        self.allowed = [scope_root]
        self.index = {}

    def _path(self, value):
        path = Path(value).absolute()
        if '..' in path.parts:
            raise ValueError('Evidence path escapes its selected scope.')
        if not any(path.is_relative_to(allowed) for allowed in self.allowed):
            raise ValueError('Evidence points outside the selected synthetic scope.')
        if any(part.is_symlink() for part in (path, *path.parents) if part != self.root.parent):
            raise ValueError('Evidence links are forbidden.')
        return path

    def bytes(self, value, *, maximum=64 * 1024 * 1024, remember=True):
        path = self._path(value)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > maximum):
                raise ValueError('Evidence must be a bounded independent regular file.')
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (len(data) != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise ValueError('Evidence changed while being exported.')
        if remember:
            self.index[path.relative_to(self.root).as_posix()] = hashlib.sha256(data).hexdigest()
        return data

    def json(self, path):
        return json.loads(self.bytes(path))

    def hash_file(self, value, *, expected=None, allow_invalid=False):
        path = self._path(value)
        if not path.exists():
            if allow_invalid:
                return {'available': False, 'expected_sha256': expected, 'verified': False}
            raise ValueError('A completed execution evidence file is missing.')
        # Artifacts can exceed the JSON budget; never place their bytes in a report.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError('An ordinary independent artifact is required.')
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise ValueError('Artifact changed while being exported.')
        verified = expected is None or actual == expected
        if not verified and not allow_invalid:
            raise ValueError('Completed execution evidence no longer matches its recorded SHA.')
        relative = path.relative_to(self.root).as_posix()
        self.index[relative] = actual
        return {'path': relative, 'available': True, 'bytes': before.st_size,
                'sha256': actual, 'expected_sha256': expected, 'verified': verified}

    def producer(self, path):
        record = self.json(path)
        terminal = record.get('state') == 'finished' or 'receipt_sha256' in record
        invalid = record.get('terminal_integrity') == 'failed' or not terminal
        sources = []
        for source, expected in record.get('task', {}).get('source_hashes', {}).items():
            sources.append(self.hash_file(source, expected=expected, allow_invalid=invalid))
        artifacts = {}
        receipt = record.get('receipt') or {}
        for prefix in ('artifact', 'candidate'):
            artifact, expected = receipt.get(prefix + '_path'), receipt.get(prefix + '_sha256')
            if artifact is not None:
                if not invalid and (not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected)):
                    raise ValueError('A terminal artifact lacks its exact SHA binding.')
                artifacts[prefix] = self.hash_file(artifact, expected=expected, allow_invalid=invalid)
        result = {'path': str(Path(path).relative_to(self.root)), 'record': record,
                  'source_files': sources, 'artifact_bindings': artifacts,
                  'raw_receipt_sha256': self.index[Path(path).relative_to(self.root).as_posix()]}
        trace = record.get('attempt_trace_path') if not terminal else None
        trace = Path(trace) if trace else Path(path).parent / 'trace.json'
        if trace.exists():
            result['trace'] = self.json(trace)
        return result


_SECRET = re.compile(r'(?:\bsk-[A-Za-z0-9_-]{20,}|\bBearer\s+[A-Za-z0-9_.-]{12,}|'
                     r'[?&](?:access_token|refresh_token|api_key|token)=[^\s&]+)', re.I)
_HOST_PATH = re.compile(r'(?<![A-Za-z0-9:])/(?:home|root|tmp|run|var|usr|opt|etc|mnt|proc|dev|sys)(?:/[^\s"<>]*)?')


def _portable(value, root):
    if isinstance(value, dict):
        return {_portable(str(key), root): _portable(child, root) for key, child in value.items()}
    if isinstance(value, list):
        return [_portable(child, root) for child in value]
    if isinstance(value, tuple):
        return [_portable(child, root) for child in value]
    if isinstance(value, str):
        if _SECRET.search(value):
            raise ValueError('Credential-like material cannot be exported.')
        value = value.replace(str(root), '/pipeline')
        return _HOST_PATH.sub('<local-path>', value)
    return value


def _stats(values):
    actual = [x for x in values if type(x) in (int, float) and math.isfinite(x) and x >= 0]
    return {'known_count': len(actual), 'missing_count': len(values) - len(actual),
            'sum': sum(actual), 'mean': statistics.mean(actual) if actual else None,
            'median': statistics.median(actual) if actual else None}


def _metrics(pairs):
    grouped = defaultdict(list)
    for pair in pairs:
        for arm in ('baseline', 'candidate'):
            grouped[(pair.get('split'), arm)].append(pair.get(arm, {}))
    groups = []
    for (split, arm), rows in sorted(grouped.items()):
        groups.append({'split': split, 'arm': arm, 'planned_distinct_tasks': len(rows),
            'joint_successes': sum(row.get('joint_passed') is True for row in rows),
            'delivery_successes': sum(row.get('delivered') is True for row in rows),
            'visual_successes': sum(row.get('visual_passed') is True for row in rows),
            'mechanical_successes': sum(row.get('mechanical_passed') is True for row in rows),
            'material_defects': sum(row.get('material_defect') is True for row in rows),
            'unverified': sum(row.get('unverified') is True for row in rows),
            'incomplete_or_failed': sum(row.get('status') != 'completed' for row in rows),
            'elapsed_seconds': _stats([row.get('elapsed_seconds') for row in rows]),
            'tool_calls': _stats([row.get('tool_calls') for row in rows]),
            'review_calls': _stats([row.get('review_calls') for row in rows]),
            'mechanical_quality_score': _stats([row.get('mechanical', {}).get('quality_score') for row in rows])})
    held = [pair for pair in pairs if pair.get('split') == 'holdout']
    return {'groups': groups, 'fresh_holdout_pairs': len(held),
            'candidate_wins': sum(pair.get('candidate', {}).get('joint_passed') is True
                                  and pair.get('baseline', {}).get('joint_passed') is not True for pair in held),
            'candidate_losses': sum(pair.get('baseline', {}).get('joint_passed') is True
                                    and pair.get('candidate', {}).get('joint_passed') is not True for pair in held),
            'ties': sum(bool(pair.get('baseline', {}).get('joint_passed')) ==
                        bool(pair.get('candidate', {}).get('joint_passed')) for pair in held),
            'repeats_are_not_independent_tasks': True, 'cost_usd': None,
            'cost_note': 'Calls, tools and elapsed time are cost proxies; subscription USD is not metered here.'}


def _producer_costs(producers):
    groups = defaultdict(list)
    for item in producers:
        if item.get('interrupted_attempt'):
            continue
        record = item['record']
        run_id = record.get('run_id', '')
        label = run_id.rsplit('/', 1)[-1]
        if item['phase'] == 'development':
            arm = ('baseline' if label.startswith('base') else 'INV' if label.startswith('inv')
                   else 'DOM' if label.startswith('dom') else 'composition')
        else:
            arm = 'candidate' if label.endswith('-candidate') else 'baseline'
        groups[(item['phase'], arm)].append(record)
    return [{'phase': phase, 'arm': arm, 'terminal_records': sum(row.get('state') == 'finished' for row in rows),
             'records': len(rows), 'tool_calls': _stats([row.get('receipt', {}).get('tool_calls') for row in rows]),
             'elapsed_seconds': _stats([row.get('receipt', {}).get('elapsed_seconds') for row in rows]),
             'recorded_review_attempts': sum(len(row.get('receipt', {}).get('reviews', [])) for row in rows),
             'independent_quality_not_measured_in_search': phase == 'development'}
            for (phase, arm), rows in sorted(groups.items())]


def _construction_controls(cycle, evidence):
    """Export only the exact preflight selected before this cycle's models."""
    expected = cycle['config'].get('construction_controls_sha256')
    if expected is None:
        return None
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Cycle construction-control commitment is invalid.')
    selected = []
    for path in sorted((evidence.scope_root / 'construction-controls').rglob('preflight.json')):
        raw = evidence.bytes(path, remember=False)
        record = json.loads(raw)
        if _digest(record) == expected:
            selected.append((path, raw, record))
    if len(selected) != 1:
        raise ValueError('The exact selected authored construction receipt is missing or ambiguous.')
    path, raw, record = selected[0]
    if record.get('status') != 'pass' or not record.get('cases'):
        raise ValueError('The selected authored construction controls did not pass.')
    evidence.index[path.relative_to(evidence.root).as_posix()] = hashlib.sha256(raw).hexdigest()
    descriptor_map = {_digest(row['descriptor']): row for rows in cycle['manifest'].values() for row in rows}
    bindings = []
    for case in record['cases']:
        descriptor = descriptor_map.get(case.get('descriptor_sha256'))
        if descriptor is None or case.get('source_commitment') != descriptor['commitment']:
            raise ValueError('Authored controls do not match this cycle\'s sealed source inputs.')
        if case.get('status') != 'pass' or set(case.get('controls', {})) != {'positive', 'negative'}:
            raise ValueError('Authored positive and negative construction evidence is incomplete.')
        controls = case['controls']
        if (controls['positive'].get('mechanical', {}).get('status') != 'pass'
                or controls['negative'].get('mechanical', {}).get('status') != 'fail'
                or controls['negative'].get('mechanical', {}).get('structural_status') != 'pass'):
            raise ValueError('Authored control grades differ from the sealed positive/negative protocol.')
        files = []
        for control in controls.values():
            if not isinstance(control.get('files'), dict) or not control['files']:
                raise ValueError('Authored control file bindings are missing.')
            if control.get('artifact_sha256') not in control['files'].values():
                raise ValueError('Authored control artifact is absent from its bound files.')
            for relative, sha in control['files'].items():
                if (not isinstance(relative, str) or '\\' in relative or Path(relative).is_absolute()
                        or '..' in Path(relative).parts or not relative
                        or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha)):
                    raise ValueError('Authored control file escaped its bound preflight directory.')
                files.append(evidence.hash_file(path.parent / relative, expected=sha))
        sources = []
        for name, sha in case['source_commitment']['inputs'].items():
            if Path(name).name != name or '\\' in name or name in {'.', '..'}:
                raise ValueError('Authored source file escaped its bound input directory.')
            source = path.parent / descriptor['descriptor']['id'] / 'workspace/inputs' / name
            sources.append(evidence.hash_file(source, expected=sha))
        bindings.append({'task_id': descriptor['descriptor']['id'], 'files': files, 'source_files': sources})
    return {'path': path.relative_to(evidence.root).as_posix(), 'record': record,
            'selected_receipt_sha256': expected, 'raw_receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'verified_bindings': bindings,
            'evidence_class': 'local_authored_construction_controls_no_models_no_visual_aesthetic_verdict'}


def _version_state(root, scope):
    path = root / 'versions.sqlite3'
    if not path.exists():
        return {'versions': [], 'decisions': [], 'active': [], 'activation_events': [], 'applications': []}
    db = _connection(path)
    try:
        where = 'project_id=? AND chat_id=? AND domain=?'
        versions = [dict(row) for row in db.execute('SELECT * FROM harness_versions WHERE ' + where,
                                                  scope)]
        for row in versions:
            row['evidence'], row['metadata'] = json.loads(row['evidence']), json.loads(row['metadata'])
            identity = [row[key] for key in ('project_id', 'chat_id', 'domain', 'parent_id', 'procedure')]
            identity.extend((_canonical(row['evidence']), row['source'], _canonical(row['metadata'])))
            if (hashlib.sha256(row['procedure'].encode()).hexdigest() != row['procedure_sha256']
                    or _digest(identity) != row['id']):
                raise ValueError('Version state failed its immutable identity check.')
            if (row['source'] == 'bootstrap-current' and row['procedure']
                    and row['evidence'].get('visibility') != 'public_fixture'):
                raise ValueError('A non-public existing procedure cannot enter a public report.')
        ids = {row['id'] for row in versions}
        decisions = []
        for source in db.execute('SELECT d.* FROM harness_decisions d JOIN harness_versions v ON d.version_id=v.id '
                                 'WHERE v.project_id=? AND v.chat_id=? AND v.domain=?', scope):
            row = dict(source)
            row['accepted'], row['evidence'] = bool(row['accepted']), json.loads(row['evidence'])
            if _digest([row['version_id'], row['accepted'], row['kind'], _canonical(row['evidence']), row['reason']]) != row['id']:
                raise ValueError('Version decision failed its immutable identity check.')
            decisions.append(row)
        result = {'versions': versions, 'decisions': decisions}
        for label, table in (('active', 'harness_active'), ('activation_events', 'harness_activation_events'),
                             ('applications', 'harness_applications')):
            result[label] = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' WHERE ' + where, scope)]
            if any(row.get('version_id') not in ids for row in result[label]):
                raise ValueError('Deployment state points outside this public scope.')
        return result
    finally:
        db.close()


def _learned_followups(evidence, scope, cycles, versions, cases, exposures):
    """Export completed learned applications, keeping their fixture/native labels."""
    parent = evidence.scope_root / 'learned-followups'
    if not parent.exists():
        return []
    evidence._path(parent)
    known = {item['journal']['id']: item for item in cycles}
    version_ids = {row['id']: row for row in versions['versions']}
    decisions = {row['id']: row for row in versions['decisions']}
    applications = {row['id']: row for row in versions['applications']}
    case_ids = {row['id']: row['payload'] for row in cases}
    reservations = {row['identity']: row for row in exposures}
    exported = []

    def require(condition, detail):
        if not condition:
            raise ValueError('Learned follow-up evidence is incomplete or inconsistent: ' + detail)

    for directory in sorted(parent.iterdir()):
        evidence._path(directory)
        require(directory.is_dir() and directory.name in known, 'selected cycle')
        for name in ('policy', 'identity', 'freeze', 'report'):
            require((directory / (name+'.json')).is_file(), 'completed follow-up directory')
        files = {name: evidence.json(directory / (name+'.json'))
                 for name in ('policy', 'identity', 'freeze', 'report')}
        policy, identity, freeze, report = (files[name] for name in ('policy', 'identity', 'freeze', 'report'))
        cycle = known[directory.name]['journal']
        version = version_ids.get(report.get('learned_version_id'))
        decision = decisions.get(report.get('learned_admission_decision_id'))
        require(version is not None and decision is not None, 'admitted version and decision')
        require(cycle['decision'].get('accepted') is True and decision['accepted'] is True
                and decision['kind'] == 'independent-evaluation' and version['source'] == 'harness2'
                and cycle['frozen']['candidate_id'] == version['id'] == cycle['decision'].get('candidate_id')
                and decision['version_id'] == version['id']
                and cycle['decision'].get('version_decision_id') == decision['id']
                and decision['evidence'].get('cycle_id') == directory.name
                and decision['evidence'].get('freeze_sha256') == _digest(cycle['frozen'])
                and decision['evidence'].get('evaluation_sha256') == _digest(known[directory.name]['files']['evaluation.json'])
                and cycle['frozen']['candidate_sha256'] == version['procedure_sha256'], 'independent cycle admission')
        require(policy.get('schema') == report.get('schema') == 'oak-learned-followup-v1'
                and policy.get('scope') == report.get('scope') == list(scope)
                and policy.get('cycle_id') == report.get('cycle_id') == directory.name
                and policy.get('model') == report.get('model') == 'gpt-6.1-sol'
                and policy.get('effort') == report.get('effort') == 'low'
                and policy.get('mode') == report.get('mode') in {'local-fixture', 'native-subscription'}
                and policy.get('version_id') == version['id']
                and policy.get('procedure_sha256') == version['procedure_sha256']
                and policy.get('admission_decision_id') == decision['id']
                and policy.get('admission_evidence') == report.get('learned_admission_evidence') == decision['evidence']
                and report.get('policy_sha256') == _digest(policy)
                and report.get('followup_source_sha256') == policy.get('source_sha256')
                and report.get('media_implementation_sha256') == policy.get('media_implementation', {}).get('sha256')
                and report.get('active_version_id') in version_ids
                and policy.get('quality_gain_claim') is False and report.get('quality_gain_claim') is False
                and report.get('live_provider_verification') is False, 'scope, policy and execution labels')
        try:
            recorded = datetime.fromisoformat(report['recorded_at'])
            require(recorded.tzinfo is not None, 'completion timestamp timezone')
            recorded = recorded.timestamp()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Learned follow-up completion timestamp is invalid.') from exc
        history = [row for row in versions['activation_events'] if row['created'] <= recorded]
        require(bool(history) and report['active_version_id'] ==
                max(history, key=lambda row: row['generation'])['version_id'], 'historical completion active version')
        preparations = []
        archive_root = directory / 'interrupted-preparations'
        if archive_root.exists() or archive_root.is_symlink():
            evidence._path(archive_root)
            require(archive_root.is_dir(), 'preparation archive directory')
            for archive in sorted(archive_root.iterdir()):
                evidence._path(archive)
                require(archive.is_dir() and re.fullmatch(r'preparation-[0-9]{4}', archive.name),
                        'preparation archive name')
                marker = archive / 'preparation.json'
                metadata = evidence.json(marker)
                ordinal = metadata.get('original_attempt')
                require(metadata.get('classification') == 'interrupted_preparation_before_intent'
                        and metadata.get('producer_started') is False and metadata.get('quality_failure_claim') is False
                        and metadata.get('policy_sha256') == _digest(policy)
                        and metadata.get('archive_name') == archive.name and type(ordinal) is int and ordinal >= 1
                        and metadata.get('original_attempt_path') == str(directory / 'attempts' / str(ordinal))
                        and isinstance(metadata.get('files'), dict), 'preparation archive classification')
                actual = {}
                for path in sorted(archive.rglob('*')):
                    evidence._path(path)
                    require(path.is_dir() or path.is_file(), 'preparation archive ordinary files')
                    if path.is_file() and path != marker:
                        actual[path.relative_to(archive).as_posix()] = path
                require(set(actual) == set(metadata['files'])
                        and not {'intent.json', 'native.json', 'receipt.json', 'evaluation.json'}.intersection(actual),
                        'preparation archive file identities')
                bindings = [evidence.hash_file(marker)]
                for relative, path in actual.items():
                    expected = metadata['files'][relative]
                    require(isinstance(expected, str) and re.fullmatch('[a-f0-9]{64}', expected),
                            'preparation archive file SHA')
                    bindings.append(evidence.hash_file(path, expected=expected))
                preparations.append({'name': archive.name, 'record': metadata, 'files': bindings})
        preparation_cap = policy.get('max_interrupted_preparations', 0)
        require(type(preparation_cap) is int and 0 <= preparation_cap <= 8
                and type(report.get('interrupted_preparation_count', 0)) is int
                and len(preparations) == report.get('interrupted_preparation_count', 0) <= preparation_cap,
                'bounded preparation archive count')
        descriptor = identity['descriptor']
        require(report.get('descriptor') == descriptor and descriptor.get('split') == 'followup'
                and freeze.get('descriptor_sha256') == _digest(descriptor)
                and freeze.get('policy_sha256') == _digest(policy)
                and freeze.get('admission_freeze_sha256') == _digest(cycle['frozen']), 'frozen follow-up identity')
        attempts = []
        attempts_root = directory / 'attempts'
        require(attempts_root.is_dir(), 'producer attempts')
        for attempt in sorted(attempts_root.iterdir()):
            evidence._path(attempt)
            require(attempt.is_dir() and re.fullmatch('[1-9][0-9]*', attempt.name), 'attempt number')
            require(all((attempt / (name+'.json')).is_file() for name in ('intent', 'task')), 'attempt intent and task')
            saved = {name: evidence.json(attempt / (name+'.json')) for name in ('intent', 'task')}
            intent, task = saved['intent'], saved['task']
            source_hashes = task.get('source_hashes', {})
            inputs = {Path(path).name: sha for path, sha in source_hashes.items()}
            commitment = {'descriptor_sha256': _digest(descriptor), 'inputs': inputs}
            commitment['sha256'] = _digest(commitment)
            pixels = [sha for name, sha in inputs.items() if name.endswith('.png') and not name.startswith('broken-')]
            require(intent.get('attempt') == int(attempt.name) and intent.get('task_sha256') == _digest(task)
                    and intent.get('descriptor_sha256') == _digest(descriptor)
                    and intent.get('policy_sha256') == _digest(policy)
                    and intent.get('version_id') == version['id']
                    and intent.get('procedure_sha256') == version['procedure_sha256']
                    and intent.get('source_commitment') == commitment
                    and intent.get('exposure_identity') == _digest(sorted(pixels or inputs.values()))
                    and intent.get('model') == policy['model'] and intent.get('effort') == policy['effort']
                    and intent.get('timeout') == policy.get('timeout')
                    and task.get('task_id') == descriptor['id'] and bool(inputs)
                    and len(inputs) == len(source_hashes), 'sealed attempt source and execution identity')
            for path in source_hashes:
                require(evidence._path(path).is_relative_to(attempt / 'workspace'), 'attempt source confinement')
            item = {'attempt': int(attempt.name), 'files': saved}
            unknown = attempt / 'interrupted-unknown.json'
            if unknown.exists():
                saved['interrupted-unknown'] = evidence.json(unknown)
                require(saved['interrupted-unknown'].get('intent_sha256') == evidence.index[(attempt/'intent.json').relative_to(evidence.root).as_posix()]
                        and saved['interrupted-unknown'].get('classification') == 'interrupted_unknown'
                        and saved['interrupted-unknown'].get('quality_failure_claim') is False, 'interrupted attempt binding')
            if (attempt / 'native.json').exists():
                require(all((attempt / (name+'.json')).is_file() for name in ('receipt', 'evaluation')), 'terminal attempt')
                native, evaluation = (evidence.json(attempt / (name+'.json')) for name in ('native', 'evaluation'))
                saved.update(native=native, evaluation=evaluation)
                item['producer'] = evidence.producer(attempt / 'receipt.json')
                record = item['producer']['record']
                require(record.get('state') == 'finished' and record.get('receipt') == native
                        and record.get('task') == task and record.get('source_commitment') == commitment
                        and record.get('descriptor_sha256') == _digest(descriptor)
                        and record.get('procedure_sha256') == version['procedure_sha256']
                        and record.get('model') == policy['model'] and record.get('effort') == policy['effort']
                        and record.get('timeout') == policy['timeout']
                        and evidence._path(record['run_dir']) == attempt
                        and evidence._path(record['workspace']) == attempt / 'workspace', 'terminal wrapper')
                for prefix in ('artifact', 'candidate'):
                    if native.get(prefix+'_path') is not None:
                        require(evidence._path(native[prefix+'_path']).is_relative_to(attempt), 'terminal artifact confinement')
                require(report.get('terminal_attempt') == int(attempt.name)
                        and report.get('native_receipt_sha256') == _digest(native)
                        and report.get('evaluation_sha256') == _digest(evaluation)
                        and report.get('evaluation') == evaluation
                        and report.get('artifact_sha256') == native.get('artifact_sha256')
                        and report.get('harness_application') == native.get('harness_application')
                        and report.get('source_commitment') == freeze.get('source_commitment') == commitment
                        and report.get('exposure_identity') == intent['exposure_identity']
                        and evaluation.get('freeze_sha256') == _digest(freeze)
                        and evaluation.get('procedure_sha256') == version['procedure_sha256'], 'terminal report hashes and freeze')
                if evaluation.get('delivered') is True:
                    require(evaluation.get('artifact_sha256') == native.get('artifact_sha256')
                            and re.fullmatch('[a-f0-9]{64}', str(native.get('artifact_sha256')))
                            and native.get('delivered') is True and record.get('terminal_integrity') == 'verified'
                            and native.get('status') == evaluation.get('status') == 'completed',
                            'delivered evaluation and native artifact SHA')
                if evaluation.get('joint_passed') is True:
                    require(evaluation.get('artifact_sha256') == native.get('artifact_sha256')
                            and re.fullmatch('[a-f0-9]{64}', str(native.get('artifact_sha256')))
                            and native.get('status') == evaluation.get('status') == 'completed'
                            and native.get('delivered') is True and record.get('terminal_integrity') == 'verified'
                            and native.get('model') == policy['model'] and native.get('effort') == policy['effort']
                            and evaluation.get('model_valid') is True and evaluation.get('budget_valid') is True
                            and evaluation.get('delivered') is True and evaluation.get('source_immutable') is True
                            and not evaluation.get('unverified')
                            and evaluation.get('visual_passed') is True and evaluation.get('mechanical_passed') is True,
                            'joint evaluation and delivered artifact SHA')
                from .harness_versions import HarnessVersions
                app = native.get('harness_application') or {}
                actual = applications.get(app.get('id'))
                fields = ('project_id', 'chat_id', 'domain', 'request_id', 'version_id',
                          'procedure_sha256', 'context_sha256', 'input_sha256', 'turn_id')
                applied = bool(actual is not None and all(actual[key] == app.get(key) for key in fields)
                            and tuple(app.get(key) for key in ('project_id','chat_id','domain')) == scope
                            and app.get('version_id') == version['id'] and bool(app.get('turn_id'))
                            and app.get('procedure_sha256') == version['procedure_sha256']
                            and app.get('context_sha256') == hashlib.sha256(HarnessVersions.application_context(
                                {**version, 'decision': decision}).encode()).hexdigest())
                require(type(report.get('application_provenance_verified')) is bool
                        and report['application_provenance_verified'] == applied, 'activation provenance')
                elapsed = native.get('elapsed_seconds')
                calls, reviews, renders = native.get('tool_calls', 0), native.get('reviews', []), native.get('render_calls', [])
                model_valid = native.get('model') == policy['model'] and native.get('effort') == policy['effort']
                budget_valid = (type(calls) is int and 0 <= calls <= 64
                    and isinstance(reviews, list) and len(reviews) <= 4
                    and isinstance(renders, list) and len(renders) <= 16
                    and type(elapsed) in (int, float) and elapsed >= 0)
                require(evaluation.get('model_valid') is model_valid and evaluation.get('budget_valid') is budget_valid,
                        'native model and tool-budget condition receipts')
                if evaluation.get('visual_passed') is True:
                    require(record.get('terminal_integrity') == 'verified'
                            and re.fullmatch('[a-f0-9]{64}', str(native.get('artifact_sha256')))
                            and any(isinstance(row, dict) and row.get('verdict') == 'pass'
                                    and row.get('sha256') == native.get('artifact_sha256') for row in reviews),
                            'same delivered SHA visual receipt')
                conditions = (record.get('terminal_integrity') == 'verified'
                    and evaluation.get('model_valid') is True and evaluation.get('budget_valid') is True
                    and type(elapsed) in (int, float) and math.isfinite(elapsed)
                    and 0 <= elapsed <= policy['timeout'])
                negative = (conditions and applied and evaluation.get('status') == 'completed'
                    and evaluation.get('delivered') is True and evaluation.get('source_immutable') is True
                    and native.get('source_immutable') is True
                    and not evaluation.get('unverified')
                    and evaluation.get('mechanical', {}).get('status') in {'pass', 'fail'}
                    and evaluation.get('joint_passed') is False)
                outcome = ('activation_unverified' if not applied else 'verified_quality_negative' if negative else
                    'joint_pass' if conditions and evaluation.get('joint_passed') else 'terminal_unverified_or_incomplete')
                require(type(report.get('native_conditions_verified')) is bool
                        and report['native_conditions_verified'] == conditions
                        and report.get('outcome') == outcome, 'derived native conditions and outcome')
                item['verified_quality_negative'] = negative
            else:
                require(unknown.is_file() and not (attempt/'receipt.json').exists()
                        and not (attempt/'evaluation.json').exists(), 'unknown attempt classification')
                item['source_files'] = [evidence.hash_file(path, expected=sha, allow_invalid=True)
                                        for path, sha in source_hashes.items()]
            attempts.append(item)
        require(len(attempts) == report.get('producer_attempts')
                and 1 <= len(attempts) <= policy.get('max_producer_attempts', 0)
                and sum('producer' in item for item in attempts) == 1
                and sum('interrupted-unknown' in item['files'] for item in attempts) == report.get('interrupted_unknown_attempts'),
                'complete attempt counts')
        terminal = next(item for item in attempts if 'producer' in item)
        exposure = reservations.get(report.get('exposure_identity'), {})
        require(exposure.get('identity') == report.get('exposure_identity')
                and exposure.get('scope') == _canonical(scope) and exposure.get('cycle_id') == directory.name
                and exposure.get('split') == 'followup'
                and isinstance(exposure.get('opened'), str) and bool(exposure['opened']),
                'scoped follow-up exposure reservation')
        monitoring = case_ids.get(report.get('monitoring_case_id'), {})
        require(monitoring.get('source') == 'learned-candidate-native-followup'
                and monitoring.get('cycle_id') == directory.name
                and monitoring.get('procedure_version') == version['id']
                and monitoring.get('execution_mode') == report['mode']
                and monitoring.get('native_receipt_sha256') == report['native_receipt_sha256']
                and monitoring.get('receipt_reference_sha256') == terminal['producer']['raw_receipt_sha256'], 'scoped monitoring case')
        rollback = directory / 'rollback.json'
        if rollback.exists():
            files['rollback'] = evidence.json(rollback)
            require(terminal['verified_quality_negative'] and report.get('rollback') == files['rollback']
                    and files['rollback'].get('from_version_id') == version['id']
                    and files['rollback'].get('to_version_id') == version['parent_id']
                    and files['rollback'].get('monitoring_case_id') == report['monitoring_case_id']
                    and files['rollback'].get('evaluation_sha256') == report['evaluation_sha256']
                    and any(row['action'] == 'rollback' and row['previous_id'] == version['id']
                            and row['version_id'] == version['parent_id']
                            and row['reason'] == 'Verified learned follow-up quality regression; monitoring case '
                                + report['monitoring_case_id'] for row in versions['activation_events']), 'trusted rollback')
        else:
            require(report.get('rollback') is None and not terminal['verified_quality_negative'], 'rollback receipt')
        receipts = [item.get('producer', {}).get('record', {}).get('receipt', {}) for item in attempts]
        exported.append({'cycle_id': directory.name, 'files': files, 'attempts': attempts,
            'preparation_archives': preparations, 'interrupted_preparation_count': len(preparations),
            'exposure_reservation': exposure,
            'evidence_class': 'recorded_learned_followup_' + report['mode'], 'cost_usd': None,
            'producer_cost_proxies': {'planned_tasks': 1, 'initiated_attempts': len(attempts),
                'elapsed_seconds': _stats([receipt.get('elapsed_seconds') for receipt in receipts]),
                'tool_calls': _stats([receipt.get('tool_calls') for receipt in receipts]),
                'recorded_review_attempts': sum(len(receipt.get('reviews', [])) for receipt in receipts)},
            'cost_note': 'Subscription USD is unmetered; this follow-up does not establish comparative quality gain.'})
    return exported


def export_report(root, project, chat, *, domain='media-depth'):
    """Return a portable report; no model, evaluation or account file is opened."""
    root = Path(root).resolve(strict=True)
    scope = (project, chat, domain)
    key = _canonical(scope)
    scope_root = root / 'scopes' / _digest(scope)
    evidence = _Evidence(root, scope_root)
    db = _connection(root / 'pipeline.sqlite3')
    try:
        cases = [dict(row) for row in db.execute('SELECT * FROM cases WHERE scope=? ORDER BY created,id', (key,))]
        if not cases:
            raise ValueError('There are no explicitly public synthetic cases in this scope.')
        for row in cases:
            row['payload'] = json.loads(row['payload'])
            if row['payload'].get('visibility') != 'public_fixture':
                raise ValueError('Public export is forbidden because this scope contains non-public cases.')
        raw_cycles = [dict(row) for row in db.execute('SELECT * FROM cycles WHERE scope=? ORDER BY ordinal', (key,))]
        if not raw_cycles or any(row['phase'] != 'completed' for row in raw_cycles):
            raise ValueError('Only completed cycles can be exported; unopened holdout material stays private.')
        cycles = []
        for row in raw_cycles:
            for name in ('config', 'manifest', 'frozen', 'search', 'decision'):
                row[name] = json.loads(row[name]) if row[name] is not None else None
            if not row['frozen'] or not row['decision']:
                raise ValueError('A completed cycle must contain its immutable freeze and decision.')
            directory = scope_root / 'cycles' / row['id']
            files = {}
            for name in ('parent.json', 'upstream-result.json', 'freeze.json', 'evaluation.json', 'decision.json'):
                files[name] = evidence.json(directory / name)
            approved_path = directory / 'approved-development-evidence.json'
            approved_bytes = evidence.bytes(approved_path) if approved_path.exists() else None
            if approved_bytes is not None:
                files['approved-development-evidence.json'] = json.loads(approved_bytes)
            attempt_history = files.get('approved-development-evidence.json', {}).get('attempt_history', {})
            history_usage = {'adapter_extension': bool(attempt_history),
                'prior_attempts_in_context': len(attempt_history.get('attempts', [])),
                'context_sha256': hashlib.sha256(approved_bytes).hexdigest() if approved_bytes is not None else None,
                'upstream_library_entries_before': row['search'].get('provenance', {}).get('library_entries_before', []),
                'upstream_library_entries_after': row['search'].get('provenance', {}).get('library_entries_after', []),
                'verified_context_copies': [], 'recorded_file_read_requests': [],
                'limitation': 'A recorded file-read request is evidence of requesting the copied context, not full page coverage or model comprehension. NO_ENTRY does not become an upstream Library entry.'}
            if (files['freeze.json'] != row['frozen'] or files['decision.json'] != row['decision']
                    or files['upstream-result.json'] != row['search']):
                raise ValueError('Cycle files differ from the durable journal.')
            frozen = row['frozen']
            if (frozen['manifest_sha256'] != _digest(row['manifest'])
                    or frozen['config_sha256'] != _digest(row['config'])
                    or frozen['search_sha256'] != _digest(row['search'])
                    or files['evaluation.json']['freeze_sha256'] != _digest(frozen)):
                raise ValueError('Cycle evidence no longer matches its frozen commitments.')
            producers = []
            for part in ('development', 'independent'):
                for path in sorted((directory / part / 'executions').glob('*/receipt.json')):
                    producers.append({'phase': part, **evidence.producer(path)})
                for path in sorted((directory / part / 'executions').glob('*/interrupted-*.json')):
                    producers.append({'phase': part, 'interrupted_attempt': True, **evidence.producer(path)})
            role_attempts = []
            for path in sorted((directory / 'roles').glob('*/attempt-*/receipt.json')):
                receipt = evidence.json(path)
                item = {'path': str(path.relative_to(root)), 'record': receipt,
                        'raw_receipt_sha256': evidence.index[path.relative_to(root).as_posix()]}
                copied_context = path.parent / 'context/source/OAK_CASE_EVIDENCE.json'
                if approved_bytes is not None and copied_context.exists():
                    if evidence.bytes(copied_context) != approved_bytes:
                        raise ValueError('A role received a different approved case/history context.')
                    history_usage['verified_context_copies'].append({'role': receipt.get('role'),
                        'attempt': receipt.get('attempt'), 'path': copied_context.relative_to(root).as_posix()})
                    for call in receipt.get('tool_calls', []):
                        if (call.get('tool') == 'oak_h2_read' and isinstance(call.get('path'), str)
                                and call['path'].removeprefix('context/') == 'source/OAK_CASE_EVIDENCE.json'):
                            history_usage['recorded_file_read_requests'].append({'role': receipt.get('role'),
                                'attempt': receipt.get('attempt'), 'path': call['path']})
                response = path.parent / 'response.txt'
                if response.exists():
                    raw = evidence.bytes(response)
                    if receipt.get('response_sha256') and hashlib.sha256(raw).hexdigest() != receipt['response_sha256']:
                        raise ValueError('A role response no longer matches its receipt.')
                    item['response'] = raw.decode('utf-8')
                harness = path.parent / 'context/harness'
                if harness.is_dir():
                    contents = {}
                    for file in sorted(harness.rglob('*')):
                        if file.is_file():
                            contents[file.relative_to(harness).as_posix()] = evidence.bytes(file)
                    file_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
                    if receipt.get('harness_sha256') and _digest(file_hashes) != receipt['harness_sha256']:
                        raise ValueError('A role harness no longer matches its recorded SHA.')
                    item['harness'] = {name: {'sha256': file_hashes[name], 'content': raw.decode('utf-8')}
                                       for name, raw in contents.items()}
                role_attempts.append(item)
            harnesses = []
            for path in sorted((directory / 'upstream/runs').glob('*/*/harnesses/*/AGENTS.md')):
                raw = evidence.bytes(path)
                harnesses.append({'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(),
                                  'content': raw.decode('utf-8')})
            operations = [dict(operation) for operation in db.execute(
                'SELECT * FROM operations WHERE scope=? AND cycle_id=? ORDER BY id', (key, row['id']))]
            for operation in operations:
                operation['identity'], operation['details'] = json.loads(operation['identity']), json.loads(operation['details'])
            counts = Counter((operation['kind'], operation['state']) for operation in operations)
            cycles.append({'journal': row, 'files': files, 'producer_records': producers,
                'authored_construction_controls': _construction_controls(row, evidence),
                'role_attempts': role_attempts, 'harnesses': harnesses, 'operations': operations,
                'attempt_history_usage': history_usage,
                'operation_counts': [{'kind': kind, 'state': state, 'count': count}
                                     for (kind, state), count in sorted(counts.items())],
                'metrics': _metrics(files['evaluation.json']['pairs']),
                'producer_cost_proxies': _producer_costs(producers),
                'role_cost_proxies': {'attempt_records': len(role_attempts),
                    'elapsed_seconds': _stats([item['record'].get('elapsed_seconds') for item in role_attempts]),
                    'recorded_tools': sum(len(item['record'].get('tool_calls', [])) for item in role_attempts),
                    'cost_usd': None},
                'attempt_counts': {'producer_records': len(producers),
                    'producer_interrupted': sum(item.get('interrupted_attempt', False) for item in producers),
                    'role_attempts': len(role_attempts),
                    'role_statuses': dict(Counter(item['record'].get('status', 'missing') for item in role_attempts))}})
        feedback = [dict(row) for row in db.execute('SELECT * FROM feedback WHERE scope=? ORDER BY created,id', (key,))]
        for row in feedback:
            row['value'] = json.loads(row['value'])
        events = [dict(row) for row in db.execute('SELECT * FROM events WHERE scope=? ORDER BY id', (key,))]
        for row in events:
            row['value'] = json.loads(row['value'])
        queue = [dict(row) for row in db.execute('SELECT * FROM queue WHERE scope=? ORDER BY priority DESC,id', (key,))]
        exposures = [dict(row) for row in db.execute('SELECT * FROM exposure WHERE scope=?', (key,))]
    finally:
        db.close()
    activation = None
    demo = root / 'activation-demo'
    if (demo / 'config.json').is_file():
        # Read only this designated administrative fixture's config before
        # admitting its directory to the selected report's evidence boundary.
        if any(path.is_symlink() for path in (demo, demo / 'config.json')):
            raise ValueError('Controlled fixture configuration links are forbidden.')
        if (demo / 'config.json').stat().st_size > 65536:
            raise ValueError('Controlled fixture configuration exceeds its fixed bound.')
        config = json.loads((demo / 'config.json').read_text())
        if (config.get('project'), config.get('chat_id'), config.get('domain')) == scope:
            if not (demo / 'report.json').is_file():
                raise ValueError('The selected activation fixture has not completed.')
            evidence.allowed.append(demo)
            activation = {'config': evidence.json(demo / 'config.json'),
                          'report': evidence.json(demo / 'report.json'), 'files': [], 'followup_receipts': []}
            for name in ('identity.json', 'rollback.json', 'bad-candidate-gate.json'):
                if (demo / name).is_file():
                    activation['files'].append({'path': name, 'record': evidence.json(demo / name)})
            for path in sorted((demo / 'attempts').glob('*/*/*.json')):
                if path.name not in {'intent.json', 'receipt.json', 'interrupted-unknown.json'}:
                    continue
                if path.name == 'receipt.json':
                    # Administrative followups have the same bound artifact
                    # fields as native producer receipts, without a task record.
                    item = evidence.producer(path)
                    if item['record'].get('receipt_sha256') != _digest(item['record'].get('receipt')):
                        raise ValueError('Controlled followup receipt identity changed.')
                    activation['followup_receipts'].append(item)
                else:
                    activation['files'].append({'path': path.relative_to(demo).as_posix(),
                                                'record': evidence.json(path)})
    versions = _version_state(root, scope)
    learned_followups = _learned_followups(evidence, scope, cycles, versions, cases, exposures)
    report = {'schema': 'oak-harness2-public-evidence-v1', 'scope': scope,
              'upstream_commit': '1cfe254e8d25b15f37048c3730134686abe60335',
              'model': 'gpt-6.1-sol', 'effort': 'low', 'authentication': 'configured_existing_chatgpt_subscription',
              'cases': cases, 'feedback': feedback, 'queue': queue, 'events': events,
              'cycles': cycles, 'versions': versions, 'controlled_activation': activation,
              'learned_followups': learned_followups,
              'original_file_sha256': evidence.index, 'cost_usd': None,
              'cost_note': 'Upstream cost_usd=0 is an adapter compatibility value, not measured zero cost. Calls, tools and elapsed time are cost proxies.',
              'limitations': ['Native media checks do not verify external provider access.',
                  'Repeated executions of one example are not independent tasks.',
                  'Mechanical property scores do not establish visual quality.',
                  'Controlled activation decisions do not establish optimizer quality improvement.',
                  'Infrastructure interruptions and failed terminal attempts remain in the evidence.'],
              'broad_quality_improvement_claim': False}
    report['live_provider_verification'] = False
    report['execution_attestation_limit'] = 'The exporter reads preserved receipts. It neither executes models nor independently attests the transport; local fixture runs must retain their fixture labels.'
    portable = _portable(report, root)
    # Original hashes verify local source bytes. This separate hash commits the
    # sanitized document's content, excluding this self-referential field.
    portable['portable_content_sha256'] = _digest(portable)
    return portable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--chat', type=int, required=True)
    parser.add_argument('--domain', default='media-depth')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = export_report(args.root, args.project, args.chat, domain=args.domain)
    target = Path(args.output)
    if target.suffix != '.json' or target.is_symlink():
        raise ValueError('Use an ordinary JSON destination.')
    if target.resolve().is_relative_to(Path(args.root).resolve()):
        raise ValueError('Export outside the private pipeline so original evidence cannot be overwritten.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.chmod(0o600)
    temporary.replace(target)
    print(json.dumps({'output': str(target), 'cycles': len(report['cycles']),
                      'portable_content_sha256': report['portable_content_sha256']}))


if __name__ == '__main__':
    main()
