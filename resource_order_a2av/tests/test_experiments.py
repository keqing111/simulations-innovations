import importlib.util
import unittest


class ExperimentTests(unittest.TestCase):
    def test_loads_conserve_per_rank_budget_and_seed(self):
        self.assertIsNotNone(importlib.util.find_spec('resource_order.experiments'))
        from resource_order.experiments import demand
        for kind in ('balanced','zipf','zipf-shared'):
            a=demand(8,10001,kind,7);b=demand(8,10001,kind,7)
            self.assertEqual(a,b);self.assertEqual(len(a),56)
            self.assertEqual(len({(f.src,f.dst) for f in a}),56)
            for src in range(8):self.assertEqual(sum(f.bytes for f in a if f.src==src),10001)
        self.assertNotEqual(demand(8,10001,'zipf',7),demand(8,10001,'zipf',8))
        with self.assertRaises(ValueError):demand(8,6,'zipf',7)

    def test_shared_popularity_has_common_hot_receiver_not_independent_rows(self):
        from resource_order.experiments import demand
        f=demand(32,1048576,'zipf-shared',41)
        hot=[max((x for x in f if x.src==s),key=lambda x:x.bytes).dst for s in range(32)]
        self.assertEqual(len(set(hot)),2)  # hot source itself must choose next receiver
        self.assertEqual(max(hot.count(d) for d in hot),31)


if __name__=='__main__':unittest.main()
