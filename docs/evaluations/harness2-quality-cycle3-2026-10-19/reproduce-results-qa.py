#!/usr/bin/env python3
"""Independent JSON-only Harness2 audit. No engine imports, media or model calls.

python reproduce-results-qa.py --export /path/to/final-export.json \
  --original-export /path/to/native-two-cycle-results.json \
  --role-audit /path/to/role-context-independent-qa.json \
  --pre-native-qa /path/to/pre-native-independent-qa.json \
  --output /path/to/new-independent-qa.json --table /path/to/new-tables.md
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text())


def stats(values):
    actual = [v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0]
    return {'known_count': len(actual), 'missing_count': len(values)-len(actual),
            'sum': sum(actual), 'mean': statistics.mean(actual) if actual else None,
            'median': statistics.median(actual) if actual else None}


def equal(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
    return left == right


def image_identity(row):
    return digest(sorted(v for n, v in row['commitment']['inputs'].items()
                         if n.endswith('.png') and not n.startswith('broken-')))


def portable(value, root):
    """Compare raw JSON to its published form without importing the exporter."""
    if isinstance(value, dict):
        return {portable(str(k),root):portable(v,root) for k,v in value.items()}
    if isinstance(value, list):
        return [portable(v,root) for v in value]
    if isinstance(value, str):
        value=value.replace(str(root),'/pipeline')
        return re.sub(r'(?<![A-Za-z0-9:])/(?:home|root|tmp|run|var|usr|opt|etc|mnt|proc|dev|sys)(?:/[^\s"<>]*)?', '<local-path>', value)
    return value


def grade_group(pairs, split, arm):
    values = [p.get(arm, {}) for p in pairs if p.get('split') == split]
    result = {'split': split, 'arm': arm, 'planned_distinct_tasks': len(values)}
    for output, field in [('joint_successes', 'joint_passed'), ('delivery_successes', 'delivered'),
                          ('visual_successes', 'visual_passed'), ('mechanical_successes', 'mechanical_passed'),
                          ('material_defects', 'material_defect'), ('unverified', 'unverified')]:
        result[output] = sum(v.get(field) is True for v in values)
    result['incomplete_or_failed'] = sum(v.get('status') != 'completed' for v in values)
    for field in ('elapsed_seconds', 'tool_calls', 'review_calls'):
        result[field] = stats([v.get(field) for v in values])
    result['mechanical_quality_score'] = stats([v.get('mechanical', {}).get('quality_score') for v in values])
    return result


def required_rejection_reasons(pairs, policy):
    """Read-only arithmetic over results, independent of Oak's admission function."""
    reasons = set()
    counts = Counter(p.get('split') for p in pairs)
    if (counts['regression'] != policy['regression_tasks'] or counts['holdout'] != policy['holdout_tasks']
            or len(pairs) != policy['regression_tasks'] + policy['holdout_tasks']):
        reasons.add('incomplete_planned_comparison')
    ids = [p.get('task_id') for p in pairs]
    if any(v is None for v in ids) or len(set(ids)) != len(ids):
        reasons.add('duplicate_or_missing_task_identity')
    for pair in pairs:
        if not all(a in pair for a in ('baseline', 'candidate')):
            reasons.add('missing_arm')
            continue
        for arm in ('baseline', 'candidate'):
            row = pair[arm]
            if row.get('status') in {None, 'interrupted', 'interrupted_unknown', 'running'}:
                reasons.add('unknown_execution_' + arm)
            if arm == 'candidate' and (row.get('status') != 'completed' or row.get('unverified')):
                reasons.add('incomplete_or_unverified_' + arm)
            if row.get('source_immutable') is not True:
                reasons.add('source_changed_' + arm)
            if arm == 'candidate' and row.get('delivered') is not True:
                reasons.update({'delivery_missing_candidate', 'candidate_delivery_missing'})
            if arm == 'candidate' and row.get('material_defect'):
                reasons.add('material_visual_defect_candidate')
            elapsed, tools = row.get('elapsed_seconds'), row.get('tool_calls')
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed <= policy['max_elapsed_seconds']:
                reasons.add('time_budget_' + arm)
            if type(tools) is not int or not 0 <= tools <= policy['max_tool_calls']:
                reasons.add('tool_budget_' + arm)
            if row.get('model_valid', True) is not True or row.get('budget_valid', True) is not True:
                reasons.add('invalid_conditions_' + arm)
            if row.get('joint_passed') and (not row.get('artifact_sha256') or row.get('status') != 'completed'
                    or row.get('unverified') or row.get('material_defect')
                    or not all(row.get(f) for f in ('delivered', 'visual_passed', 'mechanical_passed'))):
                reasons.add('invalid_joint_sha_binding_' + arm)
        if pair.get('split') == 'regression' and pair['baseline'].get('joint_passed') and not pair['candidate'].get('joint_passed'):
            reasons.add('regression_loss')
    held = [p for p in pairs if p.get('split') == 'holdout']
    baseline = sum(p.get('baseline', {}).get('joint_passed') is True for p in held)
    candidate = sum(p.get('candidate', {}).get('joint_passed') is True for p in held)
    if candidate <= baseline:
        reasons.add('no_strict_fresh_holdout_gain')
    return sorted(reasons), baseline, candidate


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--export', type=Path, required=True)
    ap.add_argument('--original-export', type=Path, required=True)
    ap.add_argument('--role-audit', type=Path)
    ap.add_argument('--pre-native-qa', type=Path)
    ap.add_argument('--root', type=Path,
                    help='Optional private pipeline root: verifies raw JSON search seals before path sanitization. Never reads media.')
    ap.add_argument('--expected-cycles', type=int, default=3)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--table', type=Path, required=True)
    args = ap.parse_args()
    report, original = load(args.export), load(args.original_export)
    checks, unperformed = [], []
    def check(name, passed, evidence=None):
        checks.append({'check': name, 'passed': bool(passed),
                       **({'evidence': evidence} if evidence is not None else {})})
    check('expected_number_of_completed_cycles', len(report['cycles']) == args.expected_cycles)
    check('fixed_report_model_effort_auth', report['model'] == 'gpt-6.1-sol' and report['effort'] == 'low'
          and report['authentication'] == 'configured_existing_chatgpt_subscription')
    check('fixed_upstream_pin', report['upstream_commit'] == original['upstream_commit']
          == '1cfe254e8d25b15f37048c3730134686abe60335')
    check('portable_document_hash', digest({k:v for k,v in report.items() if k != 'portable_content_sha256'})
          == report['portable_content_sha256'])
    check('no_broad_quality_or_live_provider_claim', report['broad_quality_improvement_claim'] is False
          and report['live_provider_verification'] is False)
    old_by_id = {c['journal']['id']: c for c in original['cycles']}
    current_by_id = {c['journal']['id']: c for c in report['cycles']}
    old_bindings = {}
    for identity, old in old_by_id.items():
        new = current_by_id.get(identity)
        check('unchanged_original_cycle:' + str(old['journal']['ordinal']), new == old)
        old_bindings[str(old['journal']['ordinal'])] = {'original_sha256':digest(old),
                                                     'current_sha256':digest(new) if new else None}
    changed_old_files = {p:{'original':s,'current':report['original_file_sha256'].get(p)}
                         for p,s in original['original_file_sha256'].items()
                         if report['original_file_sha256'].get(p) != s}
    check('all_original_source_file_hashes_preserved', not changed_old_files,
          {'original_file_bindings':len(original['original_file_sha256']), 'changed':changed_old_files})
    prior_identities, cycles_out, group_table, pair_table = set(), [], [], []
    fixed_files = original['cycles'][0]['journal']['config']['implementation']['files']
    quality_files = [n for n in fixed_files if n.startswith('oak/media_pilot_') or n in {'oak/quality.py','oak/quality_media.py'}]
    for cycle in sorted(report['cycles'], key=lambda c:c['journal']['ordinal']):
        journal, files = cycle['journal'], cycle['files']
        ordinal, config, manifest = journal['ordinal'], journal['config'], journal['manifest']
        prefix = 'cycle' + str(ordinal) + ':'
        frozen, evaluation, decision = files['freeze.json'], files['evaluation.json'], files['decision.json']
        pairs = evaluation['pairs']
        check(prefix+'completed', journal['phase'] == 'completed')
        check(prefix+'file_journal_freeze_decision_search_equal', frozen == journal['frozen']
              and decision == journal['decision'] and files['upstream-result.json'] == journal['search'])
        check(prefix+'manifest_seal', frozen['manifest_sha256'] == digest(manifest))
        check(prefix+'config_seal', frozen['config_sha256'] == digest(config))
        if args.root:
            relative=Path('scopes')/digest(report['scope'])/'cycles'/journal['id']/'upstream-result.json'
            raw_search=load(args.root/relative)
            check(prefix+'raw_json_search_seal',frozen['search_sha256']==digest(raw_search))
            check(prefix+'portable_search_equals_raw_json',portable(raw_search,args.root)==journal['search'])
            check(prefix+'raw_search_file_hash',sha(args.root/relative)==report['original_file_sha256'].get(relative.as_posix()))
        else:
            unperformed.append(prefix+'raw_json_search_seal: portable path normalization changes the search digest; supply --root for raw JSON verification.')
        check(prefix+'evaluation_freeze_binding', evaluation['freeze_sha256'] == digest(frozen))
        check(prefix+'decision_evaluation_binding', decision['evidence']['evaluation_sha256'] == digest(evaluation))
        check(prefix+'decision_freeze_policy_binding', decision['evidence']['freeze_sha256'] == digest(frozen)
              and decision['policy_sha256'] == digest(config['policy']))
        check(prefix+'fixed_model_effort', config['model'] == 'gpt-6.1-sol' and config['effort'] == 'low')
        authored=cycle.get('authored_construction_controls')
        if config.get('construction_controls_sha256'):
            check(prefix+'selected_control_receipt_seal',authored is not None and
                  authored['selected_receipt_sha256']==config['construction_controls_sha256']==digest(authored['record']))
            if authored:
                check(prefix+'control_raw_file_hash_binding',authored['raw_receipt_sha256']==report['original_file_sha256'].get(authored['path']))
                controls=authored['record']['cases']
                check(prefix+'positive_negative_controls_pass',authored['record']['status']=='pass' and bool(controls)
                      and all(c['status']=='pass' and c['controls']['positive']['mechanical']['status']=='pass'
                              and c['controls']['negative']['mechanical']['status']=='fail'
                              and c['controls']['negative']['mechanical']['structural_status']=='pass' for c in controls))
                committed={r['commitment']['descriptor_sha256']:r['commitment'] for rows in manifest.values() for r in rows}
                check(prefix+'control_sources_are_exact_committed_cases',all(committed.get(c['descriptor_sha256'])==c['source_commitment'] for c in controls))
                check(prefix+'all_exported_control_bytes_bindings_verified',all(
                    f.get('verified') is True and f.get('sha256')==f.get('expected_sha256')
                    and report['original_file_sha256'].get(f['path'])==f.get('sha256')
                    for binding in authored['verified_bindings'] for key in ('files','source_files')
                    for f in binding[key]))
        for name in quality_files:
            check(prefix+'unchanged_quality_source:' + name, config['implementation']['files'][name] == fixed_files[name])
        fresh = [image_identity(r) for r in manifest['holdout']]
        known = {image_identity(r) for split in ('dev','regression') for r in manifest[split]}
        check(prefix+'fresh_source_identities_unique', len(fresh) == len(set(fresh)))
        check(prefix+'fresh_disjoint_current_dev_regression', not set(fresh)&known)
        check(prefix+'fresh_disjoint_all_prior_cycle_exposures', not set(fresh)&prior_identities,
              {'fresh':len(fresh),'prior_unique':len(prior_identities)})
        prior_identities.update(image_identity(r) for rows in manifest.values() for r in rows)
        planned = {(split,r['descriptor']['id']) for split in ('regression','holdout') for r in manifest[split]}
        check(prefix+'complete_distinct_planned_pairs', {(p['split'],p['task_id']) for p in pairs} == planned and len(pairs) == len(planned))
        terminal = [r for r in cycle['producer_records'] if not r.get('interrupted_attempt')]
        producers = {r['record']['run_id']:r for r in terminal}
        check(prefix+'unique_terminal_producer_run_ids', len(producers) == len(terminal))
        for pair in pairs:
            for arm in ('baseline','candidate'):
                grade = pair.get(arm, {})
                label = prefix + pair['task_id'] + ':' + arm + ':'
                item = producers.get(grade.get('run_id'))
                check(label+'producer_receipt_present', item is not None)
                if item is None:
                    continue
                record, receipt = item['record'], item['record']['receipt']
                artifact = grade.get('artifact_sha256')
                check(label+'same_recorded_candidate_delivery_sha', grade.get('candidate_sha256') == artifact
                      == receipt.get('candidate_sha256') == receipt.get('artifact_sha256'))
                check(label+'exported_artifact_bytes_verified', all(
                    item['artifact_bindings'].get(kind,{}).get('verified') is True
                    and item['artifact_bindings'][kind].get('sha256') == artifact for kind in ('candidate','artifact'))
                    if grade.get('delivered') else True)
                check(label+'fixed_model_effort', all(v == 'gpt-6.1-sol' for v in (record.get('model'),receipt.get('model'),grade.get('model')))
                      and all(v == 'low' for v in (record.get('effort'),receipt.get('effort'),grade.get('effort'))))
                check(label+'same_frozen_harness', grade.get('procedure_sha256') == record['procedure_sha256']
                      == (frozen['parent_sha256'] if arm == 'baseline' else frozen['candidate_sha256']))
                committed=next(r['commitment'] for r in manifest[pair['split']] if r['descriptor']['id']==pair['task_id'])
                check(label+'exact_committed_source',record['source_commitment']==committed)
                check(label+'immutable_source_claim_has_verified_bytes',all(
                    f.get('verified') is True and f.get('sha256')==f.get('expected_sha256')
                    for f in item.get('source_files',[])) if grade.get('source_immutable') else True)
                reviews = receipt.get('reviews', [])
                visual = any(r.get('verdict') == 'pass' and r.get('sha256') == artifact for r in reviews)
                material = any(r.get('sha256') == artifact and any(i.get('severity') in {'major','blocker'} for i in r.get('issues', [])) for r in reviews)
                check(label+'visual_boolean_matches_same_sha_receipt', bool(grade.get('visual_passed')) == visual)
                check(label+'material_defect_matches_receipt', bool(grade.get('material_defect')) == material)
                mechanical = (grade.get('mechanical',{}).get('status') == 'pass'
                              and grade.get('source_immutable') is True
                              and grade.get('model_valid') is True and grade.get('budget_valid') is True)
                check(label+'mechanical_boolean_matches_measurement_and_conditions',
                      bool(grade.get('mechanical_passed')) == mechanical)
                check(label+'joint_is_delivery_visual_mechanical_conjunction', bool(grade.get('joint_passed')) == all(grade.get(f) is True for f in ('delivered','visual_passed','mechanical_passed')))
                check(label+'time_tools_reviews_match_receipt', grade.get('elapsed_seconds') == receipt.get('elapsed_seconds')
                      and grade.get('tool_calls') == receipt.get('tool_calls') and grade.get('review_calls') == len(reviews))
                check(label+'equal_per_attempt_limits', record['timeout'] == config['producer_timeout']
                      and grade.get('configured_timeout_seconds') == config['producer_timeout'])
                check(label+'pair_freeze_binding', grade.get('freeze_sha256') == digest(frozen))
                pair_table.append({'cycle':ordinal,'task_id':pair['task_id'],'split':pair['split'],'arm':arm,
                    'status':grade.get('status'),'delivered':grade.get('delivered'),'visual':grade.get('visual_passed'),
                    'mechanical':grade.get('mechanical_passed'),'joint':grade.get('joint_passed'),
                    'unverified':grade.get('unverified'),'material_defect':grade.get('material_defect'),
                    'quality_score':grade.get('mechanical',{}).get('quality_score'),'artifact_sha256':artifact,
                    'seconds':grade.get('elapsed_seconds'),'tools':grade.get('tool_calls'),'reviews':grade.get('review_calls'),
                    'failed_criteria':[x['criterion'] for x in grade.get('mechanical',{}).get('checks',[]) if x.get('status') != 'pass']})
        for split in ('holdout','regression'):
            for arm in ('baseline','candidate'):
                computed = grade_group(pairs,split,arm)
                exported = next((g for g in cycle['metrics']['groups'] if (g['split'],g['arm']) == (split,arm)),{})
                check(prefix+'reported_metrics:'+split+':'+arm, equal(computed,exported))
                group_table.append({'cycle':ordinal,**computed})
        held = [p for p in pairs if p['split']=='holdout']
        wins = sum(p.get('candidate',{}).get('joint_passed') is True and p.get('baseline',{}).get('joint_passed') is not True for p in held)
        losses = sum(p.get('baseline',{}).get('joint_passed') is True and p.get('candidate',{}).get('joint_passed') is not True for p in held)
        ties = len(held)-wins-losses
        check(prefix+'reported_fresh_wins_losses_ties', [wins,losses,ties] == [cycle['metrics'][k] for k in ('candidate_wins','candidate_losses','ties')])
        reasons,bh,ch = required_rejection_reasons(pairs,config['policy'])
        check(prefix+'decision_matches_all_results', decision['accepted'] == (not reasons) and decision['reasons'] == reasons)
        check(prefix+'reported_holdout_joint_counts', [bh,ch] == [decision['baseline_holdout_joint'],decision['candidate_holdout_joint']])
        check(prefix+'rejection_does_not_activate_candidate', decision['accepted'] or decision.get('active_id') == frozen['parent_id'])
        ops = Counter(r['kind'] for r in cycle['operations'])
        check(prefix+'reservations_within_caps', all(ops[k] <= config[v] for k,v in [('producer','max_producers'),('role','max_roles'),('role_tool','max_role_tools')]))
        check(prefix+'all_operations_in_reported_counts', sum(r['count'] for r in cycle['operation_counts']) == len(cycle['operations']))
        role_records = [r['record'] for r in cycle['role_attempts']]
        role_seconds = stats([r.get('elapsed_seconds') for r in role_records])
        role_tools = sum(len(r.get('tool_calls',[])) for r in role_records)
        check(prefix+'role_cost_proxies_match_records', equal(role_seconds,cycle['role_cost_proxies']['elapsed_seconds'])
              and role_tools == cycle['role_cost_proxies']['recorded_tools'])
        check(prefix+'role_attempt_counts_match', len(role_records) == cycle['attempt_counts']['role_attempts']
              and dict(Counter(r.get('status','missing') for r in role_records)) == cycle['attempt_counts']['role_statuses'])
        check(prefix+'producer_attempt_counts_match', len(cycle['producer_records']) == cycle['attempt_counts']['producer_records']
              and sum(bool(r.get('interrupted_attempt')) for r in cycle['producer_records']) == cycle['attempt_counts']['producer_interrupted'])
        producer_groups=defaultdict(list)
        for item in terminal:
            record=item['record'];label=record['run_id'].rsplit('/',1)[-1]
            if item['phase']=='development':
                arm=('baseline' if label.startswith('base') else 'INV' if label.startswith('inv')
                     else 'DOM' if label.startswith('dom') else 'composition')
            else:
                arm='candidate' if label.endswith('-candidate') else 'baseline'
            producer_groups[(item['phase'],arm)].append(record)
        for (phase,arm),records in producer_groups.items():
            recomputed={'phase':phase,'arm':arm,
                'terminal_records':sum(r.get('state')=='finished' for r in records),'records':len(records),
                'tool_calls':stats([r.get('receipt',{}).get('tool_calls') for r in records]),
                'elapsed_seconds':stats([r.get('receipt',{}).get('elapsed_seconds') for r in records]),
                'recorded_review_attempts':sum(len(r.get('receipt',{}).get('reviews',[])) for r in records),
                'independent_quality_not_measured_in_search':phase=='development'}
            published=next((g for g in cycle['producer_cost_proxies'] if (g['phase'],g['arm'])==(phase,arm)),{})
            check(prefix+'producer_cost_group:'+phase+':'+arm,equal(recomputed,published))
        p_seconds = stats([r['record'].get('receipt',{}).get('elapsed_seconds') for r in cycle['producer_records']])
        p_tools = stats([r['record'].get('receipt',{}).get('tool_calls') for r in cycle['producer_records']])
        p_reviews = sum(len(r['record'].get('receipt',{}).get('reviews',[])) for r in cycle['producer_records'])
        regression = [p for p in pairs if p['split']=='regression']
        cycles_out.append({'cycle':ordinal,'cycle_id':journal['id'],'k':config['k'],'producer_attempts':len(cycle['producer_records']),
            'development_terminal_runs':sum(r['phase']=='development' for r in terminal),
            'independent_terminal_runs':sum(r['phase']=='independent' for r in terminal),
            'producer_interrupted':cycle['attempt_counts']['producer_interrupted'],
            'producer_seconds':p_seconds,'producer_tools':p_tools,'producer_reviews':p_reviews,
            'role_attempts':len(role_records),'role_statuses':dict(Counter(r.get('status') for r in role_records)),
            'role_seconds':role_seconds,'role_tools':role_tools,'regression_tasks':len(regression),
            'regression_baseline_joint':sum(p['baseline'].get('joint_passed') is True for p in regression),
            'regression_candidate_joint':sum(p['candidate'].get('joint_passed') is True for p in regression),
            'regression_losses':sum(p['baseline'].get('joint_passed') is True and p['candidate'].get('joint_passed') is not True for p in regression),
            'holdout_tasks':len(held),'holdout_baseline_joint':bh,'holdout_candidate_joint':ch,
            'fresh_wins':wins,'fresh_losses':losses,'fresh_ties':ties,'accepted':decision['accepted'],
            'reasons':reasons,'active_id':decision.get('active_id'),'candidate_id':decision.get('candidate_id'),
            'unknown_pair_arms':sum(p.get(a,{}).get('status') in {None,'running','interrupted','interrupted_unknown'} for p in pairs for a in ('baseline','candidate')),
            'unverified_pair_arms':sum(p.get(a,{}).get('unverified') is True for p in pairs for a in ('baseline','candidate')),
            'freeze_sha256':digest(frozen),'evaluation_sha256':digest(evaluation),'cycle_canonical_sha256':digest(cycle)})
    if args.role_audit:
        role = load(args.role_audit)
        last = report['cycles'][-1]
        check('role_audit_targets_last_cycle', role['cycle_id'] == last['journal']['id'])
        indexed = {r['record']['role']:r for r in last['role_attempts']}
        for r in role['roles']:
            got = indexed.get(r['role'])
            check('role_audit_receipt_binding:'+r['role'], got is not None and got['raw_receipt_sha256'] == r['receipt_sha256']
                  and got['record']['context_sha256'] == r['context_sha256'] and got['record']['response_sha256'] == r['response_sha256'])
        selector = next(r for r in last['role_attempts'] if r['record']['role']=='select.seq2')
        check('actual_selector_limit_preserved', role['selector']['candidate_output_images_present'] is False
              and 'rather than inspectable animations' in selector['response']
              and not any(r.get('tool')=='oak_h2_view' and not r.get('path','').startswith('source/') for r in selector['record']['tool_calls']))
        role_binding = {'file':args.role_audit.name,'sha256':sha(args.role_audit),
                        'selector_output_images':0,'selector_source_view_requests':6,
                        'limitation':'Actual selector saw source images and delivery/VLM text, not candidate rendered frames. Context/read requests do not prove complete page coverage or comprehension.'}
    else:
        role_binding = None
    if args.pre_native_qa:
        pre = load(args.pre_native_qa);last = report['cycles'][-1]
        check('pre_native_audit_targets_last_cycle',pre['cycle_id']==last['journal']['id'])
        check('pre_native_manifest_and_config_binding',pre['sealed_bindings']['manifest_sha256']==digest(last['journal']['manifest'])
              and pre['sealed_bindings']['config_sha256']==digest(last['journal']['config']))
        pre_binding={'file':args.pre_native_qa.name,'sha256':sha(args.pre_native_qa)}
    else:
        pre_binding=None
    final = cycles_out[-1]
    active = [v for v in report['versions']['active'] if v['project_id']==report['scope'][0]
              and v['chat_id']==report['scope'][1] and v['domain']==report['scope'][2]]
    check('final_scope_active_version_matches_decision',len(active)==1 and active[0]['version_id']==final['active_id'])
    check('rejected_final_candidate_has_no_activation_event',final['accepted'] or not any(
        e.get('version_id')==final['candidate_id'] for e in report['versions']['activation_events']))
    result={'schema':'oak-harness2-three-cycle-independent-json-qa-v1','recorded_at':datetime.now(timezone.utc).isoformat(),
        'audit_script':{'file':Path(__file__).name,'sha256':sha(Path(__file__))},
        'inspection_boundary':'JSON-only consistency and provenance audit. No engine imports, media opening/decoding, grading, model calls or transport attestation.',
        'source_export':{'file':args.export.name,'sha256':sha(args.export),'portable_content_sha256':report['portable_content_sha256']},
        'original_two_cycle_export':{'file':args.original_export.name,'sha256':sha(args.original_export)},
        'check_count':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),
        'checks':checks,'unperformed_checks':unperformed,'original_cycle_bindings':old_bindings,'cycle_table':cycles_out,'group_table':group_table,'pair_table':pair_table,
        'role_context_audit':role_binding,'pre_native_audit':pre_binding,
        'cost_scope':'Cycle work only. Administrative activation fixtures and separate post-cycle followups are excluded from these totals; subscription USD is not metered.',
        'quality_claim':'Admission status is recomputed from sealed comparisons; neither property-score differences nor native selection alone establish broad quality improvement.'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['| Cycle | k | Producers / roles | Regression joint baseline → candidate | Fresh joint baseline → candidate | Fresh W/L | Regression losses | Decision |',
           '|---|---:|---:|---|---|---:|---:|---|']
    for c in cycles_out:
        lines.append(f"| {c['cycle']} | {c['k']} | {c['producer_attempts']} / {c['role_attempts']} | {c['regression_baseline_joint']}/{c['regression_tasks']} → {c['regression_candidate_joint']}/{c['regression_tasks']} | {c['holdout_baseline_joint']}/{c['holdout_tasks']} → {c['holdout_candidate_joint']}/{c['holdout_tasks']} | {c['fresh_wins']}/{c['fresh_losses']} | {c['regression_losses']} | {'ACCEPT' if c['accepted'] else 'REJECT'} |")
    lines += ['', '| Cycle | Split | Arm | Joint / tasks | Mean property score | Seconds sum | Tools | Reviews | Unverified | Failed/incomplete |',
              '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in group_table:
        score=g['mechanical_quality_score']['mean'];score='missing' if score is None else f'{score:.6f}'
        lines.append(f"| {g['cycle']} | {g['split']} | {g['arm']} | {g['joint_successes']}/{g['planned_distinct_tasks']} | {score} | {g['elapsed_seconds']['sum']:.3f} | {g['tool_calls']['sum']} | {g['review_calls']['sum']} | {g['unverified']} | {g['incomplete_or_failed']} |")
    lines += ['', '| Cycle | Producer seconds / tools / reviews | Role seconds / tools | Interrupted producer attempts | Unknown / unverified pair arms |',
              '|---|---:|---:|---:|---:|']
    for c in cycles_out:
        lines.append(f"| {c['cycle']} | {c['producer_seconds']['sum']:.3f} / {c['producer_tools']['sum']} / {c['producer_reviews']} | {c['role_seconds']['sum']:.3f} / {c['role_tools']} | {c['producer_interrupted']} | {c['unknown_pair_arms']} / {c['unverified_pair_arms']} |")
    lines += ['', 'JSON-only audit; this does not independently attest visuals or the model transport. Property scores include completed failures. Missing values and unknown executions remain explicit in JSON. Cost tables cover cycle work only; no USD cost is inferred.', '']
    args.table.parent.mkdir(parents=True,exist_ok=True);args.table.write_text('\n'.join(lines))
    print(json.dumps({'checks':result['check_count'],'passed':result['passed'],'failed':result['failed'],
                      'output':str(args.output),'table':str(args.table),'sha256':sha(args.output),
                      'failed_checks':[c['check'] for c in checks if not c['passed']]},indent=2))
    return 0 if result['failed']==0 else 1


if __name__=='__main__':
    raise SystemExit(main())
