import unittest
from resource_order.model import Message
from resource_order.topology import build_topology
from resource_order.readiness import fair_rates,construct_ready,sharing_finish_times
from resource_order.experiments import demand


class ReadinessTests(unittest.TestCase):
    def test_small_message_finishes_before_large_one_on_same_link(self):
        times=sharing_finish_times({0,1},{0:1.,1:100.},{0:('a',),1:('a',)},dict(a=1.),{0:0.,1:0.})
        self.assertEqual(times,{0:2.,1:101.})

    def test_filling_reclaims_capacity_of_other_bottleneck(self):
        rates=fair_rates({0:('a','b'),1:('a',),2:('c',)},dict(a=100.,b=10.,c=200.))
        self.assertEqual(rates,{0:10.,1:90.,2:200.})

    def test_all_path_constraints(self):
        paths={0:('a','b'),1:('b','c'),2:('a','c'),3:('a',)}
        caps=dict(a=200.,b=80.,c=100.);rates=fair_rates(paths,caps)
        for e,cap in caps.items():
            self.assertLessEqual(sum(rates[i] for i in paths if e in paths[i]),cap+1e-9)

    def test_receiver_is_not_reserved_for_unready_sender(self):
        net=build_topology('leaf-spine',racks=2,servers_per_rack=2,gpus_per_server=2)
        flows=[Message(0,0,2,2*1024*1024),Message(1,0,4,128*1024),Message(2,6,4,128*1024)]
        plan,info=construct_ready(flows,net)
        self.assertLess(plan.order.index(2),plan.order.index(1))
        self.assertLess(info['events'][2]['start_us'],info['events'][0]['data_us'])
        self.assertEqual(plan.messages,tuple(flows))

    def test_dense_and_empty_and_deterministic(self):
        net=build_topology('leaf-spine',racks=2,servers_per_rack=1,gpus_per_server=2)
        flows=demand(4,4096,'zipf',17)
        for shared in (True,False):
            plan,info=construct_ready(flows,net,shared=shared)
            self.assertEqual(len(plan.order),12)
            self.assertEqual((plan,info),construct_ready(flows,net,shared=shared))
            for i in plan.order:
                event=info['events'][i]
                for pred,key in ((plan.send_after[i],'ack_us'),(plan.receive_after[i],'data_us')):
                    if pred is not None:self.assertGreaterEqual(event['reserve_us']+1e-8,info['events'][pred][key])
        self.assertEqual(construct_ready([],net)[0].order,())

    def test_tail_mode_preserves_release_order_and_whole_messages(self):
        net=build_topology('leaf-spine',racks=2,servers_per_rack=1,gpus_per_server=2)
        flows=demand(4,4096,'zipf',19)
        plan,info=construct_ready(flows,net,objective='tail')
        self.assertEqual(plan.messages,tuple(flows))
        for i in plan.order:
            for pred,key in ((plan.send_after[i],'ack_us'),(plan.receive_after[i],'data_us')):
                if pred is not None:self.assertGreaterEqual(info['events'][i]['reserve_us']+1e-8,info['events'][pred][key])
