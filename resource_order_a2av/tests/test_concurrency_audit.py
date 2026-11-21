"""Synthetic event audit tests, not communication-performance measurements."""
import unittest
from resource_order.model import Message,Plan
from integration.ns3.replay import audit

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.plan=Plan.from_order([Message(0,0,1,10),Message(1,0,2,20)],[0,1])
        self.events=[(0,'begin',-1),(0,'credit_send',0),(0,'credit_send',1),
            (1,'credit_arrive',0),(1,'credit_arrive',1),(1,'data_release',0),
            (1,'data_release',1),(2,'data_ready',1),(3,'ack_done',1),
            (10,'data_ready',0),(11,'ack_done',0),(11,'finished',-1)]

    def rows(self):return [dict(time_ns=t,event=e,flow_id=i) for t,e,i in self.events]

    def test_out_of_order_completion_is_valid(self):
        metrics=audit(self.plan,{0:0,1:1},self.rows(),2)
        self.assertEqual(metrics['peak_send_slots'],2)
        self.assertEqual(metrics['delivery_ns'],10)
        self.assertEqual(metrics['payload_bytes'],30)

    def test_send_limit_is_enforced(self):
        with self.assertRaisesRegex(ValueError,'send capacity'):audit(self.plan,{0:0,1:1},self.rows(),1)

    def test_fifo_is_enforced(self):
        self.events[5],self.events[6]=self.events[6],self.events[5]
        with self.assertRaisesRegex(ValueError,'FIFO'):audit(self.plan,{0:0,1:1},self.rows(),2)

    def test_missing_ack_is_rejected(self):
        self.events.pop(8)
        with self.assertRaises((ValueError,KeyError)):audit(self.plan,{0:0,1:1},self.rows(),2)

    def test_receive_reservations_are_counted_before_data(self):
        self.plan=Plan.from_order([Message(0,0,2,10),Message(1,1,2,20)],[0,1])
        with self.assertRaisesRegex(ValueError,'receive reservation'):audit(self.plan,{0:0,1:1},self.rows(),1)

if __name__=='__main__':unittest.main()
