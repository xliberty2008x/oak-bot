"""Opt-in production observations and a bounded, approved Harness² queue worker.

Real conversations contribute local metadata only. Raw prompts, replies, files,
paths and provider errors never become optimizer inputs through this bridge.
The current native replay adapter supports approved synthetic choreography;
other real cases remain awaiting an adapter and source authorization.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
from functools import partial
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from .harness2_pipeline import BudgetStopped, PipelineStore, canonical, digest, file_sha, work
from .runtime import RuntimeClient


class ExecutionRuntimeClient:
    """Use the deployment's protected subscription without exposing its home.

    Host context/tool directories remain host-side. Only the native process cwd
    and explicit native cwd requests are mapped into its fixed approved mount.
    Permissions, model, dynamic tools and callbacks are passed through unchanged.
    """
    def __init__(self, *, execution, native_guard=None, native_lifecycle=None, **kwargs):
        from .executor import load_profile
        profile = load_profile(execution)
        if kwargs.get('sandbox') != 'read-only' or kwargs.get('config', {}).get('web_search') != 'disabled':
            raise ValueError('Improvement inference requires the confined read-only runtime.')
        self.native_guard, self.native_lifecycle = native_guard, native_lifecycle
        self.registration = None
        if self.native_guard is not None:
            self.native_guard()
        self.workspace = profile['workspace']
        kwargs.update(cwd=self.workspace, home=profile['runtime_home'])
        self.client = RuntimeClient(execution=execution, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def __aenter__(self):
        if self.native_guard is not None:
            self.native_guard()
        if self.native_lifecycle is not None:
            self.registration = self.native_lifecycle.register_native()
        try:
            await self.client.__aenter__()
        except BaseException:
            self._unregister()
            raise
        return self

    async def __aexit__(self, *args):
        try:
            return await self.client.__aexit__(*args)
        finally:
            self._unregister()

    def _unregister(self):
        if self.registration is not None:
            self.native_lifecycle.unregister_native(self.registration)
            self.registration = None

    async def request(self, method, params):
        if method == 'turn/start' and self.native_guard is not None:
            self.native_guard()
        if method in {'config/read', 'thread/start', 'thread/resume'} and 'cwd' in params:
            params = {**params, 'cwd': self.workspace}
        return await self.client.request(method, params)


def configuration(deployment):
    value = deployment.get('harness_improvement')
    if value is None or isinstance(value, dict) and value.get('enabled') is False:
        return None
    if not isinstance(value, dict) or set(value) - {'enabled', 'root', 'project_id', 'domain', 'chat_ids', 'worker'}:
        raise ValueError('Invalid opt-in improvement configuration.')
    if value.get('enabled', True) is not True:
        raise ValueError('Improvement enabled must be boolean.')
    root = Path(value.get('root', ''))
    chats = value.get('chat_ids')
    if (not root.is_absolute() or '..' in root.parts or root == Path('/') or root.is_symlink()
            or not isinstance(chats, list) or not 1 <= len(chats) <= 20
            or any(type(chat) is not int for chat in chats) or len(set(chats)) != len(chats)):
        raise ValueError('Improvement requires a private absolute root and explicit conversation IDs.')
    for name in ('project_id', 'domain'):
        if not isinstance(value.get(name), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value[name]):
            raise ValueError('Improvement scope identifiers are required.')
    worker = value.get('worker', {})
    if not isinstance(worker, dict) or set(worker) - {'enabled', 'cycle_limit', 'poll_seconds', 'max_producers', 'max_roles', 'max_role_tools'}:
        raise ValueError('Invalid bounded improvement worker configuration.')
    settings = {'enabled': True, 'cycle_limit': 1, 'poll_seconds': 30,
                'max_producers': 16, 'max_roles': 8, 'max_role_tools': 512, **worker}
    if type(settings['enabled']) is not bool:
        raise ValueError('Worker enabled must be boolean.')
    for key, maximum in [('cycle_limit',20), ('poll_seconds',3600), ('max_producers',32), ('max_roles',12), ('max_role_tools',768)]:
        if type(settings[key]) is not int or not 1 <= settings[key] <= maximum:
            raise ValueError('Worker cycle, call and tool limits must be finite.')
    return {**value, 'root': str(root), 'worker': settings}


def _metadata(kind, supplied):
    """Only fixed categorical facts, counts and opaque identifiers may persist."""
    if kind not in {'task_terminal', 'artifact_review', 'artifact_queued', 'artifact_delivery', 'owner_feedback'}:
        raise ValueError('Unknown production observation.')
    if not isinstance(supplied, dict):
        raise ValueError('Observation metadata must be structured.')
    result = {}
    enums = {'status': {'completed','failed','interrupted','uncertain'},
             'verdict': {'pass','revise','unverified'}, 'delivery_status': {'queued','confirmed'},
             'transport': {'telegram'}, 'model': {'gpt-6.1-sol'}}
    for name, allowed in enums.items():
        if isinstance(supplied.get(name), str) and supplied[name] in allowed:
            result[name] = supplied[name]
    for name in ('sha256','procedure_sha256','version_id'):
        if isinstance(supplied.get(name), str) and re.fullmatch('[a-f0-9]{64}', supplied[name]):
            result[name] = supplied[name]
    for name in ('message_id','reply_to_message_id','delivery_reference','review_id'):
        value = supplied.get(name)
        if type(value) in (str,int) and 1 <= len(str(value)) <= 240:
            result[name + '_ref'] = digest(str(value))
    for name in ('issues','major_issues','checks'):
        if type(supplied.get(name)) is int and 0 <= supplied[name] <= 32:
            result[name] = supplied[name]
    return result


class ProductionBridge:
    def __init__(self, deployment):
        self.settings = configuration(deployment)
        if self.settings is None:
            raise ValueError('Production improvement is not enabled.')
        self.deployment = deployment
        self.root = Path(self.settings['root'])
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.chats = tuple(self.settings['chat_ids'])
        self.project, self.domain = self.settings['project_id'], self.settings['domain']
        self._shutdown = threading.Event()
        self._native_lock = threading.RLock()
        self._native_tasks = set()
        self.activation = {'store_path': str(self.root / 'versions.sqlite3'),
                           'project_id': self.project, 'domain': self.domain}
        if deployment.get('harness_activation') not in (None, self.activation):
            raise ValueError('Improvement and application must use the same trusted version scope.')
        self.path = self.root / 'production.sqlite3'
        with self._db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
                domain TEXT NOT NULL, kind TEXT NOT NULL,
                request_ref TEXT, turn_ref TEXT, case_id TEXT NOT NULL, metadata TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS worker_status (
                project_id TEXT NOT NULL, chat_id INTEGER NOT NULL, domain TEXT NOT NULL,
                value TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(project_id,chat_id,domain));''')
        self.path.chmod(0o600)

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def store(self, chat):
        if chat not in self.chats:
            raise ValueError('Conversation is outside the configured improvement scope.')
        return PipelineStore(self.root, self.project, chat, self.domain)

    def _guard(self, chat):
        if self._shutdown.is_set():
            raise BudgetStopped('Production worker is shutting down.')
        store = self.store(chat)
        try:
            if store.setting('stopped'):
                raise BudgetStopped('Production improvement is paused for this conversation.')
        finally:
            store.close()

    def register_native(self):
        item = (asyncio.get_running_loop(), asyncio.current_task())
        with self._native_lock:
            if self._shutdown.is_set():
                raise BudgetStopped('Production worker is shutting down.')
            self._native_tasks.add(item)
        return item

    def unregister_native(self, item):
        with self._native_lock:
            self._native_tasks.discard(item)

    def shutdown(self):
        """Cancel only this worker's native tasks; never persist an owner pause."""
        self._shutdown.set()
        with self._native_lock:
            for loop, task in tuple(self._native_tasks):
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass  # A task whose event loop already closed cannot launch more work.

    def ensure_baseline(self, chat):
        if chat not in self.chats:
            return
        from .harness_versions import HarnessVersions
        versions = HarnessVersions(self.activation['store_path'])
        try:
            if versions.resolve(self.project, chat, self.domain) is None:
                versions.bootstrap_current(self.project, chat, self.domain, '', evidence={
                    'source': 'existing-production-controller', 'comparative_gain_claim': False})
        finally:
            versions.close()

    def record(self, chat, kind, supplied, *, request_id=None, turn_id=None):
        if chat not in self.chats:
            return None
        metadata = _metadata(kind, supplied)
        if kind == 'artifact_delivery' and metadata.get('delivery_status') != 'confirmed':
            return None  # A queued or uncertain transport is not delivery evidence.
        request, turn = (digest(str(v)) if v is not None else None for v in (request_id,turn_id))
        identity = digest({'scope': [self.project,chat,self.domain], 'kind':kind,
                           'request_ref':request,'turn_ref':turn,'metadata':metadata})
        with self._db() as db:
            old = db.execute('SELECT case_id FROM observations WHERE id=?', (identity,)).fetchone()
            if old:
                return old[0]
            if db.execute('SELECT COUNT(*) FROM observations WHERE project_id=? AND chat_id=? AND domain=?',
                          (self.project,chat,self.domain)).fetchone()[0] >= 10000:
                return None  # Bound metadata retention without exposing discarded content.
            store = self.store(chat)
            try:
                payload = {'visibility':'local_metadata','source':'production-observation',
                    'adapter_status':'awaiting_adapter','event':kind,'request_ref':request,
                    'turn_ref':turn,'metadata':metadata,'optimizer_disclosure_authorized':False}
                case_id = store.record_case(payload, signature='production-'+kind, approved=False,
                    priority=100 if kind=='owner_feedback' or metadata.get('status') in {'failed','uncertain'}
                    or metadata.get('verdict') in {'revise','unverified'} else 10)
                target = None
                if kind == 'owner_feedback' and metadata.get('delivery_reference_ref'):
                    target = db.execute('SELECT case_id FROM observations WHERE project_id=? AND chat_id=? AND domain=? AND request_ref=? '
                        'AND kind="artifact_delivery" ORDER BY created DESC LIMIT 1',
                        (self.project,chat,self.domain, metadata['delivery_reference_ref'])).fetchone()
                if target:
                    store.add_feedback(target[0], {'source':'owner-reply-reference','metadata':metadata,
                                                  'optimizer_disclosure_authorized':False})
                db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (identity,self.project,chat,self.domain,kind,request,turn,case_id,canonical(metadata),time.time()))
                return case_id
            finally:
                store.close()

    def _status(self, chat, state, **extra):
        value = {'state':state,'model':'gpt-6.1-sol','effort':'low','scope':[self.project,chat,self.domain],
            'monitoring':'local-metadata-only; production quality rollback requires conclusive supported evaluation',
            'automatic_private_disclosure':False,**extra}
        with self._db() as db:
            db.execute('INSERT INTO worker_status VALUES (?,?,?,?,?) ON CONFLICT(project_id,chat_id,domain) '
                       'DO UPDATE SET value=excluded.value,updated=excluded.updated',
                       (self.project,chat,self.domain,canonical(value),time.time()))
        return value

    def status(self):
        with self._db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT value FROM worker_status WHERE project_id=? '
                'AND domain=? AND chat_id IN ('+','.join('?' for _ in self.chats)+') ORDER BY chat_id',
                (self.project,self.domain,*self.chats))]

    def control(self, chat, action='status'):
        if action not in {'status','stop','resume'}:
            raise ValueError('Use status, stop or resume.')
        store = self.store(chat)
        try:
            if action != 'status':
                store.setting('stopped', action=='stop')
            cases, cycles = store.cases(), store.cycles()
            paused = bool(store.setting('stopped'))
            from .harness_versions import HarnessVersions
            versions = HarnessVersions(self.activation['store_path'])
            try:
                active = versions.resolve(self.project, chat, self.domain)
            finally:
                versions.close()
            with self._db() as db:
                row = db.execute('SELECT value FROM worker_status WHERE project_id=? AND chat_id=? AND domain=?',
                                 (self.project,chat,self.domain)).fetchone()
            state = json.loads(row[0]) if row else {}
            last = cycles[-1] if cycles else None
            decision = last.get('decision') if last else None
            return {'enabled':True,'phase':'stopped' if paused else state.get('state','awaiting-approved-case'),
                'cases':len(cases),'approved_cases':sum(bool(c['approved']) for c in cases),
                'awaiting_adapter':sum(c['payload'].get('adapter_status')=='awaiting_adapter' for c in cases),
                'awaiting_approval':sum(not c['approved'] for c in cases),
                'active_version':active['id'] if active else None, 'last_cycle':last['ordinal'] if last else None,
                'last_decision':({'accepted':decision['accepted'],'reasons':decision['reasons']} if decision else None),
                'paused':paused,'reason':state.get('reason')}
        finally:
            store.close()

    def transport(self):
        from .executor import require_boundary
        execution = self.deployment.get('execution')
        if not isinstance(execution, dict):
            raise ValueError('Protected subscription execution profile required; no local auth fallback.')
        profile = require_boundary(execution)
        identity = {'kind':'existing-oak-protected-subscription','policy_digest':profile['policy_digest'],
                    'profile_sha256':file_sha(execution['profile']), 'manifest_sha256':digest(profile['manifest']),
                    'bridge_source_sha256':file_sha(__file__), 'model':'gpt-6.1-sol','effort':'low'}
        return partial(ExecutionRuntimeClient, execution=dict(execution)), profile['runtime_home'], identity

    def tick(self):
        """One bounded restartable pass; never invent cases or launch a demo."""
        with (self.root/'production-worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return self.status()
            return self._tick()

    def _tick(self):
        states = []
        for chat in self.chats:
            if self._shutdown.is_set():
                break
            store = self.store(chat)
            try:
                cfg = self.settings['worker']
                if not cfg['enabled'] or store.setting('stopped'):
                    states.append(self._status(chat,'stopped')); continue
                cycles = store.cycles()
                # One persistent authorization across all chats in this project/domain,
                # including chats subsequently removed from the allowlist. Never per restart.
                scopes = [json.loads(row[0]) for row in store.db.execute('SELECT scope FROM cycles')]
                total_cycles = sum(scope[0] == self.project and scope[2] == self.domain for scope in scopes)
                pending = any(c['phase']!='completed' for c in cycles)
                if pending and total_cycles>cfg['cycle_limit']:
                    states.append(self._status(chat,'stopped',reason='cycle_budget_exhausted')); continue
                if not pending and total_cycles >= cfg['cycle_limit']:
                    states.append(self._status(chat,'stopped',reason='cycle_budget_exhausted')); continue
                cases = store.cases()
                eligible = [c for c in cases if c['approved'] and c['payload'].get('descriptor',{}).get('family')=='choreography'
                            and c['payload']['descriptor'].get('split')=='dev']
                if not pending and not eligible:
                    states.append(self._status(chat,'awaiting-approved-case',
                        awaiting_adapter=sum(c['payload'].get('adapter_status')=='awaiting_adapter' for c in cases),
                        unsupported_approved_cases=sum(c['approved'] for c in cases))); continue
                try:
                    factory, home, identity = self.transport()
                except Exception:
                    states.append(self._status(chat,'blocked',reason='protected_subscription_execution_unavailable')); continue
                self._status(chat,'running')
                guard = partial(self._guard, chat)
                store.invocation_guard = guard
                factory = partial(factory, native_guard=guard, native_lifecycle=self)
                before = len(cycles)
                try:
                    work(store, runtime_home=home, max_cycles=1, native_client_factory=factory,
                         transport_identity=identity, budget_limits={k:cfg[k] for k in ('max_producers','max_roles','max_role_tools')})
                except BudgetStopped:
                    states.append(self._status(chat,'stopped',reason='paused_before_next_native_attempt')); continue
                except Exception as exc:
                    states.append(self._status(chat,'blocked',reason='cycle_requires_operator_review',error_class=type(exc).__name__)); continue
                after = store.cycles()
                states.append(self._status(chat,'awaiting-approved-case',completed_cycles=sum(c['phase']=='completed' for c in after),
                    reason='no_new_approved_feedback_revision' if len(after)==before and not pending else 'bounded_pass_completed'))
            finally:
                store.close()
        return states

    async def run(self):
        # A dedicated daemon thread avoids making asyncio's default executor
        # wait for an entire model cycle during gateway shutdown. Active native
        # tasks are explicitly cancelled; both reservation and client launch
        # guards prevent subsequent native attempts in the shutting-down thread.
        loop, thread = asyncio.get_running_loop(), None
        try:
            while not self._shutdown.is_set():
                completed = loop.create_future()

                def finish(error):
                    if not completed.done():
                        completed.set_result(error)

                def execute():
                    error = None
                    try:
                        self.tick()
                    except BaseException as exc:
                        error = type(exc).__name__
                    try:
                        loop.call_soon_threadsafe(finish, error)
                    except RuntimeError:
                        pass

                thread = threading.Thread(target=execute, name='oak-improvement', daemon=True)
                thread.start()
                if await completed:
                    for chat in self.chats:
                        try:
                            self._status(chat,'blocked',reason='worker_observation_unavailable')
                        except Exception:
                            pass
                await asyncio.sleep(self.settings['worker']['poll_seconds'])
        finally:
            self.shutdown()
            deadline = loop.time() + 2
            while thread is not None and thread.is_alive() and loop.time() < deadline:
                await asyncio.sleep(0.02)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('command', choices=['worker','status'])
    args = parser.parse_args(argv)
    from .__main__ import load_config
    bridge = ProductionBridge(load_config(args.config)[1])
    if args.command == 'status':
        print(canonical(bridge.status()))
    else:
        asyncio.run(bridge.run())


if __name__ == '__main__':
    main()
