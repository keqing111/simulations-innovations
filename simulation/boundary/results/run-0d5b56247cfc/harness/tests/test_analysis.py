import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('boundary_analysis_test',ROOT/'analysis.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)


class AnalysisTests(unittest.TestCase):
    def test_frozen_pipeline_hides_local_dispatch_instead_of_summing(self):
        raw=dict(mode='A1-opt',balance_us=2,intrinsic_us=15,dispatch_us=[4,3],stages=[
            dict(links={'wan:0:1':1000},local_us=0),dict(links={'server-out:0':1000},local_us=0)])
        self.assertAlmostEqual(a.frozen_cost(raw,dict(wan_us=10),{'wan:0:1':100,'server-out:0':400}),24)
        self.assertAlmostEqual(a.frozen_cost(raw,dict(wan_us=20),{'wan:0:1':100,'server-out:0':400}),31.88)
        self.assertAlmostEqual(a.frozen_cost(raw,dict(wan_us=20),{'wan:0:1':400,'server-out:0':400}),31.82)
        with self.assertRaises(ValueError):a.frozen_cost(raw,dict(wan_us=20),{'wan:0:1':0})

    def test_zero_wan_and_spreadout_local_rounds(self):
        raw=dict(mode='spreadout-opt',stages=[dict(links={'wan:0:1':0},local_us=4),dict(links={'server-out:0':1000},local_us=8)])
        self.assertEqual(a.frozen_cost(raw,dict(wan_us=5000),{'wan:0:1':100,'server-out:0':400}),12)

    def test_signature_ignores_pricing_but_not_payload(self):
        raw=dict(mode='A1-opt',service=[1],balance_us=2,intrinsic_us=3,dispatch_us=[4],stages=[dict(freq=1,map=[1,0],payload=[1,1],local_us=0,serial_us=1,alpha_us=2)])
        changed=copy.deepcopy(raw);changed['stages'][0]['serial_us']=999
        self.assertEqual(a.schedule_signature(raw),a.schedule_signature(changed))
        changed['stages'][0]['payload'][0]=2
        self.assertNotEqual(a.schedule_signature(raw),a.schedule_signature(changed))

    def test_roots_keep_multiple_transitions_and_sampled_ties(self):
        roots=a.roots_on_grid(lambda x:(x-1)*(x-3),[0,2,4])
        self.assertEqual(len(roots['roots']),2)
        for got,want in zip(roots['roots'],[1,3]):self.assertAlmostEqual(got['root_us'],want,places=6)
        self.assertEqual(a.roots_on_grid(lambda x:0,[0,2,4])['state'],'sampled_tie')
        self.assertEqual(a.roots_on_grid(lambda x:x+5,[0,2,4])['roots'],[])
        with self.assertRaises(ValueError):a.roots_on_grid(lambda x:float('nan'),[0,2])

    def test_real_analysis_repricing_and_bound_figure_provenance(self):
        s=a.study
        with tempfile.TemporaryDirectory(prefix='fast-boundary-analysis-') as tmp:
            out=Path(tmp);s.prepare(out,True);s.run(out,True)
            result=a.analyze(out,True);self.assertEqual(len(result),48)
            for row in result:
                if row['strategy'] in ['F000','maxmin','spreadout']:
                    self.assertAlmostEqual(row['total_ms'],row['frozen_ms'],places=8)
                    self.assertFalse(row['schedule_changed'])
            a.render(out,True)
            self.assertEqual((out/'figures/heatmap.png').read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
            a.render(out,True)
            provenance=json.loads((out/'figures/provenance.json').read_text())
            self.assertFalse(any(name.startswith('figures/') for name in provenance['derived_tables']))
            path=out/'analysis.json';path.write_text('[]')
            with self.assertRaises(ValueError):a.render(out,True)


if __name__=='__main__':unittest.main()
