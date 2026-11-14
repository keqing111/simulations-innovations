"""Smoke the real stage-batch campaign, refusing a forged member map."""
import json
import hashlib
import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class BatchStudyTests(unittest.TestCase):
    def test_packet_summary_is_recomputed_from_real_backend_observations(self):
        from simai_a2a.runner import run_scenario
        binary=Path(os.environ.get('SIMAI_NS3_BINARY',ROOT.parents[2]/'bin/SimAI_simulator'))
        if not binary.is_file():self.skipTest('ns3 backend absent')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'AS_SEND_LAT':'0'}):
            _,study=self.prepare_smoke(tmp);output=Path(tmp);case=study['cases'][0];directory=output/'cases'/case['name']
            run=run_scenario(directory,'native1',directory/'topology.txt',binary,timeout_s=20,ns3_threads=0,
                             config_template=ROOT/'configs/ns3-multidc.conf')
            self.assertTrue(run['success'],run)
            (output/'packet.json').write_text(json.dumps({case['name']:{'native1':
                dict(success=True,attempt=run['attempt_dir'])}}))
            spec=importlib.util.spec_from_file_location('batch_campaign',ROOT/'scripts/fast_batch_study.py')
            module=importlib.util.module_from_spec(spec)
            with patch.object(sys,'path',[str(ROOT/'scripts'),*sys.path]):spec.loader.exec_module(module)
            args=SimpleNamespace(output=output,binary=binary)
            scoped={**study,'cases':[{**case,'algorithms':['native1']}]}
            module.packet_rows(args,scoped)
            summary_path=Path(run['attempt_dir'])/'summary.json';summary=json.loads(summary_path.read_text())
            summary['actual_makespan_ns']=1;summary_path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError,'summary'):module.packet_rows(args,scoped)

    def prepare_smoke(self,tmp):
        command=[sys.executable,str(ROOT/'scripts/fast_batch_study.py'),'--phase','prepare','--output',tmp,'--smoke']
        done=subprocess.run(command,capture_output=True,text=True,timeout=90)
        self.assertEqual(done.returncode,0,done.stdout+done.stderr)
        return command, json.loads((Path(tmp)/'study.json').read_text())

    def test_audit_binds_variant_limits_to_registered_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            command,study=self.prepare_smoke(tmp)
            artifact=Path(tmp)/'cases'/study['cases'][0]['name']/'schedules/native-bounded/schedule_manifest.json'
            manifest=json.loads(artifact.read_text());manifest['metadata']['batching']['max_peers']=99
            artifact.write_text(json.dumps(manifest));command[command.index('prepare')]='audit'
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(done.returncode,0);self.assertIn('policy',done.stdout+done.stderr)

    def test_mutated_registration_is_not_treated_as_frozen_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            command,study=self.prepare_smoke(tmp);study['cases'][0]['seed']=999
            (Path(tmp)/'study.json').write_text(json.dumps(study));command[command.index('prepare')]='audit'
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(done.returncode,0);self.assertIn('grid',done.stdout+done.stderr)

    def test_direct_pair_misrouting_cannot_pass_just_by_conserving_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            command,study=self.prepare_smoke(tmp)
            directory=Path(tmp)/'cases'/study['cases'][0]['name']/'schedules/direct-concurrent'
            path=directory/'schedule.csv';lines=path.read_text().splitlines();fields=lines[0].split(',')
            fields[1]='2' if fields[1]!='2' else '3';lines[0]=','.join(fields)
            path.write_text('\n'.join(lines)+'\n')
            manifest_path=directory/'schedule_manifest.json';manifest=json.loads(manifest_path.read_text())
            manifest['schedule_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest));command[command.index('prepare')]='audit'
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(done.returncode,0);self.assertIn('Direct',done.stdout+done.stderr)

    def test_real_prepare_and_audit_reject_corrupt_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            command=[sys.executable,str(ROOT/'scripts/fast_batch_study.py'),'--phase','prepare',
                     '--output',tmp,'--smoke']
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertEqual(done.returncode,0,done.stdout+done.stderr)
            path=Path(tmp);study=json.loads((path/'study.json').read_text())
            self.assertEqual(len(study['cases']),2);self.assertTrue(study['prepared'])
            self.assertEqual(len(json.loads((path/'preparation.json').read_text())),14)
            command[command.index('prepare')]='audit'
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertEqual(done.returncode,0,done.stdout+done.stderr)
            name=study['cases'][0]['name'];artifact=path/'cases'/name/'schedules/native-bounded/schedule_manifest.json'
            manifest=json.loads(artifact.read_text());manifest['metadata']['batching']['members'][0]=[999999]
            artifact.write_text(json.dumps(manifest))
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(done.returncode,0);self.assertIn('member',done.stdout+done.stderr)
