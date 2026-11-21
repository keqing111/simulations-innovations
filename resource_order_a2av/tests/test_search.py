import unittest
import random
import tempfile
from unittest.mock import patch

from resource_order.model import Message,Plan


class SearchTests(unittest.TestCase):
    def test_resource_promotion_requires_directed_shared_path_and_overlap(self):
        from resource_order.model import Network,Link
        from resource_order.search import resource_promotions,promote_message
        net=Network(4,[Link(0,4,100,0),Link(1,4,100,0),Link(4,5,10,0),Link(5,2,100,0),Link(5,3,100,0)],1)
        p=Plan.from_order([Message(0,0,2,100),Message(1,1,3,100)],[0,1])
        e={(0,'data_release'):0,(0,'data_ready'):10,(0,'credit_arrive'):0,
           (1,'data_release'):5,(1,'data_ready'):20,(1,'credit_arrive'):5}
        self.assertIn(('global',-1,0,1),resource_promotions(p,net,e))
        new=promote_message(p,'global',-1,0,1)
        self.assertEqual(new.order,(1,0))
        # Disjoint endpoint queues can make a global reorder a physical no-op;
        # optimize's dependency cache correctly avoids re-evaluating it.
        self.assertEqual(new.dependency_key(),p.dependency_key())
        e[1,'data_release']=11
        self.assertEqual(resource_promotions(p,net,e),[])

    def test_bilateral_promotion_can_change_a_cyclic_single_port_edit(self):
        from resource_order.search import promote_message,swap_adjacent,queues
        flows=[Message(0,0,2,10),Message(1,1,2,10),Message(2,1,3,10),Message(3,0,3,10)]
        p=Plan.from_order(flows,[0,1,2,3])
        self.assertIsNone(swap_adjacent(p,'send',0,0,3))
        new=promote_message(p,'send',0,0,3)
        self.assertEqual(new.order,(3,0,1,2))
        self.assertEqual(queues(new)['send'][1],[1,2])
        self.assertEqual(queues(new)['receive'][2],[0,1])
        self.assertEqual(new.messages,p.messages)

    def test_send_gap_requires_full_ack_not_only_data(self):
        from resource_order.search import send_gap_moves
        p=Plan.from_order([Message(0,0,1,10),Message(1,0,2,10)],[0,1])
        events={(0,'credit_arrive'):100.,(0,'data_release'):100.,
                (1,'credit_arrive'):10.,(1,'data_release'):120.,(1,'data_ready'):160.,(1,'ack_done'):170.}
        self.assertEqual(send_gap_moves(p,events),[('send',0,0,1)])
        events[1,'ack_done']=250.
        self.assertEqual(send_gap_moves(p,events),[])

    def test_one_port_exchange_preserves_all_other_orders(self):
        from resource_order.search import swap_adjacent,queues
        p=Plan.from_order([Message(0,0,2,10),Message(1,0,3,20),Message(2,1,3,30),Message(3,1,2,40)],[0,1,2,3])
        q=swap_adjacent(p,'receive',3,1,2)
        self.assertIsNotNone(q)
        before=queues(p);after=queues(q)
        self.assertEqual(after['receive'][3],[2,1])
        self.assertEqual(before['send'],after['send'])
        self.assertEqual(before['receive'][2],after['receive'][2])
        self.assertEqual(p.messages,q.messages)

    def test_cyclic_exchange_is_rejected_not_silently_repaired(self):
        from resource_order.search import swap_adjacent
        p=Plan.from_order([Message(0,0,2,10),Message(1,0,3,20),Message(2,1,3,30),Message(3,1,2,40)],[0,1,2,3])
        self.assertIsNone(swap_adjacent(p,'receive',2,0,3))
        with self.assertRaises(ValueError):swap_adjacent(p,'send',0,1,0)

    def test_critical_candidates_follow_observed_start_dependency(self):
        from resource_order.search import critical_moves
        p=Plan.from_order([Message(0,0,2,10),Message(1,1,3,20),Message(2,0,3,30)],[0,1,2])
        event={(0,'data_ready'):20,(0,'ack_done'):22,(0,'credit_arrive'):1,
               (1,'data_ready'):9,(1,'ack_done'):10,(1,'credit_arrive'):1,
               (2,'data_ready'):40,(2,'ack_done'):42,(2,'credit_arrive'):12}
        self.assertEqual(critical_moves(p,event),[('send',0,0,2)])

    def test_gap_insertion_keeps_send_order_and_fills_only_predicted_window(self):
        from resource_order.search import insert_before,gap_moves,queues
        from resource_order.topology import build_topology
        n=build_topology('leaf-spine',racks=2,servers_per_rack=1,gpus_per_server=2)
        p=Plan.from_order([Message(0,0,2,100),Message(1,1,2,100),Message(2,0,3,100)],[2,0,1])
        ev={(2,'ack_done'):100000,(2,'data_ready'):97000,(2,'data_release'):0,(2,'credit_arrive'):0,
            (0,'credit_send'):0,(0,'credit_arrive'):3000,(0,'data_release'):100000,(0,'data_ready'):110000,(0,'ack_done'):113000,
            (1,'credit_send'):110000,(1,'credit_arrive'):113000,(1,'data_release'):113000,(1,'data_ready'):123000,(1,'ack_done'):126000}
        self.assertEqual(gap_moves(p,n,ev),[('receive',2,0,1)])
        q=insert_before(p,'receive',2,0,1)
        self.assertEqual(queues(q)['receive'][2],[1,0]);self.assertEqual(queues(q)['send'],queues(p)['send'])
        ev[1,'data_ready']=313000
        self.assertEqual(gap_moves(p,n,ev),[])

    def test_random_queue_moves_preserve_unmodified_ports(self):
        from resource_order.search import swap_adjacent,queues
        flows=[Message(i*4+j-(j>i),i,j,100+i+j) for i in range(5) for j in range(5) if i!=j]
        order=[f.id for f in flows];random.Random(7).shuffle(order);p=Plan.from_order(flows,order)
        before=queues(p);valid=0
        for role,ports in before.items():
            for rank,queue in ports.items():
                for a,b in zip(queue,queue[1:]):
                    changed=swap_adjacent(p,role,rank,a,b)
                    if changed is None:continue
                    valid+=1;after=queues(changed)
                    for r,all_ports in before.items():
                        for port,ids in all_ports.items():
                            expected=list(ids)
                            if (r,port)==(role,rank):
                                at=expected.index(a);expected[at:at+2]=[b,a]
                            self.assertEqual(after[r][port],expected)
        self.assertGreater(valid,0);self.assertEqual(queues(p),before)

    def test_search_accepts_only_strict_improvement_and_retains_seed_on_failure(self):
        from resource_order.search import optimize
        p=Plan.from_order([Message(0,0,1,10),Message(1,0,2,20)],[0,1])
        # Mock objective tests control flow only; these are not performance data.
        for outcome in (90,100,None):
            def objective(plan,*args,**kwargs):
                value=100 if plan.order==(0,1) else outcome
                return dict(status='success',delivery_ns=value) if value is not None else dict(status='failed')
            with tempfile.TemporaryDirectory() as tmp,patch('resource_order.search.run',side_effect=objective),\
                 patch('resource_order.search.read_events',return_value={}),\
                 patch('resource_order.search.critical_moves',side_effect=lambda p,e:[('send',0,*p.order)]):
                result=optimize(p,None,tmp+'/search','unused',max_evaluations=8)
                self.assertEqual(result['best_delivery_ns'],90 if outcome==90 else 100)
                self.assertEqual(len(result['accepted']),1 if outcome==90 else 0)
                self.assertEqual(len(result['evaluations']),2)
                if outcome is None:self.assertEqual(result['stop_reason'],'incomplete_neighborhood_due_to_failures')


if __name__=='__main__':unittest.main()
