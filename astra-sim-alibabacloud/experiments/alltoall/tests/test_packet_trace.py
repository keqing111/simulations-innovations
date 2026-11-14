"""Actual native C++ layout and literal overflow/retransmission evidence."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
NS3=ROOT.parents[1]/'extern/network_backend/ns3-interface/simulation/src/point-to-point'


class PacketTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.directory=Path(cls.tmp.name)
        exe=cls.directory/'fixture';cls.path=cls.directory/'trace.out'
        r=subprocess.run(['g++','-std=c++17','-I',str(NS3/'model'),'-I',str(NS3/'helper'),
            str(ROOT/'tests/cpp/packet_trace_fixture.cc'),'-o',str(exe)],capture_output=True,text=True)
        if r.returncode:raise RuntimeError(r.stderr)
        cls.abi=json.loads(subprocess.check_output([str(exe),str(cls.path)]))

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_actual_cpp_layout_and_wrapped_payload_reconstruction(self):
        from simai_a2a.packet_trace import read_trace,flow_diagnostic
        rows,meta=read_trace(self.path)
        self.assertEqual(self.abi,dict(record_bytes=56,seq_offset=36,ack_seq_offset=40))
        self.assertEqual(meta['ports'],[dict(node=0,port=1,bps=100000000000)])
        self.assertEqual(len(rows),16)
        r=flow_diagnostic(rows,src=0,dst=1,sport=10000,dport=100,size_bytes=65540,mtu=65536,
                          queue_cap_bytes=1048576)
        self.assertEqual(r['source']['packets'],4);self.assertEqual(r['source']['unique_bytes'],65540)
        self.assertEqual(r['source']['repeated_bytes'],65540)
        self.assertEqual(r['receiver']['unique_bytes'],65540)
        self.assertEqual(r['receiver']['repeated_bytes'],4)
        hop=next(h for h in r['egress'] if h['node']==2)
        self.assertEqual(hop['attempts'],4);self.assertEqual(hop['dequeues'],3)
        self.assertEqual(hop['unforwarded_attempts'],1);self.assertEqual(hop['predicted_overflow_attempts'],1)
        self.assertEqual(r['drop_events'],0);self.assertEqual(r['nacks'],[dict(time_ns=40,seq=0)])
        self.assertEqual(r['last_ack_ns'],70)

    def test_truncated_headers_records_and_invalid_events_are_rejected(self):
        from simai_a2a.packet_trace import read_trace
        for i,content in enumerate([b'\0\0',b'\xff'*4,self.path.read_bytes()[:-1]]):
            p=self.directory/f'bad{i}';p.write_bytes(content)
            with self.assertRaises(ValueError):read_trace(p)
        p=self.directory/'event';content=bytearray(self.path.read_bytes());content[19+27]=99;p.write_bytes(content)
        with self.assertRaisesRegex(ValueError,'event'):read_trace(p)

    def test_wrong_flow_sequence_or_truncated_payload_is_not_silently_counted(self):
        from simai_a2a.packet_trace import read_trace,flow_diagnostic
        rows,_=read_trace(self.path)
        for key,value in [('seq',999999),('payload16',1)]:
            altered=np.array(rows,copy=True);altered[key][0]=value
            with self.assertRaises(ValueError):
                flow_diagnostic(altered,src=0,dst=1,sport=10000,dport=100,size_bytes=65540,mtu=65536,
                                queue_cap_bytes=1048576)
