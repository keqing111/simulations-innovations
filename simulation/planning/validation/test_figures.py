import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('report_figures',Path(__file__).with_name('report_figures.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)


class FigureTests(unittest.TestCase):
    def test_final_cpu_provenance_and_geometric_mean(self):
        f.require_final_binary({'binary_sha256':'final'},{'identity':{'binary_sha256':'final'}})
        with self.assertRaises(ValueError):f.require_final_binary({'binary_sha256':'final'},{'identity':{'binary_sha256':'v1'}})
        self.assertEqual(f.gm([.5,2]),1)


if __name__=='__main__':unittest.main()
