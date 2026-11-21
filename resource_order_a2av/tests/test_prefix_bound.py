import ctypes
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from resource_order.cover import (initial_cover, isolated_costs, dependency_bound,
                                  alternating_cycles, exchange, minimize_joint)
from resource_order.prefix_bound import prefix_evaluator
from resource_order.group_order import minimize_order
from resource_order.refinement import minimize_leximax
from resource_order.model import Message
from resource_order.topology import build_topology

LIBRARY = Path(__file__).parents[1]/'build/dependency_prefix.so'


@unittest.skipUnless(LIBRARY.exists(), 'optional prefix library not built')
class PrefixBoundTests(unittest.TestCase):
    def problem(self, n, seed, sparse):
        net = build_topology('tree', racks=2, servers_per_rack=1, gpus_per_server=n//2)
        rng = random.Random(seed)
        flows = [Message(s*n+d,s,d,rng.randrange(1,100000))
                 for s in range(n) for d in range(n) if s != d and (not sparse or rng.random()<.7)]
        return net, flows, initial_cover(range(n))

    def test_exact_all_cycles_cutoffs_and_sparse_prefixes(self):
        for n in (4,8,16):
            for sparse in (False,True):
                net,flows,cover = self.problem(n,91+n,sparse)
                costs = isolated_costs(flows,net)
                fast = prefix_evaluator(cover,costs,ctypes.CDLL(str(LIBRARY)))
                for a in range(n-1):
                    for b in range(a+1,n-1):
                        for cycle in alternating_cycles(cover[a],cover[b]):
                            changed = exchange(cover,a,b,cycle)
                            expected = dependency_bound(changed,costs)
                            for cutoff in (float('inf'),expected*.2,expected/2,expected,expected+1e-9):
                                self.assertEqual(fast(a,b,cycle,cutoff),
                                                 dependency_bound(changed,costs,cutoff=cutoff))
                fast.close()
                with self.assertRaises(ValueError): fast(0,1,[0])

    def test_partial_covers_empty_work_and_full_group_swap(self):
        covers = (((None,1,None),(2,None,0),(1,0,2)),
                  ((None,None,None),(None,None,None),(1,2,0)))
        for cover in covers:
            for costs in ({},{(s,d):(.7,.4,float(1+s+d)) for s in range(3) for d in range(3)}):
                fast = prefix_evaluator(cover,costs,ctypes.CDLL(str(LIBRARY)))
                for a,b in ((0,2),(1,2),(2,0),(1,1)):
                    for cycle in ([],[0],[1],[2],[0,1,2]):
                        changed = [list(row) for row in cover]
                        for src in cycle: changed[a][src],changed[b][src] = changed[b][src],changed[a][src]
                        for cutoff in (0.,1.,4.,float('inf')):
                            self.assertEqual(fast(a,b,cycle,cutoff),dependency_bound(changed,costs,cutoff=cutoff))
                fast.close()

    def test_optimizers_preserve_trajectories(self):
        net,flows,cover = self.problem(8,12,False)
        for module,optimizer,kwargs in (
            ('resource_order.cover',minimize_joint,dict(cache_pairs=True)),
            ('resource_order.refinement',minimize_leximax,{}),
            ('resource_order.group_order',minimize_order,{})):
            expected = optimizer(flows,cover,net,**kwargs)
            with patch(module+'.native_evaluator',prefix_evaluator):
                actual = optimizer(flows,cover,net,native_bound=LIBRARY,**kwargs)
            self.assertEqual(actual,expected)

    def test_invalid_inputs_and_context_lifetimes(self):
        lib = ctypes.CDLL(str(LIBRARY))
        for cover in ((),((0,1),(0,)),((0,3),(1,0))):
            with self.assertRaises(ValueError): prefix_evaluator(cover,{},lib)
        a = prefix_evaluator(initial_cover(range(4)),{(0,1):(1.,1.,2.)},lib)
        b = prefix_evaluator(initial_cover(range(4)),{},lib)
        self.assertEqual(b(0,1,[]),0.)
        self.assertGreater(a(0,1,[]),0.)
        with self.assertRaises(ValueError): a(-1,0,[])
        with self.assertRaises(ValueError): a(0,1,[4])
        b.close()
        self.assertGreater(a(0,1,[]),0.)
        a.close()

    def test_refinement_detects_prefix_abi_without_patching(self):
        net,flows,cover = self.problem(8,13,False)
        self.assertEqual(minimize_leximax(flows,cover,net,native_bound=LIBRARY),
                         minimize_leximax(flows,cover,net))


if __name__ == '__main__':
    unittest.main()
