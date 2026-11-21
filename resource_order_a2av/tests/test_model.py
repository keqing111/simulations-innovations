import importlib.util
import unittest


class ModelTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('resource_order.model'), 'new model missing')
        from resource_order import model
        return model

    def test_order_creates_endpoint_dependencies_not_global_barrier(self):
        m=self.module();flows=[m.Message(0,0,1,100),m.Message(1,2,3,200),m.Message(2,0,3,300)]
        plan=m.Plan.from_order(flows,[0,1,2])
        self.assertEqual(plan.send_after,{0:None,1:None,2:0})
        self.assertEqual(plan.receive_after,{0:None,1:None,2:1})
        self.assertEqual(sum(f.bytes for f in plan.messages),600)
        gate=m.EndpointGate(plan)
        self.assertEqual(gate.ready(),[0,1]);gate.release(0);gate.release(1)
        gate.received(0);gate.acknowledged(0)
        self.assertEqual(gate.ready(),[])
        gate.received(1)
        self.assertEqual(gate.ready(),[2])
        gate.release(2);gate.received(2);gate.acknowledged(2)
        self.assertFalse(gate.finished());gate.acknowledged(1);self.assertTrue(gate.finished())

    def test_gpu_can_send_and_receive_simultaneously(self):
        m=self.module();p=m.Plan.from_order([m.Message(0,0,1,100),m.Message(1,1,0,100)],[0,1])
        self.assertEqual(m.EndpointGate(p).ready(),[0,1])

    def test_invalid_order_and_duplicate_pair_are_rejected(self):
        m=self.module();a=m.Message(0,0,1,100)
        for flows,order in [([a],[0,0]),([a],[]),([a,m.Message(1,0,1,5)],[0,1])]:
            with self.assertRaises(ValueError):m.Plan.from_order(flows,order)
        for size in [0,-1,1.5,True]:
            with self.assertRaises(ValueError):m.Message(0,0,1,size)
        for ident in [False,0.0]:
            with self.assertRaises(ValueError):m.Plan.from_order([a],[ident])

    def test_gate_rejects_ack_before_data_and_double_release(self):
        m=self.module();p=m.Plan.from_order([m.Message(0,0,1,1)],[0]);g=m.EndpointGate(p)
        with self.assertRaises(ValueError):g.received(0)
        g.release(0)
        with self.assertRaises(ValueError):g.release(0)
        with self.assertRaises(ValueError):g.acknowledged(0)

    def test_shared_resource_work_and_fixed_path(self):
        m=self.module()
        n=m.Network(4,[m.Link(0,4,100,1),m.Link(2,4,100,1),m.Link(4,5,10,2),
                       m.Link(5,1,100,1),m.Link(5,3,100,1)],2)
        flows=[m.Message(0,0,1,1250),m.Message(1,2,3,2500)]
        resources=n.resources(flows)
        self.assertEqual(resources[4,5],(0,1))
        self.assertAlmostEqual(n.work_us(flows)[4,5],3.)
        self.assertAlmostEqual(n.delay_us(0,1),4.)
        self.assertEqual(n.path(0,1),((0,4),(4,5),(5,1)))


if __name__=='__main__':unittest.main()
