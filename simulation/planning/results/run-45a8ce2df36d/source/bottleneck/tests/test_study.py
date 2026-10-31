import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class StudyTests(unittest.TestCase):
    def test_grid_and_real_cli_reference_cache_identity(self):
        self.assertTrue((ROOT/'study.py').exists(),'study/CLI missing')
        import study
        cases=study.specs(False)
        self.assertEqual(len(cases),60)
        self.assertEqual({c['g'] for c in cases},{8})
        self.assertEqual({c['seed'] for c in cases if c['split']=='validation'},{280201,280203})
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);binary=study.build(out)
            oracle=json.loads((ROOT.parent/'reproduction/assets/oracle.json').read_text())
            for c in oracle['cases']:
                case=dict(n=c['n'],g=c['g'],wan_us=50)
                raw=study.invoke(binary,case,'legacy-profile',c['matrix'])
                self.assertAlmostEqual(raw['total_us'],c['fast_us'],places=9)
                self.assertAlmostEqual(raw['legacy_spreadout_us'],c['spreadout_us'],places=9)
            paper=study.invoke(binary,dict(n=2,g=1,wan_us=50),'legacy',[0,100000000,100000000,0])
            self.assertAlmostEqual(paper['total_us'],2000.7)
            case=study.specs(True)[0];data=study.matrix(case,binary)
            a=study.invoke(binary,case,'reference',data);b=study.invoke(binary,case,'cached',data)
            self.assertEqual(study.semantic(a),study.semantic(b))
            row,totals=study.reconstruct(case,'cached',data,b)
            self.assertGreater(row['total_ms'],0)
            self.assertGreater(sum(totals.values()),0)

    def test_real_smoke_and_independent_raw_digest(self):
        self.assertTrue((ROOT/'study.py').exists(),'study/CLI missing')
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            def call(phase):return subprocess.run([sys.executable,str(ROOT/'study.py'),'--phase',phase,'--output',str(out),'--smoke'],capture_output=True,text=True)
            for phase in ['prepare','run','audit']:
                result=call(phase);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            rows=json.loads((out/'comparison.json').read_text());self.assertEqual(len(rows),14)
            raw=next((out/'raw').glob('*.json'));value=json.loads(raw.read_text());value['extra_planner_us']=0;raw.write_text(json.dumps(value))
            self.assertNotEqual(call('audit').returncode,0)

if __name__=='__main__':unittest.main()
