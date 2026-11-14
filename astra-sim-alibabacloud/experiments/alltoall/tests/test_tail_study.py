"""Trace-enabled real CLI smoke and rejection of forged derived diagnostics."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class TailStudyTests(unittest.TestCase):
    def test_another_valid_full_decomposition_cannot_impersonate_registered_partition(self):
        from dataclasses import replace
        import hashlib
        from simai_a2a.artifacts import read_matrix_artifact,read_schedule_artifact,write_schedule_artifact
        from simai_a2a.scenario import load_hardware
        from simai_a2a.schedulers import get_scheduler
        from simai_a2a.batching import batch_schedule
        from simai_a2a.paper_model import PaperTopology
        with tempfile.TemporaryDirectory() as tmp:
            cmd=[sys.executable,str(ROOT/'scripts/fast_tail_study.py'),'--phase','prepare','--output',tmp,'--smoke']
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            out=Path(tmp);d=out/'inputs/s401';matrix,mm=read_matrix_artifact(d/'matrix');hw,_,hwsha=load_hardware(d/'hardware.json')
            top=PaperTopology((0,0,1,1,2,2),400,100,1600,5003000)
            native=get_scheduler('fast-cpp-native',fast_options=top.to_dict()).schedule(matrix,hw,seed=401)
            full=replace(native,algorithm='fast-cpp-partition')
            write_schedule_artifact(full,d/'full',matrix_sha256=mm['matrix_sha256'],hardware_sha256=hwsha)
            plan=batch_schedule(full,hw,'partition-bounded',max_peers=4,max_receive_bytes=128*1024**2)
            plan.metadata.update(tail_source_fingerprint=json.loads((out/'study.json').read_text())['source_fingerprint'],
                tail_full_manifest_sha256=hashlib.sha256((d/'full/schedule_manifest.json').read_bytes()).hexdigest())
            write_schedule_artifact(plan,d/'schedules/partition-bounded',matrix_sha256=mm['matrix_sha256'],hardware_sha256=hwsha)
            direct,_=read_schedule_artifact(d/'schedules/direct-concurrent')
            direct.metadata['tail_full_manifest_sha256']=hashlib.sha256((d/'full/schedule_manifest.json').read_bytes()).hexdigest()
            write_schedule_artifact(direct,d/'schedules/direct-concurrent',matrix_sha256=mm['matrix_sha256'],hardware_sha256=hwsha)
            cmd[cmd.index('prepare')]='audit';p=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(p.returncode,0);self.assertIn('registered full',p.stdout+p.stderr)

    def test_real_smoke_then_recompute_rejects_forged_packet_evidence(self):
        binary=Path(os.environ.get('SIMAI_NS3_BINARY',ROOT.parents[2]/'bin/SimAI_simulator'))
        if not binary.is_file():self.skipTest('ns3 backend absent')
        with tempfile.TemporaryDirectory() as tmp:
            cmd=[sys.executable,str(ROOT/'scripts/fast_tail_study.py'),'--phase','prepare',
                 '--output',tmp,'--smoke','--binary',str(binary)]
            for phase in ['prepare','run','audit']:
                cmd[cmd.index('--phase')+1]=phase
                p=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            out=Path(tmp);rows=json.loads((out/'comparison.json').read_text());self.assertEqual(len(rows),2)
            self.assertTrue(all(r['makespan_ms']>0 for r in rows))
            self.assertEqual(rows[0]['makespan_ms'],rows[1]['makespan_ms'])
            path=out/'analysis'/f"{rows[0]['id']}.json";data=json.loads(path.read_text())
            data['diagnostic']['source']['repeated_packets']+=1;path.write_text(json.dumps(data))
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(p.returncode,0);self.assertIn('diagnostic',p.stdout+p.stderr)
            data['diagnostic']['source']['repeated_packets']-=1;path.write_text(json.dumps(data))
            attempt=Path(json.loads((out/'runs.json').read_text())[rows[0]['id']]['attempt'])
            mutations=[(attempt/'ns3.conf',lambda t:t.replace('VAR_WIN 1','VAR_WIN 0')),
                (attempt/'run_manifest.json',lambda t:json.dumps({**json.loads(t),'command':['/wrong/binary']})),
                (attempt/'topology.txt',lambda t:t+'\n'),(attempt/'schedule.csv',lambda t:t+'\n'),
                (attempt/'stages.csv',lambda t:t+'\n')]
            for target,change in mutations:
                with self.subTest(artifact=target.name):
                    original=target.read_text();target.write_text(change(original))
                    try:
                        p=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
                        self.assertNotEqual(p.returncode,0);self.assertIn('executed',p.stdout+p.stderr)
                    finally:target.write_text(original)
