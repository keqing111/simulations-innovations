"""Read existing ns3 native binary traces; no backend/traffic modifications.

Supported ABI: little-endian x86_64 TraceFormat, verified by a C++ fixture.
Native uint16 wire/payload fields wrap at64KiB: never sum them as real bytes.
"""
from collections import Counter
from numbers import Integral
from pathlib import Path
import struct
import sys
import numpy as np

TRACE_DTYPE=np.dtype(dict(names=['time_ns','node','port','qidx','qlen','sip','dip','wire16','proto',
    'event','ecn','node_type','sport','dport','seq','ack_seq','payload16'],
    formats=['<u8','<u2','u1','u1','<u4','<u4','<u4','<u2','u1','u1','u1','u1','<u2','<u2','<u4','<u4','<u2'],
    offsets=[0,8,10,11,12,16,20,24,26,27,28,29,32,34,36,40,50],itemsize=56))


def read_trace(path):
    path=Path(path);size=path.stat().st_size
    if sys.byteorder!='little':raise ValueError('unsupported native trace endianness')
    with path.open('rb') as stream:
        first=stream.read(4)
        if len(first)!=4:raise ValueError('truncated trace header')
        count=struct.unpack('<I',first)[0];offset=8+11*count
        if count>65536 or size<offset or (size-offset)%56:raise ValueError('invalid trace header/record length')
        ports=[];seen=set()
        for _ in range(count):
            node,port,bps=struct.unpack('<HBQ',stream.read(11))
            if not bps or (node,port) in seen:raise ValueError('invalid/duplicate trace port')
            seen.add((node,port));ports.append(dict(node=node,port=port,bps=bps))
        window=struct.unpack('<I',stream.read(4))[0]
    rows=np.memmap(path,dtype=TRACE_DTYPE,mode='r',offset=offset) if size>offset else np.empty(0,dtype=TRACE_DTYPE)
    if np.any(rows['event']>3):raise ValueError('invalid native trace event')
    if np.any(rows['time_ns'][1:]<rows['time_ns'][:-1]):raise ValueError('native trace time reversed')
    return rows,dict(header_bytes=offset,record_bytes=56,ports=ports,window=window,record_count=len(rows))


def flow_diagnostic(rows,*,src,dst,sport,dport,size_bytes,mtu,queue_cap_bytes):
    for value in [src,dst,sport,dport,size_bytes,mtu,queue_cap_bytes]:
        if isinstance(value,bool) or not isinstance(value,Integral):raise ValueError('flow parameters must be integers')
    if (src==dst or min(src,dst)<0 or max(src,dst)>65535 or not 0<sport<=65535 or not 0<dport<=65535 or
            not 0<size_bytes<2**32 or not 0<mtu<2**32 or not 0<queue_cap_bytes<2**32):
        raise ValueError('unsupported flow/trace sequence or queue range')
    sender=((rows['sip']>>8)&65535);receiver=((rows['dip']>>8)&65535)
    data=rows[(sender==src)&(receiver==dst)&(rows['proto']==17)&(rows['sport']==sport)&(rows['dport']==dport)]
    if not len(data):raise ValueError('selected data flow absent from trace')
    seq=data['seq'].astype(np.int64)
    if np.any(seq>=size_bytes) or np.any(seq%mtu):raise ValueError('data sequence inconsistent with flow/MTU')
    payload=np.minimum(mtu,size_bytes-seq)
    if np.any(payload%65536!=data['payload16']):raise ValueError('wrapped payload field inconsistent with flow')
    header=(data['wire16'].astype(np.int64)-payload)%65536
    if np.any(header<=0) or np.any(header>=4096) or len(np.unique(header))!=1:
        raise ValueError('wrapped wire size/header inconsistent with flow')
    wire=payload+header

    def summarize(mask):
        a=data[mask];p=payload[mask]
        unique,first=np.unique(a['seq'],return_index=True)
        return dict(packets=len(a),unique_packets=len(unique),unique_bytes=int(p[first].sum()),
            repeated_packets=len(a)-len(unique),repeated_bytes=int(p.sum()-p[first].sum()),
            first_ns=int(a['time_ns'].min()) if len(a) else None,
            last_ns=int(a['time_ns'].max()) if len(a) else None)

    source_mask=(data['node']==src)&(data['event']==2)
    destination_mask=(data['node']==dst)&(data['event']==0)
    source=summarize(source_mask);destination=summarize(destination_mask)
    if source['unique_bytes']!=size_bytes or destination['unique_bytes']!=size_bytes:
        raise ValueError('incomplete source/receiver sequence coverage')
    egress=[]
    for node,port in sorted({(int(r['node']),int(r['port'])) for r in data if r['event']==1}):
        at=(data['node']==node)&(data['port']==port);enq=at&(data['event']==1);deq=at&(data['event']==2)
        attempts=Counter(map(int,data['seq'][enq]));sent=Counter(map(int,data['seq'][deq]))
        overflow=enq&(data['qlen'].astype(np.uint64)+wire.astype(np.uint64)>=queue_cap_bytes)
        egress.append(dict(node=node,port=port,attempts=int(enq.sum()),dequeues=int(deq.sum()),
            unforwarded_attempts=sum((attempts-sent).values()),
            predicted_overflow_attempts=int(overflow.sum()),peak_queue_bytes=int(data['qlen'][at].max()),
            first_overflow_ns=int(data['time_ns'][overflow].min()) if np.any(overflow) else None,
            last_overflow_ns=int(data['time_ns'][overflow].max()) if np.any(overflow) else None))
    control=rows[(sender==dst)&(receiver==src)&np.isin(rows['proto'],[252,253])&
        (rows['sport']==dport)&(rows['dport']==sport)&(rows['node']==src)&(rows['event']==0)]
    if not len(control) or int(control['ack_seq'].max())!=size_bytes:
        raise ValueError('missing/invalid final ACK evidence')
    nacks=[dict(time_ns=int(r['time_ns']),seq=int(r['ack_seq'])) for r in control if r['proto']==253]
    injected=data[source_mask];gaps=np.diff(injected['time_ns'].astype(np.int64))
    return dict(source=source,receiver=destination,egress=egress,nacks=nacks,
        drop_events=int((data['event']==3).sum()),last_ack_ns=int(control['time_ns'][-1]),
        wire_header_bytes=int(header[0]),max_source_gap_ns=int(gaps.max(initial=0)),
        source_timeline=[[int(r['time_ns']),int(r['seq']),int(p)] for r,p in zip(injected,payload[source_mask])],
        receiver_timeline=[[int(r['time_ns']),int(r['seq']),int(p)] for r,p in zip(data[destination_mask],payload[destination_mask])],
        interpretation='unforwarded attempts require completed trace; no Drop event does not prove no loss')
