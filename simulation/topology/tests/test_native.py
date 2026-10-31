"""Real native binaries; no SimAI imports or ns-3 dependency."""
import json
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


class NativeCompatibilityTests(unittest.TestCase):
    def invoke(self,n,g,mode,matrix):
        return json.loads(subprocess.check_output([str(ROOT/'build/native_topology'),str(n),str(g),mode,'5000'],
            input=' '.join(map(str,matrix)).encode()))

    def test_default_and_legacy_hook_match_prechange_oracle(self):
        oracle=json.loads((ROOT/'results/oracle.json').read_text())
        binary=ROOT/'build/native_topology'
        self.assertTrue(binary.is_file(),'native topology executable not implemented')
        for case in oracle['cases']:
            for mode in ['legacy','legacy-hook']:
                with self.subTest(case=case['id'],mode=mode):
                    output=subprocess.check_output([str(binary),str(case['n']),str(case['g']),mode],
                        input=' '.join(map(str,case['matrix'])).encode())
                    result=json.loads(output)
                    self.assertAlmostEqual(result['total_us'],case['fast_us'],places=9)
                    self.assertAlmostEqual(result['legacy_spreadout_us'],case['spreadout_us'],places=9)

    def test_actual_payload_is_conserved_for_every_policy(self):
        n,g=6,4;r=n*g
        matrix=[0 if s==d else ((s*79+d*23)%100)*1000 for s in range(r) for d in range(r)]
        for mode in ['native','wan-repair','link-cost','link-cost-wan','maxmin','spreadout']:
            with self.subTest(mode=mode):
                result=self.invoke(n,g,mode,matrix)
                total=sum(v for s in range(r) for d in range(r) if s//g!=d//g for v in [matrix[s*r+d]])
                self.assertEqual(result['inter_bytes'],total)
                self.assertTrue(result['payload_verified'])
                self.assertLessEqual(len(result['stages']),n*n if mode!='spreadout' else r)

    def test_fully_real_homogeneous_reduces_to_original(self):
        n,g=2,4;r=n*g;matrix=[0 if s==d else 1000000 for s in range(r) for d in range(r)]
        legacy=self.invoke(n,g,'legacy',matrix)
        actual=self.invoke(n,g,'homogeneous',matrix)
        self.assertAlmostEqual(legacy['total_us'],actual['total_us'],places=9)

    def test_empty_and_intra_only_demand_need_no_network_stage(self):
        self.assertEqual(self.invoke(3,1,'native',[0]*9)['total_us'],0)
        matrix=[0]*36;matrix[1]=1000000
        result=self.invoke(3,2,'native',matrix)
        self.assertEqual(result['inter_bytes'],0)
        self.assertAlmostEqual(result['total_us'],2.4)

    def test_malformed_matrix_rejected_before_native_overflow(self):
        for matrix in [[-1]*9,[2**32]*9,[1]*8,[1]*10]:
            result=subprocess.run([str(ROOT/'build/native_topology'),'3','1','native','5000'],
                input=' '.join(map(str,matrix)),text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)

    def test_signed_native_intermediate_boundary_is_checked(self):
        matrix=[0]*16;matrix[2]=2**31-1
        accepted=self.invoke(2,2,'native',matrix)
        self.assertEqual(accepted['service'][1],1073741824)
        self.assertGreater(accepted['balance_us'],0)
        matrix[2]+=1
        result=subprocess.run([str(ROOT/'build/native_topology'),'2','2','native','5000'],
            input=' '.join(map(str,matrix)),text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0,'unsafe signed conversion accepted')
        self.assertIn('signed',result.stderr)


if __name__=='__main__':unittest.main()
