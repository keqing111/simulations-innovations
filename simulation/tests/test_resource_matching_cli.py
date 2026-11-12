import json
import pathlib
import subprocess
import sys
import unittest
import itertools

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/'ds/decomposition'))
import paper_counterexample as ce
import study
import dynamic_small_cases as dynamic


class ResourceMatchingCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-s','-C',str(ROOT),'bvn-decompose-build'],check=True)

    def run_case(self,data,n,g,rounds=3,scaling=0,strategy=1,topology=1,unit=1,skip=None):
        command=[str(study.BIN),'--mode','pipeline','--n',str(n),'--g',str(g),
                               '--strategy',str(strategy),'--topology',str(topology),
                               '--scaling',str(scaling),'--resource-rounds',str(rounds),
                               '--unit-mb',str(unit),'--repeat','1']
        if skip is not None:command+=['--resource-skip-step',str(skip)]
        return subprocess.run(command,input=' '.join(map(str,data)),
                              text=True,capture_output=True)

    def test_single_decision_counterfactual_and_same_state_trace(self):
        original=self.run_case(ce.DEMAND,6,1)
        skipped=self.run_case(ce.DEMAND,6,1,skip=0)
        self.assertEqual(skipped.returncode,0,skipped.stderr)
        a,b=json.loads(original.stdout),json.loads(skipped.stdout)
        self.assertAlmostEqual(a['total_us'],345.6)
        self.assertAlmostEqual(b['total_us'],425.6)
        for raw in (a,b):
            for index,step in enumerate(raw['steps']):
                self.assertEqual(sorted(step['resource_seed']),list(range(6)))
                self.assertLessEqual(step['resource_chosen_us'],step['resource_seed_us']+1e-8)
        self.assertEqual(b['permutations'][0],b['steps'][0]['resource_seed'])
        self.assertNotEqual(self.run_case(ce.DEMAND,6,1,rounds=0,skip=0).returncode,0)

    def test_witness_repaired_without_more_stages(self):
        for g,want in [(1,345.6),(4,1065.6)]:
            r=6*g
            data=[ce.DEMAND[(s//g)*6+d//g] if s%g==d%g else 0
                  for s in range(r) for d in range(r)]
            for scaling in (0,1):
                done=self.run_case(data,6,g,scaling=scaling)
                self.assertEqual(done.returncode,0,done.stderr)
                raw=json.loads(done.stdout)
                self.assertAlmostEqual(raw['total_us'],want)
                self.assertEqual(raw['stage_count'],2)
                self.assertEqual([s['alpha'] for s in raw['steps']],[1,1])
                self.assertGreater(raw['resource_accepts'],0)
                self.assertLessEqual(raw['resource_proposals'],6)
                study.audit_pipeline(data,6,g,raw)

    def test_zipf_conservation_and_invalid_options(self):
        data=study.fixed_load_matrix(8,4,'zipf','pipeline')
        done=self.run_case(data,8,4,unit=.001024)
        self.assertEqual(done.returncode,0,done.stderr)
        study.audit_pipeline(data,8,4,json.loads(done.stdout))
        for options in [dict(rounds=9),dict(topology=0),dict(strategy=0)]:
            self.assertNotEqual(self.run_case(data,8,4,**options).returncode,0)

    def test_disabled_matches_unmodified_s1(self):
        done=self.run_case(ce.DEMAND,6,1,rounds=0)
        self.assertEqual(done.returncode,0,done.stderr)
        raw=json.loads(done.stdout)
        self.assertAlmostEqual(raw['total_us'],425.6)
        self.assertEqual(raw['resource_proposals'],0)

    def test_small_exact_bottleneck_oracle_and_reconstruction(self):
        # Test-only exhaustive permutations; never used by the online matcher.
        for n in (3,4,5):
            data=dynamic.weighted_balanced(n,101)
            done=self.run_case(data,n,1,unit=.001024)
            self.assertEqual(done.returncode,0,done.stderr)
            raw=json.loads(done.stdout);remaining=data[:]
            for p,step in zip(raw['permutations'],raw['steps']):
                optimum=max(min(remaining[s*n+d] for s,d in enumerate(candidate))
                            for candidate in itertools.permutations(range(n)))
                self.assertEqual(step['alpha'],optimum)
                self.assertEqual(sorted(p),list(range(n)))
                self.assertEqual(min(remaining[s*n+d] for s,d in enumerate(p)),optimum)
                for s,d in enumerate(p):remaining[s*n+d]-=optimum
            self.assertFalse(any(remaining))
            self.assertLessEqual(raw['resource_proposals'],3*raw['stage_count'])


if __name__=='__main__':
    unittest.main()
