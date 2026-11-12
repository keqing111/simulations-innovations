import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ds/decomposition'))
import residual_opportunity as opportunity


def independent_raw():
    return dict(n=2, g=1, mode='pipeline', permutations=[[1, 0]],
                payload_units=[[1, 1]], steps=[dict(alpha=1, alpha_scaled=1)],
                topology_snapshot=dict(n=2, g=1, dc=[0, 1],
                    resource_names=['wan:0:1', 'wan:1:0'], bandwidth_gbps=[100, 100],
                    server_paths=[dict(resource_ids=[], propagation_us=0),
                        dict(resource_ids=[0], propagation_us=2),
                        dict(resource_ids=[1], propagation_us=2),
                        dict(resource_ids=[], propagation_us=0)]))


class ResidualOpportunity(unittest.TestCase):
    def test_consume_preserves_unselected_demand_and_rejects_negative(self):
        pending = [0, 7, 4, 0]
        self.assertEqual(opportunity.consume(pending, [1, 0], 3, 2, 1), [0, 4, 1, 0])
        self.assertEqual(pending, [0, 7, 4, 0])
        with self.assertRaises(ValueError):
            opportunity.consume([0, -1, 4, 0], [1, 0], 3, 2, 1)

    def test_capacity_and_endpoint_bounds_have_physical_units(self):
        # One MB per independent direction: 80us serialization, 2us propagation.
        result = opportunity.remaining_features(independent_raw(), [0, 1, 1, 0], 1000000)
        self.assertAlmostEqual(result['capacity_lb_us'], 80)
        self.assertAlmostEqual(result['endpoint_lb_us'], 82)
        self.assertAlmostEqual(result['combined_lb_us'], 82)
        self.assertAlmostEqual(result['wan_l2_us'], 113.13708498984761)
        empty = opportunity.remaining_features(independent_raw(), [0, 0, 0, 0], 1000000)
        self.assertEqual(empty['combined_lb_us'], 0)

    def test_action_uses_real_capacity_not_padded_bytes(self):
        result = opportunity.action_features(independent_raw(), [0, 1, 1, 0], [1, 0], 9, 1000000)
        self.assertAlmostEqual(result['stage_us'], 82)
        self.assertEqual(result['capacity_score_us'], 82)
        self.assertEqual(result['remaining']['combined_lb_us'], 0)

    def test_exact_independent_network_plan(self):
        oracle = opportunity.UniformOracle(independent_raw(), 1000000)
        self.assertAlmostEqual(oracle.value(oracle.full_mask), 82)
        self.assertEqual(oracle.plan(oracle.full_mask), [[1, 0]])

    def test_oracle_rejects_nonuniform_real_and_auxiliary_demand(self):
        for field in ('real', 'auxiliary', 'diagonal'):
            raw = independent_raw()
            if field == 'real':
                raw['payload_units'][0][0] = 2
            elif field == 'auxiliary':
                raw['steps'][0]['alpha_scaled'] = 2
            else:
                raw['permutations'] = [[0, 1]]
            with self.assertRaises(ValueError, msg=field):
                opportunity.UniformOracle(raw, 1000000)

    def test_first_lan_stage_does_not_destroy_optimal_uniform_suffix(self):
        path = ROOT / 'ds/decomposition/causal-certified-20261112/balanced-n6-top2-baseline.json'
        raw = json.loads(path.read_text())['raw']
        oracle = opportunity.UniformOracle(raw, 1024)
        self.assertAlmostEqual(oracle.value(oracle.full_mask), 21931.24832)
        chosen = oracle.mask_of(raw['permutations'][0])
        self.assertAlmostEqual(oracle.stage_cost(chosen) + oracle.value(oracle.full_mask ^ chosen),
                               21931.24832)
        seed = oracle.mask_of(raw['steps'][0]['resource_seed'])
        self.assertAlmostEqual(oracle.stage_cost(seed) + oracle.value(oracle.full_mask ^ seed),
                               26612.3616)

    def test_two_edge_exchange_can_miss_a_legal_improving_three_cycle(self):
        raw = json.loads((ROOT / 'ds/decomposition/causal-certified-20261112/balanced-n6-top2-baseline.json').read_text())['raw']
        oracle = opportunity.UniformOracle(raw, 1024)
        mask = oracle.full_mask ^ oracle.mask_of(raw['permutations'][0])
        result = opportunity.exchange_neighborhood(oracle, mask, raw['permutations'][1])
        self.assertEqual(result['2']['legal'], 3)
        self.assertEqual(result['2']['improving'], 0)
        self.assertAlmostEqual(result['2']['best_us'], 10442.14464)
        self.assertEqual(result['3']['legal'], 8)
        self.assertGreater(result['3']['improving'], 0)
        self.assertAlmostEqual(result['3']['best_us'], 5322.47232)


if __name__ == '__main__':
    unittest.main()
