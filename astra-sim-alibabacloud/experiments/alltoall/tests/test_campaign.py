import json
import tempfile
import unittest
from pathlib import Path

from simai_a2a.campaign import aggregate_campaign, expand_campaign
from simai_a2a.cli import build_parser


class CampaignTests(unittest.TestCase):
    def test_campaign_cli_accepts_parallel_jobs(self):
        args = build_parser().parse_args(
            [
                "campaign-run",
                "--config", "campaign.json",
                "--output", "results",
                "--binary", "sim",
                "--jobs", "4",
            ]
        )

        self.assertEqual(args.jobs, 4)

    def test_multidc_topology_and_ns3_config_are_carried_into_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hardware = root / "hardware.json"
            hardware.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "num_servers": 6,
                        "gpus_per_server": 4,
                        "intra_alpha_ns": 100,
                        "intra_beta_ns_per_byte": 0.0023,
                        "inter_alpha_ns": 700,
                        "inter_beta_ns_per_byte": 0.02,
                        "topology_id": "multidc",
                        "link_bandwidth_gbps": 100,
                        "oversubscription": 1,
                    }
                ),
                encoding="utf-8",
            )
            ns3_config = root / "ns3.conf"
            ns3_config.write_text("config\n", encoding="utf-8")
            topology = {
                "kind": "multi-dc-full-mesh",
                "data_centers": 3,
                "servers_per_data_center": 2,
                "intra_dc_bandwidth_gbps": 400,
                "intra_dc_latency_ns": 700,
                "inter_dc_bandwidth_gbps": 100,
                "inter_dc_latency_ns": 500000000,
            }
            campaign = root / "campaign.json"
            campaign.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "multidc",
                        "profiles": [{"id": "M0", "hardware": "hardware.json"}],
                        "workloads": [
                            {
                                "id": "balanced-low",
                                "generator": "balanced",
                                "total_bytes_per_device": 16777216,
                                "seeds": [11],
                            }
                        ],
                        "schedulers": ["direct-concurrent"],
                        "topology": topology,
                        "ns3_config": "ns3.conf",
                        "ns3_threads": 0,
                        "fast_options": {"server_dc_ids": [0,0,1,1,2,2]},
                    }
                ),
                encoding="utf-8",
            )

            cases = expand_campaign(campaign)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["topology"], topology)
            self.assertEqual(cases[0]["ns3_config"], str(ns3_config.resolve()))
            self.assertEqual(cases[0]["ns3_threads"], 0)
            self.assertEqual(cases[0]["traffic"]["total_bytes"], 402653184)
            self.assertEqual(cases[0]['fast_options']['server_dc_ids'], [0,0,1,1,2,2])

    def test_aggregate_includes_multidc_congestion_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = root / "scenario"
            attempt = root / "attempt"
            (scenario / "matrix").mkdir(parents=True)
            (scenario / "schedules" / "direct-concurrent").mkdir(parents=True)
            attempt.mkdir()

            def write(path: Path, value):
                path.write_text(json.dumps(value), encoding="utf-8")

            write(
                root / "campaign_manifest.json",
                {
                    "cases": {
                        "M0__balanced-low__s11": {
                            "scenario": str(scenario),
                            "algorithms": {
                                "direct-concurrent": {
                                    "success": True,
                                    "attempt": str(attempt),
                                }
                            },
                        }
                    }
                },
            )
            write(
                scenario / "scenario_manifest.json",
                {"seed": 11},
            )
            write(
                scenario / "matrix" / "matrix_manifest.json",
                {
                    "num_devices": 24,
                    "metadata": {"generator": "balanced"},
                },
            )
            write(
                scenario / "hardware.json",
                {"num_servers": 6, "gpus_per_server": 4, "oversubscription": 1},
            )
            write(
                scenario
                / "schedules"
                / "direct-concurrent"
                / "schedule_manifest.json",
                {"scheduler_wall_time_ns": 10},
            )
            write(
                attempt / "summary.json",
                {
                    "logical_bytes": 100,
                    "physical_bytes": 100,
                    "actual_makespan_ns": 20,
                    "pfc": {
                        "pause_count": 3,
                        "resume_count": 2,
                        "affected_port_count": 1,
                    },
                    "congestion_control": {
                        "cnp": {"total_count": 7, "affected_flow_count": 2},
                        "qp_rate": {"min_bps": 50, "max_bps": 100},
                    },
                    "network": {
                        "queue": {
                            "gateway_peak_queue_bytes": 900,
                            "gateway_peak_port_bytes": 1000,
                        },
                        "bandwidth": {"gateway_peak_utilization": 0.9},
                    },
                    "stage_violations": {
                        "violation_fraction": 0.25,
                        "max_violation_ns": 30,
                    },
                },
            )

            rows = aggregate_campaign(root)

            self.assertEqual(rows[0]["pfc_pause_count"], 3)
            self.assertEqual(rows[0]["cnp_total_count"], 7)
            self.assertEqual(rows[0]["gateway_peak_queue_bytes"], 900)
            self.assertEqual(rows[0]["stage_violation_fraction"], 0.25)


if __name__ == "__main__":
    unittest.main()
