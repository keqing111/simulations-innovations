import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class ReproductionTests(unittest.TestCase):
    def test_pristine_driver_hand_calculated_and_original_factories(self):
        import reproduce
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);binary=reproduce.build(out)
            matrix=out/'input.txt';matrix.write_text('0 100 100 0\n')
            def run(profile):
                return json.loads(subprocess.check_output([str(binary),'2','1','1',profile,'1','400','3600','1',str(matrix),str(out/'matrix.bin')]))
            paper=run('paper')
            self.assertAlmostEqual(paper['fast_us'],2000.7)
            self.assertAlmostEqual(paper['spread_us'],2000.7)
            self.assertAlmostEqual(paper['ideal_us'],2000)
            self.assertAlmostEqual(paper['fast_gbps_bytes'],49.982506122857)
            self.assertEqual(paper['total_units'],200)
            original=run('upstream-default')
            self.assertAlmostEqual(original['fast_us'],8400.7)
            self.assertEqual(original['inter_beta'],84)
            self.assertEqual(original['intra_beta'],2.3)
            self.assertAlmostEqual(run('upstream-400')['fast_us'],2200.7)

    def test_actual_upstream_matches_prechange_oracle(self):
        import reproduce
        oracle=json.loads((ROOT/'assets/oracle.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);binary=reproduce.build(out)
            for case in oracle['cases']:
                matrix=out/'input.txt';matrix.write_text(' '.join(map(str,case['matrix'])))
                d=json.loads(subprocess.check_output([str(binary),str(case['n']),str(case['g']),'1','upstream-400','0.000001','400','3600','1',str(matrix),str(out/'matrix.bin')]))
                self.assertAlmostEqual(d['fast_us'],case['fast_us'],places=9)
                self.assertAlmostEqual(d['spread_us'],case['spreadout_us'],places=9)

    def test_registered_grid_and_independent_ideal_audit(self):
        import reproduce
        cases=reproduce.cases(False)
        self.assertEqual(sum(c['group']=='scale' for c in cases),400)
        self.assertEqual(sum(c['group']=='ratio' for c in cases),560)
        self.assertEqual(sum(c['group']=='defaults' for c in cases),9)
        self.assertEqual(sum(c['group']=='alpha' for c in cases),12)
        self.assertEqual({c['seed'] for c in cases if c['group']=='scale'},set(range(280001,280021)))

    def test_real_smoke_and_raw_or_input_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            def call(phase):return subprocess.run([sys.executable,str(ROOT/'reproduce.py'),'--phase',phase,'--output',str(out),'--smoke'],capture_output=True,text=True)
            for phase in ['prepare','run','audit']:
                result=call(phase);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(len(json.loads((out/'comparison.json').read_text())),2)
            path=next((out/'raw').glob('*.json'));original=path.read_text();value=json.loads(original)
            value['fast_us']+=1;path.write_text(json.dumps(value));self.assertNotEqual(call('audit').returncode,0)
            path.write_text(original)
            value=json.loads(original)
            value['pipeline_cpu_us']=value['full_cpu_us']=0
            value['all_gbps_bytes']=value['all_full_gbps_bytes']=value['fast_gbps_bytes']
            path.write_text(json.dumps(value))
            self.assertNotEqual(call('audit').returncode,0,'Self-consistent CPU timing forgery must be rejected')
            path.write_text(original)
            matrix=next((out/'matrices').glob('*.bin'));matrix.write_bytes(matrix.read_bytes()[:-4])
            self.assertNotEqual(call('audit').returncode,0)

    def test_source_legacy_intra_bound_is_not_endpoint_bound(self):
        import reproduce
        data=[0 if i==j else 100 for i in range(32) for j in range(32)]
        self.assertEqual(reproduce.source_legacy_bound(data,4,8,1,20,20),56000)
        self.assertEqual(19200*20/8,48000)


if __name__=='__main__':unittest.main()
