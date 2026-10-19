"""Apply an independently accepted learned procedure to one fresh native task.

Admission is never manufactured here. This separate, bounded follow-up proves
actual Controller activation, independently checks the delivered bytes, records
scoped monitoring, and rolls back a verified terminal quality regression. Its
two-attempt budget is separate from the sealed optimizer/comparison budget.
The isolated media runtime currently supports conversation 1 only.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid
from datetime import datetime

from .harness2_pipeline import (BudgetStopped, MODEL, PipelineStore, canonical, digest,
                                file_sha, input_identity, now, write_once)
from .harness_versions import HarnessVersions

_MAX_INTERRUPTED_PREPARATIONS = 8


class FollowupInterrupted(RuntimeError):
    """A producer intent has no terminal receipt; it is not a quality verdict."""


def _load(path):
    return json.loads(Path(path).read_text())


def _native(task, workspace, activation, *, runtime_home, timeout):
    from .media_pilot_runtime import PilotRuntime
    runtime = PilotRuntime({'harness_activation': activation}, runtime_home=runtime_home,
                           model=MODEL, effort='low', timeout=timeout)
    return asyncio.run(runtime.run(task, workspace))


def _admission(store, versions, cycle_id):
    cycle = store.cycle(cycle_id)
    decision, frozen = cycle.get('decision') or {}, cycle.get('frozen') or {}
    if cycle['phase'] != 'completed' or decision.get('accepted') is not True:
        raise ValueError('Follow-up requires a completed independently accepted cycle.')
    version = versions.version(frozen.get('candidate_id'))
    receipt = versions.decision(version['id'])
    if (version['source'] != 'harness2' or not receipt or not receipt['accepted']
            or receipt['kind'] != 'independent-evaluation'
            or receipt['evidence'].get('cycle_id') != cycle_id
            or decision.get('candidate_id') != version['id']
            or decision.get('version_decision_id') != receipt['id']
            or frozen.get('candidate_sha256') != version['procedure_sha256']
            or receipt['evidence'].get('freeze_sha256') != digest(frozen)
            or cycle['config'].get('model') != MODEL or cycle['config'].get('effort') != 'low'
            or tuple(version[k] for k in ('project_id', 'chat_id', 'domain')) != store.scope):
        raise ValueError('Learned version, scope and independent admission evidence do not agree.')
    return cycle, version, receipt


def _application(receipt, versions, expected):
    app = receipt.get('harness_application') or {}
    saved = next((row for row in versions.applications(expected['project_id'], expected['chat_id'], expected['domain'])
                  if row['id'] == app.get('id')), None)
    version = {**expected, 'decision': versions.decision(expected['id'])}
    context_sha = hashlib.sha256(versions.application_context(version).encode()).hexdigest()
    fields = ('project_id', 'chat_id', 'domain', 'request_id', 'version_id',
              'procedure_sha256', 'context_sha256', 'input_sha256', 'turn_id')
    return bool(saved and all(saved[key] == app.get(key) for key in fields)
                and app.get('version_id') == expected['id'] and app.get('turn_id')
                and app.get('procedure_sha256') == expected['procedure_sha256']
                and app.get('context_sha256') == context_sha)


def _integrity(record):
    """Validate persisted native evidence without rerunning its producer."""
    base = Path(record['run_dir']).resolve()
    task, receipt = record['task'], record['receipt']
    try:
        if any(file_sha(path) != sha for path, sha in task['source_hashes'].items()):
            return 'failed'
        for prefix in ('artifact', 'candidate'):
            path, sha = receipt.get(prefix + '_path'), receipt.get(prefix + '_sha256')
            if path is None and sha is None:
                continue
            if (not isinstance(path, str) or not isinstance(sha, str) or not Path(path).is_absolute()
                    or not Path(path).resolve().is_relative_to(base) or Path(path).is_symlink()
                    or file_sha(path) != sha):
                return 'failed'
    except (OSError, ValueError, TypeError):
        return 'failed'
    return 'verified'


def _record_bindings(record, native, task, intent, descriptor, version, policy, attempt):
    expected = {'state': 'finished', 'run_id': 'learned-followup-' + policy['cycle_id'],
        'run_dir': str(attempt), 'workspace': str(attempt / 'workspace'), 'task': task,
        'source_commitment': intent['source_commitment'], 'descriptor_sha256': digest(descriptor),
        'procedure_sha256': version['procedure_sha256'], 'model': MODEL, 'effort': 'low',
        'timeout': policy['timeout'], 'receipt': native}
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError('Terminal wrapper differs from the immutable native receipt or execution identity.')
    if (intent.get('policy_sha256') != digest(policy) or intent.get('descriptor_sha256') != digest(descriptor)
            or intent.get('task_sha256') != digest(task) or intent.get('version_id') != version['id']
            or intent.get('procedure_sha256') != version['procedure_sha256']
            or intent.get('model') != MODEL or intent.get('effort') != 'low'
            or intent.get('timeout') != policy['timeout']):
        raise ValueError('Persisted task and producer intent differ from their sealed identity.')
    workspace = (attempt / 'workspace').resolve()
    if any(not Path(path).is_absolute() or Path(path).is_symlink()
           or not Path(path).resolve().is_relative_to(workspace) for path in task['source_hashes']):
        raise ValueError('Follow-up task sources escaped their committed workspace.')
    if record['terminal_integrity'] == 'verified':
        from .harness2_media import source_commitment
        if source_commitment(descriptor, task) != intent['source_commitment']:
            raise ValueError('Task source commitment differs from its initial execution intent.')


def _fixture_label(value):
    if isinstance(value, dict):
        return (value.get('fixture_only') is True
                or any(value.get(key) in ('local-fixture', 'controlled-fixture')
                       for key in ('mode', 'execution_mode', 'source', 'kind'))
                or any(_fixture_label(item) for item in value.values()))
    return isinstance(value, (list, tuple)) and any(_fixture_label(item) for item in value)


def _archive_preparation(directory, attempt, policy):
    """No intent means native dispatch was impossible; retain and rebuild safely."""
    if not attempt.exists():
        return
    if attempt.is_symlink() or not attempt.is_dir() or (attempt / 'native.json').exists():
        raise ValueError('An orphan preparation contains uncertain native evidence.')
    archive = directory / 'interrupted-preparations'
    if archive.is_symlink():
        raise ValueError('Preparation archives cannot be links.')
    archive.mkdir(exist_ok=True, mode=0o700)
    previous = list(archive.iterdir())
    if len(previous) >= policy['max_interrupted_preparations']:
        raise BudgetStopped('Interrupted preparation archive cap exhausted; evidence is retained.')
    marker = attempt / 'preparation.json'
    if marker.exists():
        metadata = _load(marker)
    else:
        files = {}
        for path in attempt.rglob('*'):
            if path.is_symlink():
                raise ValueError('Preparation evidence cannot contain links.')
            if path.is_file():
                files[path.relative_to(attempt).as_posix()] = file_sha(path)
        metadata = {'classification': 'interrupted_preparation_before_intent',
            'original_attempt': int(attempt.name), 'original_attempt_path': str(attempt),
            'policy_sha256': digest(policy), 'archive_name': 'preparation-' + str(len(previous)+1).zfill(4),
            'files': files, 'archived_at': now(), 'producer_started': False, 'quality_failure_claim': False}
        write_once(marker, metadata)
    if (metadata.get('original_attempt_path') != str(attempt) or metadata.get('policy_sha256') != digest(policy)
            or not re.fullmatch(r'preparation-[0-9]{4}', str(metadata.get('archive_name')))):
        raise ValueError('Interrupted preparation metadata changed.')
    target = archive / metadata['archive_name']
    if target.exists() or target.is_symlink():
        raise ValueError('An interrupted preparation archive already exists.')
    attempt.replace(target)


def _classification(record, evaluation, applied, timeout):
    receipt = record['receipt']
    elapsed = receipt.get('elapsed_seconds')
    conditions = (record['terminal_integrity'] == 'verified'
        and evaluation.get('model_valid') is True and evaluation.get('budget_valid') is True
        and type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= timeout)
    same_artifact = (evaluation.get('artifact_sha256') == receipt.get('artifact_sha256')
                     and re.fullmatch('[a-f0-9]{64}', str(receipt.get('artifact_sha256'))) is not None)
    if evaluation.get('joint_passed') is True and not same_artifact:
        raise ValueError('A joint follow-up verdict is not bound to its delivered artifact.')
    conclusive = (conditions and applied
        and evaluation.get('status') == 'completed' and evaluation.get('delivered') is True
        and evaluation.get('source_immutable') is True and not evaluation.get('unverified')
        and evaluation.get('mechanical', {}).get('status') in {'pass', 'fail'})
    if conclusive and not same_artifact:
        raise ValueError('A conclusive follow-up verdict is not bound to its delivered artifact.')
    negative = (conclusive and same_artifact and evaluation.get('joint_passed') is False
                and receipt.get('status') == 'completed' and receipt.get('delivered') is True
                and receipt.get('source_immutable') is True)
    outcome = ('activation_unverified' if not applied else 'verified_quality_negative' if negative else
               'joint_pass' if conditions and evaluation.get('joint_passed') else 'terminal_unverified_or_incomplete')
    return conditions, negative, outcome


def _report_fields(policy, version, admission, descriptor, intent, receipt, evaluation, applied, conditions, outcome):
    return {'schema': 'oak-learned-followup-v1', 'scope': policy['scope'], 'cycle_id': policy['cycle_id'],
        'mode': policy['mode'], 'model': MODEL, 'effort': 'low', 'policy_sha256': digest(policy),
        'followup_source_sha256': policy['source_sha256'], 'media_implementation_sha256': policy['media_implementation']['sha256'],
        'learned_version_id': version['id'], 'learned_admission_decision_id': admission['id'],
        'learned_admission_evidence': admission['evidence'], 'descriptor': descriptor,
        'source_commitment': intent['source_commitment'], 'exposure_identity': intent['exposure_identity'],
        'application_provenance_verified': applied, 'harness_application': receipt.get('harness_application'),
        'native_receipt_sha256': digest(receipt), 'evaluation_sha256': digest(evaluation),
        'artifact_sha256': receipt.get('artifact_sha256'), 'evaluation': evaluation,
        'outcome': outcome, 'native_conditions_verified': conditions, 'terminal_attempt': intent['attempt'],
        'nominal_followup_tasks': 1, 'quality_gain_claim': False, 'live_provider_verification': False}


def _cached_report(report, directory, store, versions, policy, version, admission, descriptor, intent, record, evaluation):
    applied = _application(record['receipt'], versions, version)
    conditions, negative, outcome = _classification(record, evaluation, applied, policy['timeout'])
    expected = _report_fields(policy, version, admission, descriptor, intent, record['receipt'],
                              evaluation, applied, conditions, outcome)
    attempts = directory / 'attempts'
    expected.update(producer_attempts=len(list(attempts.glob('*/intent.json'))),
        interrupted_unknown_attempts=len(list(attempts.glob('*/interrupted-unknown.json'))),
        interrupted_preparation_count=len(list((directory/'interrupted-preparations').glob('preparation-*'))))
    if any(canonical(report.get(key)) != canonical(value) for key, value in expected.items()):
        raise ValueError('Cached follow-up report differs from its original evidence or execution labels.')
    case = next((row for row in store.cases() if row['id'] == report.get('monitoring_case_id')), None)
    payload = case['payload'] if case else {}
    if (payload.get('source') != 'learned-candidate-native-followup'
            or payload.get('native_receipt_sha256') != digest(record['receipt'])
            or payload.get('receipt_reference_sha256') != file_sha(attempts/str(intent['attempt'])/'receipt.json')
            or payload.get('execution_mode') != policy['mode'] or payload.get('procedure_version') != version['id']
            or payload.get('cycle_id') != policy['cycle_id']):
        raise ValueError('Cached follow-up monitoring reference changed.')
    rollback = directory / 'rollback.json'
    if negative:
        saved = _load(rollback) if rollback.is_file() else None
        wanted = {'from_version_id': version['id'], 'to_version_id': version['parent_id'],
                  'monitoring_case_id': report['monitoring_case_id'], 'evaluation_sha256': digest(evaluation)}
        if (saved != wanted or report.get('rollback') != saved or not any(
                row['action'] == 'rollback' and row['previous_id'] == version['id']
                and row['version_id'] == version['parent_id']
                and row['reason'] == 'Verified learned follow-up quality regression; monitoring case ' + report['monitoring_case_id']
                for row in versions.activation_history(*store.scope))):
            raise ValueError('Cached follow-up rollback lacks its verified negative and archived event.')
    elif report.get('rollback') is not None or rollback.exists():
        raise ValueError('A non-negative follow-up cannot claim a rollback.')
    recorded = datetime.fromisoformat(report['recorded_at']).timestamp()
    history = [row for row in versions.activation_history(*store.scope) if row['created'] <= recorded]
    active_at_completion = max(history, key=lambda row: row['generation'])['version_id'] if history else None
    if report.get('active_version_id') != active_at_completion:
        raise ValueError('Cached active version differs from the recorded completion history.')


def _reserve_exposure(store, cycle_id, descriptor, commitment):
    identity = input_identity({'descriptor': descriptor, 'commitment': commitment})
    with store.lock, store.db:
        previous = store.db.execute('SELECT cycle_id,split FROM exposure WHERE scope=? AND identity=?',
                                    (store.key, identity)).fetchone()
        if previous and (previous['cycle_id'] != cycle_id or previous['split'] != 'followup'):
            raise ValueError('Follow-up source bytes were already exposed in another role.')
        store.db.execute('INSERT OR IGNORE INTO exposure VALUES(?,?,?,?,?)',
                         (identity, store.key, cycle_id, 'followup', now()))
    return identity


def _monitor(store, version, cycle_id, descriptor, record, evaluation, reference, mode, applied):
    outcome = {key: evaluation.get(key) for key in ('status', 'delivered', 'visual_passed', 'mechanical_passed',
        'joint_passed', 'source_immutable', 'unverified', 'tool_calls', 'elapsed_seconds', 'terminal_integrity')}
    payload = {'visibility': 'public_fixture', 'source': 'learned-candidate-native-followup',
        'execution_mode': mode, 'model_backed': mode == 'native-subscription',
        'cycle_id': cycle_id, 'task_id': descriptor['id'], 'procedure_version': version['id'],
        'artifact_reference': str(reference), 'receipt_reference_sha256': file_sha(reference),
        'native_receipt_sha256': digest(record['receipt']), 'artifact_sha256': record['receipt'].get('artifact_sha256'),
        'application_provenance_verified': applied,
        'result': outcome, 'failure_reason': (None if evaluation.get('joint_passed') and applied else
            'application_provenance_unverified' if not applied else 'followup_joint_delivery_failed_or_unverified'),
        'quality_gain_claim': False}
    return store.record_case(payload, signature='source-preserving-depth', approved=False,
                             priority=10 if evaluation.get('joint_passed') else 100)


def run_followup(root, *, project, chat, domain, cycle_id, runtime_home=None, timeout=600,
                 max_attempts=2, runner=None, evaluator=None, materializer=None):
    """Execute once after real learned admission; local seams never claim model evidence."""
    if chat != 1 or type(chat) is not int:
        raise ValueError('The isolated PilotRuntime follow-up supports exact conversation scope 1 only.')
    if type(timeout) is not int or not 30 <= timeout <= 600 or type(max_attempts) is not int or not 1 <= max_attempts <= 2:
        raise ValueError('Use a finite 30–600s timeout and at most two producer attempts.')
    if runner is None and (evaluator is not None or materializer is not None):
        raise ValueError('Custom evaluation/materialization is available only for explicitly local runner fixtures.')
    from .harness2_media import (evaluator_fingerprint, evaluate_delivery, make_descriptor,
                                 materialize_descriptor, source_commitment)
    mode = 'local-fixture' if runner is not None else 'native-subscription'
    runner, evaluator = runner or _native, evaluator or evaluate_delivery
    materializer = materializer or materialize_descriptor
    store = PipelineStore(root, project, chat, domain)
    versions = HarnessVersions(store.root / 'versions.sqlite3')
    try:
        with (store.root / 'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Another worker owns this pipeline root.') from exc
            cycle, version, admission = _admission(store, versions, cycle_id)
            if mode == 'native-subscription' and _fixture_label((cycle, version, admission)):
                raise ValueError('Native learned follow-up cannot use an explicitly fixture-only admission.')
            directory = store.root / 'scopes' / digest(store.scope) / 'learned-followups' / cycle_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            policy = {'schema': 'oak-learned-followup-v1', 'scope': store.scope, 'cycle_id': cycle_id,
                'version_id': version['id'], 'procedure_sha256': version['procedure_sha256'],
                'admission_decision_id': admission['id'], 'admission_evidence': admission['evidence'],
                'model': MODEL, 'effort': 'low', 'timeout': timeout, 'max_producer_attempts': max_attempts,
                'max_interrupted_preparations': _MAX_INTERRUPTED_PREPARATIONS,
                'mode': mode, 'source_sha256': file_sha(__file__),
                'media_implementation': evaluator_fingerprint(), 'quality_gain_claim': False,
                'rollback_rule': 'Only a terminal completed, integrity-verified, source-immutable, model/budget-valid delivered artifact with conclusive independent quality failure may roll back. Unknown infrastructure evidence cannot.'}
            # JSON comparison must see the same sequence type after restart.
            policy = json.loads(canonical(policy))
            write_once(directory / 'policy.json', policy)
            report_path = directory / 'report.json'
            if report_path.exists():
                report = _load(report_path)
                if report['policy_sha256'] != digest(policy):
                    raise ValueError('Cached follow-up policy changed.')
                terminal = directory / 'attempts' / str(report['terminal_attempt'])
                native = _load(terminal / 'native.json')
                record = _load(terminal / 'receipt.json')
                descriptor = _load(directory / 'identity.json')['descriptor']
                intent = _load(terminal / 'intent.json')
                _record_bindings(record, native, _load(terminal / 'task.json'), intent,
                                 descriptor, version, policy, terminal)
                freeze = _load(directory / 'freeze.json')
                evaluation = _load(terminal / 'evaluation.json')
                if (digest(native) != report['native_receipt_sha256']
                        or digest(evaluation) != report['evaluation_sha256']
                        or evaluation.get('freeze_sha256') != digest(freeze)
                        or freeze['policy_sha256'] != digest(policy)
                        or freeze['descriptor_sha256'] != digest(descriptor)
                        or freeze['source_commitment'] != record['source_commitment']
                        or freeze['admission_freeze_sha256'] != admission['evidence']['freeze_sha256']
                        or report['descriptor'] != descriptor or report['source_commitment'] != record['source_commitment']
                        or _integrity(record) != record['terminal_integrity']
                        or bool(report['application_provenance_verified']) != _application(native, versions, version)):
                    raise ValueError('Completed follow-up evidence changed; producer will not be replayed.')
                _cached_report(report, directory, store, versions, policy, version, admission,
                               descriptor, intent, record, evaluation)
                return report
            if store.setting('stopped'):
                raise BudgetStopped('Pipeline stopped before a learned follow-up.')
            current = versions.resolve(*store.scope)
            terminal_exists = any((directory / 'attempts').glob('*/native.json'))
            if (current is None or current['id'] != version['id']) and not terminal_exists:
                raise ValueError('Accepted learned candidate must be active before a new follow-up.')
            identity_path = directory / 'identity.json'
            if identity_path.exists():
                identity = _load(identity_path)
            else:
                namespace = 'learned-followup-' + uuid.uuid4().hex
                identity = {'descriptor': make_descriptor(namespace, 1, split='followup'), 'namespace': namespace}
                write_once(identity_path, identity)
            descriptor = identity['descriptor']
            attempts_root = directory / 'attempts'
            attempts_root.mkdir(exist_ok=True, mode=0o700)
            attempts = sorted(attempts_root.glob('*/intent.json'))
            attempt = next((path.parent for path in attempts if (path.parent / 'native.json').exists()), None)
            if attempt is None:
                for intent in attempts:
                    marker = intent.parent / 'interrupted-unknown.json'
                    if not marker.exists():
                        write_once(marker, {'classification': 'interrupted_unknown', 'intent_sha256': file_sha(intent),
                            'observed_at': now(), 'quality_failure_claim': False})
                if len(attempts) >= max_attempts:
                    raise BudgetStopped('Learned follow-up producer budget exhausted; unknown intents remain recorded.')
                attempt = attempts_root / str(len(attempts) + 1)
                _archive_preparation(directory, attempt, policy)
                attempt.mkdir(exist_ok=True, mode=0o700)
                workspace = attempt / 'workspace'
                workspace.mkdir(exist_ok=True, mode=0o700)
                task = materializer(descriptor, workspace)
                commitment = source_commitment(descriptor, task)
                exposure = _reserve_exposure(store, cycle_id, descriptor, commitment)
                write_once(attempt / 'task.json', task)
                intent = {'attempt': len(attempts) + 1, 'descriptor_sha256': digest(descriptor),
                    'task_sha256': digest(task),
                    'source_commitment': commitment, 'exposure_identity': exposure,
                    'version_id': version['id'], 'procedure_sha256': version['procedure_sha256'],
                    'policy_sha256': digest(policy), 'model': MODEL, 'effort': 'low', 'timeout': timeout,
                    'started_at': time.time()}
                write_once(attempt / 'intent.json', intent)
                try:
                    receipt = runner(task, workspace, {'store_path': str(store.root / 'versions.sqlite3'),
                        'project_id': project, 'domain': domain}, runtime_home=runtime_home, timeout=timeout)
                except Exception as exc:
                    write_once(attempt / 'interrupted-unknown.json', {'classification': 'interrupted_unknown',
                        'intent_sha256': file_sha(attempt / 'intent.json'), 'error_class': type(exc).__name__,
                        'quality_failure_claim': False, 'observed_at': now()})
                    raise FollowupInterrupted('Native follow-up has no terminal receipt; resume only within its remaining attempt budget.') from None
                # Retain terminal native results before any grading/monitoring.
                write_once(attempt / 'native.json', receipt)
            receipt = _load(attempt / 'native.json')
            task, intent = _load(attempt / 'task.json'), _load(attempt / 'intent.json')
            if intent['policy_sha256'] != digest(policy) or intent['descriptor_sha256'] != digest(descriptor):
                raise ValueError('Persisted producer identity differs from this sealed follow-up.')
            record = {'state': 'finished', 'run_id': 'learned-followup-' + cycle_id, 'run_dir': str(attempt),
                'workspace': str(attempt / 'workspace'), 'task': task, 'source_commitment': intent['source_commitment'],
                'descriptor_sha256': digest(descriptor), 'procedure_sha256': version['procedure_sha256'],
                'model': MODEL, 'effort': 'low', 'timeout': timeout, 'receipt': receipt}
            record['terminal_integrity'] = _integrity(record)
            _record_bindings(record, receipt, task, intent, descriptor, version, policy, attempt)
            marker = attempt / 'receipt.json'
            write_once(marker, record)
            freeze = {'policy_sha256': digest(policy), 'admission_freeze_sha256': admission['evidence']['freeze_sha256'],
                      'descriptor_sha256': digest(descriptor), 'source_commitment': intent['source_commitment']}
            write_once(directory / 'freeze.json', freeze)
            evaluation_path = attempt / 'evaluation.json'
            if evaluation_path.exists():
                evaluation = _load(evaluation_path)
            else:
                evaluation = evaluator(descriptor, attempt, freeze_sha256=digest(freeze))
                write_once(evaluation_path, evaluation)
            if evaluation.get('freeze_sha256') != digest(freeze) or evaluation.get('procedure_sha256') != version['procedure_sha256']:
                raise ValueError('Independent follow-up evaluation lacks its frozen procedure binding.')
            applied = _application(receipt, versions, version)
            case_id = _monitor(store, version, cycle_id, descriptor, record, evaluation, marker, mode, applied)
            conditions, negative, outcome = _classification(record, evaluation, applied, timeout)
            rollback = None
            if negative:
                rollback_path = directory / 'rollback.json'
                if rollback_path.exists():
                    rollback = _load(rollback_path)
                else:
                    current = versions.resolve(*store.scope)
                    reason = 'Verified learned follow-up quality regression; monitoring case ' + case_id
                    prior = next((row for row in versions.activation_history(*store.scope)
                        if row['action'] == 'rollback' and row['previous_id'] == version['id']
                        and row['version_id'] == version['parent_id'] and row['reason'] == reason), None)
                    if prior and current['id'] == version['parent_id']:
                        previous = current
                    else:
                        previous = versions.rollback(*store.scope, target_id=version['parent_id'], expected_active=version['id'],
                            reason=reason)
                    rollback = {'from_version_id': version['id'], 'to_version_id': previous['id'],
                                'monitoring_case_id': case_id, 'evaluation_sha256': digest(evaluation)}
                    write_once(rollback_path, rollback)
            report = {**_report_fields(policy, version, admission, descriptor, intent, receipt,
                                      evaluation, applied, conditions, outcome),
                'monitoring_case_id': case_id, 'rollback': rollback, 'recorded_at': now(),
                'active_version_id': versions.resolve(*store.scope)['id'],
                'producer_attempts': len(list(attempts_root.glob('*/intent.json'))),
                'interrupted_unknown_attempts': len(list(attempts_root.glob('*/interrupted-unknown.json'))),
                'interrupted_preparation_count': len(list((directory/'interrupted-preparations').glob('preparation-*')))}
            write_once(report_path, report)
            return report
    finally:
        versions.close()
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--chat', type=int, required=True)
    parser.add_argument('--domain', default='media-depth')
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--runtime-home')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--max-attempts', type=int, default=2)
    args = parser.parse_args(argv)
    try:
        report = run_followup(args.root, project=args.project, chat=args.chat, domain=args.domain,
            cycle_id=args.cycle_id, runtime_home=args.runtime_home, timeout=args.timeout, max_attempts=args.max_attempts)
    except FollowupInterrupted:
        print(canonical({'outcome': 'interrupted_unknown', 'quality_failure_claim': False}))
        raise SystemExit(2)
    except BudgetStopped:
        print(canonical({'outcome': 'stopped_or_budget_exhausted', 'quality_failure_claim': False}))
        raise SystemExit(2)
    except ValueError:
        print(canonical({'outcome': 'followup_refused', 'error_code': 'admission_or_evidence_validation_failed',
                         'quality_failure_claim': False}))
        raise SystemExit(1)
    except Exception as exc:
        print(canonical({'outcome': 'followup_processing_error', 'error_class': type(exc).__name__,
                         'quality_failure_claim': False}))
        raise SystemExit(1)
    print(canonical({key: report[key] for key in ('cycle_id', 'learned_version_id', 'outcome',
        'application_provenance_verified', 'monitoring_case_id', 'rollback', 'producer_attempts', 'quality_gain_claim')}))


if __name__ == '__main__':
    main()
