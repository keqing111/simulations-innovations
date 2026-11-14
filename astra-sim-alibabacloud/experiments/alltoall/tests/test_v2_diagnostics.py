import tempfile
from pathlib import Path
import unittest
import numpy as np
from simai_a2a.model import TrafficMatrix,Schedule,ScheduledFlow

class V2DiagnosticsTests(unittest.TestCase):
    def test_original_delivery_and_separate_frontiers(self):
        from simai_a2a.v2_diagnostics import collect
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'native.csv.events.csv').write_text('time_ns,event,flow_id,rank,stage\n0,begin,-1,-1,-1\n1,data_release,0,0,0\n6,data_ready,0,1,0\n8,ack_done,0,0,0\n9,redis_done,-1,1,0\n10,finished,-1,-1,-1\n')
            (root/'native_wan.csv').write_text('start_ns,node_id,port_id,duration_ns,wire_bytes,kind,flow_id,payload_bytes\n1,5,1,2,104,0,0,64\n3,5,1,2,60,2,-1,0\n')
            s=Schedule('test',[ScheduledFlow(0,0,1,64,0,'inter',0)],None,
                dict(native_rank_count=2,n_stages=1,delivery_chunks={'0':[[0,1,64]]},local_delivery_records=[],native_gpus_per_server=1))
            matrix=TrafficMatrix(np.array([[0,64],[0,0]],dtype=np.int64))
            result=collect(s,matrix,root)
            self.assertEqual(result['delivered_bytes'],64)
            self.assertEqual(result['original_source_completion_skew_ns'],0)
            self.assertEqual(result['source_q100_max_ns'],6)
            self.assertTrue((root/'progress_quantiles.csv').is_file())
            self.assertTrue((root/'rank_progress.csv').exists());self.assertTrue((root/'stage_skew.csv').exists())
            import csv
            with (root/'wan_bins_100us.csv').open() as stream:bins=list(csv.DictReader(stream))
            self.assertGreater(float(bins[0]['other_gbps']),0)
            s.metadata['delivery_chunks']['0'][0][2]=63
            with self.assertRaises(ValueError):collect(s,matrix,root)

