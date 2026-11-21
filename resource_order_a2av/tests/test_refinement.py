import random
import unittest
from pathlib import Path

from resource_order.cover import (initial_cover, minimize_joint, proposals,
                                  isolated_costs, dependency_bound, exchange, cover_plan)
from resource_order.model import Message
from resource_order.topology import build_topology
from resource_order.refinement import minimize_leximax, resource_proxy, score_key


class RefinementTests(unittest.TestCase):
    def problem(self, seed):
        net = build_topology('tree', racks=2, servers_per_rack=1, gpus_per_server=4)
        rng = random.Random(seed)
        flows = [Message(s*8+d, s, d, rng.randrange(1000, 100000))
                 for s in range(8) for d in range(8) if s != d]
        return net, flows

    def test_max_tie_reduces_secondary_without_changing_messages(self):
        net, flows = self.problem(7)
        old, _ = minimize_joint(flows, initial_cover(range(8)), net)
        final, history = minimize_leximax(flows, old, net)
        self.assertEqual(history[0]['score_key'][0], history[-1]['score_key'][0])
        self.assertLess(history[-1]['score_key'][1], history[0]['score_key'][1])
        self.assertEqual(cover_plan(flows, final, range(8)).messages, tuple(flows))
        for src in range(8):
            self.assertEqual(sorted(row[src] for row in final), [d for d in range(8) if d != src])
        for row in final:
            self.assertEqual(sorted(row), list(range(8)))

    def test_plateau_can_unlock_lower_primary_proxy(self):
        net, flows = self.problem(3)
        old, _ = minimize_joint(flows, initial_cover(range(8)), net)
        _, history = minimize_leximax(flows, old, net)
        self.assertGreater(len(history), 1)
        self.assertLess(history[-1]['score_key'][0], history[0]['score_key'][0])

    def test_cache_and_native_preserve_search_and_local_stationarity(self):
        net, flows = self.problem(4)
        initial = initial_cover(range(8))
        final, history = minimize_leximax(flows, initial, net, cache_pairs=False)
        cached, ch = minimize_leximax(flows, initial, net, cache_pairs=True)
        self.assertEqual(final, cached)
        self.assertEqual(history, ch)
        library = Path(__file__).parents[1]/'build/dependency_partial.so'
        if library.exists():
            native, nh = minimize_leximax(flows, initial, net, native_bound=library)
            self.assertEqual(final, native)
            self.assertEqual(history, nh)
        for before, after in zip(history, history[1:]):
            self.assertLess(after['score_key'], before['score_key'])
        costs = isolated_costs(flows, net)
        moves, _ = proposals(flows, final, net, positive_only=False)
        for m in moves:
            changed = exchange(final, m['a'], m['b'], m['cycle'])
            exact = score_key(resource_proxy(flows, changed, net), dependency_bound(changed, costs))
            self.assertGreaterEqual(exact, history[-1]['score_key'])

    def test_empty_and_invalid_score(self):
        net, _ = self.problem(7)
        final, history = minimize_leximax([], initial_cover(range(8)), net)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['score_key'], (0, 0))
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                score_key(value, 1.)


if __name__ == '__main__':
    unittest.main()
