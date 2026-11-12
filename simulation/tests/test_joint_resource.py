import importlib.util
import itertools
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ds/decomposition'))
import study
import baseline_control

joint = None
if importlib.util.find_spec('joint_resource'):
    import joint_resource as joint


def toy():
    p, q = [1, 0, 3, 2], [3, 2, 1, 0]
    paths = [dict(resource_ids=[], propagation_us=0) for _ in range(16)]
    for e, perm in enumerate((p, q)):
        for s, d in enumerate(perm):
            paths[4*s+d] = dict(resource_ids=[e], propagation_us=0)
    return dict(n=4, g=1, permutations=[p, q],
                steps=[dict(alpha=1, alpha_scaled=1)]*2,
                payload_units=[[1]*4, [1]*4],
                topology_snapshot=dict(n=4, g=1, resource_names=['a', 'b'],
                    bandwidth_gbps=[.016, .016], server_paths=paths))


class JointResource(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(joint, 'joint coefficient prototype is not implemented')

    def test_weighted_assignment_handles_negative_and_forbidden_edges(self):
        costs = [[None, -5, 2], [3, None, -4], [-3, 1, None]]
        self.assertEqual(joint.assignment(costs), [1, 2, 0])
        self.assertIsNone(joint.assignment([[0, None], [0, None]]))
        self.assertEqual(joint.assignment([[0]]), [0])

    def test_large_finite_costs_do_not_report_false_infeasibility(self):
        p = joint.assignment([[-1e308, 1e308], [-1e308, 1e308]])
        self.assertIsNotNone(p)
        self.assertEqual(sorted(p), [0, 1])

    def test_one_threshold_is_hard_maximum(self):
        raw = dict(n=3, g=1, permutations=[[2, 1, 0], [0, 2, 1], [1, 0, 2]],
                   steps=[dict(alpha=q, alpha_scaled=q) for q in (2, 1, 1)],
                   payload_units=[[2, 0, 2], [0, 1, 1], [1, 1, 0]],
                   topology_snapshot=dict(n=3, g=1, resource_names=['shared'],
                       bandwidth_gbps=[100], server_paths=[dict(
                           resource_ids=[] if s==d else [0], propagation_us=0)
                           for s in range(3) for d in range(3)]))
        state = joint.State(raw, 1)
        actions = joint.propose(state, 'stage', 1)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['q'], 2)
        self.assertEqual(state.action([2, 1, 0])['q'], 2)

    def test_threshold_budget_cannot_exceed_four(self):
        with self.assertRaises(ValueError):
            joint.propose(joint.State(toy(), 1), 'stage', 5)

    def test_weighted_direction_long_cycle_and_disjoint_components_stay_perfect(self):
        current = list(range(6)); target = [1, 2, 0, 4, 5, 3]
        trials = list(joint.cycle_trials(current, target))
        self.assertEqual(len(trials), 3)
        self.assertIn([1, 2, 0, 3, 4, 5], trials)
        self.assertIn([0, 1, 2, 4, 5, 3], trials)
        for p in trials:
            self.assertEqual(sorted(p), current)
        self.assertEqual(list(joint.cycle_trials(current, [1, 2, 3, 4, 5, 0])),
                         [[1, 2, 3, 4, 5, 0]])

    def test_assignment_optimum_matches_independent_tiny_enumeration(self):
        costs = [[4, 1, None], [2, 7, 3], [5, None, 6]]
        result = joint.assignment(costs)
        valid = [p for p in itertools.permutations(range(3))
                 if all(costs[s][d] is not None for s, d in enumerate(p))]
        self.assertEqual(sum(costs[s][d] for s, d in enumerate(result)),
                         min(sum(costs[s][d] for s, d in enumerate(p)) for p in valid))

    def test_resource_guided_assignment_crosses_linear_price_tie(self):
        state = joint.State(toy(), 1)
        actions = joint.propose(state, 'stage', 4)
        self.assertAlmostEqual(min(a['time_us'] for a in actions), 1)
        result = joint.plan(toy(), 1, 'stage')
        self.assertEqual(len(result['permutations']), 2)
        self.assertAlmostEqual(result['network_us'], 2)

    def test_threshold_pool_is_bounded_and_lookahead_keeps_delivery(self):
        state = joint.State(toy(), 1)
        self.assertLessEqual(len(joint.propose(state, 'capacity', 4)), 4)
        result = joint.plan(toy(), 1, 'capacity', lookahead=True)
        self.assertEqual(result['delivered_bytes'], 8)
        self.assertAlmostEqual(result['network_us'], 2)

    def test_propagation_degree_is_separate_from_resource_work(self):
        raw = toy()
        for path in raw['topology_snapshot']['server_paths']:
            if path['resource_ids']:
                path['propagation_us'] = 2
        state = joint.State(raw, 1)
        self.assertEqual(state.L, 2)
        self.assertEqual(state.Q, 4)
        action = state.action([3, 0, 1, 2])
        self.assertEqual(action['time_us'], 3)
        self.assertEqual(action['next'].potential('degree'), 3)
        self.assertEqual(joint.score(state, action, 'degree'), 0)

    def test_actual_coefficient_and_real_delivery_not_threshold_or_padding(self):
        state = joint.State(toy(), 1)
        action = state.action([1, 0, 3, 2])
        self.assertEqual(action['q'], 1)
        self.assertEqual(action['bytes'], 4)
        after = state.after(action)
        self.assertEqual(sum(after.U), 4)
        self.assertEqual(state.R, [0, 1, 0, 1, 1, 0, 1, 0,
                                   0, 1, 0, 1, 1, 0, 1, 0])
        raw = toy()
        raw['steps'] = [dict(alpha=3, alpha_scaled=1)]*2
        state = joint.State(raw, 1)
        self.assertEqual(state.kappa, 3)
        self.assertEqual(state.action([1, 0, 3, 2])['bytes'], 4)
        self.assertAlmostEqual(state.action([1, 0, 3, 2])['time_us'], 2)

    def test_potential_consistency_and_zero_payload(self):
        for objective in ('stage', 'capacity', 'degree'):
            result = joint.plan(toy(), 1, objective)
            self.assertTrue(all(s['excess_us'] >= -1e-8 for s in result['trace']))
        raw = toy(); raw['payload_units'] = [[0]*4]*2
        result = joint.plan(raw, 1, 'degree')
        self.assertEqual(result['network_us'], 0)
        self.assertEqual(result['delivered_bytes'], 0)

    def test_sparse_invalid_auxiliary_and_unknown_objective_rejected(self):
        raw = toy(); raw['permutations'][0] = [1, 1, 3, 2]
        with self.assertRaises(ValueError):
            joint.State(raw, 1)
        with self.assertRaises(ValueError):
            joint.plan(toy(), 1, 'not-an-objective')


class ReplayPlan(unittest.TestCase):
    def run_cli(self, matrix, n, g, plan=None, scaling=1, unit=1024):
        cmd = [str(study.BIN), '--mode', 'pipeline', '--n', str(n), '--g', str(g),
               '--topology', '1', '--scaling', str(scaling), '--unit-mb', str(unit/1e6), '--repeat', '1']
        if plan is not None:
            cmd += ['--replay-plan', str(plan)]
        return subprocess.run(cmd, input=' '.join(map(str, matrix)), text=True,
                              capture_output=True, timeout=20)

    def test_native_replay_preserves_full_pipeline_and_payload(self):
        data = study.fixed_load_matrix(3, 2, 'zipf', 'pipeline')
        native = json.loads(self.run_cli(data, 3, 2).stdout)
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / 'plan.txt'
            lines = [str(len(native['steps']))]
            lines += [' '.join(map(str, [s['alpha']]+p))
                      for p, s in zip(native['permutations'], native['steps'])]
            path.write_text('\n'.join(lines)+'\n')
            result = self.run_cli(data, 3, 2, path)
            self.assertEqual(result.returncode, 0, result.stderr)
            replay = json.loads(result.stdout)
            self.assertTrue(replay['replay_only'])
            for key in ('permutations', 'payload_units', 'network_stages_us',
                        'dispatch_us', 'balance_us', 'intrinsic_us', 'total_us'):
                self.assertEqual(replay[key], native[key], key)
            baseline_control.audit(replay, data, 3, 2, 1024)

    def test_replay_with_actual_scaling_disabled_scaling_and_empty_demand(self):
        for n, g, kind, scaling, unit in ((6, 4, 'zipf', 1, 16),
                                          (2, 1, 'balanced', 0, 16),
                                          (2, 1, 'empty', 0, 16)):
            data = [0]*4 if kind == 'empty' else study.fixed_load_matrix(n, g, kind, 'pipeline')
            native = json.loads(self.run_cli(data, n, g, scaling=scaling, unit=unit).stdout)
            if kind == 'zipf':
                self.assertGreater(native['steps'][0]['alpha']//native['steps'][0]['alpha_scaled'], 1)
            with tempfile.TemporaryDirectory() as temp:
                path = pathlib.Path(temp) / 'plan.txt'
                lines = [str(len(native['steps']))]
                lines += [' '.join(map(str, [s['alpha']]+p))
                          for p, s in zip(native['permutations'], native['steps'])]
                path.write_text('\n'.join(lines)+'\n')
                completed = self.run_cli(data, n, g, path, scaling=scaling, unit=unit)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                replay = json.loads(completed.stdout)
                for key in ('permutations', 'payload_units', 'network_stages_us',
                            'dispatch_us', 'balance_us', 'intrinsic_us', 'total_us'):
                    self.assertEqual(replay[key], native[key], key)
                baseline_control.audit(replay, data, n, g, unit)

    def test_reordered_complete_decomposition_is_legal_execution(self):
        data = study.fixed_load_matrix(6, 4, 'perturbed', 'pipeline')
        native = json.loads(self.run_cli(data, 6, 4, scaling=0).stdout)
        order = sorted(range(len(native['steps'])),
                       key=lambda k: (native['steps'][k]['alpha'], k), reverse=True)
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp)/'descending.txt'
            path.write_text(str(len(order))+'\n'+'\n'.join(
                ' '.join(map(str, [native['steps'][k]['alpha']]+native['permutations'][k]))
                for k in order)+'\n')
            result = self.run_cli(data, 6, 4, path, scaling=0)
            self.assertEqual(result.returncode, 0, result.stderr)
            baseline_control.audit(json.loads(result.stdout), data, 6, 4, 1024)

    def test_replay_rejects_duplicates_and_incomplete_plans(self):
        data = study.fixed_load_matrix(2, 1, 'balanced', 'pipeline')
        for text in ('1\n1 1 0\n', '1\n39060 1 1\n', '0\n',
                     '1\n39060 1\n', '1\n39060 1 0\ntrailing\n'):
            with tempfile.TemporaryDirectory() as temp:
                path = pathlib.Path(temp) / 'plan.txt'; path.write_text(text)
                result = self.run_cli(data, 2, 1, path)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('unknown option', result.stderr)


if __name__ == '__main__':
    unittest.main()
