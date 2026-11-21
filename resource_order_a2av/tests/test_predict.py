import unittest
from resource_order.model import Link,Message,Network,Plan
from resource_order.predict import evaluate,evaluate_cohorts


class PredictorTests(unittest.TestCase):
    def test_cohort_single_bottleneck_and_queue_invariance(self):
        net=Network(4,[Link(0,4,8000.,0.),Link(1,4,8000.,0.),Link(4,5,8.,0.),Link(5,2,8000.,0.),Link(5,3,8000.,0.)],1)
        flows=[Message(0,0,2,1000),Message(1,1,3,100000)]
        a=Plan.from_order(flows,[0,1]);b=Plan.from_order(flows,[1,0])
        self.assertEqual(evaluate_cohorts(a,net),evaluate_cohorts(b,net))
        self.assertAlmostEqual(evaluate_cohorts(a,net)['predicted_ns'],101000.)
        self.assertEqual(evaluate_cohorts(Plan.from_order([],[]),net)['predicted_ns'],0.)

    def test_cohort_propagates_sender_ack_dependency(self):
        net=Network(3,[Link(0,3,8.,1.),Link(1,3,8.,1.),Link(2,3,8.,1.)],1)
        plan=Plan.from_order([Message(0,0,1,10000),Message(1,0,2,1000)],[0,1])
        self.assertEqual(evaluate_cohorts(plan,net)['predicted_ns'],19000.)

    def test_credit_is_preposted_while_sender_busy(self):
        net=Network(3,[Link(0,3,8.,1.),Link(1,3,8.,1.),Link(2,3,8.,1.)],1)
        plan=Plan.from_order([Message(0,0,1,10000),Message(1,0,2,1000)],[0,1])
        e=evaluate(plan,net)['events']
        self.assertEqual(e[1,'credit_send'],0.)
        self.assertEqual(e[1,'data_release'],e[0,'ack_done'])
        self.assertEqual(e[0,'data_ready'],14000.)
        self.assertEqual(e[1,'data_ready'],19000.)

    def test_one_link_equal_sharing_small_then_large(self):
        net=Network(4,[Link(0,4,8000.,0.),Link(1,4,8000.,0.),Link(4,5,8.,0.),Link(5,2,8000.,0.),Link(5,3,8000.,0.)],1)
        plan=Plan.from_order([Message(0,0,2,1000),Message(1,1,3,100000)],[0,1])
        e=evaluate(plan,net)['events']
        self.assertAlmostEqual(e[0,'data_ready'],2000.)
        self.assertAlmostEqual(e[1,'data_ready'],101000.)

    def test_empty(self):
        net=Network(2,[Link(0,1,8.,0.)],1)
        self.assertEqual(evaluate(Plan.from_order([],[]),net)['predicted_ns'],0.)
