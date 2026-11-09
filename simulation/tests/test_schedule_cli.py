"""Exercise the real merged executable; optional historical data are test-only."""
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'build/all2all_simulator'


def invoke(binary, data, n=6, g=1, mode='native', wan=100, gateway=400,
           latency=5000, mbpu=1e-6, dc=None):
    dc = dc if dc is not None else [i * 3 // n for i in range(n)]
    return subprocess.run([str(binary), '--topology', str(n), str(g), mode,
                           str(latency), '400', str(gateway), str(wan), str(mbpu),
                           ','.join(map(str, dc))], input=' '.join(map(str, data)),
                          capture_output=True, text=True, timeout=30)


class ScheduleCLI(unittest.TestCase):
    def test_mechanisms_and_actual_payload(self):
        data = [0 if i == j else 100000000 for i in range(6) for j in range(6)]
        for mode, gateway, wan, want in [('native', 400, 100, 81014),
                                       ('bandwidth', 400, 100, 54012.6),
                                       ('time', 800, 800, 30012.6)]:
            with self.subTest(mode=mode):
                done = invoke(BINARY, data, mode=mode, gateway=gateway, wan=wan)
                self.assertEqual(done.returncode, 0, done.stderr)
                raw = json.loads(done.stdout)
                self.assertAlmostEqual(raw['total_us'], want)
                self.assertEqual(sum(sum(s['payload']) for s in raw['stages']), 3000000000)

    def test_invalid_cli_rejected(self):
        data = [0] * 36
        for opts in [dict(mode='unknown'), dict(latency='nan'), dict(wan=0),
                     dict(mbpu=0), dict(dc=[0]), dict(n=0), dict(n=65)]:
            self.assertNotEqual(invoke(BINARY, data, **opts).returncode, 0)
        self.assertNotEqual(invoke(BINARY, data[:-1]).returncode, 0)
        data[0] = 1
        self.assertNotEqual(invoke(BINARY, data).returncode, 0)

    def test_historical_26_cases_78_outputs(self):
        archive = ROOT / 'independent/results/run-2c90c8145c7d-serial'
        if not (archive / 'study.json').exists():
            self.skipTest('optional historical data not installed; core tests do not require it')
        study = json.loads((archive / 'study.json').read_text())
        # Match the archived paper link profile only in this test binary.
        # No archived scheduler or Planner is compiled into it.
        with tempfile.TemporaryDirectory(prefix='fast-merge-paper-') as tmp:
            tmp = Path(tmp)
            flags = ['g++', '-std=c++11', '-O2', '-DNOLOG', '-include', 'cstring',
                     '-I'+str(ROOT), '-I'+str(ROOT/'algorithm'), '-I'+str(ROOT/'scheduler')]
            subprocess.run(flags + ['-Dget_inter_link_info=upstream_get_inter_link_info',
                                    '-Dget_intra_link_info=upstream_get_intra_link_info',
                                    '-c', str(ROOT/'scheduler/config.cpp'), '-o', str(tmp/'config.o')],
                           check=True, capture_output=True)
            shim = '#include "config.h"\nlink_info_t get_inter_link_info(INTER_LINK_TYPE){link_info_t r={};r.alpha=.7;r.beta=20;return r;}\nlink_info_t get_intra_link_info(INTRA_LINK_TYPE){link_info_t r={};r.alpha=.1;r.beta=8000./3600;return r;}\n'
            subprocess.run(flags + ['-c', '-x', 'c++', '-', '-o', str(tmp/'profile.o')],
                           input=shim, text=True, check=True, capture_output=True)
            sources = ['main.cpp', 'test.cpp', 'algorithm/all2all.cpp', 'algorithm/matrix.cpp',
                       'scheduler/local.cpp', 'scheduler/global.cpp', 'topology/model.cpp']
            binary = tmp/'native'
            subprocess.run(flags + [str(ROOT/p) for p in sources] +
                           [str(tmp/'config.o'), str(tmp/'profile.o'), '-lpthread', '-o', str(binary)],
                           check=True, capture_output=True)
            for case in study['cases']:
                expected = json.loads((archive/'raw'/f"{case['id']}.json").read_text())
                key = f"n{case['n']}g{case['g']}-{case['kind']}-s{case['seed']}-m{case['mib']}"
                matrix_path = archive / 'matrices' / (key + '.json')
                data = json.loads(matrix_path.read_text())
                for mode in ['native', 'bandwidth', 'time']:
                    with self.subTest(case=case['id'], mode=mode):
                        done = invoke(binary, data, case['n'], case['g'], mode,
                                      case['wan_bw'], case['gateway_bw'], case['wan_us'], dc=case['dc'])
                        self.assertEqual(done.returncode, 0, done.stderr)
                        raw = json.loads(done.stdout)
                        old = expected[mode]
                        for key in ['total_us', 'balance_us', 'intrinsic_us']:
                            self.assertTrue(math.isclose(raw[key], old[key], rel_tol=1e-12, abs_tol=1e-9), (key, raw[key], old[key]))
                        self.assertEqual(raw['dispatch_us'], old['dispatch_us'])
                        self.assertEqual(len(raw['stages']), len(old['stages']))
                        for actual, prior in zip(raw['stages'], old['stages']):
                            for key in ['freq', 'map', 'payload']:
                                self.assertEqual(actual[key], prior[key])
                            for key in ['alpha_us', 'serial_us', 'communication_us']:
                                self.assertTrue(math.isclose(actual[key], prior[key], rel_tol=1e-12, abs_tol=1e-9), (key, actual[key], prior[key]))


if __name__ == '__main__':
    unittest.main()
