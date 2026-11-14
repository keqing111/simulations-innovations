import tempfile
import unittest
from pathlib import Path

import numpy as np

from simai_a2a.analysis import (
    analyze_fct,
    analyze_congestion_monitors,
    analyze_network_monitors,
    analyze_pfc_events,
    fast_stage_violations,
)
from simai_a2a.artifacts import write_matrix_artifact, write_schedule_artifact
from simai_a2a.model import Schedule, ScheduledFlow, TrafficMatrix


def _ip(node: int) -> str:
    return f"{0x0B000001 + ((node // 256) << 16) + ((node % 256) << 8):08x}"


def _artifacts(root: Path, flows: list[ScheduledFlow]) -> tuple[Path, Path]:
    matrix = TrafficMatrix(np.array([[0, 100], [200, 0]], dtype=np.int64))
    matrix_dir = root / "matrix"
    schedule_dir = root / "schedule"
    matrix_manifest = write_matrix_artifact(matrix, matrix_dir)
    schedule = Schedule("fast", flows, 160, {"logical_bytes": 300})
    write_schedule_artifact(
        schedule,
        schedule_dir,
        matrix_sha256=matrix_manifest["matrix_sha256"],
        hardware_sha256="hardware",
    )
    return matrix_dir, schedule_dir


class AnalysisTests(unittest.TestCase):
    def test_network_monitor_statistics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qlen = root / "qlen.txt"
            qlen.write_text(
                "time, sw_id, port_id, q_id, q_len, port_len\n"
                "1000, 8, 1, 3, 200, 300\n"
                "2000, 9, 2, 3, 500, 700\n",
                encoding="utf-8",
            )
            bw = root / "bw.txt"
            bw.write_text(
                "time, node_id, port_id, bandwidth\n"
                "1000, 8, 1, 100\n"
                "1000, 9, 2, 200\n",
                encoding="utf-8",
            )
            result = analyze_network_monitors(
                qlen, bw, tor_id=9, tor_port_capacity_gbps=400
            )
            self.assertEqual(result["queue"]["peak_queue_bytes"], 500)
            self.assertEqual(result["queue"]["tor_peak_port_bytes"], 700)
            self.assertEqual(result["bandwidth"]["tor_peak_gbps"], 200)
            self.assertEqual(result["bandwidth"]["tor_peak_utilization"], 0.5)

    def test_network_monitor_aggregates_all_tors_and_gateways(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qlen = root / "qlen.txt"
            qlen.write_text(
                "time, sw_id, port_id, q_id, q_len, port_len\n"
                "1000, 8, 1, 3, 200, 300\n"
                "1000, 9, 1, 3, 500, 700\n"
                "1000, 10, 2, 3, 600, 800\n",
                encoding="utf-8",
            )
            bw = root / "bw.txt"
            bw.write_text(
                "time, node_id, port_id, bandwidth\n"
                "1000, 8, 1, 100\n"
                "1000, 9, 1, 200\n"
                "1000, 10, 2, 90\n",
                encoding="utf-8",
            )

            result = analyze_network_monitors(
                qlen,
                bw,
                tor_id=None,
                tor_ids=(8, 9),
                gateway_ids=(10,),
                gateway_wan_ports=((10, 2),),
                tor_port_capacity_gbps=400,
                gateway_port_capacity_gbps=100,
            )

            self.assertEqual(result["queue"]["tor_peak_queue_bytes"], 500)
            self.assertEqual(result["queue"]["gateway_peak_queue_bytes"], 600)
            self.assertEqual(result["bandwidth"]["tor_peak_gbps"], 200)
            self.assertEqual(result["bandwidth"]["gateway_peak_gbps"], 90)
            self.assertEqual(result["bandwidth"]["gateway_peak_utilization"], 0.9)

    def test_gateway_statistics_exclude_non_wan_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qlen = root / "qlen.txt"
            qlen.write_text(
                "time, sw_id, port_id, q_id, q_len, port_len\n"
                "1000, 33, 1, 3, 900, 1000\n"
                "1000, 33, 2, 3, 80, 100\n"
                "1000, 33, 3, 3, 70, 90\n",
                encoding="utf-8",
            )
            bw = root / "bw.txt"
            bw.write_text(
                "time, node_id, port_id, bandwidth\n"
                "1000, 33, 1, 200\n"
                "1000, 33, 2, 90\n"
                "1000, 33, 3, 80\n",
                encoding="utf-8",
            )

            result = analyze_network_monitors(
                qlen,
                bw,
                gateway_ids=(33,),
                gateway_wan_ports=((33, 2), (33, 3)),
                gateway_port_capacity_gbps=100,
            )

            self.assertEqual(result["queue"]["gateway_peak_queue_bytes"], 80)
            self.assertEqual(result["queue"]["gateway_peak_port_bytes"], 100)
            self.assertEqual(result["bandwidth"]["gateway_peak_gbps"], 90)
            self.assertEqual(result["bandwidth"]["gateway_peak_utilization"], 0.9)

    def test_pfc_events_split_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pfc.txt"
            path.write_text(
                "1000 30 1 2 1\n"
                "1200 30 1 2 0\n"
                "1500 33 1 4 1\n",
                encoding="utf-8",
            )

            result = analyze_pfc_events(path)

            self.assertEqual(result["event_count"], 3)
            self.assertEqual(result["pause_count"], 2)
            self.assertEqual(result["resume_count"], 1)
            self.assertEqual(result["affected_port_count"], 2)
            self.assertEqual(result["first_event_ns"], 1000)
            self.assertEqual(result["last_event_ns"], 1500)

    def test_congestion_monitors_summarize_cnp_and_qp_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rate = root / "rate.txt"
            rate.write_text(
                "time, src, dst, sport, dport, size, curr_rate\n"
                "1000, 0, 8, 10, 20, 1000, 100000000000\n"
                "2000, 0, 8, 10, 20, 900, 50000000000\n"
                "2500, 1, 9, 11, 21, 800, 75000000000\n",
                encoding="utf-8",
            )
            cnp = root / "cnp.txt"
            cnp.write_text(
                "time, src, dst, sport, dport, size, cnp_number\n"
                "1500, 0, 8, 10, 20, 950, 1\n"
                "2200, 0, 8, 10, 20, 900, 3\n"
                "2300, 1, 9, 11, 21, 800, 2\n",
                encoding="utf-8",
            )

            result = analyze_congestion_monitors(rate, cnp)

            self.assertEqual(result["cnp"]["total_count"], 5)
            self.assertEqual(result["cnp"]["affected_flow_count"], 2)
            self.assertEqual(result["qp_rate"]["change_record_count"], 3)
            self.assertEqual(result["qp_rate"]["min_bps"], 50000000000)
            self.assertEqual(result["qp_rate"]["max_bps"], 100000000000)

    def test_exact_join_and_collective_statistics(self):
        flows = [
            ScheduledFlow(0, 0, 1, 100, 0, "balance", 0),
            ScheduledFlow(1, 1, 0, 200, 100, "inter", 1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix_dir, schedule_dir = _artifacts(root, flows)
            fct = root / "fct.txt"
            fct.write_text(
                f"{_ip(0)} {_ip(1)} 1 100 100 1000 40 42 0\n"
                f"{_ip(1)} {_ip(0)} 2 100 200 1100 60 62 1\n",
                encoding="utf-8",
            )
            pfc = root / "pfc.txt"
            pfc.write_text(
                "1000 8 1 1 1\n1100 8 1 1 0\n",
                encoding="utf-8",
            )

            summary = analyze_fct(matrix_dir, schedule_dir, fct, pfc)

            self.assertEqual(summary["collective_epoch_ns"], 1000)
            self.assertEqual(summary["actual_makespan_ns"], 160)
            self.assertEqual(summary["logical_bytes"], 300)
            self.assertEqual(summary["physical_bytes"], 300)
            self.assertEqual(summary["scheduled_bytes"], 300)
            self.assertEqual(summary["schedule_byte_amplification"], 1.0)
            self.assertEqual(summary["algorithm_bandwidth_gbps"], 15.0)
            self.assertEqual(summary["per_device_algorithm_bandwidth_gbps"], 7.5)
            self.assertEqual(summary["fct_ns"]["mean"], 50.0)
            self.assertEqual(summary["fct_ns"]["p50"], 50.0)
            self.assertEqual(summary["fct_ns"]["p95"], 59.0)
            self.assertAlmostEqual(summary["fct_ns"]["p99"], 59.8)
            self.assertEqual(summary["fct_ns"]["max"], 60)
            self.assertEqual(summary["pfc_count"], 2)
            self.assertEqual(summary["pfc"]["pause_count"], 1)
            self.assertEqual(summary["pfc"]["resume_count"], 1)
            self.assertEqual(summary["predicted_makespan_ns"], 160)
            self.assertEqual(summary["fidelity_ratio"], 1.0)
            self.assertEqual(summary["stage_violations"]["violation_count"], 0)

    def test_missing_duplicate_unknown_and_mismatched_ids_are_rejected(self):
        flows = [
            ScheduledFlow(0, 0, 1, 100, 0, "balance", 0),
            ScheduledFlow(1, 1, 0, 200, 100, "inter", 1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix_dir, schedule_dir = _artifacts(root, flows)
            pfc = root / "pfc.txt"
            pfc.write_text("", encoding="utf-8")
            cases = {
                "missing": f"{_ip(0)} {_ip(1)} 1 100 100 1000 40 42 0\n",
                "duplicate": (
                    f"{_ip(0)} {_ip(1)} 1 100 100 1000 40 42 0\n"
                    f"{_ip(0)} {_ip(1)} 2 100 100 1001 40 42 0\n"
                ),
                "unknown": (
                    f"{_ip(0)} {_ip(1)} 1 100 100 1000 40 42 0\n"
                    f"{_ip(1)} {_ip(0)} 2 100 200 1100 60 62 9\n"
                ),
                "mismatch": (
                    f"{_ip(0)} {_ip(1)} 1 100 101 1000 40 42 0\n"
                    f"{_ip(1)} {_ip(0)} 2 100 200 1100 60 62 1\n"
                ),
            }
            for name, content in cases.items():
                with self.subTest(name=name):
                    fct = root / f"{name}.txt"
                    fct.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        analyze_fct(matrix_dir, schedule_dir, fct, pfc)

    def test_fast_stage_violation_boundaries(self):
        schedule = Schedule(
            "fast",
            [
                ScheduledFlow(0, 0, 1, 10, 0, "balance", 0),
                ScheduledFlow(1, 1, 0, 10, 100, "inter", 0),
                ScheduledFlow(2, 0, 1, 10, 200, "dispatch", 0),
            ],
            250,
        )
        observations = {
            0: {"actual_start_ns": 1000, "fct_ns": 120},
            1: {"actual_start_ns": 1100, "fct_ns": 80},
            2: {"actual_start_ns": 1200, "fct_ns": 30},
        }

        result = fast_stage_violations(schedule, observations, collective_epoch_ns=1000)

        self.assertEqual(result["boundary_count"], 2)
        self.assertEqual(result["violation_count"], 1)
        self.assertEqual(result["violation_fraction"], 0.5)
        self.assertEqual(result["max_violation_ns"], 20)
        self.assertEqual(result["sum_violation_ns"], 20)
        self.assertEqual([item["violation_ns"] for item in result["boundaries"]], [20, 0])


if __name__ == "__main__":
    unittest.main()
