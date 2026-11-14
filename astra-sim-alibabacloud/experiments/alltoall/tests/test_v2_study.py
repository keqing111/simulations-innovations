import importlib.util
from pathlib import Path
import unittest

class V2StudyTests(unittest.TestCase):
    def test_case_grid_is_deduplicated_and_qp_sensitivity_absent(self):
        path=Path(__file__).resolve().parents[1]/'tools/run_v2_study.py'
        spec=importlib.util.spec_from_file_location('v2study',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        catalogs={name:dict(layers=[1,2,3],experts=expert) for name,expert in [('deepseek',256),('kimi',384),('qwen',128),('llama',128)]}
        cases=module.case_specs(catalogs)
        self.assertEqual(len(cases),134);self.assertEqual(len({c['name'] for c in cases}),134)
        self.assertEqual(len([c for c in cases if c['rho']>0]),18)
        for c in cases:
            if c.get('model') and c['dc']==3:self.assertEqual(c['model'],'kimi')
        self.assertEqual(len(module.METHODS),6)

    def test_prepare_failure_is_recorded_without_losing_other_cases(self):
        from unittest.mock import patch
        import tempfile
        path=Path(__file__).resolve().parents[1]/'tools/run_v2_study.py'
        spec=importlib.util.spec_from_file_location('v2study',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def prepare(base,c,catalogs):
            if c['name']=='short':raise ValueError('trace window lacks tokens')
        with tempfile.TemporaryDirectory() as tmp,patch.object(module,'prepare_case',side_effect=prepare):
            valid,errors=module.prepare_cases(Path(tmp),[dict(name=n) for n in ('first','short','last')],{})
        self.assertEqual([c['name'] for c in valid],['first','last']);self.assertEqual(errors[0]['case'],'short')

