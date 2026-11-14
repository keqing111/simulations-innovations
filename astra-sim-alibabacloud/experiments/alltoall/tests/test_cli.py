import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from simai_a2a.artifacts import read_matrix_artifact, read_schedule_artifact


def run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "simai_a2a", *map(str, args)],
        cwd=cwd,
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def write_hardware(path: Path, *, omit=None, servers=2, gpus=2):
    data = {
        "schema_version": 1,
        "num_servers": servers,
        "gpus_per_server": gpus,
        "intra_alpha_ns": 100,
        "intra_beta_ns_per_byte": 0.0023,
        "inter_alpha_ns": 700,
        "inter_beta_ns_per_byte": 0.022,
        "topology_id": "h100_2s2g_100g",
        "link_bandwidth_gbps": 100,
        "oversubscription": 1,
    }
    if omit:
        data.pop(omit)
    path.write_text(json.dumps(data), encoding="utf-8")


class CliTests(unittest.TestCase):
    def test_prepare_propagates_pair_profiles_and_validates_fast_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_hardware(root / 'hardware.json')
            config = root / 'scenario.json'
            config.write_text(json.dumps(dict(
                schema_version=1, name='wan', hardware='hardware.json', seed=11,
                traffic=dict(generator='balanced', num_devices=4, pair_bytes=100000),
                schedulers=['fast', 'fast-wan', 'fast-weighted', 'fast-wan-weighted'],
                fast_options=dict(server_dc_ids=[0,1], intra_dc_rtt_ns=100,
                                  inter_dc_rtt_ns=1000, intra_dc_bandwidth_gbps=400,
                                  inter_dc_bandwidth_gbps=100))), encoding='utf-8')
            output = root / 'prepared'
            result = run_cli('prepare', '--config', config, '--output', output)
            self.assertEqual(result.returncode, 0, result.stderr)
            for algorithm in ['fast', 'fast-wan', 'fast-weighted', 'fast-wan-weighted']:
                _, manifest = read_schedule_artifact(output/'schedules'/algorithm)
                self.assertEqual(manifest['delivery_validation']['logical_bytes_delivered'], 1200000)

    def test_generate_and_schedule_write_readable_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            matrix_dir = root / "matrix"
            generated = run_cli(
                "generate",
                "--generator", "balanced",
                "--num-devices", "4",
                "--pair-bytes", "16",
                "--gpus-per-server", "2",
                "--output", matrix_dir,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            matrix, matrix_manifest = read_matrix_artifact(matrix_dir)
            self.assertEqual(matrix.logical_bytes, 192)
            self.assertIn("server_pair_gini", matrix_manifest)

            hardware = root / "hardware.json"
            write_hardware(hardware)
            schedule_dir = root / "schedule"
            scheduled = run_cli(
                "schedule",
                "--matrix-dir", matrix_dir,
                "--hardware", hardware,
                "--algorithm", "direct-concurrent",
                "--seed", "7",
                "--output", schedule_dir,
            )
            self.assertEqual(scheduled.returncode, 0, scheduled.stderr)
            schedule, schedule_manifest = read_schedule_artifact(schedule_dir)
            self.assertEqual(len(schedule.flows), 12)
            self.assertEqual(schedule_manifest["matrix_sha256"], matrix_manifest["matrix_sha256"])
            for field in ("scheduler_wall_time_ns", "python_version", "numpy_version"):
                self.assertIn(field, schedule_manifest)

    def test_prepare_reuses_one_matrix_for_all_schedulers_and_writes_micro(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hardware = root / "hardware.json"
            write_hardware(hardware)
            config = root / "scenario.json"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "balanced-smoke",
                        "hardware": "hardware.json",
                        "seed": 11,
                        "traffic": {
                            "generator": "balanced",
                            "num_devices": 4,
                            "pair_bytes": 64,
                        },
                        "schedulers": [
                            "direct-concurrent",
                            "spreadout",
                            "roundrobin",
                            "fast",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "prepared"

            completed = run_cli("prepare", "--config", config, "--output", output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            _, matrix_manifest = read_matrix_artifact(output / "matrix")
            for algorithm in ("direct-concurrent", "spreadout", "roundrobin", "fast"):
                _, manifest = read_schedule_artifact(output / "schedules" / algorithm)
                self.assertEqual(manifest["matrix_sha256"], matrix_manifest["matrix_sha256"])
            workload = (output / "workload.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                workload[0],
                "MICRO model_parallel_NPU_group: 1 ep: 4 all_gpus: 4",
            )
            self.assertEqual(workload[1], "1")
            self.assertIn("ALLTOALL", workload[2])

    def test_unknown_config_keys_and_missing_hardware_fields_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hardware = root / "hardware.json"
            write_hardware(hardware, omit="inter_beta_ns_per_byte")
            config = root / "scenario.json"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "bad",
                        "hardware": "hardware.json",
                        "seed": 1,
                        "traffic": {
                            "generator": "balanced",
                            "num_devices": 4,
                            "pair_bytes": 1,
                        },
                        "schedulers": ["direct-concurrent"],
                        "unexpected": True,
                    }
                ),
                encoding="utf-8",
            )

            completed = run_cli("prepare", "--config", config, "--output", root / "out")

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unexpected", completed.stderr)

            data = json.loads(config.read_text(encoding="utf-8"))
            data.pop("unexpected")
            config.write_text(json.dumps(data), encoding="utf-8")
            completed = run_cli("prepare", "--config", config, "--output", root / "out2")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("inter_beta_ns_per_byte", completed.stderr)

    def test_prepare_writes_configured_multidc_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hardware = root / "hardware.json"
            write_hardware(hardware, servers=6, gpus=4)
            config = root / "scenario.json"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "multidc-balanced-smoke",
                        "hardware": "hardware.json",
                        "seed": 11,
                        "traffic": {
                            "generator": "balanced",
                            "num_devices": 24,
                            "pair_bytes": 4096,
                        },
                        "topology": {
                            "kind": "multi-dc-full-mesh",
                            "data_centers": 3,
                            "servers_per_data_center": 2,
                            "intra_dc_bandwidth_gbps": 400,
                            "intra_dc_latency_ns": 700,
                            "inter_dc_bandwidth_gbps": 100,
                            "inter_dc_latency_ns": 500000000,
                        },
                        "schedulers": ["direct-concurrent"],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "prepared"

            completed = run_cli("prepare", "--config", config, "--output", output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(
                (output / "scenario_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["topology"]["kind"], "multi-dc-full-mesh")
            self.assertEqual(manifest["topology"]["tor_ids"], [30, 31, 32])
            self.assertEqual(manifest["topology"]["gateway_ids"], [33, 34, 35])
            header = (output / "topology.txt").read_text().splitlines()[0].split()
            self.assertEqual(header[:5], ["36", "4", "6", "6", "36"])

    def test_nonempty_output_requires_new_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "matrix"
            args = (
                "generate",
                "--generator", "balanced",
                "--num-devices", "2",
                "--pair-bytes", "1",
                "--gpus-per-server", "1",
                "--output", output,
            )
            first = run_cli(*args)
            second = run_cli(*args)
            third = run_cli(*args, "--new-attempt")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertTrue((output / "attempt-0001" / "matrix.npy").is_file())


if __name__ == "__main__":
    unittest.main()
