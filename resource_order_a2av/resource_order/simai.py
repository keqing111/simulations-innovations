"""Whole-message adapter to the installed SimAI ns-3 event transport.

ABI slots encode endpoint queue positions, NOT synchronized algorithm stages.
Both barrier flags are zero, no local timing sidecar is used, and every message
(including same-server GPU pairs) remains a packet flow. No legacy planner is
imported. Backend and linked-module hashes accompany every run.
"""
from collections import deque
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from .model import Plan


def encode(plan, ranks):
    plan=Plan.from_order(plan.messages,plan.order)
    if not plan.messages:raise ValueError('empty packet experiment')
    if any(f.src>=ranks or f.dst>=ranks for f in plan.messages):raise ValueError('rank outside topology')
    by_id={f.id:f for f in plan.messages};slots={};mapping={};rows=[];send={};recv={}
    for wire_id,ident in enumerate(plan.order):
        f=by_id[ident];mapping[wire_id]=ident
        slot=1+max(slots.get(plan.send_after[ident],-1),slots.get(plan.receive_after[ident],-1))
        slots[ident]=slot;send[slot,f.src]=wire_id;recv[slot,f.dst]=wire_id
        rows.append(f'{f.src},{f.dst},{f.bytes},0')
    count=max(slots.values())+1
    commands=[f'{ranks},{count},0,0']
    for slot in range(count):
        for rank in range(ranks):
            # An empty receive in slot 0 has a self peer: no dummy wire credit.
            commands.append(f'{rank},{slot},{send.get((slot,rank),-1)},{recv.get((slot,rank),-1)},{rank}')
    return '\n'.join(rows)+'\n','\n'.join(commands)+'\n',mapping


def prepare(plan, network, root):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    rows,commands,mapping=encode(plan,network.gpu_count)
    (root/'schedule.csv').write_text(rows);(root/'commands.csv').write_text(commands)
    (root/'plan.json').write_text(json.dumps(dict(messages=[vars(f) for f in plan.messages],
        order=plan.order,wire_to_message=mapping),indent=2)+'\n')
    nodes=sorted(network.adjacency);n=network.gpu_count;servers=n//network.gpus_per_server
    if nodes!=list(range(len(nodes))):raise ValueError('SimAI requires contiguous node IDs')
    # Factory topology ABI: GPU IDs, local NVSwitch IDs, then regular switches.
    for gpu in range(n):
        if network.adjacency[gpu]!=[n+gpu//network.gpus_per_server]:
            raise ValueError('adapter expects one physical local switch per server')
    links=[v for (u,w),v in sorted(network.links.items()) if u<w]
    lines=[f'{len(nodes)} {network.gpus_per_server} {servers} {len(nodes)-n-servers} {len(links)} A100',
           ' '.join(map(str,range(n,len(nodes))))]
    lines += [f'{e.src} {e.dst} {e.gbps:.12g}Gbps {e.delay_us:.12g}us 0' for e in links]
    (root/'topology.txt').write_text('\n'.join(lines)+'\n')
    routes=[]
    for dst in nodes:
        distance={dst:0};todo=deque([dst])
        while todo:
            u=todo.popleft()
            for v in network.adjacency[u]:
                if v not in distance:distance[v]=distance[u]+1;todo.append(v)
        for u in nodes:
            if u==dst:continue
            choices=sorted(v for v in network.adjacency[u] if distance[v]==distance[u]-1)
            v=choices[(dst//network.gpus_per_server)%len(choices)]
            routes.append(f'{u},{dst},{v}')
    (root/'routes.csv').write_text('\n'.join(routes)+'\n')
    max_bytes=max(sum(f.bytes for f in plan.messages if f.src==i) for i in range(n))
    (root/'workload.txt').write_text(f'MICRO model_parallel_NPU_group: 1 ep: {n} all_gpus: {n}\n1\n'
                                  f'0 0 0 NONE 0 0 NONE 0 0 ALLTOALL {max_bytes} 0\n')
    for name in ('flow.txt','trace.txt'):(root/name).write_text('0\n')
    config=(Path(__file__).parents[1]/'ns3.conf').read_text()
    names={k:str(root/v) for k,v in dict(FLOW_FILE='flow.txt',TRACE_FILE='trace.txt',TRACE_OUTPUT_FILE='trace.out',
        FCT_OUTPUT_FILE='fct.txt',PFC_OUTPUT_FILE='pfc.txt',QLEN_MON_FILE='qlen.txt',BW_MON_FILE='bw.txt',
        RATE_MON_FILE='rate.txt',CNP_MON_FILE='cnp.txt').items()}
    # Explicit thresholds for every physical rate (backend uses integer bit/s).
    rates=sorted({int(float(f'{e.gbps:.12g}')*1e9) for e in links})
    for key,factor in [('KMIN_MAP',2),('KMAX_MAP',8),('PMAX_MAP',None)]:
        pairs=' '.join(f'{rate} {0.2 if factor is None else max(1,round(rate/1e9*factor))}' for rate in rates)
        names[key]=f'{len(rates)} {pairs}'
    (root/'ns3.conf').write_text(config.format(**names))
    return mapping


def validate_events(plan,mapping,rows):
    events={};begin=[];finished=[]
    for row in rows:
        event=row['event'];t=int(row['time_ns']);wire=int(row['flow_id'])
        if event=='begin':begin.append(t)
        if event=='finished':finished.append(t)
        if event in ('credit_send','credit_arrive','data_release','data_ready','ack_done') and wire>=0:
            if wire not in mapping:raise ValueError('unknown event flow')
            key=(mapping[wire],event)
            if key in events:raise ValueError('duplicate event')
            events[key]=t
    if len(begin)!=1 or len(finished)!=1:raise ValueError('missing/duplicate runtime begin or finish')
    for f in plan.messages:
        try:
            grant,credit,start,data,ack=[events[f.id,e] for e in
                ('credit_send','credit_arrive','data_release','data_ready','ack_done')]
            if not begin[0]<=grant<=credit<=start<=data<=ack<=finished[0]:raise ValueError('event causality violation')
            prev=plan.send_after[f.id]
            if prev is not None and start<events[prev,'ack_done']:raise ValueError('send queue bypass')
            prev=plan.receive_after[f.id]
            if prev is not None and grant<events[prev,'data_ready']:raise ValueError('receive queue bypass')
        except KeyError as exc:raise ValueError('incomplete real message events') from exc
    return dict(delivery_ns=max(events[f.id,'data_ready'] for f in plan.messages)-begin[0],
                ack_drain_ns=max(events[f.id,'ack_done'] for f in plan.messages)-begin[0],
                messages=len(plan.messages),payload_bytes=sum(f.bytes for f in plan.messages))


def validate_fct(plan,mapping,lines):
    """SimAI FCT exports hexadecimal IPs and logical payload, excluding +8 B."""
    delivered={}
    for line in lines:
        row=line.split()
        if len(row)!=9:raise ValueError('unexpected FCT schema')
        wire=int(row[8])
        if wire not in mapping or mapping[wire] in delivered:raise ValueError('unknown/duplicate payload QP')
        delivered[mapping[wire]]=((int(row[0],16)>>8)&0xffff,(int(row[1],16)>>8)&0xffff,int(row[4]))
    if delivered!={f.id:(f.src,f.dst,f.bytes) for f in plan.messages}:
        raise ValueError('payload delivery differs from original demand')


def run(plan,network,root,binary,timeout_s=120,send_latency_ns=0):
    root=Path(root).resolve();binary=Path(binary).resolve()
    if root.exists():raise ValueError('run output must be new; never overwrite results')
    if type(send_latency_ns) is not int or send_latency_ns<0:raise ValueError('invalid send latency')
    mapping=prepare(plan,network,root)
    env={k:v for k,v in os.environ.items() if not k.startswith('SIMAI_') and k not in
         ('AS_SEND_LAT','NS_ATTRIBUTE_DEFAULT','NS_GLOBAL_VALUE')}
    env.update(SIMAI_NCCL_VERSION='v2.30',SIMAI_NATIVE_FAST_FILE=str(root/'commands.csv'),
               SIMAI_STATIC_ROUTES=str(root/'routes.csv'),AS_SEND_LAT=str(send_latency_ns))
    # Interface 0 is loopback; subsequent devices follow topology link order.
    ports={u:0 for u in network.adjacency};monitored=[]
    for (u,v) in sorted(network.links):
        if u>=v:continue
        for node in (u,v):
            ports[node]+=1
            if node>=network.gpu_count:monitored.append(f'{node}:{ports[node]}')
    env['SIMAI_NATIVE_WAN_PORTS']=','.join(monitored)
    command=[str(binary),'-t','0','-n',str(root/'topology.txt'),'-c',str(root/'ns3.conf'),
             '-w',str(root/'workload.txt'),'-s',str(root/'schedule.csv')]
    files=[binary,*sorted((binary.parent.parent/'lib').glob('libns3.*.so'))]
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    result=dict(status='running',command=command,send_latency_ns=send_latency_ns,
                backend_sha256={str(p):digest(p) for p in files},
                source_sha256={str(p.relative_to(Path(__file__).parents[1])):digest(p)
                               for p in Path(__file__).parent.glob('*.py')})
    started=time.monotonic()
    try:
        with (root/'stdout.log').open('w') as out,(root/'stderr.log').open('w') as err:
            done=subprocess.run(command,cwd=root,env=env,stdout=out,stderr=err,timeout=timeout_s)
        result['returncode']=done.returncode
        if done.returncode:raise ValueError('backend returned nonzero status')
        with (root/'commands.csv.events.csv').open() as f:rows=list(csv.DictReader(f))
        metrics=validate_events(plan,mapping,rows)
        expected={tuple(map(int,l.split(','))) for l in (root/'routes.csv').read_text().splitlines()}
        applied={tuple(map(int,l.split(','))) for l in (root/'routes.csv.applied.csv').read_text().splitlines()}
        if not applied<=expected:raise ValueError('backend routing differs')
        for flow in plan.messages:
            for a,b in network.path(flow.src,flow.dst):
                if (a,flow.dst,b) not in applied:raise ValueError('unverified payload route')
            for a,b in network.path(flow.dst,flow.src):
                if (a,flow.src,b) not in applied:raise ValueError('unverified reverse route')
        validate_fct(plan,mapping,(root/'fct.txt').read_text().splitlines())
        result.update(status='success',**metrics)
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        result.update(status='failed',error=str(exc))
    result['wall_s']=time.monotonic()-started
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
