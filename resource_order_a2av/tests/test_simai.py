import importlib.util
import tempfile
import unittest
from pathlib import Path

from resource_order.model import Message, Plan
from resource_order.topology import build_topology


class SimAITests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('resource_order.simai'), 'SimAI adapter missing')
        from resource_order import simai
        return simai

    def test_slots_preserve_only_send_and_receive_predecessors(self):
        s=self.module()
        # These GPU pairs include intra-server traffic; none may be removed.
        p=Plan.from_order([Message(8,0,1,100),Message(3,2,3,200),Message(9,0,3,300)],[8,3,9])
        rows,commands,mapping=s.encode(p,4)
        self.assertEqual(rows,'0,1,100,0\n2,3,200,0\n0,3,300,0\n')
        self.assertEqual(mapping,{0:8,1:3,2:9})
        lines=commands.splitlines()
        self.assertEqual(lines[0],'4,2,0,0')  # No barriers or round coupling.
        c={tuple(map(int,l.split(',')))[:2]:tuple(map(int,l.split(',')))[2:] for l in lines[1:]}
        self.assertEqual(c[0,0][0],0);self.assertEqual(c[2,0][0],1)
        self.assertEqual(c[0,1][0],2);self.assertEqual(c[3,1][1],2)
        self.assertEqual(c[0,0][2],0)  # Empty first receive: no dummy credit packet.

    def test_export_routes_match_planner_and_keep_local_messages(self):
        s=self.module();n=build_topology('leaf-spine',racks=2,servers_per_rack=1,gpus_per_server=2)
        p=Plan.from_order([Message(0,0,1,123),Message(1,0,3,456)],[0,1])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s.prepare(p,n,root)
            self.assertEqual(len((root/'schedule.csv').read_text().splitlines()),2)
            routes={ (int(a),int(d)):int(v) for a,d,v in
                    (l.split(',') for l in (root/'routes.csv').read_text().splitlines())}
            for src in range(n.gpu_count):
                for dst in range(n.gpu_count):
                    for u,v in n.path(src,dst):self.assertEqual(routes[u,dst],v)
            self.assertEqual((root/'topology.txt').read_text().splitlines()[0].split()[:4],['10','2','2','4'])

    def test_trace_validation_requires_data_ack_and_exact_dependencies(self):
        s=self.module();p=Plan.from_order([Message(0,0,1,1),Message(1,0,2,1)],[0,1])
        events=[(0,'begin',-1),(0,'credit_send',0),(1,'credit_arrive',0),(1,'data_release',0),
                (2,'data_ready',0),(3,'ack_done',0),(0,'credit_send',1),(1,'credit_arrive',1),
                (3,'data_release',1),(4,'data_ready',1),(5,'ack_done',1),(5,'finished',-1)]
        rows=[dict(time_ns=t,event=e,flow_id=i) for t,e,i in events]
        result=s.validate_events(p,{0:0,1:1},rows)
        self.assertEqual(result['delivery_ns'],4);self.assertEqual(result['ack_drain_ns'],5)
        with self.assertRaises(ValueError):s.validate_events(p,{0:0,1:1},rows[:-1])
        broken=[dict(row) for row in rows];broken[8]['time_ns']=2
        with self.assertRaises(ValueError):s.validate_events(p,{0:0,1:1},broken)

    def test_fct_uses_hex_ip_and_payload_bytes_not_protocol_trailer(self):
        s=self.module();p=Plan.from_order([Message(7,0,2,1048576)],[7])
        rows=['0b000001 0b000201 10000 100 1048576 3014 28324 27853 0']
        s.validate_fct(p,{0:7},rows)
        with self.assertRaises(ValueError):s.validate_fct(p,{0:7},[rows[0].replace('1048576','1048584')])
        with self.assertRaises(ValueError):s.validate_fct(p,{0:7},rows+rows)


if __name__=='__main__':unittest.main()
