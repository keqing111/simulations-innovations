"""Physical, combinatorial and weighted-service contracts for FAST extensions."""

import unittest

import numpy as np

from simai_a2a.model import HardwareProfile, LinkModel, TrafficMatrix
from simai_a2a.schedulers import fast_port
from simai_a2a.schedulers.fast import FastScheduler


class DispatchTests(unittest.TestCase):
    def test_dispatch_is_executed_at_the_destination_server(self):
        data = np.zeros((4, 4), dtype=np.int64)
        data[0, 3] = 1_000_000
        hardware = HardwareProfile(2, 2, LinkModel(10, .001),
                                   LinkModel(100, .08), "test", 400, 1)
        schedule = FastScheduler().schedule(TrafficMatrix(data), hardware, seed=1)
        dispatch = [(f.src, f.dst, f.size_bytes) for f in schedule.flows
                    if f.phase == "dispatch"]
        self.assertEqual(dispatch, [(2, 3, 500_000)])


class MatchingTests(unittest.TestCase):
    def test_incremental_support_preserves_native_neighbor_iteration_order(self):
        from simai_a2a.schedulers.fast_wan import update_support
        rng = np.random.default_rng(91)
        a = fast_port.FastAll2All(np.zeros((6,6),dtype=np.int64),1,'ETHER400','H100')
        b = fast_port.FastAll2All(np.zeros((6,6),dtype=np.int64),1,'ETHER400','H100')
        previous = np.zeros((6,6),dtype=bool)
        for _ in range(20):
            residual = rng.integers(0,3,(6,6),dtype=np.int64)
            a.SDS_mat = residual.copy()
            b.SDS_mat = residual.copy()
            a.update_edges()
            previous = update_support(b, previous)
            self.assertEqual([list(row) for row in b.row_to_col],
                             [list(row) for row in a.row_to_col])

    def repair(self, support, matching, real):
        # Import in the test to expose missing implementation as the RED step.
        from simai_a2a.schedulers.fast_wan import repair_matching
        return repair_matching(support, matching, real)

    def test_alternating_cycle_preserves_perfection_and_includes_target(self):
        support = [[1, 2], [0, 2], [0, 1]]
        matching = [1, 2, 0]
        real = np.zeros((3, 3), dtype=bool)
        real[0, 2] = True
        repaired, changed = self.repair(support, matching, real)
        self.assertTrue(changed)
        self.assertEqual(repaired, [2, 0, 1])
        self.assertEqual(sorted(repaired), [0, 1, 2])
        self.assertEqual(matching, [1, 2, 0])

    def test_existing_real_target_is_accepted_without_change(self):
        target = np.array([[False, True], [False, False]])
        repaired, changed = self.repair([[1], [0]], [1, 0], target)
        self.assertEqual(repaired, [1, 0])
        self.assertFalse(changed)

    def test_unextendable_edge_falls_back_to_original_matching(self):
        target = np.array([[False, True], [False, False]])
        repaired, changed = self.repair([[0, 1], [1]], [0, 1], target)
        self.assertEqual(repaired, [0, 1])
        self.assertFalse(changed)


class QuotaTests(unittest.TestCase):
    def test_fast_pair_sends_more_bytes_for_the_same_service_time(self):
        from simai_a2a.schedulers.fast_wan import PairProfile, weighted_quotas
        profile = PairProfile((0, 0, 1, 1), 10, 100, 32, 8)
        # 1 GPU/server: 4 bytes/ns DC, 1 byte/ns WAN. WAN drains in 110 ns.
        remaining = np.zeros((4, 1, 4), dtype=np.int64)
        remaining[0, 0, 1] = 1000
        remaining[1, 0, 2] = 10
        remaining[2, 0, 3] = 1000
        remaining[3, 0, 0] = 10
        quotas, duration = weighted_quotas(remaining, [1, 2, 3, 0], profile)
        self.assertEqual(duration, 110)
        self.assertEqual(quotas, [400, 10, 400, 10])

    def test_empty_matched_pair_gets_no_virtual_payload(self):
        from simai_a2a.schedulers.fast_wan import PairProfile, weighted_quotas
        remaining = np.zeros((2, 1, 2), dtype=np.int64)
        remaining[0, 0, 1] = 1
        quotas, duration = weighted_quotas(
            remaining, [1, 0], PairProfile((0, 1), 1, 100, 8, 8))
        self.assertEqual(quotas, [1, 0])
        self.assertEqual(duration, 101)

    def test_pair_whose_rtt_exceeds_horizon_is_not_transmitted(self):
        from simai_a2a.schedulers.fast_wan import PairProfile, weighted_quotas
        remaining = np.zeros((4, 1, 4), dtype=np.int64)
        for src, dst in enumerate([1, 2, 3, 0]):
            remaining[src, 0, dst] = 10
        profile = PairProfile((0, 0, 1, 1), 1000, 1, 8, 8)
        quotas, duration = weighted_quotas(remaining, [1, 2, 3, 0], profile)
        self.assertEqual(duration, 11)
        self.assertEqual(quotas, [0, 10, 0, 10])


OPTIONS = dict(server_dc_ids=[0, 0, 1], intra_dc_rtt_ns=100,
               inter_dc_rtt_ns=1000, intra_dc_bandwidth_gbps=400,
               inter_dc_bandwidth_gbps=100)


class ExtensionTests(unittest.TestCase):
    def test_invalid_pair_profile_is_rejected_for_local_only_input(self):
        from simai_a2a.schedulers import get_scheduler
        with self.assertRaises(ValueError):
            get_scheduler('fast-weighted',fast_options={**OPTIONS,'inter_dc_bandwidth_gbps':'bad'})
        with self.assertRaises(ValueError):
            get_scheduler('fast-wan',fast_options={**OPTIONS,'server_dc_ids':None})

    def test_no_wan_requirement_preserves_native_uniform_schedule(self):
        from simai_a2a.schedulers import get_scheduler
        data = np.arange(36, dtype=np.int64).reshape(6,6) * 100000
        np.fill_diagonal(data, 0)
        hardware = HardwareProfile(3,2,LinkModel(10,.001),LinkModel(100,.02),'test',400,1)
        original = FastScheduler().schedule(TrafficMatrix(data), hardware, seed=1)
        options = {**OPTIONS, 'server_dc_ids': [0,0,0]}
        extended = get_scheduler('fast-wan', fast_options=options).schedule(
            TrafficMatrix(data), hardware, seed=1)
        self.assertEqual(extended.flows, original.flows)
        self.assertEqual(extended.predicted_makespan_ns, original.predicted_makespan_ns)

    def test_random_sparse_demands_preserve_logical_delivery(self):
        from simai_a2a.schedulers import get_scheduler
        from simai_a2a.delivery import validate_fast_delivery
        rng = np.random.default_rng(20261023)
        hardware = HardwareProfile(3,2,LinkModel(10,.001),LinkModel(100,.02),'test',400,1)
        for seed in range(20):
            data = rng.integers(0,100000,(6,6),dtype=np.int64)
            data[rng.random((6,6)) < .65] = 0
            np.fill_diagonal(data,0)
            for name in ('fast','fast-wan','fast-weighted','fast-wan-weighted'):
                with self.subTest(seed=seed, name=name):
                    schedule = get_scheduler(name, fast_options=OPTIONS).schedule(
                        TrafficMatrix(data), hardware, seed=seed)
                    validate_fast_delivery(TrafficMatrix(data), schedule, 2)
                    if 'weighted' in name:
                        self.assertLessEqual(schedule.metadata['n_stages'],6)

    def test_four_variants_conserve_every_server_pair_and_gpu_net_bytes(self):
        from simai_a2a.schedulers import get_scheduler
        data = np.arange(1, 37, dtype=np.int64).reshape(6, 6) * 100_000
        np.fill_diagonal(data, 0)
        hardware = HardwareProfile(3, 2, LinkModel(10, .001),
                                   LinkModel(100, .02), "test", 400, 1)
        for algorithm in ("fast", "fast-wan", "fast-weighted", "fast-wan-weighted"):
            with self.subTest(algorithm=algorithm):
                schedule = get_scheduler(algorithm, fast_options=OPTIONS).schedule(
                    TrafficMatrix(data.copy()), hardware, seed=1)
                actual = np.zeros((3, 3), dtype=np.int64)
                net = np.zeros(6, dtype=np.int64)
                for flow in schedule.flows:
                    net[flow.src] += flow.size_bytes
                    net[flow.dst] -= flow.size_bytes
                    if flow.phase == "inter":
                        actual[flow.src//2, flow.dst//2] += flow.size_bytes
                expected = data.reshape(3, 2, 3, 2).sum(axis=(1, 3))
                np.fill_diagonal(expected, 0)
                np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal(net, data.sum(axis=1)-data.sum(axis=0))
                from simai_a2a.delivery import validate_fast_delivery
                self.assertEqual(validate_fast_delivery(TrafficMatrix(data), schedule, 2)
                                 ['logical_bytes_delivered'], int(data.sum()))
                self.assertEqual(schedule.algorithm, algorithm)
                if "weighted" in algorithm:
                    self.assertLessEqual(schedule.metadata["n_stages"], 6)
                if "wan" in algorithm:
                    self.assertTrue(all(not s['wan_pending_before'] or s['real_wan_bytes'] > 0
                                        for s in schedule.metadata['stage_records']))

    def test_local_only_and_zero_demands_do_not_create_inter_flows(self):
        from simai_a2a.schedulers import get_scheduler
        hardware = HardwareProfile(3, 2, LinkModel(10, .001),
                                   LinkModel(100, .02), "test", 400, 1)
        for amount in (0, 100):
            data = np.zeros((6, 6), dtype=np.int64)
            data[4, 5] = amount
            schedule = get_scheduler("fast-wan-weighted", fast_options=OPTIONS).schedule(
                TrafficMatrix(data), hardware, seed=1)
            self.assertEqual(schedule.physical_bytes, amount)
            self.assertFalse(any(f.phase == "inter" for f in schedule.flows))

    def test_original_local_only_emitter_does_not_replay_intrinsic_bytes(self):
        data = np.zeros((4, 4), dtype=np.int64)
        data[0, 1] = 10
        hardware = HardwareProfile(2, 2, LinkModel(10, .001),
                                   LinkModel(100, .02), "test", 400, 1)
        schedule = FastScheduler().schedule(TrafficMatrix(data), hardware, seed=1)
        self.assertEqual(schedule.physical_bytes, 10)

    def test_wan_priority_repairs_a_real_local_only_stage(self):
        from simai_a2a.schedulers import get_scheduler
        data = np.array([
            [0,8,3,5,7,8], [28,0,8,8,8,2], [1,1,0,28,3,8],
            [6,1,20,0,5,2], [2,8,8,8,0,16], [2,7,0,8,0,0],
        ], dtype=np.int64) * 100_000
        options = {**OPTIONS, 'server_dc_ids': [0,0,1,1,2,2]}
        hardware = HardwareProfile(6, 1, LinkModel(10, .001),
                                   LinkModel(100, .02), "test", 400, 1)
        schedule = get_scheduler('fast-wan', fast_options=options).schedule(
            TrafficMatrix(data), hardware, seed=1)
        self.assertGreater(schedule.metadata['synthesis']['repair_count'], 0)
        self.assertEqual(schedule.metadata['synthesis']['repair_fallback_count'], 0)

    def test_delivery_validator_detects_wrong_final_destination(self):
        from simai_a2a.delivery import validate_fast_delivery
        from simai_a2a.model import Schedule, ScheduledFlow
        data = np.zeros((4, 4), dtype=np.int64)
        data[0, 3] = 100
        wrong = Schedule('fast', [ScheduledFlow(0, 0, 2, 100, 0, 'inter', 0)], None)
        with self.assertRaisesRegex(ValueError, 'dispatch'):
            validate_fast_delivery(TrafficMatrix(data), wrong, 2)


class WanMonitorTests(unittest.TestCase):
    def test_missing_trace_is_not_reported_as_zero_utilization(self):
        from simai_a2a.analysis import analyze_wan_bandwidth
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                analyze_wan_bandwidth(Path(tmp)/'absent.txt', [[33,2]], epoch_ns=0,
                                      makespan_ns=4000, interval_ns=1000,capacity_gbps=100)

    def test_omitted_zero_samples_are_included_in_utilization_and_gaps(self):
        from simai_a2a.analysis import analyze_wan_bandwidth
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            bw = Path(tmp) / 'bw.txt'
            bw.write_text('time, node_id, port_id, bandwidth\n'
                          '1000, 33, 2, 50\n3000, 33, 2, 100\n'
                          '2000, 33, 1, 400\n', encoding='utf-8')
            result = analyze_wan_bandwidth(bw, [[33,2]], epoch_ns=0,
                                          makespan_ns=4000, interval_ns=1000,
                                          capacity_gbps=100)
            port = result['ports'][0]
            self.assertAlmostEqual(port['mean_utilization'], .375)
            self.assertEqual(port['sampled_idle_ns'], 2000)
            self.assertEqual(port['max_sampled_idle_gap_ns'], 1000)
            self.assertEqual(port['active_window_idle_ns'], 1000)


if __name__ == "__main__":
    unittest.main()
