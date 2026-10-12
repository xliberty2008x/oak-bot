#!/usr/bin/env python3
"""Frozen independent-site benchmark of the actual deployed generic broker.

Run through stdin as the configured gateway UID. Default mode is local
validation only. --run enables real public-page GETs via fresh broker leases.
No field input, owner profile, model mock, API stub or retry exists.
The optional uniform method step submits one eligible zero-field action only.
All errors remain in the denominator. Print only bounded metadata to stdout.
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

FROZEN = {'sites': [{'site_id': 'stack_overflow', 'name': 'Stack Overflow', 'category': 'developer', 'url': 'https://stackoverflow.com/users/login'}, {'site_id': 'discord', 'name': 'Discord', 'category': 'communication', 'url': 'https://discord.com/login'}, {'site_id': 'netflix', 'name': 'Netflix', 'category': 'media', 'url': 'https://www.netflix.com/login'}, {'site_id': 'etsy', 'name': 'Etsy', 'category': 'retail', 'url': 'https://www.etsy.com/signin'}, {'site_id': 'notion', 'name': 'Notion', 'category': 'productivity', 'url': 'https://www.notion.so/login'}, {'site_id': 'coinbase', 'name': 'Coinbase', 'category': 'finance', 'url': 'https://login.coinbase.com/signin'}, {'site_id': 'airbnb', 'name': 'Airbnb', 'category': 'travel', 'url': 'https://www.airbnb.com/login'}, {'site_id': 'medium', 'name': 'Medium', 'category': 'publishing_community', 'url': 'https://medium.com/m/signin'}, {'site_id': 'atlassian', 'name': 'Atlassian', 'category': 'productivity', 'url': 'https://id.atlassian.com/login'}, {'site_id': 'gitlab', 'name': 'GitLab', 'category': 'developer', 'url': 'https://gitlab.com/users/sign_in'}, {'site_id': 'reddit', 'name': 'Reddit', 'category': 'publishing_community', 'url': 'https://www.reddit.com/login'}, {'site_id': 'twitch', 'name': 'Twitch', 'category': 'media', 'url': 'https://www.twitch.tv/login'}], 'selection_sha256': 'c85b624a59f38e98fb0d5d1c18c2704b068cbd2fd59ea41e0606985ecadf3ac4', 'source_commit': '71f966e57230b68971a44347a78882e31295ba9b', 'code_sha256': {'oak/login_broker.py': '5ad933ea9b3cdbfb66aaf7efa9dac2a70540d3042939d74436622c826741920c', 'oak/signin_providers.py': '6f938919d91aa4f9252007a021bcf86986145c1721863035c75b8e4a7fbc65fd', 'oak/website_forms.py': '576960e44a0d3a807f9ce0ce8e23c05d3aeffc9c11f0a6fa2478af45d73e228f', 'oak/website_resources.py': '106f9c8c6cab5fa43b02f9b73181248d9f6104979345813883439374c679f268', 'oak/website_ui.py': '7a6e3b0481c111624388c1a2c1e7641a5d8105e33a8a05e875d34034a87e8066', 'oak/website_planner.py': 'a3a3bf0525bbcca9b93154425e62504f5e85a62d377aa177e0675a3a307093d1', 'oak/website_composer.py': '26bfa931705b6f6ecbf1bd951e0da2ffb88e45f464af936730c42e6f31b3fb6e', 'oak/executor.py': '6bb37b8960235bdeafd2548babf5b8c874b8de6c2d2a0d7a74a7a062056de50f'}, 'runtime_digest': 'e0d3a9b7099e7444719032e06baa90fb620448a329ee4d79598368db6c844a0c'}
CONFIG = Path('/etc/oak-website-forms/config.json')
STAGES = {'identifier','password','otp','login','device_approval','provider_blocked',
          'unsupported_challenge','unsupported_layout','authenticated','form','review'}
STATES = {'waiting','active','authenticated','owner_confirmed','cancelled','expired','failed'}
TYPES = {'text','email','password','otp','checkbox','select'}
EMITTED = set()
CODES = {'stale_form','navigation_pending','composition_unavailable','submit_uncertain'}
PUBLIC_METHODS = {'use email','continue with email','sign in with email','log in with email',
                  'use password','log in with password'}


def emit(record):
    if record.get('event') == 'site_result':
        EMITTED.add(record['site_id'])
    print(json.dumps(record, sort_keys=True, allow_nan=False), flush=True)


def elapsed(start):
    return round((time.monotonic() - start) * 1000)


class Refused(Exception):
    def __init__(self, code):
        self.code = code


def guard(initial=None):
    try:
        raw = CONFIG.read_bytes()
        current = hashlib.sha256(raw).hexdigest()
        config = json.loads(raw)
        if 'composer' in config:
            raise Refused('composer_configured')
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


def form_metadata(value):
    if type(value) is not dict or value.get('state') not in STATES:
        return {'validation': 'invalid_response'}, None
    report = {'state': value['state']}
    if value['state'] != 'active':
        return report, None
    form = value.get('form')
    if (type(form) is not dict or form.get('provider') != 'website' or
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
            'observations':[{'number':n,'outcome':'not_attempted'} for n in (1,2)]}


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
    interrupted = False
    report['method_step'] = {'enabled':public_method_step,'eligible':False,'attempted':False}
    try:
        guard(config_hash)
        binding = {'request_id':secrets.token_hex(16), 'owner':987654321, 'chat_id':987654321,
                   'thread_id':'oak-cross-site-read-benchmark','turn_id':'oak-cross-site-read-benchmark',
                   'runtime_epoch':'oak-cross-site-read-benchmark','panel_hash':'2'*64,
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
                summary, revision = form_metadata(value)
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
                    summary, _ = form_metadata(value)
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
    sites = selection()
    config, config_hash = guard()
    sys.path.insert(0, config['code_root'])
    from oak.executor import boundary_check
    if boundary_check(config['execution']) is not True:
        raise Refused('boundary_unavailable')
    emit({'event':'benchmark_manifest', 'site_count':12, 'selection_sha256':FROZEN['selection_sha256'],
          'runtime_digest':FROZEN['runtime_digest'], 'source_commit':FROZEN['source_commit'],
          'mode':'actual_deployed_broker_generic_read_only', 'composer_configured':False,
          'public_method_step_enabled':public_method_step, 'renderer_measured':False, 'concurrency':1,
          'operation_timeout_seconds':35, 'reads_per_attached_lease':2, 'retries':0})
    if not run:
        emit({'event':'validation_only', 'network_calls':0})
        return 0
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
                report = await probe(control,human,config,config_hash,site,public_method_step)
                if report.get('interrupted'):
                    stop = 'not_attempted_after_interruption'
                elif report['cleanup'] in {'unconfirmed','unknown_create'}:
                    stop = 'not_attempted_unconfirmed_cleanup'
                elif report['outcome'] in {'composer_configured','configuration_changed','runtime_digest_changed'}:
                    stop = 'not_attempted_configuration_change'
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
          'model_calls_requested':0,'model_telemetry_measured':False,'login_success_claimed':False})
    return 0 if len(reports)==12 and all(r['cleanup'] in {'confirmed','not_created'} for r in reports) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--public-method-step',action='store_true')
    args = parser.parse_args()
    try:
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
    raise SystemExit(code)
