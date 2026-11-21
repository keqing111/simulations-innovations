import ctypes
from pathlib import Path
import random
import unittest

from resource_order.cover import (initial_cover,isolated_costs,dependency_bound,
    alternating_cycles,exchange,native_evaluator,minimize_joint)
from resource_order.model import Message
from resource_order.topology import build_topology


LIBRARY=Path(__file__).parents[1]/'build/dependency_native.so'


@unittest.skipUnless(LIBRARY.exists(),'optional native library not built')
class NativeBoundTests(unittest.TestCase):
    def test_all_cycles_and_cutoffs_match_python_exactly(self):
        for n in (4,8,16):
            net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=n//2)
            rng=random.Random(91+n)
            flows=[Message(s*n+d,s,d,rng.randrange(1,1000000))
                   for s in range(n) for d in range(n) if s!=d and rng.random()<.7]
            cover=initial_cover(range(n));costs=isolated_costs(flows,net)
            fast=native_evaluator(cover,costs,ctypes.CDLL(str(LIBRARY)))
            for a in range(n-1):
                for b in range(a+1,n-1):
                    for cycle in alternating_cycles(cover[a],cover[b]):
                        changed=exchange(cover,a,b,cycle);expected=dependency_bound(changed,costs)
                        for cutoff in (float('inf'),expected/2,expected,expected+1e-9):
                            self.assertEqual(fast(a,b,cycle,cutoff),dependency_bound(changed,costs,cutoff=cutoff))

    def test_optimizer_trajectory_is_unchanged(self):
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=4)
        for seed in range(3):
            rng=random.Random(seed)
            flows=[Message(s*8+d,s,d,rng.randrange(100,100000))
                   for s in range(8) for d in range(8) if s!=d]
            cover=initial_cover(range(8))
            c,h=minimize_joint(flows,cover,net,cache_pairs=True)
            nc,nh=minimize_joint(flows,cover,net,cache_pairs=True,native_bound=LIBRARY)
            self.assertEqual(c,nc)
            for key in ['move','joint_us','dependency_evaluations','dependency_bound_us']:
                self.assertEqual([r[key] for r in h],[r[key] for r in nh])

    def test_bad_exchange_rejected_before_native_call(self):
        cover=initial_cover(range(4));fast=native_evaluator(cover,{},ctypes.CDLL(str(LIBRARY)))
        with self.assertRaises(ValueError):fast(-1,0,[0])
        with self.assertRaises(ValueError):fast(0,1,[4])

    def test_dependency_ablation_keeps_its_own_objective_and_converges(self):
        from resource_order.cover import proposals
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=4)
        rng=random.Random(51)
        flows=[Message(s*8+d,s,d,rng.randrange(1,100000)) for s in range(8) for d in range(8) if s!=d]
        c,h=minimize_joint(flows,initial_cover(range(8)),net,cache_pairs=True,
                           native_bound=LIBRARY,dependency_only=True)
        for a,b in zip(h,h[1:]):self.assertLess(b['objective_us'],a['objective_us'])
        self.assertEqual(h[-1]['objective_us'],h[-1]['dependency_bound_us'])
        costs=isolated_costs(flows,net);moves,_=proposals(flows,c,net,positive_only=False)
        for m in moves:
            changed=exchange(c,m['a'],m['b'],m['cycle'])
            self.assertGreaterEqual(dependency_bound(changed,costs)+1e-9,h[-1]['objective_us'])


if __name__=='__main__':unittest.main()
