import unittest

import numpy as np

from simai_a2a.model import HardwareProfile, LinkModel, TrafficMatrix
from simai_a2a.schedulers.fast import FastScheduler


def profile() -> HardwareProfile:
    return HardwareProfile(
        num_servers=3,
        gpus_per_server=2,
        intra=LinkModel(alpha_ns=10, beta_ns_per_byte=1),
        inter=LinkModel(alpha_ns=100, beta_ns_per_byte=1),
        topology_id="fast-test",
        link_bandwidth_gbps=100,
        oversubscription=1,
    )


def demand() -> TrafficMatrix:
    values = np.arange(1, 37, dtype=np.int64).reshape(6, 6)
    np.fill_diagonal(values, 0)
    return TrafficMatrix(values)


class FastSchedulerTests(unittest.TestCase):
    def test_single_server_uses_deterministic_local_degenerate_schedule(self):
        matrix = TrafficMatrix(
            np.array(
                [
                    [0, 1, 2, 3],
                    [4, 0, 5, 6],
                    [7, 8, 0, 9],
                    [10, 11, 12, 0],
                ],
                dtype=np.int64,
            )
        )
        hardware = HardwareProfile(
            1,
            4,
            LinkModel(100, 0.01),
            LinkModel(700, 0.08),
            "single",
            100,
            1,
        )

        schedule = FastScheduler().schedule(matrix, hardware, seed=3)

        self.assertEqual(schedule.physical_bytes, matrix.logical_bytes)
        self.assertEqual(len(schedule.flows), 12)
        self.assertTrue(all(flow.phase == "intrinsic" for flow in schedule.flows))
        self.assertTrue(schedule.metadata["degenerate_single_server"])

    def test_pipeline2_emits_phases_and_native_timeline(self):
        schedule = FastScheduler().schedule(demand(), profile(), seed=5)

        self.assertEqual(schedule.predicted_makespan_ns, 486)
        self.assertEqual(schedule.metadata["stage_starts_ns"], [32, 165, 293])
        self.assertEqual(schedule.metadata["balance_end_ns"], 32)
        self.assertEqual(schedule.metadata["final_dispatch_start_ns"], 450)
        self.assertEqual(
            {flow.phase for flow in schedule.flows},
            {"balance", "intrinsic", "inter", "dispatch"},
        )
        for stage_id, start_ns in enumerate(schedule.metadata["stage_starts_ns"]):
            inter = [
                flow for flow in schedule.flows
                if flow.phase == "inter" and flow.stage_id == stage_id
            ]
            self.assertTrue(inter)
            self.assertEqual({flow.start_ns for flow in inter}, {start_ns})
            if stage_id == 0:
                intrinsic = [flow for flow in schedule.flows if flow.phase == "intrinsic"]
                self.assertEqual({flow.start_ns for flow in intrinsic}, {start_ns})
            else:
                dispatch = [
                    flow for flow in schedule.flows
                    if flow.phase == "dispatch" and flow.stage_id == stage_id - 1
                ]
                self.assertEqual({flow.start_ns for flow in dispatch}, {start_ns})

    def test_inter_server_bytes_match_original_server_pair_demand(self):
        matrix = demand()
        schedule = FastScheduler().schedule(matrix, profile(), seed=5)
        actual = np.zeros((3, 3), dtype=np.int64)
        for flow in schedule.flows:
            if flow.phase == "inter":
                actual[flow.src // 2, flow.dst // 2] += flow.size_bytes
        expected = np.array(
            [[0, 26, 34], [66, 0, 82], [114, 122, 0]],
            dtype=np.int64,
        )
        np.testing.assert_array_equal(actual, expected)

    def test_each_inter_flow_uses_the_recorded_per_gpu_transfer(self):
        schedule = FastScheduler().schedule(demand(), profile(), seed=5)

        inter = [flow for flow in schedule.flows if flow.phase == "inter"]

        self.assertTrue(inter)
        self.assertTrue(all(flow.src % 2 == flow.dst % 2 for flow in inter))
        self.assertGreater(schedule.physical_bytes, demand().logical_bytes)

    def test_schedule_is_deterministic_and_keeps_logical_physical_separate(self):
        first = FastScheduler().schedule(demand(), profile(), seed=5)
        second = FastScheduler().schedule(demand(), profile(), seed=5)

        self.assertEqual(first, second)
        self.assertEqual(first.metadata["logical_bytes"], demand().logical_bytes)
        self.assertEqual(first.metadata["physical_bytes"], first.physical_bytes)
        self.assertEqual([flow.flow_id for flow in first.flows], list(range(len(first.flows))))


if __name__ == "__main__":
    unittest.main()
