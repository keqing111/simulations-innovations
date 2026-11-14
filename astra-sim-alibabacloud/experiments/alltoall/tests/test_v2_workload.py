import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

class V2WorkloadTests(unittest.TestCase):
    def test_synthetic_row_totals_and_common_scale(self):
        from simai_a2a.v2_workload import synthetic
        for p in (64,96,128):
            for family in ('balanced','zipf08','zipf12'):
                a=synthetic(p,family,1101,1);b=synthetic(p,family,1101,4)
                self.assertEqual(len(set(map(int,a.values.sum(axis=1)))),1)
                self.assertTrue(np.all(np.diag(a.values)==0))
                ca=a.values//a.metadata['block_bytes'];cb=b.values//b.metadata['block_bytes']
                np.testing.assert_array_equal(ca,cb)
                if family=='balanced':self.assertEqual(len(set(ca[ca>0])),1)
                else:self.assertGreater(int(ca.sum(axis=0).max()),2*int(ca.sum(axis=0).mean()))

    def test_trace_windows_nested_and_no_repetition(self):
        from simai_a2a.v2_workload import trace_input
        with tempfile.TemporaryDirectory() as temp:
            files=[]
            for f in range(8):
                path=Path(temp)/f'{f}.json';path.write_text(json.dumps([{'1':[[i%8,(i+1)%8] for i in range(12)]}]))
                files.append(path)
            catalog=dict(files=list(map(str,files)),layers=[1],experts=8)
            a=trace_input(catalog,0,1,4,2);b=trace_input(catalog,0,1,4,4)
            self.assertEqual(a.metadata['tokens'],8);self.assertEqual(b.metadata['tokens'],16)
            self.assertEqual(a.logical_bytes+a.metadata['self_route_bytes'],8*2*8192)
            self.assertEqual(a.metadata['token_origins'],b.metadata['token_origins'][:8])
            self.assertTrue(all(1<=origin[0]<3 for origin in b.metadata['token_origins']))
            with self.assertRaises(ValueError):trace_input(catalog,0,1,4,100)

