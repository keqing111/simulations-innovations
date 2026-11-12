"""Analytic checks for GPU cyclic rounds under the shared topology evaluator."""
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT/'build/bvn_decompose_simulator'


class SpreadoutCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-s', '-C', str(ROOT), 'bvn-decompose-build'], check=True)

    def invoke(self, n, g, entries, topology=1, unit_bytes=1048576,profile=0):
        ranks = n*g
        data = [0]*(ranks*ranks)
        for src, dst, amount in entries:
            data[src*ranks+dst] = amount
        command=[str(BIN), '--mode', 'spreadout', '--n', str(n), '--g', str(g),
                 '--unit-mb', str(unit_bytes/1e6), '--topology', str(topology), '--repeat', '2']
        if profile:command+=['--wan-profile',str(profile)]
        done = subprocess.run(command,
                              input=' '.join(map(str, data)), text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        raw = json.loads(done.stdout)
        sent = {}
        for stage in raw['rounds']:
            for src, dst, units in stage['flows']:
                self.assertNotIn((src, dst), sent)
                sent[src, dst] = units
        self.assertEqual(sent, {(s, d): x for s, d, x in entries if x})
        self.assertAlmostEqual(raw['total_us'], sum(r['total_us'] for r in raw['rounds']))
        return raw

    def test_shared_wan_and_full_duplex(self):
        # Two same-round GPUs share 100Gbps, reverse direction is independent.
        raw = self.invoke(4, 2, [(0, 4, 1), (1, 5, 1), (4, 0, 1), (5, 1, 1)])
        self.assertEqual(raw['active_rounds'], 1)
        self.assertAlmostEqual(raw['total_us'], 52.8+167.77216)

    def test_independent_wan_routes_overlap(self):
        raw = self.invoke(4, 1, [(0, 2, 1), (1, 3, 1)])
        self.assertAlmostEqual(raw['total_us'], 52.8+83.88608)

    def test_same_dc_shared_nic(self):
        raw = self.invoke(4, 2, [(0, 2, 1), (1, 3, 1)])
        self.assertAlmostEqual(raw['total_us'], 1.4+41.94304)

    def test_local_only_and_mixed_local_network_round(self):
        local = self.invoke(2, 2, [(0, 1, 1)])
        self.assertAlmostEqual(local['total_us'], .1+1.048576*2.3)
        mixed = self.invoke(2, 2, [(0, 1, 1), (1, 2, 1)])
        self.assertEqual(mixed['active_rounds'], 1)
        self.assertAlmostEqual(mixed['total_us'], 52.8+83.88608)

    def test_empty_and_legacy_independent_pair_reference(self):
        self.assertEqual(self.invoke(2, 2, [])['total_us'], 0)
        raw = self.invoke(2, 2, [(0, 2, 1), (1, 3, 3)], topology=0)
        self.assertAlmostEqual(raw['total_us'], .7+3*1.048576*22)
        self.assertAlmostEqual(raw['total_us'], raw['legacy_spreadout_us'])

    def test_fractional_mb_conversion_keeps_integer_byte_audit(self):
        sys.path.insert(0, str(ROOT.parent/'ds/decomposition'))
        import baseline_control
        raw = self.invoke(4, 1, [(0, 2, 4139)], unit_bytes=16)
        data = [0]*16
        data[2] = 4139
        result = baseline_control.audit(raw, data, 4, 1, 16)
        self.assertEqual(result['resource_totals']['server-out:0'], 66224)

    def test_shared_gateway_profile(self):
        raw=self.invoke(4,1,[(0,2,1),(1,3,1)],profile=1)
        self.assertAlmostEqual(raw['total_us'],52.8+167.77216)
        snapshot=raw['topology_snapshot']
        resources=dict(zip(snapshot['resource_names'],snapshot['bandwidth_gbps']))
        self.assertEqual(resources['gateway-out:0'],100)

    def test_heterogeneous_wan_profile(self):
        raw=self.invoke(4,2,[(0,4,1),(1,5,1)],profile=2)
        self.assertAlmostEqual(raw['total_us'],202.8+335.54432)
        snapshot=raw['topology_snapshot']
        resources=dict(zip(snapshot['resource_names'],snapshot['bandwidth_gbps']))
        self.assertEqual(resources['wan:0:1'],50)
        self.assertEqual(resources['wan:0:2'],200)


if __name__ == '__main__':
    unittest.main()
