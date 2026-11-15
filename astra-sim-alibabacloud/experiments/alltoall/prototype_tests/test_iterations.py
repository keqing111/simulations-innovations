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
