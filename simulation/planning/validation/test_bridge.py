import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('bridge',Path(__file__).with_name('bridge.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)


class BridgeTests(unittest.TestCase):
    def test_only_documented_export_and_implementation_fields_differ(self):
        old=dict(mode='A1',policy={'row_order':0,'exact':True},stages=[{'freq':10,'map':[1,0],'payload':[10,3]}],
                 decisions=[{'weight':10,'row_order':[0,1],'row_pressure':[]}],initial_rows=[10,3],support_diagnostics=[],wall_us=5,timing_samples=[])
        new=copy.deepcopy(old);new.update(mode='A1-opt',policy={'optimized':True,'static_path':False,'exact':True},wall_us=9)
        for key in ['initial_rows','support_diagnostics']:new.pop(key)
        new['decisions'][0].pop('row_order');new['decisions'][0].pop('row_pressure')
        b.require_equal(old,new)
        for key in ['payload','map','freq']:
            bad=copy.deepcopy(new);bad['stages'][0][key]=[0,0] if key!='freq' else 9
            with self.assertRaises(ValueError):b.require_equal(old,bad)
        bad=copy.deepcopy(new);bad['policy']['exact']=False
        with self.assertRaises(ValueError):b.require_equal(old,bad)


if __name__=='__main__':unittest.main()
