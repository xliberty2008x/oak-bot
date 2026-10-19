"""Real production seams with local journals and mocked native transport only."""
import asyncio
from functools import partial
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from oak.controller import Controller
from oak.harness2_pipeline import BudgetStopped, PipelineStore, default_config, digest, run_cycle
from oak.harness2_production import ExecutionRuntimeClient, ProductionBridge, configuration


def deployment(root, *, chats=(41,), project='live-project', domain='media-depth', **worker):
    return {'harness_improvement': {'enabled': True, 'root': str(root), 'project_id': project,
            'domain': domain, 'chat_ids': list(chats), 'worker': worker}}


def approved_case(store, label='public-dev'):
    return store.record_case({'visibility':'public_fixture', 'descriptor':{
        'id':label, 'family':'choreography', 'split':'dev'}}, signature='public-depth', approved=True)


def complete_fake_cycle(store, **kwargs):
    """Exercise worker ownership/quota, not optimization or model quality."""
    manifest = {split:[{'descriptor':{'id':split},
        'commitment':{'inputs':{'source.png':digest(split)}, 'sha256':digest(split)}}]
        for split in ('dev','regression','holdout')}
    config = {'model':'gpt-6.1-sol','effort':'low',
        'upstream_commit':'1cfe254e8d25b15f37048c3730134686abe60335',
        'max_producers':16,'max_roles':8,'max_role_tools':512,'producer_timeout':600,'role_timeout':300}
    cycle = store.create_cycle('public-depth', manifest, config)
    store.update(cycle['id'], phase='completed')


class ProductionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)/'private-improvement'
        self.config = deployment(self.root)
        self.bridge = ProductionBridge(self.config)

    def store(self, bridge=None, chat=41):
        store = (bridge or self.bridge).store(chat)
        self.addCleanup(store.close)
        return store

    def test_opt_in_is_explicit_scoped_and_bounded(self):
        self.assertIsNone(configuration({}))
        self.assertIsNone(configuration({'harness_improvement':{'enabled':False}}))
        for change in ({'chat_ids':[]}, {'chat_ids':[True]}, {'root':'relative'},
                       {'worker':{'cycle_limit':0}}, {'worker':{'max_producers':100000}},
                       {'worker':{'unbounded':True}}, {'private_prompt':'CANARY'}):
            config = deployment(self.root)
            config['harness_improvement'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ProductionBridge(config)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.bridge.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.bridge.store(42)
        self.assertIsNone(self.bridge.record(42,'task_terminal',{'status':'failed'}))
        with self.assertRaises(ValueError):
            ProductionBridge({**self.config,'harness_activation':{'store_path':'foreign'}})

    def test_real_observations_are_private_metadata_unapproved_and_idempotent(self):
        canary = 'PRIVATE_PROMPT_PATH_ACCOUNT_ERROR_CANARY'
        metadata = {'status':'failed','sha256':'a'*64, 'checks':3,'prompt':canary,
            'path':'/'+canary,'caption':canary,'summary':canary,'provider_error':canary,
            'review_id':canary,'model':'another-model','verdict':{'text':canary}}
        first = self.bridge.record(41,'artifact_review',metadata, request_id=canary,turn_id=canary)
        self.assertEqual(first,self.bridge.record(41,'artifact_review',metadata,request_id=canary,turn_id=canary))
        store = self.store()
        rows = store.cases()
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]['approved'])
        self.assertEqual(rows[0]['payload']['adapter_status'],'awaiting_adapter')
        self.assertFalse(rows[0]['payload']['optimizer_disclosure_authorized'])
        self.assertNotIn('descriptor',rows[0]['payload'])
        self.assertEqual(store.cases(approved_only=True),[])
        self.assertNotIn(canary, json.dumps(rows))
        with self.bridge._db() as db:
            dump = '\n'.join(db.iterdump())
        self.assertNotIn(canary,dump)
        self.assertNotIn('another-model',dump)
        with patch.object(self.bridge,'transport') as transport, patch('oak.harness2_production.work') as worker:
            self.bridge.tick()
        transport.assert_not_called()
        worker.assert_not_called()
        self.assertEqual(self.bridge.control(41)['awaiting_adapter'],1)
        self.assertEqual(self.bridge.control(41)['awaiting_approval'],1)

    def test_scoped_owner_reply_links_only_confirmed_delivery_reference_without_text(self):
        self.assertIsNone(self.bridge.record(41,'artifact_delivery',{'delivery_status':'queued'},request_id='not-confirmed'))
        target = self.bridge.record(41,'artifact_delivery',{'delivery_status':'confirmed','transport':'telegram',
            'message_id':517}, request_id='telegram-delivery:7')
        self.bridge.record(41,'owner_feedback',{'delivery_reference':'telegram-delivery:7',
            'reply_to_message_id':517,'message':'PRIVATE_REPLY_CANARY'},request_id='telegram-update:10')
        store = self.store()
        row = store.db.execute('SELECT case_id,value FROM feedback WHERE scope=?',(store.key,)).fetchone()
        self.assertEqual(row['case_id'],target)
        self.assertNotIn('PRIVATE_REPLY_CANARY',row['value'])
        self.assertFalse(json.loads(row['value'])['optimizer_disclosure_authorized'])
        for config in (deployment(self.root,chats=(42,)),deployment(self.root,project='another-project'),
                       deployment(self.root,domain='another-domain')):
            other = ProductionBridge(config)
            chat = other.chats[0]
            other.record(chat,'owner_feedback',{'delivery_reference':'telegram-delivery:7'},request_id='different')
            other_store = self.store(other,chat)
            self.assertEqual(other_store.db.execute('SELECT COUNT(*) FROM feedback WHERE scope=?',
                                                   (other_store.key,)).fetchone()[0],0)

    def test_status_and_stop_resume_do_not_cross_project_chat_or_domain(self):
        self.bridge._status(41,'running')
        for config in (deployment(self.root,chats=(42,)),deployment(self.root,project='another-project'),
                       deployment(self.root,domain='another-domain')):
            other = ProductionBridge(config)
            self.assertEqual(other.status(),[])
            other._status(other.chats[0],'blocked')
        self.assertEqual([r['state'] for r in self.bridge.status()],['running'])
        self.assertTrue(self.bridge.control(41,'stop')['paused'])
        self.assertFalse(self.bridge.control(41,'resume')['paused'])
        with self.assertRaises(ValueError):
            self.bridge.control(42,'stop')
        with self.assertRaises(ValueError):
            self.bridge.control(41,'approve')

    def test_protected_transport_requires_current_boundary_and_seals_actual_bridge_source(self):
        with self.assertRaises(ValueError):
            self.bridge.transport()
        profile_path = self.root/'profile.json'
        profile_path.write_text('{"protected":"fixture"}')
        self.bridge.deployment = {**self.config,'execution':{'profile':str(profile_path)}}
        profile = {'workspace':'/fixed/workspace','runtime_home':'/fixed/home',
                   'policy_digest':'p'*64,'manifest':{'executor.py':'a'*64}}
        with patch('oak.executor.require_boundary',return_value=profile) as boundary:
            factory,home,identity = self.bridge.transport()
        boundary.assert_called_once_with(self.bridge.deployment['execution'])
        self.assertIs(factory.func,ExecutionRuntimeClient)
        self.assertEqual(home,'/fixed/home')
        self.assertEqual(identity['model'],'gpt-6.1-sol')
        self.assertEqual(identity['effort'],'low')
        self.assertEqual(len(identity['bridge_source_sha256']),64)

    def test_missing_boundary_blocks_before_work_and_never_falls_back(self):
        approved_case(self.store())
        with (patch.object(self.bridge,'transport',side_effect=ValueError('PRIVATE_ERROR')),
              patch('oak.harness2_production.work') as worker):
            state = self.bridge.tick()[0]
        worker.assert_not_called()
        self.assertEqual(state['state'],'blocked')
        self.assertEqual(state['reason'],'protected_subscription_execution_unavailable')
        self.assertNotIn('PRIVATE_ERROR',json.dumps(state))

    def test_global_persistent_one_cycle_limit_across_chats_and_restarts(self):
        bridge = ProductionBridge(deployment(self.root,chats=(41,42),cycle_limit=1))
        for chat in bridge.chats:
            approved_case(self.store(bridge,chat),str(chat))
        transport = (Mock(name='confined-factory'),'/protected/home',{'kind':'test-boundary'})
        with (patch.object(bridge,'transport',return_value=transport),
              patch('oak.harness2_production.work',side_effect=complete_fake_cycle) as worker):
            states = bridge.tick()
        self.assertEqual(worker.call_count,1)
        kwargs = worker.call_args.kwargs
        self.assertEqual(kwargs['max_cycles'],1)
        self.assertEqual(kwargs['budget_limits'],{'max_producers':16,'max_roles':8,'max_role_tools':512})
        self.assertEqual(kwargs['transport_identity'],{'kind':'test-boundary'})
        self.assertEqual(states[1]['reason'],'cycle_budget_exhausted')
        reopened = ProductionBridge(deployment(self.root,chats=(42,),cycle_limit=1))
        with patch.object(reopened,'transport') as transport, patch('oak.harness2_production.work') as worker:
            self.assertEqual(reopened.tick()[0]['reason'],'cycle_budget_exhausted')
        transport.assert_not_called()
        worker.assert_not_called()

    def test_operator_pause_prevents_new_budget_intents_and_can_resume(self):
        store = self.store()
        self.bridge.control(41,'stop')
        store.invocation_guard = partial(self.bridge._guard,41)
        # The guard runs before a cycle lookup or durable operation reservation.
        with self.assertRaises(BudgetStopped):
            store.budget_hook('not-a-real-cycle')('producer',{})
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)
        self.bridge.control(41,'resume')
        self.bridge._guard(41)
        self.bridge.shutdown()
        with self.assertRaises(BudgetStopped):
            self.bridge._guard(41)
        self.assertFalse(bool(store.setting('stopped')))


class ProtectedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_native_cwd_is_mapped_and_actual_model_permissions_tools_are_preserved(self):
        client = SimpleNamespace(request=AsyncMock(return_value={'ok':True}),
            __aenter__=AsyncMock(),__aexit__=AsyncMock())
        profile = {'workspace':'/approved/workspace','runtime_home':'/approved/home'}
        callback,guard = AsyncMock(),Mock()
        config = {'web_search':'disabled','approval_policy':'never','features.shell_tool':False}
        with (patch('oak.executor.load_profile',return_value=profile),
              patch('oak.harness2_production.RuntimeClient',return_value=client) as constructor):
            wrapped = ExecutionRuntimeClient(execution={'profile':'/protected/profile'},
                cwd='/host/private/role-context',home='/host/home',sandbox='read-only',
                config=config,tool_handler=callback,native_guard=guard)
        passed = constructor.call_args.kwargs
        self.assertEqual(passed['cwd'],'/approved/workspace')
        self.assertEqual(passed['home'],'/approved/home')
        self.assertIs(passed['tool_handler'],callback)
        self.assertEqual(passed['config'],config)
        thread = {'cwd':'/host/context','model':'gpt-6.1-sol','dynamicTools':[{'name':'view'}]}
        await wrapped.request('thread/start',thread)
        self.assertEqual(client.request.call_args.args[1],{**thread,'cwd':'/approved/workspace'})
        self.assertEqual(thread['cwd'],'/host/context')
        turn = {'model':'gpt-6.1-sol','effort':'low','input':[{'type':'text','text':'public fixture'}]}
        await wrapped.request('turn/start',turn)
        self.assertEqual(client.request.call_args.args[1],turn)
        self.assertEqual(guard.call_count,2)
        with patch('oak.executor.load_profile',return_value=profile),self.assertRaises(ValueError):
            ExecutionRuntimeClient(execution={},sandbox='workspace-write',config=config)

    async def test_gateway_shutdown_cancels_current_native_and_forbids_next_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = ProductionBridge(deployment(Path(directory)/'state'))
            began,finished = threading.Event(),threading.Event()
            calls = []
            async def running_native():
                registration = bridge.register_native()
                try:
                    calls.append('first')
                    began.set()
                    await asyncio.Event().wait()
                finally:
                    bridge.unregister_native(registration)
            def fake_tick():
                try:
                    asyncio.run(running_native())
                except asyncio.CancelledError:
                    pass
                try:
                    bridge._guard(41)
                    calls.append('second')
                except BudgetStopped:
                    pass
                finished.set()
            with patch.object(bridge,'tick',side_effect=fake_tick):
                task = asyncio.create_task(bridge.run())
                async with asyncio.timeout(2):
                    while not began.is_set():
                        await asyncio.sleep(.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(finished.is_set())
            self.assertEqual(calls,['first'])
            self.assertFalse(bridge.control(41)['paused'])


class AdmissionPauseTests(unittest.TestCase):
    def test_stop_during_final_measurement_defers_decision_and_activation_until_explicit_resume(self):
        from oak.harness_versions import HarnessVersions
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = ProductionBridge(deployment(root/'state'))
            store = bridge.store(41)
            self.addCleanup(store.close)
            approved_case(store)
            manifest = {split:[{'descriptor':{'id':split,'split':split},
                'commitment':{'inputs':{'source.png':digest(split)},'sha256':digest(split)}}]
                for split in ('dev','regression','holdout')}
            identity, factory = {'kind':'fixed-test-transport'},Mock(name='native-factory')
            config = default_config()
            config['native_transport'] = identity
            config['policy'].update(regression_tasks=1,holdout_tasks=1)
            cycle = store.create_cycle('public-depth',manifest,config)
            versions = HarnessVersions(root/'state'/'versions.sqlite3')
            self.addCleanup(versions.close)
            base = versions.bootstrap_current(*store.scope,'')
            candidate = versions.register(*store.scope,'CANDIDATE_PROCEDURE',parent_id=base['id'])
            store.update(cycle['id'],phase='frozen',frozen={
                'candidate_id':candidate['id'],'candidate_sha256':candidate['procedure_sha256'],
                'parent_id':base['id'],'parent_sha256':base['procedure_sha256']})
            created, scores = [],[]
            class MeasuredAdapter:
                def __init__(self,*args,**kwargs):
                    created.append(kwargs)
                def _task(self,task_id):
                    return task_id
                def execute(self,task_id,harness,run_id):
                    return root/run_id
                def score(self,path,task_id):
                    scores.append(path.name)
                    if len(scores)==4:
                        bridge.control(41,'stop')  # Last measurement completed; no new reservation follows.
                    passed = task_id!='holdout' or path.name.endswith('candidate')
                    return {'task_id':task_id,'status':'completed','source_immutable':True,'delivered':True,
                        'unverified':False,'material_defect':False,'elapsed_seconds':1,'tool_calls':1,
                        'artifact_sha256':'a'*64,'candidate_sha256':'a'*64,'visual_passed':True,
                        'mechanical_passed':passed,'joint_passed':passed}
            commitments = {row['descriptor']['id']:row['commitment'] for rows in manifest.values() for row in rows}
            with (patch('oak.harness2_media.MediaBenchmarkAdapter',MeasuredAdapter),
                  patch('oak.harness2_media.source_commitment',side_effect=lambda d,t:commitments[d['id']])):
                with self.assertRaises(BudgetStopped):
                    run_cycle(store,cycle['id'],native_client_factory=factory,transport_identity=identity)
                self.assertIsNone(store.cycle(cycle['id'])['decision'])
                self.assertEqual(versions.resolve(*store.scope)['id'],base['id'])
                self.assertIs(created[0]['native_client_factory'],factory)
                self.assertEqual(created[0]['transport_identity'],identity)
                bridge.control(41,'resume')
                result = run_cycle(store,cycle['id'],native_client_factory=factory,transport_identity=identity)
            self.assertTrue(result['decision']['accepted'])
            self.assertEqual(versions.resolve(*store.scope)['id'],candidate['id'])


class ControllerBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_submit_uses_accepted_scoped_procedure_and_records_metadata_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = SimpleNamespace(request=AsyncMock(return_value={'turn':{'id':'turn-one'}}))
            controller = Controller(client,root/'controller.sqlite',root,AsyncMock(),
                instructions='PRIVATE_PERSONA_CANARY',config=deployment(root/'improvement'))
            self.addCleanup(controller.close)
            controller._thread = AsyncMock(return_value='thread-one')
            controller.improvement.ensure_baseline(41)
            versions = controller.harness_versions
            base = versions.resolve('live-project',41,'media-depth')
            candidate = versions.register('live-project',41,'media-depth','APPROVED_PROCEDURE',parent_id=base['id'])
            versions.record_decision(candidate['id'],accepted=True,evidence={'controlled_fixture':True},kind='controlled-fixture')
            versions.activate(candidate['id'])
            await controller.submit(41,'PRIVATE_PROMPT_CANARY',100)
            self.assertIn('APPROVED_PROCEDURE',str(client.request.call_args))
            controller.record_improvement_event(41,'artifact_review',{'verdict':'revise','sha256':'a'*64,
                'summary':'PRIVATE_REVIEW_CANARY'},turn_id='turn-one')
            store = controller.improvement.store(41)
            try:
                row = store.cases()[0]
                self.assertEqual(row['payload']['metadata']['version_id'],candidate['id'])
                self.assertNotIn('CANARY',json.dumps(store.cases()))
            finally:
                store.close()
            client.request.reset_mock()
            await controller.submit(42,'Unconfigured scope',101)
            self.assertNotIn('APPROVED_PROCEDURE',str(client.request.call_args))
            self.assertEqual(versions.applications('live-project',42,'media-depth'),[])
            self.assertNotIn('PRIVATE_PERSONA_CANARY','\n'.join(versions.db.iterdump()))
