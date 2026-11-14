import json
from pathlib import Path
import tempfile
import unittest


class TraceWorkloadTests(unittest.TestCase):
    def test_real_routes_are_kept_with_explicit_rebatch_and_expert_placement(self):
        from simai_a2a.trace_workload import trace_matrix
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sample.json'
            path.write_text(json.dumps([{'3':[[0,1],[2,3],[1,2],[0,3]]}]))
            matrix=trace_matrix(path,layer=3,num_devices=2,expert_count=4,
                                token_limit=4,vector_bytes=100)
            self.assertEqual(matrix.values.tolist(),[[0,100],[100,0]])
            self.assertEqual(matrix.metadata['self_route_bytes'],600)
            self.assertEqual(matrix.metadata['routed_assignments'],8)
            self.assertEqual(matrix.metadata['tokens'],4)
            self.assertEqual(matrix.metadata['measurement_scope'],'expert choices only')

    def test_no_silent_repair_of_invalid_experts_or_missing_layer(self):
        from simai_a2a.trace_workload import trace_matrix
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sample.json';path.write_text(json.dumps([{'3':[[0,4]]}]))
            with self.assertRaisesRegex(ValueError,'expert'):
                trace_matrix(path,layer=3,num_devices=2,expert_count=4,token_limit=1,vector_bytes=100)
            with self.assertRaisesRegex(ValueError,'layer'):
                trace_matrix(path,layer=4,num_devices=2,expert_count=4,token_limit=1,vector_bytes=100)

