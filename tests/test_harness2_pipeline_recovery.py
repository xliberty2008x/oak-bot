"""Terminal search/recovery journals, without models, codecs or live state."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oak import harness2_pipeline as pipeline
from oak.harness2_media import make_descriptor


class PipelineRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'private'
        self.store = pipeline.PipelineStore(self.root,'selected-project',41)
        self.addCleanup(self.store.close)
        self.dev = make_descriptor('recovery-local-protocol',0)
        self.case = self.store.record_case({'visibility':'public_fixture','descriptor':self.dev},
                                           signature='layers',approved=True)
        self.manifest = {split:[self.seal(self.store,make_descriptor('recovery-local-protocol',i,split=split))
                               for i in indices]
                         for split,indices in [('dev',[0]),('regression',[1,2]),('holdout',[3,4])]}
        self.manifest['dev'] = [self.seal(self.store,self.dev)]
        self.config = pipeline.default_config()
        self.search = {'upstream':{'status':'partial','task_id':self.dev['id']},
                       'provenance':{'chosen_harness':None,'upstream_commit':pipeline.UPSTREAM_COMMIT}}
        self.auth = pipeline.digest('explicit-local-test-authorization')

    @staticmethod
    def seal(store,descriptor):
        identity = {key:value for key,value in descriptor.items() if key not in ('id','split')}
        return {'descriptor':copy.deepcopy(descriptor),
                'commitment':{'inputs':{'source.png':pipeline.digest(identity)},
                              'sha256':pipeline.digest(descriptor)}}

    def cycle(self,*,stale=False):
        config = copy.deepcopy(self.config)
        if stale:
            config['implementation']={'sha256':'0'*64,'files':{'old-generation.py':'a'*64}}
        result = self.store.create_cycle('layers',self.manifest,config)
        self.store.update(result['id'],phase='searching')
        root = pipeline._scope_root(self.store)/'cycles'/result['id']
        root.mkdir(parents=True)
        return self.store.cycle(result['id']),root

    def close(self,cycle):
        return pipeline.close_pre_freeze(self.store,cycle['id'],
            reason_code='review_transport_configuration_error',authorization_reference=self.auth)

    def assert_unmeasured(self,result):
        self.assertEqual(result['phase'],'completed')
        self.assertIsNone(result['frozen'])
        self.assertFalse(result['decision']['accepted'])
        self.assertFalse(result['decision']['evaluated'])
        self.assertFalse(result['decision']['quality_rejection'])
        self.assertIsNone(result['decision']['candidate_id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM exposure WHERE cycle_id=? '
            'AND split="holdout" AND opened IS NOT NULL',(result['id'],)).fetchone()[0],0)

    def test_fresh_upstream_empty_selection_finishes_without_candidate_or_independent_execution(self):
        cycle,root = self.cycle()
        manifest = self.manifest
        class DevAdapter:
            def __init__(self,path,descriptors,**kwargs):
                if kwargs.get('optimizer') is False:
                    raise AssertionError('Unselected candidate entered independent evaluation.')
                self.path=path
            def _task(self,task_id):
                return task_id
            def task_files(self,task_id):
                path=self.path/'source';path.mkdir(parents=True,exist_ok=True);return path
            def _procedure(self,path):
                raise AssertionError('No candidate should be materialized.')
        source = {row['descriptor']['id']:row['commitment'] for rows in manifest.values() for row in rows}
        returned = SimpleNamespace(to_dict=lambda:self.search['upstream'],oak_provenance=self.search['provenance'])
        with (patch('oak.harness2_upstream.SubscriptionRole'),
              patch('oak.harness2_upstream.run_development',return_value=returned) as development,
              patch('oak.harness2_media.MediaBenchmarkAdapter',DevAdapter),
              patch('oak.harness2_media.source_commitment',side_effect=lambda d,t:source[d['id']])):
            result = pipeline.run_cycle(self.store,cycle['id'])
            self.assert_unmeasured(result)
            self.assertEqual(result['decision']['classification'],'search_no_candidate')
            self.assertEqual(result['decision']['reasons'],['upstream_no_candidate'])
            self.assertEqual(pipeline.run_cycle(self.store,cycle['id']),result)
        development.assert_called_once()
        self.assertFalse((root/'freeze.json').exists())
        self.assertFalse((root/'independent').exists())
        versions = pipeline._versions(self.store)
        try:
            self.assertEqual(versions.db.execute('SELECT COUNT(*) FROM harness_versions').fetchone()[0],1)
            self.assertEqual(versions.db.execute('SELECT kind FROM harness_decisions').fetchone()[0],'bootstrap-current')
        finally:
            versions.close()

    def test_cached_empty_search_finishes_without_new_role_adapter_or_native_calls(self):
        cycle,root = self.cycle()
        pipeline.write_once(root/'upstream-result.json',self.search)
        before = (root/'upstream-result.json').read_bytes()
        with (patch('oak.harness2_upstream.SubscriptionRole') as role,
              patch('oak.harness2_upstream.run_development') as development,
              patch('oak.harness2_media.MediaBenchmarkAdapter') as adapter):
            result = pipeline.run_cycle(self.store,cycle['id'])
        self.assert_unmeasured(result)
        role.assert_not_called();development.assert_not_called();adapter.assert_not_called()
        self.assertEqual((root/'upstream-result.json').read_bytes(),before)
        with self.assertRaises(pipeline.BudgetStopped):
            self.store.budget_hook(cycle['id'])('producer',{})
        with self.assertRaisesRegex(ValueError,'terminal cycle'):
            self.store.update(cycle['id'],phase='searching')

    def test_explicit_infrastructure_close_preserves_stale_generation_bytes_and_is_idempotent(self):
        cycle,root = self.cycle(stale=True)
        pipeline.write_once(root/'upstream-result.json',self.search)
        (root/'development').mkdir()
        (root/'development'/'receipt.json').write_text('{"status":"unverified","synthetic":true}')
        original = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
        self.store.budget_hook(cycle['id'])('producer',{'attempt':'original'})
        with patch('oak.harness2_upstream.run_development') as development:
            with self.assertRaisesRegex(ValueError,'Implementation changed'):
                pipeline.run_cycle(self.store,cycle['id'])
            result = self.close(cycle)
        development.assert_not_called()
        self.assert_unmeasured(result)
        self.assertEqual(result['config'],cycle['config'])
        self.assertEqual(result['manifest'],cycle['manifest'])
        self.assertEqual(result['decision']['classification'],'infrastructure_abort')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],1)
        for name,raw in original.items():
            self.assertEqual((root/name).read_bytes(),raw)
        record = json.loads((root/'termination.json').read_text())
        self.assertEqual(record['authorization_reference'],self.auth)
        self.assertEqual(set(record['original_files']),set(original))
        snapshot = (root/'termination.json').read_bytes()
        self.assertEqual(self.close(cycle),result)
        self.assertEqual((root/'termination.json').read_bytes(),snapshot)
        with self.assertRaisesRegex(ValueError,'immutable'):
            pipeline.close_pre_freeze(self.store,cycle['id'],reason_code='review_transport_configuration_error',
                                     authorization_reference='f'*64)
        (root/'development'/'receipt.json').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'Original cycle evidence changed'):
            self.close(cycle)

    def test_lock_authorization_scope_and_frozen_boundaries_fail_before_closure(self):
        cycle,root=self.cycle(stale=True)
        with self.assertRaises(ValueError):
            pipeline.close_pre_freeze(self.store,cycle['id'],reason_code='raw error text',authorization_reference=self.auth)
        with self.assertRaises(ValueError):
            pipeline.close_pre_freeze(self.store,cycle['id'],reason_code='review_transport_configuration_error',authorization_reference='private prompt')
        with self.store.worker(),self.assertRaisesRegex(RuntimeError,'Another worker'):
            self.close(cycle)
        other=pipeline.PipelineStore(self.root,'foreign-project',41)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(ValueError,'Unknown cycle'):
            pipeline.close_pre_freeze(other,cycle['id'],reason_code='review_transport_configuration_error',authorization_reference=self.auth)
        self.store.update(cycle['id'],frozen={'candidate_sha256':'a'*64})
        with self.assertRaisesRegex(ValueError,'frozen candidate'):
            self.close(cycle)
        self.assertFalse((root/'termination.json').exists())

    def test_open_holdout_cannot_be_relabelled_unmeasured_even_without_freeze(self):
        cycle,root=self.cycle()
        with self.store.db:
            self.store.db.execute('UPDATE exposure SET opened=? WHERE cycle_id=? AND split="holdout"',
                                  (pipeline.now(),cycle['id']))
        with self.assertRaisesRegex(ValueError,'opened holdouts'):
            self.close(cycle)
        self.assertFalse((root/'termination.json').exists())

    def test_journal_recovery_after_terminal_file_before_database_commit_never_replays_search(self):
        cycle,root=self.cycle(stale=True)
        real_write = pipeline._write_terminal_once
        def interrupted_write(path,payload,**kwargs):
            if Path(path).name=='decision.json':
                raise OSError('simulated interruption before terminal decision publication')
            return real_write(path,payload,**kwargs)
        with patch.object(pipeline,'_write_terminal_once',side_effect=interrupted_write):
            with self.assertRaises(OSError):
                self.close(cycle)
        original = (root/'termination.json').read_bytes()
        self.assertIsNone(self.store.cycle(cycle['id'])['decision'])
        result=self.close(cycle)
        self.assert_unmeasured(result)
        self.assertEqual((root/'termination.json').read_bytes(),original)

    def interrupted_inside_terminal_write(self,target):
        cycle,root=self.cycle(stale=True)
        replace=pipeline.os.replace
        def interrupted_replace(source,destination):
            if Path(destination).name==target:
                raise OSError('simulated crash after temporary bytes before atomic publication')
            return replace(source,destination)
        with patch.object(pipeline.os,'replace',side_effect=interrupted_replace):
            with self.assertRaises(OSError):
                self.close(cycle)
        staging=pipeline._scope_root(self.store)/'termination-staging'/cycle['id']
        orphans={p:p.read_bytes() for p in staging.glob('*.tmp')}
        self.assertEqual(len(orphans),1)
        self.assertIsNone(self.store.cycle(cycle['id'])['decision'])
        result=self.close(cycle)
        self.assert_unmeasured(result)
        self.assertEqual(self.close(cycle),result)
        for path,raw in orphans.items():
            self.assertEqual(path.read_bytes(),raw)
        record=json.loads((root/'termination.json').read_text())
        self.assertFalse(any('termination-staging' in name for name in record['original_files']))

    def test_crash_inside_termination_temporary_write_retains_orphan_and_resumes(self):
        self.interrupted_inside_terminal_write('termination.json')

    def test_crash_inside_decision_temporary_write_retains_orphan_and_resumes(self):
        self.interrupted_inside_terminal_write('decision.json')

    def test_legacy_orphan_temporary_bytes_are_preserved_as_original_evidence(self):
        cycle,root=self.cycle(stale=True)
        originals={name:('original-'+name).encode() for name in ('termination.tmp','decision.tmp')}
        for name,raw in originals.items():
            (root/name).write_bytes(raw)
        result=self.close(cycle)
        self.assertEqual(self.close(cycle),result)
        record=json.loads((root/'termination.json').read_text())
        for name,raw in originals.items():
            self.assertEqual((root/name).read_bytes(),raw)
            self.assertEqual(record['original_files'][name]['sha256'],hashlib.sha256(raw).hexdigest())

    def test_authorized_replacement_uses_new_generation_fresh_holdouts_and_consumes_no_closed_holds(self):
        cycle,root=self.cycle(stale=True)
        first=self.close(cycle)
        self.store.add_feedback(self.case,{'source':'explicit-public-DEV-recovery-feedback',
            'observation':'The trusted reviewer execution binding was corrected; no quality claim.'})
        with (patch.object(pipeline,'_seal_descriptor',side_effect=self.seal),
              patch('oak.harness2_media.preflight_descriptors',return_value={'status':'pass','kind':'local-fixture-controls'})):
            next_cycle=pipeline.schedule_next(self.store,budget_limits={
                'max_producers':16,'max_roles':8,'max_role_tools':512})
        self.assertEqual(next_cycle['ordinal'],2)
        self.assertEqual(next_cycle['config']['implementation'],pipeline.implementation())
        self.assertEqual(next_cycle['config']['max_producers'],16)
        self.assertEqual(self.store.cycle(first['id']),first)
        original_holds={pipeline.input_identity(r) for r in cycle['manifest']['holdout']}
        new_sources={pipeline.input_identity(r) for split in ('regression','holdout') for r in next_cycle['manifest'][split]}
        self.assertTrue(original_holds.isdisjoint(new_sources))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM exposure WHERE cycle_id=? '
            'AND split="holdout" AND opened IS NOT NULL',(cycle['id'],)).fetchone()[0],0)

    def test_closed_cycle_authentic_dev_history_is_retained_without_unopened_holdout_content(self):
        cycle,root=self.cycle(stale=True)
        folder=root/'roles'/('a'*64)/'attempt-0001'
        folder.mkdir(parents=True)
        response='Observed DEV delivery was unverified; no measured gain. NO_ENTRY'
        (folder/'response.txt').write_text(response)
        record={'role':'inv1.a1','model':pipeline.MODEL,'effort':'low',
            'channel':'oak_chatgpt_subscription','attempt':1,'status':'completed',
            'response_sha256':hashlib.sha256(response.encode()).hexdigest()}
        (folder/'receipt.json').write_text(json.dumps(record))
        (root/'unopened-holdout-local.txt').write_text('HIDDEN_HOLDOUT_CANARY')
        self.close(cycle)
        self.store.add_feedback(self.case,{'source':'explicit-public-DEV-recovery-feedback'})
        with (patch.object(pipeline,'_seal_descriptor',side_effect=self.seal),
              patch('oak.harness2_media.preflight_descriptors',return_value={'status':'pass'})):
            next_cycle=pipeline.schedule_next(self.store)
        evidence=pipeline.approved_development_evidence(self.store,next_cycle)
        attempts=evidence['attempt_history']['attempts']
        self.assertEqual(len(attempts),1)
        self.assertEqual(attempts[0]['response'],response)
        self.assertEqual(attempts[0]['response_sha256'],record['response_sha256'])
        self.assertNotIn('HIDDEN_HOLDOUT_CANARY',json.dumps(evidence))


if __name__=='__main__':
    unittest.main()
