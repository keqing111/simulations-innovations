import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
class RefineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('refine_study',ROOT/'study.py')
        cls.study=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.study)
        cls.temp=tempfile.TemporaryDirectory();cls.out=Path(cls.temp.name);cls.binary=cls.study.build(cls.out)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_grid_shared_policies_and_native_generator(self):
        s=self.study;self.assertEqual(len(s.specs(False)),26);self.assertEqual(len(s.METHODS),13)
        frozen=ROOT.parent/'scan/results/run-7be22345a21c/native_scan'
        for seed in [280101,280203]:
            args=['--zipf',str(seed),'256']
            self.assertEqual(subprocess.check_output([str(self.binary),*args]),subprocess.check_output([str(frozen),*args]))
        for case in s.specs(True):
            data=s.matrix(case,self.binary)
            for m in ['F000','F011','F111','maxmin','spreadout']:
                a=s.invoke(self.binary,case,m,data,1);b=s.invoke(frozen,case,m,data,1)
                for k in ['stages','total_us','balance_us','intrinsic_us','dispatch_us','service','inter_bytes']:
                    self.assertEqual(a[k],b[k],(m,k))

    def test_real_cli_all_methods_and_declared_budget_guard(self):
        s=self.study;case=s.specs(True)[1];data=s.matrix(case,self.binary)
        batch=s.invoke(self.binary,case,None,data,2);self.assertEqual(set(batch),set(s.METHODS))
        for m,raw in batch.items():
            row,_=s.reconstruct(case,m,data,raw);self.assertGreater(row['total_ms'],0)
            altered=json.loads(json.dumps(raw));altered['policy']['cycle_budget']=100
            with self.assertRaises(ValueError):s.reconstruct(case,m,data,altered)
        changed=json.loads(json.dumps(batch['Q75']));changed['decisions'][0]['threshold']=1
        with self.assertRaises(ValueError):s.reconstruct(case,'Q75',data,changed)
        run=self.out/'smoke';s.prepare(run,True);s.run(run,True)
        self.assertEqual(len(s.audit(run,True)),26)
        p=next((run/'raw').glob('*.json'));v=json.loads(p.read_text());v['timing_samples'][0]['planning_us']=0;p.write_text(json.dumps(v))
        with self.assertRaises(ValueError):s.audit(run,True)

if __name__=='__main__':unittest.main()
