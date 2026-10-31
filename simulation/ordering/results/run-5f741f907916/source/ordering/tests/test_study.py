import copy
import importlib.util
import itertools
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class OrderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('ordering_study',ROOT/'study.py')
        cls.s=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.s)
        cls.temp=tempfile.TemporaryDirectory();cls.out=Path(cls.temp.name);cls.binary=cls.s.build(cls.out)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_grid_and_whole_server_permutation(self):
        s=self.s;self.assertEqual(len(s.specs(False)),52);self.assertEqual(len(s.specs(False,'confirm')),64)
        case={**s.specs(True)[1],'renumber':False};permuted={**case,'renumber':True}
        a=s.matrix(case,self.binary);b=s.matrix(permuted,self.binary);p=s.permutation(permuted);n,g=case['n'],case['g'];r=n*g
        self.assertNotEqual(p,list(range(n)));self.assertNotEqual(a,b)
        dc=s.dc_labels(case);dc2=s.dc_labels(permuted)
        for i in range(r):
            for j in range(r):self.assertEqual(a[i*r+j],b[(p[i//g]*g+i%g)*r+p[j//g]*g+j%g])
        for i in range(n):self.assertEqual(dc[i],dc2[p[i]])
        self.assertEqual(sum(a),sum(b));self.assertNotEqual(s.input_key(case),s.input_key(permuted))

    def test_support_diagnostic_against_tiny_bruteforce(self):
        # Test-only enumeration, never used by the production diagnostic/planner.
        rng=random.Random(640003)
        for n in [3,4]:
            for trial in range(12):
                a=[int(i==j or rng.random()<.4) for i in range(n) for j in range(n)];real=[int(rng.random()<.3) for _ in a]
                info=self.s.support_summary(a,n,1,list(range(n)),real,1)
                perms=[p for p in itertools.permutations(range(n)) if all(a[i*n+p[i]] for i in range(n))]
                movable={i for p in perms for i in range(n) if p[i]!=i}
                edges={(i,p[i]) for p in perms for i in range(n) if p[i]!=i}
                realrows={i for p in perms for i in movable if p[i]!=i and real[i*n+p[i]]}
                # Self assignments do not carry network bytes; only alternate off-diagonal edges can change real traffic here.
                self.assertEqual(info['movable_rows'],len(movable));self.assertEqual(info['flexible_edges'],len(edges))
                self.assertEqual(info['real_movable_rows'],len(realrows))

    def test_cli_trace_tamper_and_frozen_semantics(self):
        s=self.s;case=s.specs(True)[1];data=s.matrix(case,self.binary);batch=s.invoke(self.binary,case,None,data,2)
        self.assertEqual(set(batch),set(s.METHODS))
        for method,value in batch.items():
            row,_=s.reconstruct(case,method,data,value);self.assertGreater(row['total_ms'],0)
        for method,edit in [('A1D',lambda v:v['decisions'][0]['row_order'].reverse()),
                            ('A1P',lambda v:v['decisions'][0]['row_pressure'].__setitem__(0,1e30)),
                            ('A1DP',lambda v:v['support_diagnostics'][0].__setitem__('movable_rows',999))]:
            bad=copy.deepcopy(batch[method]);edit(bad)
            with self.assertRaises(ValueError):s.reconstruct(case,method,data,bad)
        frozen=ROOT.parent/'scenario/results/run-b04dfab5ebf3/native_scan'
        old=s.invoke(frozen,case,None,data,1)
        for method in ['F000','maxmin','spreadout','A1']:
            for key in ['stages','total_us','balance_us','intrinsic_us','dispatch_us','service','inter_bytes']:
                self.assertEqual(batch[method][key],old[method][key],(method,key))

    def test_real_archive_smoke(self):
        s=self.s;out=self.out/'smoke';s.prepare(out,True);s.run(out,True)
        self.assertEqual(len(s.audit(out,True)),28)
        path=next((out/'raw').glob('*.json'));v=json.loads(path.read_text());v['timing_samples'][0]['planning_us']=0;s.save(path,v)
        with self.assertRaises(ValueError):s.audit(out,True)

    def test_symmetric_pressure_ulp_differences_do_not_reject_native_order(self):
        s=self.s;case=next(c for c in s.specs(False) if c['n']==12 and c['dc_count']==6 and c['profile']=='shared100' and c['kind']=='balanced' and c['wan_us']==50)
        data=s.matrix(case,self.binary);raw=s.invoke(self.binary,case,'A1P',data,1)
        row,_=s.reconstruct(case,'A1P',data,raw)
        self.assertEqual(row['stages'],11)

if __name__=='__main__':unittest.main()
