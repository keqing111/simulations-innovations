import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('boundary_test',ROOT/'study.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)


class BoundaryTests(unittest.TestCase):
    def test_grid_and_native_command_use_actual_wan_capacity(self):
        cases=s.specs();self.assertEqual(len(cases),90);self.assertEqual(len({c['id'] for c in cases}),90)
        self.assertEqual({c['seed'] for c in cases if c['kind']=='zipf'},{760203,810203})
        self.assertEqual(len([c for c in cases if c['kind']=='balanced']),30)
        c=dict(cases[0],wan_bw=400,wan_us=3000)
        self.assertEqual(s.command(Path('/frozen/native_scan'),c,'A1-opt'),['/frozen/native_scan','24','8','A1-opt','3000','1',','.join(map(str,c['dc'])),'400','400','400','asymmetric'])

    def test_real_archive_capacity_repricing_and_tamper_guards(self):
        with tempfile.TemporaryDirectory(prefix='fast-boundary-test-') as tmp:
            out=Path(tmp);record=s.prepare(out,True);self.assertEqual(len(record['cases']),8)
            rows=s.run(out,True);self.assertEqual(len(rows),48)
            self.assertFalse(any('planning_us' in row for row in rows))
            engine=s.load_engine(out)
            c=dict(record['cases'][0],n=2,g=1,dc_count=2,dc=[0,1],wan_us=1,wan_bw=100)
            for bw,serial in [(100,.008),(400,.002)]:
                c['wan_bw']=bw;raw=s.invoke(out/'native_scan',c,'maxmin-opt',[0,100,100,0])
                stage=next(t for t in raw['stages'] if t['links'])
                self.assertAlmostEqual(stage['serial_us'],serial);self.assertAlmostEqual(stage['alpha_us'],3.8)
                self.assertEqual(stage['bandwidths']['wan:0:1'],bw)
                row,_=engine.reconstruct(c,'maxmin-opt',[0,100,100,0],raw)
            s.audit(out,True,replay=True)
            table=out/'comparison.json';original=table.read_bytes();table.write_text('[]')
            with self.assertRaises(ValueError):s.checked(out,True,derived=True)
            table.write_bytes(original)
            rawpath=next((out/'raw').glob('*.json'));original=rawpath.read_bytes();value=json.loads(original);value['_command'][4]='999';rawpath.write_text(json.dumps(value))
            with self.assertRaises(ValueError):s.audit(out,True,replay=False)
            rawpath.write_bytes(original)
            binary=out/'native_scan';original=binary.read_bytes();binary.write_bytes(original+b'changed')
            with self.assertRaises(ValueError):s.checked(out,True)


if __name__=='__main__':unittest.main()
