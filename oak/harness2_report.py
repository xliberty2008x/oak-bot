"""Export completed public-fixture Harness² evidence without private account data.

This is a read-only evidence exporter, not an evaluator or a model client.
All selected cases must explicitly be public fixtures, and every selected cycle
must be completed before its manifest can be published. Original file hashes
refer to private receipts before portable path substitution.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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

    def bytes(self, value, *, maximum=64 * 1024 * 1024):
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
                'role_attempts': role_attempts, 'harnesses': harnesses, 'operations': operations,
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
    report = {'schema': 'oak-harness2-public-evidence-v1', 'scope': scope,
              'upstream_commit': '1cfe254e8d25b15f37048c3730134686abe60335',
              'model': 'gpt-6.1-sol', 'effort': 'low', 'authentication': 'configured_existing_chatgpt_subscription',
              'cases': cases, 'feedback': feedback, 'queue': queue, 'events': events,
              'cycles': cycles, 'versions': versions, 'controlled_activation': activation,
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
