"""Exercise real compiled policies through the immutable study CLI."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import importlib.util

ROOT=Path(__file__).resolve().parents[1]


class EiStudyTests(unittest.TestCase):
    def test_boundary_plot_labels_only_real_winner_changes(self):
        spec=importlib.util.spec_from_file_location('boundary_renderer',ROOT/'scripts/render_fast_ei_boundaries.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        regions=[dict(lower_alpha_ns=1,winner='a'),dict(lower_alpha_ns=2,winner='a'),
                 dict(lower_alpha_ns=3,winner='b'),dict(lower_alpha_ns=4,winner='b')]
        self.assertEqual(module.winner_switches(regions),[3])

    def test_smoke_outputs_complete_grid_and_rejects_changed_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            command=[sys.executable,str(ROOT/'scripts/fast_ei_study.py'),'--output',tmp,
                     '--phase','analytical','--smoke','--workers','1']
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertEqual(done.returncode,0,done.stdout+done.stderr)
            output=Path(tmp);study=json.loads((output/'study.json').read_text())
            self.assertTrue(study['completed']);self.assertEqual(len(study['cases']),2)
            rows=json.loads((output/'analytical.json').read_text());self.assertEqual(len(rows),8)
            self.assertTrue(all(r['inter_bytes']==r['expected_inter_bytes'] for r in rows))
            command[command.index('analytical')]='audit'
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertEqual(done.returncode,0,done.stdout+done.stderr)
            command[command.index('audit')]='render'
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertEqual(done.returncode,0,done.stdout+done.stderr)
            self.assertTrue((output/'figures/model-comparison.png').is_file())
            command[command.index('render')]='audit'
            path=output/'analytical.json';rows[0]['makespan_ms']+=1
            path.write_text(json.dumps(rows))
            done=subprocess.run(command,capture_output=True,text=True,timeout=90)
            self.assertNotEqual(done.returncode,0)
            self.assertIn('row',done.stdout+done.stderr)
