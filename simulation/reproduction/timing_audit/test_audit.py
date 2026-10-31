"""Breaks caught: wrong native callback, wrong time units, and matrix accounting."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent

class TimingAuditTests(unittest.TestCase):
    def test_four_real_native_callbacks_on_hand_checked_dense_input(self):
        self.assertTrue((ROOT/'audit.py').exists(),'native timing audit harness missing')
        spec=importlib.util.spec_from_file_location('timing_audit',ROOT/'audit.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            binary=module.build(Path(tmp))
            dense=[0 if i==j else 100 for i in range(8) for j in range(8)]
            expected={'spread_out':700.7,'full_mesh':100.1,'two_ring':1001.0,'hybrid_cube':500.3}
            for model,want in expected.items():
                value=module.local(binary,model,dense,.1,1,1)
                self.assertAlmostEqual(value,want,places=8, msg=model)
            zeros=[0]*64
            for model,want in {'spread_out':0,'full_mesh':.1,'two_ring':0,'hybrid_cube':.3}.items():
                self.assertAlmostEqual(module.local(binary,model,zeros,.1,1,1),want)
            # An empty matrix cannot be silently padded if the native 8-GPU API is selected.
            failed=subprocess.run([str(binary),'local','hybrid_cube','.1','1','1'],input='0 0',text=True,capture_output=True)
            self.assertNotEqual(failed.returncode,0)

    def test_endpoint_lower_bound_ignores_self_copy_and_counts_full_fanout(self):
        self.assertTrue((ROOT/'audit.py').exists(),'native timing audit harness missing')
        spec=importlib.util.spec_from_file_location('timing_audit',ROOT/'audit.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.endpoint_bound([100 if i==j else 0 for i in range(8) for j in range(8)],1,1),0)
        self.assertEqual(module.endpoint_bound([100 if i==0 and j else 0 for i in range(8) for j in range(8)],1,1),700)

if __name__=='__main__':unittest.main()
