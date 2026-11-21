import random
import unittest
from pathlib import Path

from resource_order.cover import initial_cover, cover_plan, dependency_bound, isolated_costs
from resource_order.group_order import service_costs, minimize_order, swap_groups
from resource_order.model import Message
from resource_order.predict import evaluate_cohorts
from resource_order.refinement import resource_proxy
from resource_order.topology import build_topology


class GroupOrderTests(unittest.TestCase):
    def problem(self, seed=7):
        net = build_topology('tree', racks=2, servers_per_rack=1, gpus_per_server=4)
        rng = random.Random(seed)
        flows = [Message(s*8+d, s, d, rng.randrange(1000, 100000))
                 for s in range(8) for d in range(8) if s != d]
        return net, flows, initial_cover(range(8))

    def test_full_groups_match_existing_cohort_reference(self):
        net, flows, cover = self.problem()
        for order in (cover, swap_groups(cover, 0, 6)):
            costs = service_costs(flows, order, net)
            reference = evaluate_cohorts(cover_plan(flows, order, range(8)), net)
            self.assertEqual(dependency_bound(order, costs)*1000, reference['predicted_ns'])
        self.assertEqual(service_costs(flows, cover, net, shared=False), isolated_costs(flows, net))

    def test_pair_service_unchanged_after_group_reordering(self):
        net, flows, cover = self.problem()
        costs = service_costs(flows, cover, net)
        for a in range(len(cover)):
            for b in range(a+1, len(cover)):
                changed = swap_groups(cover, a, b)
                self.assertEqual(costs, service_costs(flows, changed, net))
                self.assertAlmostEqual(resource_proxy(flows, cover, net),
                                       resource_proxy(flows, changed, net), places=10)

    def test_descent_stationarity_and_native_equivalence(self):
        net, flows, cover = self.problem()
        final, history = minimize_order(flows, cover, net)
        self.assertEqual(sorted(final), sorted(cover))
        self.assertGreater(len(history), 1)
        for before, after in zip(history, history[1:]):
            self.assertLess(after['coupled_us'], before['coupled_us'])
        costs = service_costs(flows, cover, net)
        for a in range(len(final)):
            for b in range(a+1, len(final)):
                self.assertGreaterEqual(dependency_bound(swap_groups(final, a, b), costs)+1e-9,
                                        history[-1]['coupled_us'])
        library = Path(__file__).parents[1]/'build/dependency_partial.so'
        if library.exists():
            accelerated, ah = minimize_order(flows, cover, net, native_bound=library)
            self.assertEqual((accelerated, ah), (final, history))

    def test_shared_single_link_accounts_for_short_completion(self):
        net, _, cover = self.problem()
        # These disjoint endpoints traverse the same server NIC and uplink.
        flows = [Message(0, 0, 4, 1000), Message(1, 1, 5, 3000)]
        costs = service_costs(flows, cover, net)
        shared = set(net.path(0, 4)) & set(net.path(1, 5))
        self.assertTrue(shared)
        cap = min(net.links[e].gbps for e in shared)
        self.assertEqual(costs[0, 4][2], 2000*.008/cap)
        self.assertEqual(costs[1, 5][2], 4000*.008/cap)


if __name__ == '__main__':
    unittest.main()
