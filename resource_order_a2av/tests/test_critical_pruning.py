import ctypes
from pathlib import Path
import random
import unittest

from resource_order.benchmark import network_for
from resource_order.cover import initial_cover, isolated_costs, dependency_bound, proposals, exchange
from resource_order.model import Message
from resource_order.refinement import (critical_path, roundoff_floor, capacity_floor,
                                      resource_proxy, minimize_leximax)
from resource_order.topology import build_topology


class CriticalPruningTests(unittest.TestCase):
    def problem(self, n, seed, sparse=False):
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=n//2)
        rng=random.Random(seed)
        flows=[Message(s*n+d,s,d,rng.randrange(1,100000))
               for s in range(n) for d in range(n) if s!=d and (not sparse or rng.random()<.5)]
        return net,flows,initial_cover(range(n))

    def test_untouched_path_bounds_all_cycle_neighbors(self):
        untouched=0
        for n in (4,8,16):
            for sparse in (False,True):
                net,flows,cover=self.problem(n,73,sparse)
                costs=isolated_costs(flows,net)
                value,path=critical_path(cover,costs)
                self.assertEqual(value,dependency_bound(cover,costs))
                moves,_=proposals(flows,cover,net,positive_only=False)
                for m in moves:
                    if any((s,cover[m['a']][s]) in path or (s,cover[m['b']][s]) in path for s in m['cycle']):continue
                    untouched+=1
                    changed=exchange(cover,m['a'],m['b'],m['cycle'])
                    self.assertGreaterEqual(dependency_bound(changed,costs),roundoff_floor(value,4*len(cover)+4))
        self.assertGreater(untouched,0)

    def test_all_decisions_match_unpruned_search(self):
        normalize=lambda h:[{k:v for k,v in r.items() if k not in ('critical_pruned','dependency_evaluations')}
                            for r in h]
        for seed in range(6):
            net,flows,cover=self.problem(8,seed)
            final,h=minimize_leximax(flows,cover,net)
            changed,ch=minimize_leximax(flows,cover,net,critical_pruning=True)
            self.assertEqual(final,changed)
            self.assertEqual(normalize(h),normalize(ch))
            library=Path(__file__).parents[1]/'build/dependency_prefix.so'
            if library.exists():
                fast,fh=minimize_leximax(flows,cover,net,critical_pruning=True,native_bound=library)
                self.assertEqual((changed,ch),(fast,fh))

    def test_capacity_floor_is_below_every_neighbor_proxy(self):
        net,flows,cover=self.problem(8,19)
        work={(f.src,f.dst):tuple((e,f.bytes*.008/net.links[e].gbps) for e in net.path(f.src,f.dst)) for f in flows}
        floor=capacity_floor(work,8,len(cover))
        moves,_=proposals(flows,cover,net,positive_only=False)
        for m in moves:
            self.assertLessEqual(floor,resource_proxy(flows,exchange(cover,m['a'],m['b'],m['cycle']),net))
        self.assertEqual(critical_path(cover,{}),(0.,set()))


if __name__=='__main__':unittest.main()
