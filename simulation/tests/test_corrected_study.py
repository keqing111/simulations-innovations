import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'ds/decomposition'))
HOST = ROOT/'simulation/build/runtime_host_reference'
corrected = None
if importlib.util.find_spec('corrected_study'):
    import corrected_study as corrected


def host(data, n=2, g=2, plan=None):
    command = [str(HOST), str(n), str(g)]
    if plan is not None:
        command.append(str(plan))
    return subprocess.run(command, input=' '.join(map(str, data)), text=True,
                          capture_output=True, timeout=30)


class NativeHostReference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-s', '-C', str(ROOT/'simulation'), 'runtime-host-build'], check=True)

    def setUp(self):
        self.assertTrue(HOST.exists(), 'unmodified host reference has not been built')

    def test_original_scheduler_replay_and_provenance(self):
        # Source GPU0 alone owns an outgoing server-pair imbalance; local traffic exists.
        data = [0, 2, 9, 1, 3, 0, 0, 2, 4, 0, 0, 1, 0, 6, 2, 0]
        result = host(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads(result.stdout)
        self.assertTrue(raw['native_replay_equal'])
        self.assertEqual(raw['delivered_matrix'], data)
        self.assertGreater(sum(map(sum, raw['balance_matrices'])), 0)
        self.assertGreater(sum(map(sum, raw['intrinsic_matrices'])), 0)
        self.assertEqual([s['alpha'] for s in raw['steps']],
                         sorted([s['alpha'] for s in raw['steps']], reverse=True))

    def test_external_schedule_reconstructs_and_rejects_invalid_capacity(self):
        data = [0, 1, 4, 2, 1, 0, 0, 4, 3, 1, 0, 1, 2, 4, 1, 0]
        raw = json.loads(host(data).stdout)
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp)/'plan.txt'
            lines = [str(len(raw['steps']))]
            lines += [' '.join(map(str, [q['alpha']]+p))
                      for p, q in zip(raw['permutations'], raw['steps'])]
            path.write_text('\n'.join(lines)+'\n')
            replay = host(data, plan=path)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)['delivered_matrix'], data)
            path.write_text('1\n999999 1 0\n')
            self.assertNotEqual(host(data, plan=path).returncode, 0)

    def test_empty_and_eight_gpu_nodes(self):
        for g in (2, 8):
            result = host([0]*(2*g)**2, g=g)
            self.assertEqual(result.returncode, 0, result.stderr)
            raw = json.loads(result.stdout)
            self.assertEqual(raw['stage_count'], 0)
            self.assertEqual(sum(raw['delivered_matrix']), 0)

    def test_eight_gpu_nonzero_origin_and_native_profiler(self):
        self.assertIsNotNone(corrected)
        data, _ = corrected.traffic(2, 8, 'token_affinity', 480, 1101)
        result = host(data, g=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads(result.stdout)
        self.assertTrue(raw['native_replay_equal'])
        self.assertEqual(raw['delivered_matrix'], data)
        self.assertGreater(sum(map(sum, raw['balance_matrices'])), 0)
        self.assertAlmostEqual(corrected.native_profile_us(raw), raw['native_profile_ms']*1000, places=8)

    def test_unsafe_native_integer_domain_rejected_before_decomposition(self):
        # Native update_permutation_sets returns uint although its matrix is uint64.
        # This legal-per-cell input previously segfaulted inside the upstream call.
        data = [1000000000 if s//8 != d//8 else 0 for s in range(16) for d in range(16)]
        result = host(data, g=8)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('safe native integer domain', result.stderr)


class CorrectedTraffic(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(corrected, 'corrected workload module is missing')

    def test_token_routing_conserves_assignments_and_keeps_local_work(self):
        data, meta = corrected.traffic(6, 4, 'token_zipf', 480, 1101)
        self.assertEqual(sum(data)+sum(meta['self_routes']), 24*480*2)
        self.assertTrue(all(data[i*24+i] == 0 for i in range(24)))
        features = corrected.input_features(data, 6, 4)
        self.assertGreater(features['unbalanced_tiles'], 0)
        self.assertGreater(features['local_units'], 0)
        self.assertEqual(features['identical_source_servers'], 0)
        self.assertEqual(meta['duplicate_expert_routes'], 0)

    def test_source_batch_seed_and_affinity_are_explicit(self):
        a, ma = corrected.traffic(6, 4, 'token_affinity', 480, 1101)
        b, _ = corrected.traffic(6, 4, 'token_affinity', 480, 1102)
        self.assertNotEqual(a, b)
        self.assertEqual(ma['top_k'], 2)
        for i in range(24):
            self.assertEqual(sum(a[i*24:(i+1)*24])+ma['self_routes'][i], 960)

    def test_balanced_control_and_edge_zipf_have_distinct_contracts(self):
        data, meta = corrected.traffic(6, 4, 'balanced', 480, 1101)
        self.assertEqual(set(v for i, v in enumerate(data) if i//24 != i%24), {40})
        self.assertEqual(corrected.input_features(data, 6, 4)['unbalanced_tiles'], 0)
        a, info = corrected.traffic(6, 4, 'edge_zipf', 480, 1101)
        self.assertEqual(sum(a), sum(data))
        self.assertIsNone(info['top_k'])
        self.assertGreater(corrected.input_features(a, 6, 4)['unbalanced_tiles'], 0)

    def test_common_endpoint_model_matches_original_native_profiler(self):
        self.assertTrue(HOST.exists())
        data, _ = corrected.traffic(2, 2, 'token_zipf', 24, 1101)
        result = host(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads(result.stdout)
        self.assertAlmostEqual(corrected.native_profile_us(raw), raw['native_profile_ms']*1000, places=8)

    def test_physical_reference_plans_bytes_before_native_integer_rounding(self):
        data, _ = corrected.traffic(2, 2, 'token_zipf', 24, 1101)
        raw = corrected.host_vectors(data, 2, 2)
        direct = json.loads(host([v*corrected.UNIT for v in data]).stdout)
        self.assertEqual(raw['payload_unit_bytes'], 1)
        self.assertEqual(raw['delivered_matrix'], [v*corrected.UNIT for v in data])
        for field in ('server_matrix', 'padded_matrix', 'payload_units', 'balance_matrices',
                      'intrinsic_matrices', 'dispatch_matrices', 'steps', 'permutations'):
            self.assertEqual(raw[field], direct[field], field)
        self.assertAlmostEqual(corrected.native_profile_us(raw), direct['native_profile_ms']*1000, places=8)


if __name__ == '__main__':
    unittest.main()
