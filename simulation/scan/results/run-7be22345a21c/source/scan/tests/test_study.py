import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('scan_study',ROOT/'study.py')
        cls.study=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.study)
        cls.temp=tempfile.TemporaryDirectory();cls.output=Path(cls.temp.name)
        cls.binary=cls.study.build(cls.output)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_registered_grid_and_four_frozen_baselines(self):
        study=self.study;cases=study.specs(False)
        self.assertEqual(len(cases),26);self.assertEqual(len({c['id'] for c in cases}),26)
        self.assertEqual(len(study.METHODS),10)
        self.assertEqual({c['split'] for c in cases},{'development','diagnostic'})
        frozen=ROOT.parent/'bottleneck/results/run-33e58e040218/native_bottleneck'
        for case in [study.specs(True)[0],study.specs(True)[1],dict(n=12,g=2,mib=1,wan_us=5000,kind='zipf',seed=280101)]:
            data=study.matrix(case,self.binary)
            for current,old in [('F000','native'),('F010','bounded'),('maxmin','maxmin'),('spreadout','spreadout')]:
                new=study.invoke(self.binary,case,current,data,repeats=1)
                previous=study.legacy.invoke(frozen,case,old,data)
                keys=['total_us','stages','inter_bytes'] if current=='spreadout' else ['total_us','balance_us','intrinsic_us','dispatch_us','stages','inter_bytes']
                for key in keys:
                    self.assertEqual(new[key],previous[key],(current,key))
                study.reconstruct(case,current,data,new)

    def test_all_factors_real_cli_timing_and_tamper_detection(self):
        study=self.study;case=study.specs(True)[1];data=study.matrix(case,self.binary)
        for method in study.METHODS:
            raw=study.invoke(self.binary,case,method,data,repeats=3)
            row,_=study.reconstruct(case,method,data,raw)
            self.assertGreater(row['total_ms'],0)
            self.assertEqual(len(raw['timing_samples']),3)
            if method!='spreadout':self.assertEqual(raw['decomposition_runs'],1)
            altered=json.loads(json.dumps(raw));altered['timing_samples'][0]['planning_us']=-1
            with self.assertRaises(ValueError):study.reconstruct(case,method,data,altered)
        run=self.output/'smoke';study.prepare(run,True);study.run(run,True)
        self.assertEqual(len(study.audit(run,True)),20)
        path=next((run/'raw').glob('*.json'));value=json.loads(path.read_text())
        value['timing_samples'][0]['planning_us']=0;path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):study.audit(run,True)

    def test_repetition_count_rejected_before_integer_narrowing(self):
        for repeats in ['0','1001','4294967297']:
            result=subprocess.run([str(self.binary),'2','1','F000','50',repeats],input='0 10 10 0',
                                  capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0,repeats)

    def test_zipf_seed_matches_frozen_generator_not_constructor_clock(self):
        frozen=ROOT.parent/'bottleneck/results/run-33e58e040218/native_bottleneck'
        results=[]
        for seed in [280101,280203]:
            args=['--zipf',str(seed),'256']
            expected=json.loads(subprocess.check_output([str(frozen),*args],text=True))
            actual=json.loads(subprocess.check_output([str(self.binary),*args],text=True))
            self.assertEqual(actual,expected,'constructor must not override registered seed')
            results.append(actual)
        self.assertNotEqual(results[0],results[1])

    def test_registered_inputs_regenerate_and_pair_across_wan(self):
        study=self.study;out=self.output/'paired';record=study.prepare(out,False)
        by_input={}
        for case in record['cases']:
            key=tuple(case[k] for k in ['n','g','mib','kind','seed'])
            digest=record['inputs'][case['id']]['sha256']
            if key in by_input:self.assertEqual(digest,by_input[key])
            by_input[key]=digest
        # A self-consistent manifest/file rewrite must not turn other bytes into this seed.
        case=next(c for c in record['cases'] if c['kind']=='zipf')
        path=out/'matrices'/f'{case["id"]}.json';data=json.loads(path.read_text());data[1]+=1;study.save(path,data)
        record['inputs'][case['id']]['sha256']=study.sha(path)
        record['inputs'][case['id']]['actual_bytes']=sum(data);study.save(out/'study.json',record)
        with self.assertRaises(ValueError):study.checked(out,False)

if __name__=='__main__':unittest.main()
