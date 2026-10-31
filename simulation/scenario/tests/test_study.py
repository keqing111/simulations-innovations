import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('scenario_study',ROOT/'study.py')
        cls.s=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.s)
        cls.temp=tempfile.TemporaryDirectory();cls.out=Path(cls.temp.name);cls.binary=cls.s.build(cls.out)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_grid_and_paired_marginals(self):
        s=self.s
        self.assertEqual(len(s.specs(False)),106)
        self.assertEqual(len(s.specs(False,'confirm')),48)
        self.assertEqual(len({c['id'] for c in s.specs(False)}),106)
        for dc_count in [2,3,4,6]:
            case=next(c for c in s.specs(False) if c['dc_count']==dc_count and c['kind']=='balanced')
            b=s.matrix(case,self.binary);r=case['n']*case['g'];q=sum(b)//r
            changed={**case,'kind':'block'};block=s.matrix(changed,self.binary)
            self.assertEqual([sum(block[i*r:(i+1)*r]) for i in range(r)],[q]*r)
            self.assertEqual([sum(block[i*r+j] for i in range(r)) for j in range(r)],[q]*r)
            dc=case['dc']
            for i in range(r):
                for j in range(r):
                    if dc[i//case['g']]==dc[j//case['g']]:self.assertEqual(b[i*r+j],block[i*r+j])
            wan=lambda a:sum(a[i*r+j] for i in range(r) for j in range(r) if dc[i//case['g']]!=dc[j//case['g']])
            self.assertEqual(wan(b),wan(block));self.assertEqual(sum(b),sum(block))
            if dc_count==2:self.assertEqual(b,block)
            else:self.assertNotEqual(b,block)
            self.assertEqual(sum(s.matrix({**case,'kind':'zipf','seed':520101},self.binary)),sum(b))
        blocks=[c for c in s.specs(False) if c['kind']=='block']
        self.assertEqual(len({s.input_key(c) for c in blocks}),3)

    def test_cli_audit_tamper_and_legacy_equivalence(self):
        s=self.s
        for case in s.specs(True):
            data=s.matrix(case,self.binary);raw=s.invoke(self.binary,case,None,data,2)
            self.assertEqual(set(raw),set(s.METHODS))
            for method,value in raw.items():
                row,_=s.reconstruct(case,method,data,value);self.assertGreater(row['total_ms'],0)
                bad=copy.deepcopy(value);bad['topology']['dc'][0]=(bad['topology']['dc'][0]+1)%3
                with self.assertRaises(ValueError):s.reconstruct(case,method,data,bad)
            bad=copy.deepcopy(raw['A2']);bad['decisions'][0]['constructions']=3
            with self.assertRaises(ValueError):s.reconstruct(case,'A2',data,bad)
        frozen=ROOT.parent/'refine/results/run-7f4cfed80dd1/native_scan'
        case={**s.specs(True)[0],'dc':[0,0,1,1,2,2],'profile':'shared100','kind':'zipf','seed':280101}
        data=s.matrix(case,self.binary)
        for m in ['F000','maxmin','spreadout','X10','X11']:
            new=s.invoke(self.binary,case,m,data,1)
            old=json.loads(subprocess.check_output([str(frozen),'6','1',m,str(case['wan_us']),'1'],input=' '.join(map(str,data)),text=True))
            for key in ['stages','total_us','balance_us','intrinsic_us','dispatch_us','service','inter_bytes']:
                self.assertEqual(new[key],old[key],(m,key))
        for seed in [520101,520201]:
            args=['--zipf',str(seed),'64']
            self.assertEqual(subprocess.check_output([str(self.binary),*args]),subprocess.check_output([str(frozen),*args]))

    def test_archive_smoke_and_mutation(self):
        s=self.s;run=self.out/'smoke';s.prepare(run,True);s.run(run,True)
        self.assertEqual(len(s.audit(run,True)),len(s.specs(True))*7)
        path=next((run/'raw').glob('*.json'));bad=json.loads(path.read_text());bad['timing_samples'][0]['planning_us']=0
        s.save(path,bad)
        with self.assertRaises(ValueError):s.audit(run,True)

if __name__=='__main__':unittest.main()
