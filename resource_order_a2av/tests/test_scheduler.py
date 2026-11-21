import importlib.util
import random
import unittest
from resource_order.model import Link,Message,Network,EndpointGate


class SchedulerTests(unittest.TestCase):
    def test_physical_interleaving_changes_only_virtual_rank_order(self):
        from resource_order.topology import build_topology
        m=self.module();net=build_topology('leaf-spine',racks=2,servers_per_rack=2,gpus_per_server=2)
        order=m.interleaved_ranks(net)
        self.assertEqual(order,(0,4,2,6,1,5,3,7))
        flows=[Message(s*8+d,s,d,100) for s in range(8) for d in range(8) if s!=d]
        p=m.baseline(flows,'spreadout',8,rank_order=order)
        self.assertEqual(set(p.messages),set(flows));self.assertEqual(len(p.order),56)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('resource_order.scheduler'),'new scheduler missing')
        from resource_order import scheduler
        return scheduler

    def network(self):
        return Network(6,[Link(0,6,100,0),Link(6,7,1,0),Link(7,1,100,0),
                          Link(2,8,100,0),Link(8,9,1,0),Link(9,3,100,0),
                          Link(9,4,100,0),Link(9,5,100,0),Link(7,8,100,0)],1)

    def test_soft_alignment_selects_comparable_independent_message(self):
        m=self.module();flows=[Message(0,0,1,1000),Message(1,2,3,900),Message(2,2,4,100)]
        plan=m.construct(flows,self.network())
        self.assertEqual(plan.order,(0,1,2))
        self.assertEqual(plan.send_after,{0:None,1:None,2:1})
        gate=EndpointGate(plan);gate.release(0);gate.release(1)
        gate.received(1);gate.acknowledged(1)
        self.assertEqual(gate.ready(),[2])  # long anchor has NOT completed

    def test_shared_cost_aggregates_real_bytes(self):
        m=self.module();net=self.network()
        flows=[Message(0,2,3,125),Message(1,4,5,125)]
        # Both traverse directed9->5 only if different source route; use explicit fixture.
        net=Network(4,[Link(0,4,100,0),Link(2,4,100,0),Link(4,5,1,0),
                       Link(5,1,100,0),Link(5,3,100,0)],1)
        flows=[Message(0,0,1,125),Message(1,2,3,125)]
        self.assertEqual(m.completion_estimates(flows,net,shared=True),{0:2.,1:2.})
        self.assertEqual(m.completion_estimates(flows,net,shared=False),{0:1.,1:1.})

    def test_baselines_keep_demand_and_have_distinct_orders(self):
        m=self.module();flows=[Message(0,0,2,10),Message(1,0,1,30),Message(2,2,3,20)]
        self.assertEqual(m.baseline(flows,'spreadout',4).order,(1,2,0))
        self.assertEqual(m.baseline(flows,'small-first',4).order,(0,2,1))
        self.assertEqual(m.baseline(flows,'large-first',4).order,(1,2,0))
        with self.assertRaises(ValueError):m.baseline(flows,'unknown',4)

    def test_dense_plan_all_messages_whole_and_gate_eventually_finishes(self):
        m=self.module();net=self.network()
        flows=[Message(s*6+d,s,d,10+(s+2*d)%7) for s in range(6) for d in range(6) if s!=d]
        for alignment,shared in [(True,True),(False,True),(True,False)]:
            with self.subTest(alignment=alignment,shared=shared):
                p=m.construct(flows,net,alignment=alignment,shared=shared)
                self.assertEqual(set(p.messages),set(flows));self.assertEqual(len(p.order),30)
                gate=EndpointGate(p);count=0
                while not gate.finished():
                    ready=gate.ready();self.assertTrue(ready)
                    for ident in reversed(ready):
                        gate.release(ident);gate.received(ident);gate.acknowledged(ident);count+=1
                self.assertEqual(count,30)

    def test_empty_and_invalid_input(self):
        m=self.module();net=self.network()
        self.assertEqual(m.construct([],net).order,())
        with self.assertRaises(ValueError):m.construct([Message(0,0,10,10)],net)

    def test_isolated_cache_matches_original_recompute_policy(self):
        m=self.module();net=self.network()
        def reference(flows,alignment,anchor_shared):
            pending={f.id:f for f in flows};order=[]
            solo=m.completion_estimates(flows,net,shared=False)
            while pending:
                work=net.work_us(pending.values())
                heavy=min(work,key=lambda e:(-work[e],e))
                anchors=[f for f in pending.values() if not anchor_shared or heavy in net.path(f.src,f.dst)]
                a=min(anchors,key=lambda f:(-solo[f.id],f.id));group=[a];span=solo[a.id]
                while True:
                    send={f.src for f in group};recv={f.dst for f in group}
                    candidates=[f for f in pending.values() if f.src not in send and f.dst not in recv]
                    if not candidates:break
                    def key(f):
                        values=list(m.completion_estimates(group+[f],net,shared=False).values())
                        top=max(values)
                        return top-span,top-min(values) if alignment else 0.,0.,f.id
                    selected=min(candidates,key=key);group.append(selected)
                    span=max(m.completion_estimates(group,net,shared=False).values())
                order.extend(f.id for f in group)
                for f in group:del pending[f.id]
            return tuple(order)
        for seed in range(5):
            rng=random.Random(seed)
            flows=[Message(s*6+d,s,d,rng.choice([1,100,1000,3333]))
                   for s in range(6) for d in range(6) if s!=d and rng.random()<.8]
            for alignment in [False,True]:
                for anchor_shared in [False,True]:
                    p=m.construct(flows,net,alignment=alignment,anchor_shared=anchor_shared,fill_shared=False)
                    self.assertEqual(p.order,reference(flows,alignment,anchor_shared))


if __name__=='__main__':unittest.main()
