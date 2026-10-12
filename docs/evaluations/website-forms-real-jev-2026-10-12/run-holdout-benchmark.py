#!/usr/bin/env python3
"""Model-enabled repeat of the frozen12-site deployed generic-broker baseline.

Run through stdin as the configured gateway UID. Default mode is local
validation only. --run enables real public-page GETs via fresh broker leases.
No field input, owner profile, model mock, API stub or retry exists.
Unfinalized templates cannot run. --run also requires a new --result receipt.
The optional uniform method step submits one eligible zero-field action only.
All errors remain in the denominator. Print only bounded metadata to stdout.
No key file is read by this runner. Paid inference occurs only inside the broker.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import time
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, UnixConnector

FROZEN = {'sites': [{'site_id': 'github',
            'name': 'GitHub',
            'category': 'developer',
            'url': 'https://github.com/login'},
           {'site_id': 'slack',
            'name': 'Slack',
            'category': 'communication',
            'url': 'https://slack.com/signin'},
           {'site_id': 'zoom', 'name': 'Zoom', 'category': 'communication', 'url': 'https://zoom.us/signin'},
           {'site_id': 'spotify',
            'name': 'Spotify',
            'category': 'media',
            'url': 'https://accounts.spotify.com/en/login'},
           {'site_id': 'amazon',
            'name': 'Amazon',
            'category': 'retail',
            'url': 'https://www.amazon.com/ap/signin'},
           {'site_id': 'ebay',
            'name': 'eBay',
            'category': 'retail',
            'url': 'https://signin.ebay.com/signin/'},
           {'site_id': 'dropbox',
            'name': 'Dropbox',
            'category': 'productivity',
            'url': 'https://www.dropbox.com/login'},
           {'site_id': 'paypal',
            'name': 'PayPal',
            'category': 'finance',
            'url': 'https://www.paypal.com/signin'},
           {'site_id': 'wise', 'name': 'Wise', 'category': 'finance', 'url': 'https://wise.com/login'},
           {'site_id': 'booking_com',
            'name': 'Booking.com',
            'category': 'travel',
            'url': 'https://account.booking.com/sign-in'},
           {'site_id': 'expedia',
            'name': 'Expedia',
            'category': 'travel',
            'url': 'https://www.expedia.com/login'},
           {'site_id': 'wordpress_com',
            'name': 'WordPress.com',
            'category': 'publishing_community',
            'url': 'https://wordpress.com/log-in'}],
 'selection_sha256': 'edf2158cb9ea4d6803b82797fb3ead7214f095d558d91aef7393a1d318c446fd',
 'frozen_sites_sha256': 'b7ce7db52ad947afdd8825ce10ee2aeb7de08773d33b6d34d6f71ad0cbd956d6',
 'candidate_source_digest': '08e678e4050bafe619635904f01e65ef8bc66c7dac50418852153547a3fb1927',
 'evaluation_kind': 'independent_complement_holdout',
 'source_commit': 'cf7ba70de33d06e74702201f714f65dcdb73b5dd',
 'code_sha256': {'oak/login_broker.py': '5ad933ea9b3cdbfb66aaf7efa9dac2a70540d3042939d74436622c826741920c',
                 'oak/signin_providers.py': '6f938919d91aa4f9252007a021bcf86986145c1721863035c75b8e4a7fbc65fd',
                 'oak/website_forms.py': '1b9593facc8c63d9896b52202f7ef03fc56eca41de040fe6af20b6c7a10fa809',
                 'oak/website_resources.py': '106f9c8c6cab5fa43b02f9b73181248d9f6104979345813883439374c679f268',
                 'oak/website_ui.py': '1c9360c34085da7486b923f287d97dda90f1c2afef33910607832240f2b92b01',
                 'oak/website_planner.py': '3aaf215c33d09122f3281c8b24b6fc50baf8a3c200e1e620c466642a179d34a5',
                 'oak/website_composer.py': '26bfa931705b6f6ecbf1bd951e0da2ffb88e45f464af936730c42e6f31b3fb6e',
                 'oak/executor.py': '6bb37b8960235bdeafd2548babf5b8c874b8de6c2d2a0d7a74a7a062056de50f'},
 'runtime_digest': '112d1dc3054184cc22fcdcbb0fa1404aafa27df571b759e5384af4bcb9d32279',
 'runtime_pin_status': 'finalized'}
BEFORE = {}
BEFORE_RECEIPT_SHA256 = None
EXPECTED_MAX_CALLS = 12
FORM_REQUEST_LIMIT = 24
FORM_REQUESTS = 0
CONFIG = Path('/etc/oak-website-forms/config.json')
STAGES = {'identifier','password','otp','login','device_approval','provider_blocked',
          'unsupported_challenge','unsupported_layout','authenticated','form','review'}
STATES = {'waiting','active','authenticated','owner_confirmed','cancelled','expired','failed'}
TYPES = {'text','email','password','otp','checkbox','select'}
EMITTED = set()
RESULT_STREAM = None
CODES = {'stale_form','navigation_pending','composition_unavailable','submit_uncertain'}
PUBLIC_METHODS = {'use email','continue with email','sign in with email','log in with email',
                  'use password','log in with password'}


def emit(record):
    if record.get('event') == 'site_result':
        EMITTED.add(record['site_id'])
    line = json.dumps(record, sort_keys=True, allow_nan=False)
    if RESULT_STREAM is not None:
        RESULT_STREAM.write(line + '\n')
        RESULT_STREAM.flush()
        os.fsync(RESULT_STREAM.fileno())
    print(line, flush=True)


def elapsed(start):
    return round((time.monotonic() - start) * 1000)


class Refused(Exception):
    def __init__(self, code):
        self.code = code


def composer_policy(value):
    if (type(value) is not dict or set(value) != {'backend','model','key_file','max_calls'} or
        value.get('backend') != 'jev' or value.get('model') != 'typesafe/jev-1.13' or
        type(value.get('max_calls')) is not int or not 1 <= value['max_calls'] <= 12 or
        type(value.get('key_file')) is not str or not value['key_file'].startswith('/') or
        len(value['key_file']) > 240):
        raise Refused('composer_configuration_invalid')
    if value['max_calls'] != EXPECTED_MAX_CALLS:
        raise Refused('composer_max_calls_mismatch')
    return value['max_calls']


def guard(initial=None):
    if FROZEN.get('runtime_pin_status') != 'finalized' or not FROZEN.get('code_sha256'):
        raise Refused('runtime_pins_pending')
    try:
        raw = CONFIG.read_bytes()
        current = hashlib.sha256(raw).hexdigest()
        config = json.loads(raw)
        composer_policy(config.get('composer'))
        if initial is not None and initial != current:
            raise Refused('configuration_changed')
        if os.getuid() != config.get('gateway_uid'):
            raise Refused('wrong_execution_uid')
        profile = json.loads(Path(config['execution']['profile']).read_text())
        config['code_root'] = profile['code_root']
        for path, expected in FROZEN['code_sha256'].items():
            actual = hashlib.sha256((Path(config['code_root']) / path).read_bytes()).hexdigest()
            if actual != expected:
                raise Refused('runtime_digest_changed')
        return config, current
    except Refused:
        raise
    except Exception:
        raise Refused('configuration_unavailable') from None


def selection():
    sites = FROZEN['sites']
    encoded = json.dumps(sites, sort_keys=True, separators=(',', ':')).encode()
    if hashlib.sha256(encoded).hexdigest() != FROZEN['frozen_sites_sha256']:
        raise Refused('selection_digest_changed')
    if len(sites) != 12 or len({x['site_id'] for x in sites}) != 12:
        raise Refused('selection_invalid')
    for site in sites:
        url = urlsplit(site['url'])
        if (set(site) != {'site_id','name','category','url'} or
            not re.fullmatch(r'[a-z0-9_-]{1,48}', site['site_id']) or
            not isinstance(site['category'], str) or len(site['category']) > 80 or
            url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.port not in (None,443) or url.query or url.fragment or
            url.hostname in {'accounts.google.com','www.instagram.com','www.tiktok.com'}):
            raise Refused('selection_invalid')
    return sites


async def post(client, url, body, headers=None):
    global FORM_REQUESTS
    if url.endswith('/api/form'):
        if FORM_REQUESTS >= FORM_REQUEST_LIMIT:
            raise Refused('form_request_budget_exhausted')
        FORM_REQUESTS += 1
    started = time.monotonic()
    record = {'http_status': None, 'elapsed_ms': 0, 'outcome': 'transport_failure'}
    try:
        async with client.post(url, json=body, headers=headers, allow_redirects=False) as response:
            record['http_status'] = response.status
            if not response.headers.get('Content-Type','').startswith('application/json'):
                record['outcome'] = 'invalid_content_type'
                return record, None
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > 65536:
                    record['outcome'] = 'response_too_large'
                    return record, None
            try:
                value = json.loads(raw)
                if type(value) is not dict:
                    raise ValueError
            except Exception:
                record['outcome'] = 'invalid_json'
                return record, None
            record['outcome'] = 'ok' if response.status == 200 else 'http_error'
            if value.get('recoverable') is True and value.get('code') in CODES:
                record.update(recoverable=True, code=value['code'])
            return record, value
    except asyncio.TimeoutError:
        record['outcome'] = 'timeout'
        return record, None
    except asyncio.CancelledError:
        raise
    except Exception:
        return record, None
    finally:
        record['elapsed_ms'] = elapsed(started)


def status_metadata(value):
    if type(value) is not dict:
        return {}
    return {'state': value.get('state') if value.get('state') in STATES else 'unknown',
            'cleanup': value.get('cleanup') if value.get('cleanup') in
                {'waiting','confirmed','uncertain','retained'} else 'unknown'}


def form_metadata(value, destination):
    if type(value) is not dict or value.get('state') not in STATES:
        return {'validation': 'invalid_response'}, None
    report = {'state': value['state']}
    if value['state'] != 'active':
        return report, None
    form = value.get('form')
    if (type(form) is not dict or form.get('provider') != 'website' or
        form.get('destination') != destination or
        form.get('form_schema') != 'website-v1' or form.get('stage') not in STAGES or
        type(form.get('fields')) is not list or len(form['fields']) > 16 or
        type(form.get('actions')) is not list or len(form['actions']) > 9 or
        any(type(f) is not dict or f.get('type') not in TYPES for f in form['fields']) or
        not isinstance(form.get('revision'),str) or len(form['revision']) > 128):
        return {'validation': 'invalid_form'}, None
    report.update(validation='valid', schema='website-v1', stage=form['stage'],
                  field_count=len(form['fields']), field_types=[f['type'] for f in form['fields']],
                  action_count=len(form['actions']))
    return report, form['revision']


def empty_result(site, reason):
    return {'event':'site_result', 'site_id':site['site_id'], 'category':site['category'],
            'outcome':reason, 'cleanup':'not_created',
            'observations':[{'number':n,'outcome':'not_attempted'} for n in (1,2)],
            'model_telemetry':{'available':False,'reason':'not_exposed_by_pinned_broker_api',
                               'actual_api_calls':None,'model_elapsed_ms':None,'cost_usd':None,'judgments':None}}


def method_eligible(form):
    if (type(form) is not dict or form.get('stage') != 'form' or form.get('fields') != [] or
        type(form.get('actions')) is not list or len(form['actions']) != 1):
        return False
    action = form['actions'][0]
    return (type(action) is dict and isinstance(action.get('label'),str) and
            action['label'].casefold() in PUBLIC_METHODS and
            re.fullmatch(r'a[0-9]{1,8}',str(action.get('id',''))) is not None)


async def probe(control, human, config, config_hash, site, public_method_step):
    report = empty_result(site, 'probe_failed')
    report['operations'] = {}
    started = time.monotonic()
    scope = None
    parsed_site = urlsplit(site['url'])
    destination = parsed_site.scheme + '://' + parsed_site.netloc
    interrupted = False
    report['method_step'] = {'enabled':public_method_step,'eligible':False,'attempted':False}
    try:
        guard(config_hash)
        binding = {'request_id':secrets.token_hex(16), 'owner':987654321, 'chat_id':987654321,
                   'thread_id':'oak-cross-site-model-benchmark','turn_id':'oak-cross-site-model-benchmark',
                   'runtime_epoch':'oak-cross-site-model-benchmark','panel_hash':'2'*64,
                   'expires':time.time()+300, 'provider':'website', 'url':site['url']}
        record, created = await post(control, 'http://broker/create', binding)
        report['operations']['create'] = record
        report['cleanup'] = 'unknown_create' if record['http_status'] in (None,200) else 'not_created'
        if (record['outcome'] != 'ok' or type(created) is not dict or
            created.get('request_id') != binding['request_id'] or
            created.get('runtime_epoch') != binding['runtime_epoch'] or
            not re.fullmatch(r'[a-f0-9]{32}', str(created.get('session_id','')))):
            report['outcome'] = 'create_failed'
            return report
        scope = {key:created[key] for key in ('request_id','session_id','runtime_epoch')}
        report['cleanup'] = 'unconfirmed'
        if (created.get('provider') != 'website' or created.get('origin') != config['origin'] or
            not re.fullmatch(r'[A-Za-z0-9_-]{43}',str(created.get('ticket','')))):
            report['outcome'] = 'create_invalid'
            return report
        guard(config_hash)
        record, attached = await post(human, config['origin']+'/api/attach',
            {'request_id':binding['request_id'],'ticket':created['ticket']}, {'Origin':config['origin']})
        report['operations']['attach'] = record
        if (record['outcome'] != 'ok' or type(attached) is not dict or
            attached.get('provider') != 'website' or attached.get('form_schema') != 'website-v1' or
            attached.get('destination') != destination or
            not re.fullmatch(r'[A-Za-z0-9_-]{43}', str(attached.get('lease','')))):
            report['outcome'] = 'attach_failed'
            return report
        headers = {'Origin':config['origin'],'Authorization':'Bearer '+attached['lease']}
        revisions = []
        latest = None
        for index in range(2):
            latest = None
            if index:
                await asyncio.sleep(2)
            guard(config_hash)
            heartbeat, status = await post(control, 'http://broker/status', scope)
            observation = {'number':index+1, 'control_status':{**heartbeat, **status_metadata(status)}}
            if heartbeat['outcome'] != 'ok':
                observation['outcome'] = 'control_status_failed'
                report['observations'][index] = observation
                continue
            record, value = await post(human, config['origin']+'/api/form',
                {'request_id':binding['request_id']}, headers)
            observation.update(record)
            if record['outcome'] == 'ok':
                summary, revision = form_metadata(value, destination)
                observation.update(summary)
                if revision is not None:
                    revisions.append(revision)
                    latest = value['form']
            report['observations'][index] = observation
        report['outcome'] = 'observed'
        report['revision_changed'] = len(revisions) == 2 and revisions[0] != revisions[1]
        eligible = method_eligible(latest)
        report['method_step']['eligible'] = eligible
        if public_method_step and eligible:
            guard(config_hash)
            heartbeat, _ = await post(control,'http://broker/status',scope)
            report['method_step']['control_status'] = heartbeat
            if heartbeat['outcome'] == 'ok':
                report['method_step']['attempted'] = True
                record, value = await post(human, config['origin']+'/api/submit',
                    {'request_id':binding['request_id'],'revision':latest['revision'],
                     'action':latest['actions'][0]['id'],'values':{}},headers)
                report['method_step']['submission'] = record
                report['method_step']['accepted'] = record['outcome']=='ok' and value.get('accepted') is True
                report['method_step']['uncertain'] = (record['http_status'] is None or
                    type(value) is dict and value.get('code') == 'submit_uncertain')
                guard(config_hash)
                heartbeat, _ = await post(control,'http://broker/status',scope)
                if heartbeat['outcome'] == 'ok':
                    record, value = await post(human,config['origin']+'/api/form',
                                               {'request_id':binding['request_id']},headers)
                    summary, _ = form_metadata(value, destination)
                    report['method_step']['next_observation'] = {**record, **summary}
    except Refused as error:
        report['outcome'] = error.code
    except asyncio.CancelledError:
        report['outcome'] = 'interrupted'
        interrupted = True
    except Exception:
        report['outcome'] = 'local_failure'
    finally:
        if scope is not None:
            try:
                async def cleanup():
                    cancelled_record, cancelled = await post(control,'http://broker/cancel',scope)
                    checked_record, checked = await post(control,'http://broker/status',scope)
                    report['operations']['cancel'] = {**cancelled_record, **status_metadata(cancelled)}
                    report['operations']['cleanup_status'] = {**checked_record, **status_metadata(checked)}
                    if (cancelled_record['outcome'] == checked_record['outcome'] == 'ok' and
                        all(type(value) is dict and value.get('state') == 'cancelled' and
                            value.get('cleanup') == 'confirmed' for value in (cancelled,checked))):
                        report['cleanup'] = 'confirmed'
                await asyncio.shield(cleanup())
            except BaseException:
                report['cleanup'] = 'unconfirmed'
        report['elapsed_ms'] = elapsed(started)
        report['interrupted'] = interrupted
    return report


async def main(run, public_method_step):
    global FORM_REQUEST_LIMIT
    FORM_REQUEST_LIMIT = 36 if public_method_step else 24
    sites = selection()
    if not run:
        emit({'event':'validation_only', 'network_calls':0, 'site_count':len(sites),
              'selection_sha256':FROZEN['selection_sha256'],
              'evaluation_kind':FROZEN['evaluation_kind'],
              'candidate_source_digest':FROZEN['candidate_source_digest'],
              'runtime_pin_status':FROZEN['runtime_pin_status']})
        return 0
    config, config_hash = guard()
    sys.path.insert(0, config['code_root'])
    from oak.executor import boundary_check
    if boundary_check(config['execution']) is not True:
        raise Refused('boundary_unavailable')
    emit({'event':'benchmark_manifest', 'site_count':12, 'selection_sha256':FROZEN['selection_sha256'],
          'runtime_digest':FROZEN['runtime_digest'], 'source_commit':FROZEN['source_commit'],
          'evaluation_kind':FROZEN['evaluation_kind'],
          'frozen_sites_sha256':FROZEN['frozen_sites_sha256'],
          'mode':'actual_deployed_broker_generic_model_enabled', 'composer_configured':True,
          'composer_backend':'jev','composer_model':'typesafe/jev-1.13',
          'configured_max_calls_per_lease':EXPECTED_MAX_CALLS,
          'protocol_form_request_limit':FORM_REQUEST_LIMIT,
          'api_request_upper_bound':12*min(EXPECTED_MAX_CALLS,6 if public_method_step else 4),
          'cost_usd_cap':None,'cost_telemetry':'unavailable',
          'before_receipt_sha256':BEFORE_RECEIPT_SHA256,
          'public_method_step_enabled':public_method_step, 'renderer_measured':False, 'concurrency':1,
          'operation_timeout_seconds':35, 'reads_per_attached_lease':2, 'retries':0})
    current = asyncio.current_task()
    for number in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(number,current.cancel)
    reports = []
    timeout = ClientTimeout(total=35)
    async with ClientSession(connector=UnixConnector(path=config['socket']),timeout=timeout,
            cookie_jar=DummyCookieJar(),trust_env=False) as control, \
            ClientSession(timeout=timeout,cookie_jar=DummyCookieJar(),trust_env=False) as human:
        health_record, health = await post(control,'http://broker/health',{})
        healthy = (health_record['outcome'] == 'ok' and type(health) is dict and
                   health.get('ready') is True and health.get('protocol') == 1 and
                   set(health.get('providers',[])) == {'google','instagram','website'})
        emit({'event':'broker_health', **health_record, 'ready':healthy})
        stop = None if healthy else 'not_attempted_health_failure'
        for site in sites:
            if stop:
                report = empty_result(site,stop)
            else:
                emit({'event':'site_attempt_started','site_id':site['site_id']})
                report = await probe(control,human,config,config_hash,site,public_method_step)
                if report.get('interrupted'):
                    stop = 'not_attempted_after_interruption'
                elif report['cleanup'] in {'unconfirmed','unknown_create'}:
                    stop = 'not_attempted_unconfirmed_cleanup'
                elif report['outcome'] in {'composer_configuration_invalid','composer_max_calls_mismatch','configuration_changed','runtime_digest_changed','form_request_budget_exhausted'}:
                    stop = 'not_attempted_configuration_change'
            before = BEFORE.get(site['site_id'])
            after_count = max(o.get('field_count',0) for o in report['observations'])
            if before is not None:
                report['before_after'] = {'baseline':before,'after_max_field_count':after_count,
                    'input_exposure_delta':after_count-before['max_field_count'],
                    'authentication_tested':False}
            else:
                report['holdout'] = {'prior_runtime_result':None,
                    'max_field_count':after_count,'authentication_tested':False}
            reports.append(report)
            emit(report)
    emit({'event':'benchmark_summary','denominator':12,'reported':len(reports),
          'attached':sum('attach' in r.get('operations',{}) and
                         r['operations']['attach']['outcome']=='ok' for r in reports),
          'valid_observations':sum(o.get('validation')=='valid' for r in reports for o in r['observations']),
          'sites_with_visible_fields':sum(any(o.get('field_count',0)>0 for o in r['observations']) for r in reports),
          'sites_with_actions':sum(any(o.get('action_count',0)>0 for o in r['observations']) for r in reports),
          'confirmed_cleanups':sum(r['cleanup']=='confirmed' for r in reports),
          'actions_submitted':sum(r.get('method_step',{}).get('attempted',False) for r in reports),
          'api_form_requests_issued':FORM_REQUESTS,'actual_model_calls':None,
          'model_telemetry_measured':False,'cost_usd':None,'login_success_claimed':False,
          'baseline_sites_with_visible_fields':0 if BEFORE else None,
          'sites_with_fields_after_method_step':sum(r.get('method_step',{}).get('next_observation',{}).get('field_count',0)>0 for r in reports)})
    return 0 if len(reports)==12 and all(r['cleanup'] in {'confirmed','not_created'} for r in reports) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--result',type=Path,help='New private JSONL receipt path; required with --run')
    parser.add_argument('--public-method-step',action='store_true')
    parser.add_argument('--expect-max-calls',type=int,choices=range(1,13),default=12)
    args = parser.parse_args()
    EXPECTED_MAX_CALLS = args.expect_max_calls
    try:
        if args.run:
            if args.result is None:
                raise Refused('exclusive_result_required')
            descriptor = os.open(args.result, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            RESULT_STREAM = os.fdopen(descriptor, 'w')
            directory = os.open(args.result.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            emit({'event':'benchmark_attempt_started','selection_sha256':FROZEN['selection_sha256'],
                  'evaluation_kind':FROZEN['evaluation_kind'],'retries':0})
        code = asyncio.run(main(args.run,args.public_method_step))
    except BaseException as error:
        reason = error.code if isinstance(error,Refused) else 'interrupted_or_local_failure'
        emit({'event':'benchmark_refused','code':reason,'planned_denominator':12})
        if args.run:
            for site in FROZEN['sites']:
                if site['site_id'] not in EMITTED:
                    emit(empty_result(site,'not_attempted_preflight_or_run_failure'))
            emit({'event':'benchmark_aborted_summary','denominator':12,'reported':len(EMITTED)})
        code = 1
    finally:
        if RESULT_STREAM is not None:
            RESULT_STREAM.close()
    raise SystemExit(code)
