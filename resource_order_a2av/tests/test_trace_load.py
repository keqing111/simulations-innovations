import json
from pathlib import Path
import tempfile
import unittest
from resource_order.trace_load import load_dispatch


class TraceTests(unittest.TestCase):
    def test_mapping_conserves_choices_and_preserves_zero_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'trace.json'
            path.write_text(json.dumps([{'0':[[0,1],[2,3],[1,2],[0,3]]}]))
            flows,meta=load_dispatch(path,layer=0,ranks=4,tokens_per_rank=1,experts=4,bytes_per_token=10)
            self.assertEqual(meta['network_bytes']+meta['same_gpu_bytes'],80)
            self.assertEqual([(f.src,f.dst,f.bytes) for f in flows],[(0,1,10),(1,2,10),(1,3,10),(2,1,10),(3,0,10)])
            with self.assertRaises(ValueError):load_dispatch(path,layer=0,ranks=4,tokens_per_rank=2,experts=4)

    def test_directory_uses_only_first_record_in_numeric_order(self):
        with tempfile.TemporaryDirectory() as temp:
            for name,first in [('10.json',[[1],[0]]),('2.json',[[0],[1]])]:
                (Path(temp)/name).write_text(json.dumps([{'0':first},{'0':[[3],[3],[3]]}]))
            flows,meta=load_dispatch(temp,layer=0,ranks=4,tokens_per_rank=1,experts=4,bytes_per_token=10)
            self.assertEqual(meta['routing_choices'],4)
            self.assertTrue(meta['source_files'][0]['path'].endswith('2.json'))
            self.assertEqual([(f.src,f.dst) for f in flows],[(2,1),(3,0)])
