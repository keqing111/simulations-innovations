"""Link against the actual ns3 library: large-BDP dynamic windows must not wrap."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class RdmaWindowTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SIMAI_NS3_BINARY'),'set SIMAI_NS3_BINARY for real ns3 window test')
    def test_wan_bdp_variable_window_and_hp_window_keep_full_integer_precision(self):
        root=Path(__file__).resolve().parents[1]
        binary=Path(os.environ['SIMAI_NS3_BINARY']).resolve();build=binary.parent.parent
        libraries=build/'lib';include=build/'include'
        if not include.is_dir():include=build.parent/'build/include'
        with tempfile.TemporaryDirectory(prefix='rdma-window-test-') as tmp:
            executable=Path(tmp)/'window-test'
            command=['g++','-std=c++17',str(root/'tests/cpp/rdma_window_test.cc'),
                     '-I',str(include),'-L',str(libraries),f'-Wl,-rpath,{libraries}']
            for module in ['point-to-point','internet','applications','mtp','network','core']:
                library=next(libraries.glob(f'libns3.*-{module}-*.so'))
                command.append(f'-l:{library.name}')
            command+=['-o',str(executable)]
            compiled=subprocess.run(command,capture_output=True,text=True,timeout=30)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            result=subprocess.run([str(executable)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
