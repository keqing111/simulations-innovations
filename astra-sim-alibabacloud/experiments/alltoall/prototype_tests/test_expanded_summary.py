"""The expanded report must retain denominators and avoid mixing study blocks."""
import importlib.util
from pathlib import Path
import unittest


def module():
    path=Path(__file__).resolve().parents[4]/'research/resource_a2av/summarize.py'
    spec=importlib.util.spec_from_file_location('expanded_summary_test',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


class ExpandedSummaryTests(unittest.TestCase):
    def test_partial_study_does_not_hide_missing_cases_or_invent_speedups(self):
        m=module();cases=[dict(name='a',block='primary',topology='mixed',family='balanced'),
                          dict(name='b',block='pressure',topology='mixed',family='balanced')]
        rows=[dict(case='a',method=k,success=True,pair_eligible=True,actual_ms=t)
              for k,t in [('fast-simai',8.),('spreadout',6.),('resource-maxq',4.)]]
        s=m.aggregate(cases,rows)
        self.assertEqual((s['conditions'],s['complete_triplets']),(2,1))
        self.assertEqual(s['fast-simai']['paired'],1)
        self.assertEqual(s['fast-simai']['unpaired'],1)
        self.assertEqual(s['fast-simai']['geomean_speedup'],2.)
        self.assertEqual(s['spreadout']['geomean_speedup'],1.5)
        self.assertEqual(m.aggregate(cases[1:],rows)['spreadout']['geomean_speedup'],None)

    def test_failed_or_contract_ineligible_baseline_is_not_a_win(self):
        m=module();c=[dict(name='a')]
        r=[dict(case='a',method='resource-maxq',success=True,pair_eligible=True,actual_ms=2),
           dict(case='a',method='fast-simai',success=True,pair_eligible=False,actual_ms=6),
           dict(case='a',method='spreadout',success=False,actual_ms=None)]
        s=m.aggregate(c,r)
        self.assertEqual(s['complete_triplets'],0)
        self.assertEqual(s['fast-simai']['paired'],0)
        self.assertEqual(s['spreadout']['wins'],0)
