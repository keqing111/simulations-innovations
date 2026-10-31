import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class StudyTests(unittest.TestCase):
    def test_frozen_grid_and_native_generator(self):
        import study
        cases=study.specs(False)
        self.assertEqual(len(cases),40)
        self.assertEqual({c['seed'] for c in cases if c['split']=='validation'},{801,809})
        weights=json.loads(subprocess.check_output([str(ROOT/'build/native_topology'),'--zipf','701','20']))
        self.assertEqual(len(weights),20)
        self.assertTrue(all(1<=v<=10000 for v in weights))
        self.assertEqual(weights,json.loads(subprocess.check_output([str(ROOT/'build/native_topology'),'--zipf','701','20'])))

    def test_real_smoke_and_forged_link_or_policy_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'study'
            def call(phase):return subprocess.run([sys.executable,str(ROOT/'study.py'),'--phase',phase,'--output',str(output),'--smoke'],capture_output=True,text=True)
            for phase in ['prepare','run','audit']:
                result=call(phase);self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(len(json.loads((output/'comparison.json').read_text())),6)
            path=next((output/'raw').glob('*native.json'));old=path.read_text();raw=json.loads(old)
            key=next(iter(raw['stages'][0]['links']));raw['stages'][0]['links'][key]+=1;path.write_text(json.dumps(raw))
            self.assertNotEqual(call('audit').returncode,0)
            path.write_text(old);raw=json.loads(old);raw['mode']='wan-repair';path.write_text(json.dumps(raw))
            self.assertNotEqual(call('audit').returncode,0)


if __name__=='__main__':unittest.main()
