import collections
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ds/decomposition'))
import cycle_control as control
import study


class CycleControl(unittest.TestCase):
    def test_seed101_preserves_existing_zipf_generator(self):
        self.assertEqual(control.zipf_matrix(6, 4, 101),
                         study.fixed_load_matrix(6, 4, 'zipf', 'pipeline'))

    def test_new_seed_keeps_source_budget_and_no_same_server_flow(self):
        a = control.zipf_matrix(6, 4, 202)
        b = control.zipf_matrix(6, 4, 303)
        self.assertNotEqual(a, b)
        for matrix in (a, b):
            for s in range(24):
                self.assertEqual(sum(matrix[s*24:(s+1)*24]), 39060)
                self.assertTrue(all(matrix[s*24+d] == 0 for d in range(24) if s//4 == d//4))

    def test_panel_conditions_and_names_are_distinct(self):
        rows = list(control.cases(('development', 'new-seed', 'legacy', 'scale')))
        self.assertEqual(len(rows), 35)
        self.assertEqual(len({row['name'] for row in rows}), 35)
        conditions = collections.Counter()
        for row in rows:
            conditions[row['panel']] += len(row['units'])
        self.assertEqual(conditions, dict(development=42, **{'new-seed': 12}, legacy=12, scale=2))


if __name__ == '__main__':
    unittest.main()
