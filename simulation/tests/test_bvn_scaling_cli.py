"""Scaling controls must preserve the native pipeline's real payload."""
import json
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / 'build/bvn_decompose_simulator'


class ScalingCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-s', '-C', str(ROOT), 'bvn-decompose-build'], check=True)

    def run_case(self, scaling=None, mode='pipeline'):
        command = [str(BIN), '--mode', mode, '--n', '2', '--g', '2',
                   '--strategy', '0', '--repeat', '1', '--topology', '1' if mode == 'pipeline' else '0']
        if scaling is not None:
            command += ['--scaling', str(scaling)]
        data = ([0, 0, 17, 31, 0, 0, 23, 41, 11, 29, 0, 0, 37, 43, 0, 0]
                if mode == 'pipeline' else [0, 3, 3, 0])
        return subprocess.run(command, input=' '.join(map(str, data)), text=True, capture_output=True)

    def test_explicit_off_matches_default(self):
        default = self.run_case()
        off = self.run_case(0)
        self.assertEqual(off.returncode, 0, off.stderr)
        a, b = json.loads(default.stdout), json.loads(off.stdout)
        for field in ('permutations', 'payload_units', 'total_us', 'dispatch_us'):
            self.assertEqual(a[field], b[field])
        self.assertFalse(b['scaling_enabled'])

    def test_on_delivers_all_interserver_bytes(self):
        done = self.run_case(1)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertTrue(result['scaling_enabled'])
        sent = [[0, 0], [0, 0]]
        for p, payload in zip(result['permutations'], result['payload_units']):
            for src, dst in enumerate(p):
                if src != dst:
                    sent[src][dst] += sum(payload[src*2:src*2+2])
        self.assertEqual(sent, [[0, 112], [120, 0]])
        network = result['network_stages_us']
        expected = result['balance_us'] + max(result['intrinsic_us'], network[0])
        expected += sum(max(d, t) for d, t in zip(result['dispatch_us'], network[1:]))
        expected += result['dispatch_us'][-1]
        self.assertAlmostEqual(result['total_us'], expected)

    def test_invalid_flag_and_matrix_scaling_rejected(self):
        self.assertNotEqual(self.run_case(2).returncode, 0)
        self.assertNotEqual(self.run_case(1, mode='matrix').returncode, 0)


if __name__ == '__main__':
    unittest.main()
