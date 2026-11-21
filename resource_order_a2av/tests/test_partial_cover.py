import ctypes
from pathlib import Path
import random
import unittest

from resource_order.cover import (queue_cover,cover_plan,alternating_components,
    exchange,proposals,isolated_costs,dependency_bound,minimize_joint,native_evaluator)
from resource_order.model import Message,Plan
from resource_order.topology import build_topology


LIBRARY=Path(__file__).parents[1]/'build/dependency_partial.so'


class PartialCoverTests(unittest.TestCase):
    def sample(self,seed=71,n=8):
        rng=random.Random(seed)
        flows=[Message(s*n+d,s,d,rng.randrange(1,100000))
               for s in range(n) for d in range(n) if s!=d and rng.random()<.7]
        order=[f.id for f in flows];rng.shuffle(order)
        return flows,Plan.from_order(flows,order)

    def test_layers_preserve_exact_both_endpoint_queues(self):
        for seed in range(10):
            flows,plan=self.sample(seed)
            cover=queue_cover(plan,8)
            self.assertEqual(cover_plan(flows,cover,range(8)).dependency_key(),plan.dependency_key())
            for row in cover:
                values=[d for d in row if d is not None]
                self.assertEqual(len(values),len(set(values)))
        self.assertEqual(cover_plan([],queue_cover(Plan.from_order([],[]),8),range(8)).order,())

    def test_single_edge_and_whole_path_not_truncated(self):
        cover=((2,3,None,None),(3,None,None,None))
        self.assertEqual(alternating_components(*cover),[(0,1)])
        changed=exchange(cover,0,1,(0,1),partial=True)
        self.assertEqual(changed,((3,None,None,None),(2,3,None,None)))
        with self.assertRaises(ValueError):exchange(cover,0,1,(0,),partial=True)
        self.assertEqual(exchange(((1,None),(None,None)),0,1,(0,),partial=True),
                         ((None,None),(1,None)))
        with self.assertRaises(ValueError):alternating_components((1,None),(1,None))

    def test_random_exchanges_keep_each_pair_once_and_matching_valid(self):
        flows,plan=self.sample();c=queue_cover(plan,8);rng=random.Random(19)
        for _ in range(100):
            a,b=rng.sample(range(len(c)),2)
            parts=alternating_components(c[a],c[b])
            if not parts:continue
            c=exchange(c,a,b,rng.choice(parts),partial=True)
            after=cover_plan(flows,c,range(8))
            self.assertEqual(set(after.order),set(plan.order))
            for row in c:
                values=[d for d in row if d is not None]
                self.assertEqual(len(values),len(set(values)))

    def test_incremental_capacity_and_stationarity(self):
        flows,plan=self.sample(n=4)
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=2)
        c=queue_cover(plan,4);moves,before=proposals(flows,c,net,partial=True,positive_only=False)
        for m in moves:
            changed=exchange(c,m['a'],m['b'],m['cycle'],partial=True)
            _,after=proposals(flows,changed,net,partial=True,positive_only=False)
            self.assertAlmostEqual(before['proxy_us']-m['proxy_gain_us'],after['proxy_us'])
        final,h=minimize_joint(flows,c,net,partial=True,cache_pairs=True)
        plain,ph=minimize_joint(flows,c,net,partial=True)
        self.assertEqual(final,plain)
        self.assertEqual([r['move'] for r in h],[r['move'] for r in ph])
        costs=isolated_costs(flows,net)
        moves,info=proposals(flows,final,net,partial=True,positive_only=False)
        for m in moves:
            changed=exchange(final,m['a'],m['b'],m['cycle'],partial=True)
            score=max(info['proxy_us']-m['proxy_gain_us'],dependency_bound(changed,costs))
            self.assertGreaterEqual(score+1e-9,h[-1]['joint_us'])

    @unittest.skipUnless(LIBRARY.exists(),'partial native library not built')
    def test_native_partial_candidates_and_search_match_python(self):
        flows,plan=self.sample(n=6)
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=3)
        c=queue_cover(plan,6);costs=isolated_costs(flows,net)
        fast=native_evaluator(c,costs,ctypes.CDLL(str(LIBRARY)))
        for a in range(len(c)):
            for b in range(a+1,len(c)):
                for mask in alternating_components(c[a],c[b]):
                    changed=exchange(c,a,b,mask,partial=True)
                    expected=dependency_bound(changed,costs)
                    for cutoff in (float('inf'),expected/2,expected):
                        self.assertEqual(fast(a,b,mask,cutoff),dependency_bound(changed,costs,cutoff=cutoff))
        result,h=minimize_joint(flows,c,net,partial=True,cache_pairs=True)
        native,nh=minimize_joint(flows,c,net,partial=True,cache_pairs=True,native_bound=LIBRARY)
        self.assertEqual(result,native)
        self.assertEqual(h,nh)

    @unittest.skipUnless((LIBRARY.parent/'dependency_native.so').exists(),'legacy library absent')
    def test_legacy_native_rejects_partial_before_call(self):
        old=ctypes.CDLL(str(LIBRARY.parent/'dependency_native.so'))
        if hasattr(old,'cover_partial_abi'):self.skipTest('legacy library already updated')
        with self.assertRaisesRegex(ValueError,'does not support partial'):
            native_evaluator(((1,None),),{},old)


if __name__=='__main__':unittest.main()
