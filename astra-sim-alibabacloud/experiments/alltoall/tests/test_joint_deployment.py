"""Current planner is replayed, never approximated by a legacy SimAI policy."""
import unittest
import json
from pathlib import Path
import subprocess
import tempfile
import numpy as np

from simai_a2a.model import HardwareProfile, LinkModel, TrafficMatrix
from simai_a2a.schedulers import get_scheduler
from simai_a2a.delivery import validate_fast_delivery


class JointDeploymentTests(unittest.TestCase):
    def test_prepare_entry_exports_all_six_methods_with_the_same_input(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'study'
            done=subprocess.run(['python3',str(root/'tools/deploy_joint_study.py'),
                '--out',str(out),'--models','none','--families','balanced',
                '--data-centers','2','--vector-bytes','4096','--prepare-only'],capture_output=True,text=True,timeout=60)
            self.assertEqual(done.returncode,0,done.stderr)
            manifest=json.loads((out/'study.json').read_text())
            self.assertTrue(manifest['prepared']);self.assertFalse(manifest['complete'])
            self.assertEqual(manifest['cases'][0]['traffic_metadata']['vector_bytes'],4096)
            self.assertEqual(manifest['cases'][0]['logical_bytes'],1835008)
            case=out/manifest['cases'][0]['name']
            self.assertEqual(len(list((case/'schedules').iterdir())),6)
            digests=set()
            for path in (case/'schedules').iterdir():
                sm=json.loads((path/'schedule_manifest.json').read_text())
                digests.add(sm['matrix_sha256'])
                self.assertEqual(sm['metadata']['execution_model'],'fast-nvidia-deps-modeled-scaleup')
            self.assertEqual(len(digests),1)
            from simai_a2a.scenario import schedule_artifact
            options=json.loads((case/'schedules/fast-joint-stage-b1/schedule_manifest.json').read_text())['metadata']
            with self.assertRaisesRegex(ValueError,'event|execution'):
                schedule_artifact(matrix_dir=case/'matrix',hardware_path=case/'hardware.json',
                    algorithm='fast-joint-stage-b1',seed=1,output=out/'bad-open-loop',
                    fast_options={'topology_snapshot':options['topology_snapshot']})

    def setUp(self):
        self.hw = HardwareProfile(4, 2, LinkModel(100, .0023), LinkModel(700, .02),
                                  'multidc', 400, 1)
        values = np.arange(64, dtype=np.int64).reshape(8, 8)*1024
        np.fill_diagonal(values, 0)
        self.matrix = TrafficMatrix(values)

    def test_native_explicit_replay_preserves_order_and_source_destination_payload(self):
        native = get_scheduler('fast-native-nvidia').schedule(self.matrix, self.hw, seed=1)
        records = list(reversed(native.metadata['stage_records']))
        replay = get_scheduler('fast-native-nvidia', fast_options={
            'replay_stages': records}).schedule(self.matrix, self.hw, seed=1)
        self.assertEqual([r['mp'] for r in replay.metadata['stage_records']],
                         [r['mp'] for r in records])
        self.assertEqual([r['freq'] for r in replay.metadata['stage_records']],
                         [r['freq'] for r in records])
        validate_fast_delivery(self.matrix, replay, 2)

    def test_incomplete_replay_is_rejected_not_silently_replaced_with_native(self):
        with self.assertRaisesRegex((ValueError, RuntimeError), 'replay|reconstruct'):
            get_scheduler('fast-native-nvidia', fast_options={'replay_stages': [
                {'freq': 1, 'mp': [1, 0, 3, 2]}]}).schedule(self.matrix, self.hw, seed=1)

    def test_joint_four_variants_deliver_all_gpu_payload_and_expose_frozen_plan(self):
        from simai_a2a.topology import build_multidc_topology
        from simai_a2a.schedulers.joint import topology_snapshot
        topology = build_multidc_topology(self.hw, data_centers=2, servers_per_data_center=2,
            intra_dc_bandwidth_gbps=400, intra_dc_latency_ns=700,
            inter_dc_bandwidth_gbps=100, inter_dc_latency_ns=50000)
        snapshot = topology_snapshot(topology, self.hw)
        # Same-server path is empty; crossing 2 DCs traverses 5 directed fabric links.
        self.assertEqual(snapshot['server_paths'][0]['resource_ids'], [])
        self.assertEqual(len(snapshot['server_paths'][2]['resource_ids']), 5)
        self.assertAlmostEqual(snapshot['server_paths'][2]['propagation_us'], 53.)
        for name in ('fast-joint-stage-b1', 'fast-joint-stage-b4',
                     'fast-joint-capacity-b1', 'fast-joint-capacity-b4'):
            with self.subTest(name=name):
                schedule = get_scheduler(name, fast_options={'topology_snapshot': snapshot}).schedule(
                    self.matrix, self.hw, seed=1)
                validate_fast_delivery(self.matrix, schedule, 2)
                self.assertEqual(schedule.metadata['planner_unit_bytes'], 1)
                self.assertEqual(len(schedule.metadata['planner_source_sha256']), 64)
                self.assertGreaterEqual(schedule.metadata['planner_cpu_us'], 0)
                self.assertEqual([r['mp'] for r in schedule.metadata['stage_records']],
                                 schedule.metadata['joint_plan']['permutations'])


if __name__ == '__main__':
    unittest.main()
