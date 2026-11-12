import itertools
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ds/decomposition'))
import baseline_control as audit
import residual_opportunity as offline
import study
import dynamic_small_cases as dynamic


class ResourceCyclesCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-s', '-C', str(ROOT / 'simulation'), 'bvn-decompose-build'], check=True)

    def run_case(self, data, n=6, g=4, cycle=3, rounds=0, profile=2, scaling=1, extra=()):
        command = [str(study.BIN), '--mode', 'pipeline', '--n', str(n), '--g', str(g),
                   '--strategy', '1', '--topology', '1', '--scaling', str(scaling),
                   '--wan-profile', str(profile), '--resource-rounds', str(rounds),
                   '--unit-mb', '.001024', '--repeat', '1']
        if cycle is not None:
            command += ['--resource-cycle', str(cycle)]
        command += list(extra)
        return subprocess.run(command, input=' '.join(map(str, data)), text=True,
                              capture_output=True, timeout=30)

    def test_cycle_disabled_preserves_existing_plans(self):
        data = study.fixed_load_matrix(6, 4, 'balanced', 'pipeline')
        for rounds in (0, 3):
            a = self.run_case(data, cycle=None, rounds=rounds)
            b = self.run_case(data, cycle=0, rounds=rounds)
            self.assertEqual(b.returncode, 0, b.stderr)
            a, b = json.loads(a.stdout), json.loads(b.stdout)
            for key in ('permutations', 'payload_units', 'network_stages_us', 'total_us'):
                self.assertEqual(a[key], b[key])
            self.assertEqual(b['cycle_accepts'], 0)

    def test_three_cycle_repairs_a_two_swap_local_minimum(self):
        # Equal real demand only across DCs; no unused LAN edges can hide the
        # directed three-DC assignment cycle demonstrated in the report.
        data = [1953 if s//8 != d//8 else 0 for s in range(24) for d in range(24)]
        two, three = self.run_case(data, cycle=2), self.run_case(data, cycle=3)
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(three.returncode, 0, three.stderr)
        two, three = json.loads(two.stdout), json.loads(three.stdout)
        self.assertAlmostEqual(two['network_stages_us'][0], 10442.14464)
        self.assertAlmostEqual(three['network_stages_us'][0], 5322.47232)
        self.assertEqual(three['stage_count'], 4)
        self.assertGreater(three['cycle_accepts'], 0)
        audit.audit(three, data, 6, 4, 1024)

    def test_uniform_descent_and_final_neighborhood_are_valid(self):
        data = study.fixed_load_matrix(6, 4, 'balanced', 'pipeline')
        for rounds in (0, 3):
            done = self.run_case(data, rounds=rounds)
            self.assertEqual(done.returncode, 0, done.stderr)
            raw = json.loads(done.stdout)
            self.assertAlmostEqual(sum(raw['network_stages_us']), 21931.24832)
            self.assertEqual(raw['stage_count'], 5)
            audit.audit(raw, data, 6, 4, 1024)
            oracle = offline.UniformOracle(raw, 1024); mask = oracle.full_mask
            accepts = 0
            for k, (p, step) in enumerate(zip(raw['permutations'], raw['steps'])):
                costs = step['cycle_costs_us']; accepts += len(costs)-1
                self.assertTrue(costs)
                self.assertTrue(all(a > b for a, b in zip(costs, costs[1:])))
                self.assertAlmostEqual(costs[-1], raw['network_stages_us'][k])
                neighborhood = offline.exchange_neighborhood(oracle, mask, p)
                self.assertEqual(neighborhood['2']['improving'], 0)
                self.assertEqual(neighborhood['3']['improving'], 0)
                mask ^= oracle.mask_of(p)
            self.assertEqual(raw['cycle_accepts'], accepts)
            self.assertEqual(raw['cycle_passes'], accepts+raw['stage_count'])

    def test_nonuniform_exact_delivery_and_bottleneck_are_preserved(self):
        for n in (3, 4, 5):
            data = dynamic.weighted_balanced(n, 101)
            done = self.run_case(data, n=n, g=1, scaling=0)
            self.assertEqual(done.returncode, 0, done.stderr)
            raw = json.loads(done.stdout); remaining = data[:]
            for p, step in zip(raw['permutations'], raw['steps']):
                optimum = max(min(remaining[s*n+d] for s, d in enumerate(candidate))
                              for candidate in itertools.permutations(range(n)))
                self.assertEqual(step['alpha'], optimum)
                self.assertEqual(sorted(p), list(range(n)))
                for s, d in enumerate(p):
                    remaining[s*n+d] -= optimum
            self.assertFalse(any(remaining))
            audit.audit(raw, data, n, 1, 1024)
            scaled = self.run_case(data, n=n, g=1)
            self.assertEqual(scaled.returncode, 0, scaled.stderr)
            audit.audit(json.loads(scaled.stdout), data, n, 1, 1024)

    def test_invalid_cycle_and_skip_combination_are_rejected(self):
        data = study.fixed_load_matrix(6, 4, 'balanced', 'pipeline')
        for cycle in (1, 4):
            self.assertNotEqual(self.run_case(data, cycle=cycle).returncode, 0)
        self.assertNotEqual(self.run_case(data, rounds=3,
                            extra=['--resource-skip-step', '0']).returncode, 0)


if __name__ == '__main__':
    unittest.main()
