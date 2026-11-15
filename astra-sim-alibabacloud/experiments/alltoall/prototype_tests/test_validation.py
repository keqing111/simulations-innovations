"""Held-out validation must not select by outcome or manufacture paired wins."""
import importlib.util
from pathlib import Path
import sys
import json
import tempfile
import unittest


def driver():
    folder=Path(__file__).resolve().parents[1]/'tools'
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))
    spec=importlib.util.spec_from_file_location('validation_driver_test',folder/'run_resource_budget_validation.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class ValidationTests(unittest.TestCase):
    def test_selection_covers_layers_and_windows_and_does_not_repeat_balanced_seeds(self):
        module=driver();cases=module.validation_cases(dict(deepseek={'layers':[3,32,60]},llama={'layers':[1,25,47]}))
        self.assertEqual(len(cases),27)
        self.assertEqual(len({c['name'] for c in cases}),27)
        self.assertEqual({(c['window'],c['layer']) for c in cases if c['model']=='deepseek'},
                         {(w,l) for w in (0,1,2) for l in (3,32,60)})
        self.assertEqual({c['seed'] for c in cases if not c['model']},{2101,2102,2103})
        self.assertEqual(sum(c['model'] is None for c in cases),6)
        self.assertFalse(any(c['family']=='balanced' for c in cases))

    def test_missing_or_failed_old_baseline_is_retained_without_retry_job(self):
        module=driver()
        rows=module.source_baselines({'runs':[dict(case='x',method='fast-simai',success=False,status='timeout')]},'x')
        self.assertEqual([r['method'] for r in rows],['fast-simai','spreadout'])
        self.assertEqual([r['status'] for r in rows],['timeout','unrecorded'])
        self.assertTrue(all(r['reused'] for r in rows))
        self.assertTrue(all(r['actual_ms'] is None for r in rows))

    def test_catalog_drift_cannot_silently_change_the_registered_sample(self):
        module=driver()
        with self.assertRaisesRegex(ValueError,'registered layers'):
            module.validation_cases(dict(deepseek={'layers':[3,20,32,60]},llama={'layers':[1,25,47]}))

    def test_summary_pairs_by_case_and_excludes_missing_but_counts_it(self):
        module=driver();rows=[]
        for case,method,t,ok in [('a','resource-maxq',5,True),('a','volume-maxq',10,True),
                                  ('b','resource-maxq',12,True),('b','volume-maxq',6,True),
                                  ('c','resource-maxq',8,True),('c','volume-maxq',None,False)]:
            rows.append(dict(case=case,method=method,actual_ms=t,success=ok,group='test'))
        result=module.paired_summary(rows)['all']['volume-maxq']
        self.assertEqual(result['paired'],2)
        self.assertEqual(result['unpaired'],1)
        self.assertEqual((result['wins'],result['ties'],result['losses']),(1,0,1))
        self.assertAlmostEqual(result['geomean_speedup'],1)
        self.assertAlmostEqual(result['worst_speedup'],.5)

    def test_mismatched_replay_contract_is_not_a_valid_pair(self):
        module=driver()
        row={'success':True,'input_contract':{'matrix':'same','epoch':1}}
        with self.assertRaisesRegex(ValueError,'contract'):
            module.verify_contract(row,{'matrix':'same','epoch':2})

    def test_stale_control_does_not_destroy_the_other_valid_pair(self):
        module=driver()
        rows=[dict(method=m,success=True,actual_ms=t,status='success',input_contract={'epoch':e})
              for m,t,e in [('fast-simai',20,1),('spreadout',10,2),('volume-maxq',8,2),('resource-maxq',4,2)]]
        module.mark_pair_eligibility(rows)
        self.assertTrue(all(r['success'] for r in rows))
        self.assertFalse(rows[0]['pair_eligible'])
        self.assertTrue(rows[1]['pair_eligible'])
        self.assertTrue(rows[2]['pair_eligible'])

    def test_summary_excludes_ineligible_control_but_preserves_execution_status(self):
        module=driver();rows=[]
        for m,t,eligible in [('resource-maxq',5,True),('volume-maxq',10,False)]:
            rows.append(dict(case='a',group='g',method=m,success=True,actual_ms=t,pair_eligible=eligible))
        result=module.paired_summary(rows)['all']['volume-maxq']
        self.assertEqual(result['paired'],0)
        self.assertEqual(result['unpaired'],1)

    def test_worker_recovery_retains_completed_method_and_marks_unstarted_method(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            saved=dict(case='x',method='volume-maxq',success=True,status='success',actual_ms=5,
                       attempt_dir='/existing/attempt',input_contract={'epoch':2})
            (out/'study.json').write_text(json.dumps({'runs':[saved]}))
            rows=module.recover_worker(out,'x',['volume-maxq','resource-maxq'],ValueError('worker ended'))
            self.assertTrue(rows[0]['success'])
            self.assertEqual(rows[0]['actual_ms'],5)
            self.assertTrue(rows[0]['backend_attempted'])
            self.assertEqual(rows[1]['status'],'worker_aborted_before_method')
            self.assertFalse(rows[1]['backend_attempted'])

    def test_contract_binds_binary_contents_and_thread_count_to_execution_fingerprint(self):
        module=driver()
        from simai_a2a.model import Schedule,ScheduledFlow
        from simai_a2a.artifacts import write_schedule_artifact
        from simai_a2a.runner import execution_fingerprint
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';attempt=source/'runs'/'spreadout'/'attempt-0001';attempt.mkdir(parents=True)
            binary=root/'backend';binary.write_bytes(b'original-binary')
            for name,data in [('topology.txt','topology'),('routes.csv','route'),('background.csv',''),
                              ('hardware.json','{}'),('matrix.npy','matrix')]:
                (attempt/name).write_text(data)
                target=source/'matrix/matrix.npy' if name=='matrix.npy' else source/name
                target.parent.mkdir(exist_ok=True);target.write_text(data)
            (source/'workload.csv').write_text('workload')
            schedule=Schedule('spreadout',[ScheduledFlow(0,0,1,8,0,'inter',0)],None,metadata={'execution_model':'open-loop'})
            write_schedule_artifact(schedule,source/'schedules/spreadout',matrix_sha256='m',hardware_sha256='h')
            env={'NS_ATTRIBUTE_DEFAULT':None,'NS_GLOBAL_VALUE':None}
            fingerprint=execution_fingerprint(source,'spreadout',source/'topology.txt',binary,
                module.ROOT/'configs/ns3-leafspine.conf',0,ns3_env=env)
            manifest=dict(command=[str(binary)],algorithm='spreadout',attempt_dir=str(attempt),
                runtime_semantics_version=19,ns3_threads=0,ns3_environment=env,backend_shared_library_sha256={},
                input_fingerprint=fingerprint)
            (attempt/'ns3.conf').write_text('CC_MODE 1\n');(attempt/'summary.json').write_text('{"collective_epoch_ns":2}')
            (attempt/'run_manifest.json').write_text(json.dumps(manifest))
            c=module.contract(attempt)
            self.assertEqual(c['threads'],0)
            self.assertTrue(c['execution_fingerprint_verified'])
            manifest['ns3_threads']=1;(attempt/'run_manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'fingerprint'):module.contract(attempt)
            manifest['ns3_threads']=0;(attempt/'run_manifest.json').write_text(json.dumps(manifest))
            binary.write_bytes(b'changed-binary')
            with self.assertRaisesRegex(ValueError,'fingerprint'):module.contract(attempt)

    def test_worker_interruption_with_only_an_attempt_directory_is_not_called_unstarted(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'x/runs/resource-maxq/attempt-0001').mkdir(parents=True)
            row=module.recover_worker(out,'x',['resource-maxq'],ValueError('worker ended'))[0]
            self.assertEqual(row['status'],'worker_aborted_attempt_unknown')
            self.assertIsNone(row['backend_attempted'])

    def test_summary_counts_unknown_attempts_without_crashing_or_inventing_runs(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'source/matrix').mkdir(parents=True)
            (root/'source/matrix/matrix.npy').write_bytes(b'unread matrix fixture; hash only')
            case=dict(name='x',group='g',window=0,layer=None,seed=1,source=str(root/'source'))
            rows=[dict(case='x',group='g',method=m,status='worker_aborted',success=False,
                       actual_ms=None,reused=False,backend_attempted=None if m=='resource-maxq' else False)
                  for m in module.METHODS]
            study=dict(cases=[case],runs=rows,planned_records=4)
            module.finish(root,study)
            self.assertTrue(study['complete'])
            self.assertFalse(study['all_success'])
            self.assertEqual(study['new_attempts'],0)
            self.assertEqual(study['new_attempts_unknown'],1)
