"""Regressions for neutral reduction and real-byte cost matching."""

import unittest
import numpy as np

from simai_a2a.model import HardwareProfile, LinkModel, TrafficMatrix
from simai_a2a.schedulers import get_scheduler
from simai_a2a.delivery import validate_fast_delivery


OPTIONS = dict(server_dc_ids=[0, 0, 1], intra_dc_rtt_ns=100,
               inter_dc_rtt_ns=1000, intra_dc_bandwidth_gbps=400,
               inter_dc_bandwidth_gbps=100)
HW = HardwareProfile(3, 2, LinkModel(10, .001), LinkModel(100, .02),
                     'test', 400, 1)


class FastCostTests(unittest.TestCase):
    def test_equal_weights_reproduce_native_flows_quotas_and_timing(self):
        # Sorting raw D instead of excess cost breaks this regression.
        rng = np.random.default_rng(20261024)
        for seed in range(12):
            data = rng.integers(0, 100000, (6, 6), dtype=np.int64)
            data[rng.random((6, 6)) < .4] = 0
            np.fill_diagonal(data, 0)
            matrix = TrafficMatrix(data)
            original = get_scheduler('fast').schedule(matrix, HW, seed=seed)
            neutral = get_scheduler('fast-cost', fast_options={
                **OPTIONS, 'inter_dc_bandwidth_gbps': 400}).schedule(matrix, HW, seed=seed)
            self.assertEqual(original.flows, neutral.flows)
            self.assertEqual(original.metadata['stage_records'],
                             [{key: record[key] for key in ('stage_id', 'mp', 'quota_per_gpu_bytes')}
                              for record in neutral.metadata['stage_records']])
            self.assertEqual(original.predicted_makespan_ns, neutral.predicted_makespan_ns)

    def test_cost_order_ignores_padding_and_preserves_native_ties(self):
        from simai_a2a.schedulers.fast_wan import PairProfile, cost_ordered_support
        remaining = np.zeros((3, 1, 3), dtype=np.int64)
        remaining[0, 0, 1], remaining[0, 0, 2] = 10000, 10
        ordered = cost_ordered_support([[1, 0, 2], [2, 1], [2, 0, 1]],
                                       remaining, PairProfile.from_dict(OPTIONS))
        self.assertEqual(ordered, [[2, 1, 0], [2, 1], [2, 0, 1]])

    def test_sparse_and_local_demands_deliver_without_modifying_input(self):
        rng = np.random.default_rng(73)
        for seed in range(25):
            data = rng.integers(0, 50000, (6, 6), dtype=np.int64)
            data[rng.random((6, 6)) < .7] = 0
            np.fill_diagonal(data, 0)
            saved = data.copy()
            schedule = get_scheduler('fast-cost', fast_options=OPTIONS).schedule(
                TrafficMatrix(data), HW, seed=seed)
            validate_fast_delivery(TrafficMatrix(data), schedule, 2)
            np.testing.assert_array_equal(data, saved)
            self.assertLessEqual(schedule.metadata['n_stages'], 9)
            self.assertTrue(all(not row['wan_pending_before'] or row['real_wan_bytes'] > 0
                                for row in schedule.metadata.get('stage_records', [])))


if __name__ == '__main__':
    unittest.main()
