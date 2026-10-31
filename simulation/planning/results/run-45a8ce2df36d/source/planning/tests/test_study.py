import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('planning_under_test',ROOT/'study.py')
study=importlib.util.module_from_spec(spec);spec.loader.exec_module(study)


class StudyTests(unittest.TestCase):
    def test_frozen_grid(self):
        self.assertEqual([len(study.specs(suite=s)) for s in ['explore','confirm']],[8,32])
        self.assertEqual(len(study.METHODS),12)
        for suite in ['explore','confirm']:
            cases=study.specs(suite=suite)
            self.assertEqual(len({c['id'] for c in cases}),len(cases))
            self.assertEqual({c['profile'] for c in cases},{'asymmetric'})
            self.assertEqual({c['wan_us'] for c in cases},{50,5000})
        self.assertEqual(study.policy('SPM-opt'),{**study.scene.policy('A1'),'static_path':True,'optimized':True})

    def test_pair_guard_checks_every_semantic_field(self):
        a={'mode':'A1-ref','policy':study.policy('A1-ref'),'stages':[{'map':[1,0],'freq':3}],
           'decisions':[{'weight':3}],'timing_samples':[{'planning_us':100}],'wall_us':200}
        b=copy.deepcopy(a);b['mode']='A1-opt';b['policy']=study.policy('A1-opt');b['wall_us']=1;b['timing_samples']=[]
        study.check_pair(a,b)
        for field in ['stages','decisions','policy']:
            changed=copy.deepcopy(b)
            if field=='policy':changed[field]['static_path']=True
            else:changed[field][0]['freq' if field=='stages' else 'weight']=4
            with self.assertRaises(ValueError):study.check_pair(a,changed)

    def test_real_smoke_archive_and_tamper(self):
        with tempfile.TemporaryDirectory(prefix='fast-planning-test-') as tmp:
            out=Path(tmp);record=study.prepare(out,True)
            self.assertEqual(record['repeats'],3)
            rows=study.run(out,True)
            self.assertEqual(len(rows),len(record['cases'])*12)
            self.assertTrue(all(r['implementation'] in ['ref','opt'] for r in rows))
            raw=json.loads(next((out/'raw').glob('*A1-opt.json')).read_text())
            case=record['cases'][0];data=json.loads((out/'matrices'/f'{case["id"]}.json').read_text())
            raw['policy']['optimized']=False
            with self.assertRaises(ValueError):study.reconstruct(case,'A1-opt',data,raw)
            path=out/'source'/'scan/planner.cpp';path.write_text(path.read_text()+'\n// tamper\n')
            with self.assertRaises(ValueError):study.checked(out,True)


if __name__=='__main__':unittest.main()
