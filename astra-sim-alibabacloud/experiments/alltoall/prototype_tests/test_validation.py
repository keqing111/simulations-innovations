"""Held-out validation must not select by outcome or manufacture paired wins."""
import importlib.util
from pathlib import Path
import sys
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
