import importlib.util
from pathlib import Path
import unittest

class V2ReportingTests(unittest.TestCase):
    def test_only_complete_six_way_pairs_enter_aggregate(self):
        path=Path(__file__).resolve().parents[1]/'tools/summarize_v2_study.py'
        spec=importlib.util.spec_from_file_location('v2report',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        rows=[dict(case='complete',method=method,success=True,communication_ns=100 if method=='fast-simai' else 50)
              for method in m.METHODS]
        rows.append(dict(case='pending',method='fast-simai',success=True,communication_ns=1))
        paired,table=m.paired(rows)
        self.assertEqual(paired,['complete'])
        self.assertEqual(table['fast-joint-stage-b1']['speedup_fast_geomean'],2.)

