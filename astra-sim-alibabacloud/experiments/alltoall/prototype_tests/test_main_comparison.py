"""Primary comparisons must use FAST/Spreadout, not accidentally another denominator."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def driver():
    tools=Path(__file__).resolve().parents[1]/'tools'
    if str(tools) not in sys.path:sys.path.insert(0,str(tools))
    spec=importlib.util.spec_from_file_location('main_comparison_test',tools/'run_resource_budget_validation.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class MainComparisonTests(unittest.TestCase):
    def test_three_method_study_retains_load_factors_and_correct_speedups(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'source/matrix').mkdir(parents=True)
            (root/'source/matrix/matrix.npy').write_bytes(b'matrix hash fixture')
            case=dict(name='case',group='mixed',source=str(root/'source'),window=0,layer=None,seed=4101,
                      topology='leaf-mixed',gpu_count=128,family='zipf12',mib=16,rho=.6,block='pressure')
            methods=['fast-simai','spreadout','resource-maxq']
            rows=[dict(case='case',group='mixed',method=m,status='success',success=True,actual_ms=t,
                       input_contract={'epoch':1},reused=False,backend_attempted=True) for m,t in zip(methods,[5.,3.,2.])]
            study=dict(cases=[case],runs=rows,methods=methods,planned_records=3)
            module.finish(root,study)
            self.assertTrue(study['all_success'])
            row=next(csv.DictReader((root/'paired.csv').open()))
            self.assertEqual(float(row['fast-simai_speedup']),2.5)
            self.assertEqual(float(row['spreadout_speedup']),1.5)
            self.assertEqual((row['gpu_count'],row['mib'],row['rho'],row['block']),('128','16','0.6','pressure'))
            self.assertNotIn('volume-maxq',study['summary']['all'])

    def test_summary_can_select_only_real_baselines(self):
        module=driver()
        rows=[dict(case='c',group='g',method=m,success=True,pair_eligible=True,actual_ms=t)
              for m,t in [('fast-simai',8),('spreadout',6),('resource-maxq',4)]]
        result=module.paired_summary(rows,methods=['fast-simai','spreadout','resource-maxq'])['all']
        self.assertEqual(set(result),{'fast-simai','spreadout'})
        self.assertEqual(result['fast-simai']['geomean_speedup'],2.)
        self.assertEqual(result['spreadout']['geomean_speedup'],1.5)

    def test_generated_summary_csv_uses_lf_without_changing_fractional_values(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'rows.csv';module.save_csv(path,[{'speedup':1.0053186}])
            self.assertNotIn(b'\r\n',path.read_bytes())
            self.assertEqual(next(csv.DictReader(path.open()))['speedup'],'1.0053186')
