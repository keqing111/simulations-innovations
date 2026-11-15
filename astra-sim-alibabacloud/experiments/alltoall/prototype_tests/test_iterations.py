"""Experiment ledger must retain unsupported methods and failed validation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def driver():
    directory=Path(__file__).resolve().parents[1]/'tools'
    if str(directory) not in sys.path:sys.path.insert(0,str(directory))
    spec=importlib.util.spec_from_file_location('iteration_driver_test',directory/'run_resource_budget_iterations.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class IterationLedgerTests(unittest.TestCase):
    def test_reused_scenario_preserves_foreground_epoch_and_background_contract(self):
        import json
        import numpy as np
        from simai_a2a.model import HardwareProfile,LinkModel,TrafficMatrix
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();binary=root/'not-executed';binary.write_bytes(b'unit fixture')
            hw=HardwareProfile(16,4,LinkModel(100,.0023),LinkModel(700,.02),'v2-leafspine',400,1)
            top=module.build_leafspine(hw,2);meta=module.write_fabric(top,module.fixed_routes(top),source)
            meta.update(foreground_start_ns=2176000,background_horizon_ns=9999999)
            module.dump(source/'scenario_manifest.json',dict(schema_version=1,topology=meta))
            hardware=dict(schema_version=1,num_servers=16,gpus_per_server=4,intra_alpha_ns=100,intra_beta_ns_per_byte=.0023,
                inter_alpha_ns=700,inter_beta_ns_per_byte=.02,topology_id='v2-leafspine',link_bandwidth_gbps=400,oversubscription=1)
            module.dump(source/'hardware.json',hardware)
            values=np.zeros((64,64),dtype=np.int64);values[0,32]=100;values[32,0]=100
            module.write_matrix_artifact(TrafficMatrix(values),source/'matrix');(source/'background.csv').write_text('')
            module.run(root/'out',binary,['copied'],['resource-maxq'],prepare_only=True,source_scenario=source)
            copied=json.loads((root/'out/copied/scenario_manifest.json').read_text())['topology']
            self.assertEqual(copied['foreground_start_ns'],2176000)
            self.assertEqual(copied['background_horizon_ns'],9999999)

    def test_volume_only_baseline_reconstructs_without_topology_selection(self):
        module=driver()
        from test_resource_budget import topology
        R=[[6,8,0,0],[8,0,6,0],[0,6,0,8],[0,0,8,6]]
        U=[0,8,0,0,8,0,0,0,0,6,0,2,0,0,8,0]
        result=module.volume_plan(dict(n=4,g=1,R=R,U=U),topology(4))
        self.assertEqual(result['coefficients'],[8,6])
        reconstructed=[[0]*4 for _ in range(4)]
        for p,q in zip(result['permutations'],result['coefficients']):
            for s,d in enumerate(p):reconstructed[s][d]+=q
        self.assertEqual(reconstructed,R)
        self.assertFalse(any(result['remaining_U']))

    def test_unsupported_legacy_path_is_recorded_and_other_method_is_prepared(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'not-executed';binary.write_bytes(b'unit fixture')
            result=module.run(root/'out',binary,['chain'],['legacy-v1','resource-full'],prepare_only=True)
            self.assertEqual([r['status'] for r in result['runs']],['unsupported','prepared'])
            self.assertTrue((root/'out/chain/schedules/resource-full/schedule.csv').exists())

    def test_delivery_validation_failure_is_not_a_success_or_an_aborted_study(self):
        module=driver()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'not-executed';binary.write_bytes(b'unit fixture')
            outcome=dict(status='success',success=True,summary={'actual_makespan_ns':100},attempt_dir=str(root/'attempt'))
            # External backend/trace parser boundary only: scheduling, artifact
            # generation and the result ledger themselves are real.
            with patch.object(module,'run_scenario',return_value=outcome),patch.object(module,'collect',side_effect=ValueError('delivery mismatch')):
                result=module.run(root/'out',binary,['independent-wan'],['resource-full','resource-split'])
            self.assertTrue(result['complete'])
            self.assertEqual(len(result['runs']),2)
            for row in result['runs']:
                self.assertEqual(row['status'],'validation_failed')
                self.assertFalse(row['success'])
                self.assertIsNone(row['actual_ms'])
                self.assertIn('delivery mismatch',row['error'])
