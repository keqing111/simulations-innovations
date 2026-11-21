import random
import unittest
from resource_order.model import Message,Plan
from resource_order.topology import build_topology
from resource_order.cover import initial_cover,cover_plan,alternating_cycles,exchange,proposals,minimize_proxy,minimize_joint,dependency_bound,isolated_costs
from resource_order.cover import minimize_proxy_cached
from resource_order.diagnostics import dependency_depth


class CoverTests(unittest.TestCase):
    def test_receiver_work_floor_and_single_hot_receiver_equality(self):
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=3)
        rng=random.Random(29)
        dense=[Message(s*6+d,s,d,rng.randrange(1,100000)) for s in range(6) for d in range(6) if s!=d]
        for flows,equals in ((dense,False),([f for f in dense if f.dst==0],True)):
            costs=isolated_costs(flows,net)
            floor=max(sum(sum(v) for (s,d),v in costs.items() if d==receiver) for receiver in range(6))
            c=initial_cover(range(6))
            for _ in range(30):
                value=dependency_bound(c,costs)
                self.assertGreaterEqual(value+1e-9,floor)
                if equals:self.assertAlmostEqual(value,floor)
                a,b=rng.sample(range(5),2);c=exchange(c,a,b,rng.choice(alternating_cycles(c[a],c[b])))

    def test_exchanges_preserve_every_pair_and_bounded_dependency_depth(self):
        for n in (4,6,8,16):
            ranks=list(range(n));random.Random(7).shuffle(ranks);cover=initial_cover(ranks)
            flows=[Message(s*n+d,s,d,1+s+d) for s in range(n) for d in range(n) if s!=d]
            rng=random.Random(11)
            for _ in range(20):
                a,b=rng.sample(range(n-1),2);cycles=alternating_cycles(cover[a],cover[b]);cover=exchange(cover,a,b,rng.choice(cycles))
                for row in cover:self.assertEqual(set(row),set(range(n)))
                for s in range(n):self.assertEqual(sorted(row[s] for row in cover),[d for d in range(n) if d!=s])
                plan=cover_plan(flows,cover,ranks)
                self.assertEqual(len(plan.order),n*(n-1))
                self.assertLessEqual(max(dependency_depth(plan).values()),n-1)

    def test_resource_example_has_a_positive_valid_exchange_without_splitting(self):
        net=build_topology('tree',racks=4,servers_per_rack=1,gpus_per_server=2,
                           overrides={(12,16):(100,.7),(13,16):(100,.7)})
        ranks=(0,1,2,3,4,6,5,7);cover=initial_cover(ranks)
        flows=[Message(0,0,4,1048576),Message(1,1,6,1048576),Message(2,2,4,1048576),Message(3,3,6,1048576)]
        moves,info=proposals(flows,cover,net);self.assertTrue(moves)
        m=moves[0];new=exchange(cover,m['a'],m['b'],m['cycle']);plan=cover_plan(flows,new,ranks)
        self.assertEqual(plan.messages,tuple(flows));self.assertEqual(len(plan.order),4)
        _,after=proposals(flows,new,net)
        self.assertAlmostEqual(info['proxy_us']-after['proxy_us'],m['proxy_gain_us'])
        self.assertLess(after['proxy_us'],.51*info['proxy_us'])
        final,history=minimize_proxy(flows,cover,net)
        self.assertIsNone(history[-1]['move'])
        self.assertLess(history[-1]['proxy_us'],history[0]['proxy_us'])
        self.assertEqual(cover_plan(flows,final,ranks).messages,tuple(flows))

    def test_partial_cycle_is_rejected(self):
        c=initial_cover(range(8))
        with self.assertRaises(ValueError):exchange(c,0,2,[0])

    def test_incremental_gain_matches_independent_full_load_recomputation(self):
        net=build_topology('tree',racks=4,servers_per_rack=1,gpus_per_server=2)
        rng=random.Random(83);cover=initial_cover(range(8))
        flows=[Message(s*8+d,s,d,rng.randrange(1,100000))
               for s in range(8) for d in range(8) if s!=d and rng.random()<.7]
        by_pair={(f.src,f.dst):f for f in flows}
        def full(c):
            return sum(max(net.work_us([by_pair[s,d] for s,d in enumerate(row)
                                       if (s,d) in by_pair]).values(),default=0.) for row in c)
        for _ in range(5):
            moves,info=proposals(flows,cover,net)
            self.assertAlmostEqual(info['proxy_us'],full(cover))
            for m in moves:
                changed=exchange(cover,m['a'],m['b'],m['cycle'])
                self.assertAlmostEqual(m['proxy_gain_us'],full(cover)-full(changed))
            if not moves:break
            m=moves[0];cover=exchange(cover,m['a'],m['b'],m['cycle'])

    def test_empty_matrix_emits_no_dummy_messages(self):
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=2)
        final,h=minimize_proxy([],initial_cover(range(4)),net)
        self.assertEqual(h[0]['proxy_us'],0)
        self.assertEqual(cover_plan([],final,range(4)).order,())

    def test_dependency_bound_agrees_with_independent_isolated_event_replay(self):
        from resource_order.predict import evaluate
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=2)
        flows=[Message(s*4+d,s,d,3333*(s+d+1)) for s in range(4) for d in range(4) if s!=d]
        c=initial_cover(range(4));costs=isolated_costs(flows,net)
        value=dependency_bound(c,costs)
        actual=evaluate(cover_plan(flows,c,range(4)),net,shared=False)['predicted_ns']/1000
        self.assertAlmostEqual(value,actual)
        self.assertGreaterEqual(dependency_bound(c,costs,cutoff=value/2),value/2)
        self.assertLessEqual(dependency_bound(c,costs,cutoff=value/2),value)

    def test_joint_terminates_with_no_strictly_improving_exchange(self):
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=4)
        rng=random.Random(7)
        flows=[Message(s*8+d,s,d,rng.randrange(1000,100000)) for s in range(8) for d in range(8) if s!=d]
        c,h=minimize_joint(flows,initial_cover(range(8)),net)
        cc,ch=minimize_joint(flows,initial_cover(range(8)),net,cache_pairs=True)
        self.assertEqual(c,cc)
        self.assertEqual([r['move'] for r in h],[r['move'] for r in ch])
        self.assertEqual([r['joint_us'] for r in h],[r['joint_us'] for r in ch])
        self.assertIsNone(h[-1]['move']);costs=isolated_costs(flows,net)
        for a,b in zip(h,h[1:]):self.assertLess(b['joint_us'],a['joint_us'])
        moves,info=proposals(flows,c,net,positive_only=False)
        for m in moves:
            changed=exchange(c,m['a'],m['b'],m['cycle'])
            score=max(info['proxy_us']-m['proxy_gain_us'],dependency_bound(changed,costs))
            self.assertGreaterEqual(score+1e-8,h[-1]['joint_us'])

    def test_cache_preserves_complete_uncached_search_trajectory(self):
        net=build_topology('tree',racks=2,servers_per_rack=1,gpus_per_server=4)
        for seed in range(5):
            rng=random.Random(seed)
            flows=[Message(s*8+d,s,d,rng.randrange(100,100000)) for s in range(8) for d in range(8) if s!=d and rng.random()<.8]
            c=initial_cover(range(8));final,h=minimize_proxy(flows,c,net)
            cached,ch=minimize_proxy_cached(flows,c,net)
            self.assertEqual(final,cached)
            self.assertEqual([r['move'] for r in h],[r['move'] for r in ch])
            self.assertEqual([r['proxy_us'] for r in h],[r['proxy_us'] for r in ch])
