"""Controlled activation and rollback checks on the real Oak pipeline pointer.

The two media follow-ups use subscription-backed Oak. The temporary promotion
and deliberately rejected candidate are administrative test fixtures, not
optimizer discoveries or evidence of quality improvement. Completed native
receipts are reused; unknown interrupted attempts remain in the bounded ledger.
Each terminal follow-up also adds scoped monitoring metadata to the case
registry and improvement queue. Failures raise the group's priority. These
receipt-only cases are optimizer-unapproved and contain no task descriptor;
future DEV material still requires separate explicit consent.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
from pathlib import Path
import uuid

from .harness2_pipeline import (BudgetStopped, MODEL, PipelineStore, acceptance, canonical,
                                digest, file_sha, now, write_once)
from .harness_versions import HarnessVersions


DOMAIN = 'media-depth'
CHAT_ID = 1
CONTROLLED_ADVICE = ('\n\nControlled activation fixture: inspect the supplied source artwork before rendering. '
    'Preserve original bytes, use existing isolated tools, and retain independent review and exact-byte delivery. '
    'This fixture is a deployment-plumbing check and makes no claim of better media quality.')


def _bad_gate():
    policy = {'regression_tasks': 1, 'holdout_tasks': 1, 'max_elapsed_seconds': 600, 'max_tool_calls': 64}
    pairs = []
    for split in ('regression', 'holdout'):
        identity = 'controlled-' + split
        baseline = {'task_id': identity, 'status': 'completed', 'source_immutable': True,
                    'delivered': True, 'unverified': False, 'material_defect': False,
                    'elapsed_seconds': 1, 'tool_calls': 1, 'artifact_sha256': 'a' * 64,
                    'visual_passed': True, 'mechanical_passed': True, 'joint_passed': True}
        candidate = {**baseline, 'delivered': False, 'artifact_sha256': None,
                     'visual_passed': False, 'mechanical_passed': False, 'joint_passed': False}
        pairs.append({'task_id': identity, 'split': split, 'baseline': baseline, 'candidate': candidate})
    decision = acceptance(pairs[:1], pairs[1:], policy=policy)
    if decision['accepted']:
        raise ValueError('The controlled bad candidate unexpectedly passed admission.')
    return {'kind': 'controlled-admission-fixture', 'model_backed': False,
            'quality_gain_claim': False, 'policy': policy, 'pairs': pairs, 'decision': decision}


def _proof(receipt, versions, expected):
    application = receipt.get('harness_application') or {}
    expected['decision'] = versions.decision(expected['id'])
    context_hash = hashlib.sha256(versions.application_context(expected).encode()).hexdigest()
    stored = next((row for row in versions.applications(expected['project_id'], expected['chat_id'], expected['domain'])
                   if row['id'] == application.get('id')), None)
    return bool(stored and application.get('version_id') == expected['id']
                and application.get('procedure_sha256') == expected['procedure_sha256']
                and application.get('context_sha256') == context_hash
                and application.get('turn_id') and stored['turn_id'] == application['turn_id']
                and stored['version_id'] == expected['id'] and stored['context_sha256'] == context_hash)


def _native_runner(task, workspace, activation, *, runtime_home, timeout):
    from .media_pilot_runtime import PilotRuntime
    runtime = PilotRuntime({'harness_activation': activation}, runtime_home=runtime_home,
                           model=MODEL, effort='low', timeout=timeout)
    return asyncio.run(runtime.run(task, workspace))


def _check_stopped(root, project):
    store = PipelineStore(root, project, CHAT_ID, DOMAIN)
    try:
        if store.setting('stopped'):
            raise BudgetStopped('Pipeline stopped before a controlled native follow-up.')
    finally:
        store.close()


def _monitor_followup(activation, expected, record, terminal, mode):
    """Retain bounded result references; never enqueue raw native conversations."""
    receipt = record['receipt']
    artifact_sha = receipt.get('artifact_sha256')
    matching = [row for row in receipt.get('reviews', [])
                if artifact_sha and row.get('sha256') == artifact_sha]
    visual = bool(matching and matching[-1].get('verdict') == 'pass')
    reasons = []
    if receipt.get('status') != 'completed':
        reasons.append('terminal_result_incomplete_or_unverified')
    if receipt.get('delivered') is not True:
        reasons.append('delivery_missing')
    if not visual:
        reasons.append('same_sha_visual_review_unverified')
    if receipt.get('source_immutable') is not True:
        reasons.append('source_changed_or_unverified')
    payload = {'visibility': 'public_fixture', 'source': 'activation-demo-native-followup',
        'execution_mode': mode, 'model_backed': mode == 'native-subscription',
        'task_id': record['descriptor']['id'], 'procedure_version': expected['id'],
        'receipt_sha256': record['receipt_sha256'], 'receipt_reference_sha256': file_sha(terminal),
        'artifact_sha256': artifact_sha, 'artifact_reference': str(terminal),
        'result': {'status': receipt.get('status'), 'delivered': receipt.get('delivered'),
                   'visual_passed': visual, 'source_immutable': receipt.get('source_immutable'),
                   'mechanical_evaluation': 'not_run_in_activation_followup',
                   'tool_calls': receipt.get('tool_calls'), 'elapsed_seconds': receipt.get('elapsed_seconds')},
        'failure_reason': '; '.join(reasons) if reasons else None,
        'quality_gain_claim': False}
    store = PipelineStore(Path(activation['store_path']).parent, expected['project_id'],
                          expected['chat_id'], expected['domain'])
    try:
        return store.record_case(payload, signature='source-preserving-depth', approved=False,
                                 priority=100 if reasons else 10)
    finally:
        store.close()


def _followup(root, name, descriptor, expected, activation, versions, *, runtime_home, timeout,
              max_producers, runner, mode):
    from .harness2_media import materialize_descriptor
    attempts = root / 'attempts' / name
    attempts.mkdir(parents=True, exist_ok=True, mode=0o700)
    prior = sorted(attempts.glob('*/intent.json'))
    for intent in prior:
        terminal = intent.parent / 'receipt.json'
        if terminal.exists():
            record = json.loads(terminal.read_text())
            if (record['descriptor'] != descriptor or record['expected_version_id'] != expected['id']
                    or record['receipt_sha256'] != digest(record['receipt'])):
                raise ValueError('The cached native follow-up differs from its committed identity.')
            if record['application_provenance_verified'] != _proof(record['receipt'], versions, expected):
                raise ValueError('Cached follow-up provenance no longer matches the deployment store.')
            artifact = record['receipt'].get('artifact_path')
            if artifact and file_sha(artifact) != record['receipt'].get('artifact_sha256'):
                raise ValueError('Cached native delivery changed.')
            case_id = _monitor_followup(activation, expected, record, terminal, mode)
            return {**record, 'monitoring_case_id': case_id}
        unknown = intent.parent / 'interrupted-unknown.json'
        if not unknown.exists():
            write_once(unknown, {'classification': 'interrupted_unknown', 'intent_sha256': file_sha(intent),
                                'observed_at': now(), 'quality_failure_claim': False})
    if len(prior) >= 2 or len(list((root / 'attempts').glob('*/*/intent.json'))) >= max_producers:
        raise RuntimeError('Controlled follow-up budget exhausted; unknown attempts remain recorded.')
    _check_stopped(Path(activation['store_path']).parent, expected['project_id'])
    if versions.resolve(expected['project_id'], expected['chat_id'], expected['domain'])['id'] != expected['id']:
        raise ValueError('The expected procedure is no longer active before native execution.')
    attempt = attempts / str(len(prior) + 1)
    attempt.mkdir(mode=0o700)
    workspace = attempt / 'workspace'
    workspace.mkdir(mode=0o700)
    task = materialize_descriptor(descriptor, workspace)
    intent = {'attempt': len(prior) + 1, 'descriptor_sha256': digest(descriptor),
              'expected_version_id': expected['id'], 'procedure_sha256': expected['procedure_sha256'],
              'model': MODEL, 'effort': 'low', 'timeout': timeout, 'started_at': now()}
    write_once(attempt / 'intent.json', intent)
    receipt = runner(task, workspace, activation, runtime_home=runtime_home, timeout=timeout)
    record = {'descriptor': descriptor, 'expected_version_id': expected['id'], 'receipt': receipt,
              'receipt_sha256': digest(receipt), 'recorded_at': now(),
              'application_provenance_verified': _proof(receipt, versions, expected)}
    write_once(attempt / 'receipt.json', record)
    case_id = _monitor_followup(activation, expected, record, attempt / 'receipt.json', mode)
    return {**record, 'monitoring_case_id': case_id}


def run_demo(root, *, project='harness2-demo', runtime_home=None, timeout=600,
             max_producers=4, runner=None):
    """Run two cached native tasks; restore the original verified pointer.

    ``runner`` is solely a local-test seam. Its results are explicitly labelled
    local fixtures and cannot be presented as real model-backed executions.
    At most two unknown attempts per follow-up and four producer intents total
    are admitted. Terminal failures/unverified deliveries are never rerun.
    """
    if type(timeout) is not int or not 30 <= timeout <= 600 or type(max_producers) is not int or not 2 <= max_producers <= 4:
        raise ValueError('Use bounded native time and total producer budgets.')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    demo = root / 'activation-demo'
    demo.mkdir(exist_ok=True, mode=0o700)
    mode = 'local-fixture' if runner is not None else 'native-subscription'
    runner = runner or _native_runner
    with (root / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another worker owns this pipeline root.') from exc
        write_once(demo / 'config.json', {'project': project, 'chat_id': CHAT_ID, 'domain': DOMAIN,
            'model': MODEL, 'effort': 'low', 'timeout': timeout, 'max_producers': max_producers,
            'mode': mode, 'demo_source_sha256': file_sha(__file__),
            'quality_gain_claim': False, 'live_provider_verification': False})
        versions = HarnessVersions(root / 'versions.sqlite3')
        try:
            final_path = demo / 'report.json'
            if final_path.exists():
                report = json.loads(final_path.read_text())
                current = versions.resolve(project, CHAT_ID, DOMAIN)
                if current is None or current['id'] != report['restored_version_id']:
                    raise ValueError('The completed demo pointer changed; do not overwrite current activation.')
                return report
            _check_stopped(root, project)
            identity = demo / 'identity.json'
            if identity.exists():
                saved = json.loads(identity.read_text())
                original = versions.version(saved['original_version_id'])
                namespace = saved['namespace']
            else:
                original = versions.resolve(project, CHAT_ID, DOMAIN)
                if original is None:
                    original = versions.bootstrap_current(project, CHAT_ID, DOMAIN, '',
                        evidence={'source': 'existing-controller-instructions', 'quality_gain_claim': False})
                namespace = 'oak-activation-' + uuid.uuid4().hex
                write_once(identity, {'original_version_id': original['id'], 'namespace': namespace})
            fixture = versions.register(project, CHAT_ID, DOMAIN, original['procedure'] + CONTROLLED_ADVICE,
                parent_id=original['id'], source='controlled-fixture',
                evidence={'kind': 'activation-plumbing-fixture', 'quality_gain_claim': False})
            versions.record_decision(fixture['id'], accepted=True, kind='controlled-fixture',
                evidence={'kind': 'isolated-administrative-test', 'quality_gain_claim': False},
                reason='Controlled deployment plumbing only; not evidence of superior quality.')
            gate = _bad_gate()
            write_once(demo / 'bad-candidate-gate.json', gate)
            bad = versions.register(project, CHAT_ID, DOMAIN, 'Omit delivery in this deliberately bad controlled fixture.',
                parent_id=fixture['id'], source='controlled-fixture', evidence={'controlled_gate_sha256': digest(gate)})
            versions.record_decision(bad['id'], accepted=False, kind='controlled-fixture',
                evidence={'controlled_gate_sha256': digest(gate), 'model_backed': False},
                reason='; '.join(gate['decision']['reasons']))
            try:
                versions.activate(bad['id'])
            except ValueError:
                bad_blocked = True
            else:
                raise ValueError('Rejected controlled version activated unexpectedly.')
            rollback_path = demo / 'rollback.json'
            history = versions.activation_history(project, CHAT_ID, DOMAIN)
            rolled_back = any(row['action'] == 'rollback' and row['previous_id'] == fixture['id']
                              and row['version_id'] == original['id'] for row in history)
            if not rolled_back:
                current = versions.resolve(project, CHAT_ID, DOMAIN)
                if current['id'] != fixture['id']:
                    versions.activate(fixture['id'], expected_active=original['id'])
            from .harness2_media import make_descriptor
            activation = {'store_path': str(root / 'versions.sqlite3'), 'project_id': project, 'domain': DOMAIN}
            first = _followup(demo, 'activated', make_descriptor(namespace, 1, split='followup'), fixture,
                activation, versions, runtime_home=runtime_home, timeout=timeout,
                max_producers=max_producers, runner=runner, mode=mode)
            # Close/reopen the actual deployment store before the rollback.
            versions.close()
            versions = HarnessVersions(root / 'versions.sqlite3')
            current = versions.resolve(project, CHAT_ID, DOMAIN)
            if current['id'] == fixture['id']:
                restored = versions.rollback(project, CHAT_ID, DOMAIN, target_id=original['id'],
                    expected_active=fixture['id'], reason='Controlled rollback; return actual pipeline pointer.')
            elif current['id'] == original['id'] and rolled_back:
                restored = current
            else:
                raise ValueError('The active pointer changed outside this controlled demo.')
            if not rollback_path.exists():
                write_once(rollback_path, {'from_version_id': fixture['id'], 'to_version_id': restored['id'],
                    'kind': 'controlled-rollback', 'reopen_verified': True, 'recorded_at': now()})
            second = _followup(demo, 'restored', make_descriptor(namespace, 2, split='followup', kind='repair'), original,
                activation, versions, runtime_home=runtime_home, timeout=timeout,
                max_producers=max_producers, runner=runner, mode=mode)
            report = {'kind': 'controlled-activation-and-rollback', 'mode': mode,
                'model': MODEL, 'effort': 'low', 'project_id': project, 'chat_id': CHAT_ID, 'domain': DOMAIN,
                'original_version_id': original['id'], 'fixture_version_id': fixture['id'],
                'rejected_version_id': bad['id'], 'restored_version_id': restored['id'],
                'bad_candidate_gate_rejected': not gate['decision']['accepted'],
                'rejected_candidate_activation_blocked': bad_blocked,
                'restart_preserved_original_version': restored['id'] == original['id'],
                'activation_provenance_verified': first['application_provenance_verified'],
                'restored_provenance_verified': second['application_provenance_verified'],
                'followups': [{key: record[key] for key in ('descriptor', 'expected_version_id', 'receipt_sha256',
                                                        'application_provenance_verified', 'monitoring_case_id')} |
                    {'status': record['receipt'].get('status'), 'delivered': record['receipt'].get('delivered'),
                     'artifact_sha256': record['receipt'].get('artifact_sha256'),
                     'tool_calls': record['receipt'].get('tool_calls'),
                     'elapsed_seconds': record['receipt'].get('elapsed_seconds'),
                     'harness_application': record['receipt'].get('harness_application')} for record in (first, second)],
                'initiated_producer_attempts': len(list((demo / 'attempts').glob('*/*/intent.json'))),
                'interrupted_unknown_attempts': len(list((demo / 'attempts').glob('*/*/interrupted-unknown.json'))),
                'quality_gain_claim': False, 'live_provider_verification': False,
                'controlled_decisions_model_backed': False}
            write_once(final_path, report)
            return report
        finally:
            versions.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--project', default='harness2-demo')
    parser.add_argument('--runtime-home')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--max-producers', type=int, default=4)
    args = parser.parse_args(argv)
    print(canonical(run_demo(args.root, project=args.project, runtime_home=args.runtime_home,
                             timeout=args.timeout, max_producers=args.max_producers)))


if __name__ == '__main__':
    main()
