"""Literal batch limits and membership catch lost/duplicated payload, not text."""
import copy
from dataclasses import replace
import unittest
import os
from pathlib import Path
import subprocess
import tempfile
from simai_a2a.model import HardwareProfile,LinkModel,Schedule,ScheduledFlow


class BatchingTests(unittest.TestCase):
    def test_real_backend_tracks_simultaneous_same_endpoint_qps(self):
        from tests.test_ns3_adapter import _write_ns3_fixture,DEFAULT_BINARY
        from simai_a2a.analysis import read_external_fct
        binary=Path(os.environ.get('SIMAI_NS3_BINARY',DEFAULT_BINARY))
        if not binary.is_file():self.skipTest('ns3 backend absent')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);top,work,schedule,config=_write_ns3_fixture(root)
            schedule.write_text('0,1,8000000,0\n0,1,4000000,0\n1,0,4096,0\n')
            sidecar=root/'batches.csv';sidecar.write_text('0,0\n1,0\n2,0\n')
            done=subprocess.run([str(binary),'-t','0','-n',str(top),'-w',str(work),'-c',str(config),
                '-s',str(schedule)],cwd=root,env={**os.environ,'SIMAI_EXTERNAL_STAGE_FILE':str(sidecar)},
                capture_output=True,text=True,timeout=20)
            self.assertEqual(done.returncode,0,done.stderr)
            observed=read_external_fct(root/'fct.txt')
            self.assertEqual(set(observed),{0,1,2})
            self.assertEqual(len({o['actual_start_ns'] for o in observed.values()}),1)
            self.assertEqual(sum(o['size_bytes'] for o in observed.values()),12004096)

    def hardware(self):return HardwareProfile(4,1,LinkModel(0,0),LinkModel(0,.02),'test',100,1)
    def plan(self):
        return Schedule('fast-test',[ScheduledFlow(i,*row) for i,row in enumerate([
            (0,1,10,0,'inter',0),(2,3,20,0,'inter',0),
            (0,1,5,1,'inter',1),(2,3,7,1,'inter',1),
            (0,2,8,2,'inter',3),(2,1,9,2,'inter',3)])],None)

    def test_one_stage_keeps_payload_order_and_compacts_virtual_gap(self):
        from simai_a2a.batching import batch_schedule,audit_batch
        p=batch_schedule(self.plan(),self.hardware(),'w1',max_stages=1,coalesce=False)
        self.assertEqual([(f.src,f.dst,f.size_bytes,f.stage_id) for f in p.flows],
                         [(0,1,10,0),(2,3,20,0),(0,1,5,1),(2,3,7,1),(0,2,8,2),(2,1,9,2)])
        self.assertTrue(all(f.start_ns==0 for f in p.flows))
        self.assertEqual(audit_batch(self.plan(),p,self.hardware())['source_flows_covered'],6)

    def test_coalescing_same_endpoint_preserves_members_and_real_bytes(self):
        from simai_a2a.batching import batch_schedule,audit_batch
        p=batch_schedule(self.plan(),self.hardware(),'w2',max_stages=2)
        self.assertEqual([(f.src,f.dst,f.size_bytes,f.stage_id) for f in p.flows],
                         [(0,1,15,0),(2,3,27,0),(0,2,8,1),(2,1,9,1)])
        self.assertEqual(p.metadata['batching']['members'],[[0,2],[1,3],[4],[5]])
        proof=audit_batch(self.plan(),p,self.hardware())
        self.assertEqual(proof['peak_receive_bytes_per_rank'],27)
        self.assertEqual(proof['peak_receive_peers_per_rank'],1)

    def test_peer_bound_is_separate_for_sending_and_receiving(self):
        from simai_a2a.batching import batch_schedule,audit_batch
        p=batch_schedule(self.plan(),self.hardware(),'p1',max_peers=1)
        self.assertEqual(p.metadata['batching']['original_stage_groups'],[[0,1],[3]])
        self.assertEqual(audit_batch(self.plan(),p,self.hardware())['peak_receive_peers_per_rank'],1)

    def test_receive_limit_counts_repeated_peer_bytes_before_merge(self):
        from simai_a2a.batching import batch_schedule
        p=batch_schedule(self.plan(),self.hardware(),'m20',max_receive_bytes=20)
        self.assertEqual(p.metadata['batching']['original_stage_groups'],[[0],[1,3]])
        with self.assertRaisesRegex(ValueError,'single.*stage'):
            batch_schedule(self.plan(),self.hardware(),'m19',max_receive_bytes=19)

    def test_limits_reject_invalid_values_and_local_only_adds_no_flow(self):
        from simai_a2a.batching import batch_schedule,audit_batch
        for key in ['max_stages','max_peers','max_receive_bytes']:
            for value in [0,-1,True,1.5]:
                with self.assertRaises(ValueError):batch_schedule(self.plan(),self.hardware(),'bad',**{key:value})
        empty=Schedule('fast-test',[],None)
        p=batch_schedule(empty,self.hardware(),'empty',max_stages=1)
        self.assertEqual(p.flows,[]);self.assertEqual(audit_batch(empty,p,self.hardware())['batch_count'],0)

    def test_audit_rejects_duplicate_members_and_forged_resource_claims(self):
        from simai_a2a.batching import batch_schedule,audit_batch
        p=batch_schedule(self.plan(),self.hardware(),'all')
        meta=copy.deepcopy(p.metadata);meta['batching']['members'][0]=[0,0]
        with self.assertRaises(ValueError):audit_batch(self.plan(),replace(p,metadata=meta),self.hardware())
        meta=copy.deepcopy(p.metadata);meta['batching']['max_peers']=1
        with self.assertRaisesRegex(ValueError,'limit'):audit_batch(self.plan(),replace(p,metadata=meta),self.hardware())

    def test_hard_window_counts_original_ids_not_nonempty_stage_count(self):
        from simai_a2a.batching import batch_schedule
        p=batch_schedule(self.plan(),self.hardware(),'w3',max_stages=3)
        self.assertEqual(p.metadata['batching']['original_stage_groups'],[[0,1],[3]])
