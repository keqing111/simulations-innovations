import json
from pathlib import Path
import tempfile
import unittest

from simai_a2a.reporting import write_campaign_report


class ReportingTests(unittest.TestCase):
    def test_fast_only_campaign_does_not_require_spreadout_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [dict(algorithm=a,case_id='case',profile_id='M0',workload_id='balanced-low',
                         speedup_vs_spreadout=None,speedup_vs_fast=1.0,
                         actual_makespan_ns=1000000) for a in ('fast','fast-wan')]
            (root/'aggregate.json').write_text(json.dumps(rows))
            (root/'campaign_manifest.json').write_text(json.dumps({'run_count':2}))
            report=write_campaign_report(root)
            self.assertIn('fast-wan',report.read_text())
            self.assertIn('2/2',report.read_text())
