"""Isolated multi-slot adapter; does not modify the frozen planner/adapter."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from resource_order.simai import prepare, validate_events, validate_fct

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def audit(plan,mapping,rows,capacity):
    if capacity not in (1,2,4,8):raise ValueError('unregistered concurrency')
    by_id={f.id:f for f in plan.messages};events={};begins=[];ends=[]
    active_s={};active_r={};max_s=max_r=0;starts={};grants={}
    previous_time=-1
    for row in rows:
        time_ns=int(row['time_ns']);event=row['event'];wire=int(row['flow_id'])
        if time_ns<previous_time:raise ValueError('trace time went backwards')
        previous_time=time_ns
        if event=='begin':begins.append(time_ns)
        if event=='finished':ends.append(time_ns)
        if wire<0 or event not in ('credit_send','credit_arrive','data_release','data_ready','ack_done'):continue
        if wire not in mapping:raise ValueError('unknown message')
        ident=mapping[wire];f=by_id[ident];key=(ident,event)
        if key in events:raise ValueError('duplicate event')
        events[key]=time_ns
        if event=='credit_send':
            active_r.setdefault(f.dst,set()).add(ident)
            grants.setdefault(f.dst,[]).append(ident)
            if len(active_r[f.dst])>capacity:raise ValueError('receive reservation capacity exceeded')
            max_r=max(max_r,len(active_r[f.dst]))
        elif event=='credit_arrive':
            if (ident,'credit_send') not in events:raise ValueError('credit before grant')
        elif event=='data_release':
            if (ident,'credit_arrive') not in events:raise ValueError('data without credit')
            active_s.setdefault(f.src,set()).add(ident)
            starts.setdefault(f.src,[]).append(ident)
            if len(active_s[f.src])>capacity:raise ValueError('send capacity exceeded')
            max_s=max(max_s,len(active_s[f.src]))
        elif event=='data_ready':
            if (ident,'data_release') not in events:raise ValueError('receive before release')
            active_r[f.dst].remove(ident)
        else:
            if (ident,'data_ready') not in events:raise ValueError('ACK before data')
            active_s[f.src].remove(ident)
    if len(begins)!=1 or len(ends)!=1:raise ValueError('missing/duplicate begin or end')
    for f in plan.messages:
        times=[events[f.id,e] for e in ('credit_send','credit_arrive','data_release','data_ready','ack_done')]
        if not begins[0]<=times[0]<=times[1]<=times[2]<=times[3]<=times[4]<=ends[0]:
            raise ValueError('invalid event causality')
    if any(active_s.values()) or any(active_r.values()):raise ValueError('unfinished reservations')
    expected_s={};expected_r={}
    for ident in plan.order:
        f=by_id[ident];expected_s.setdefault(f.src,[]).append(ident);expected_r.setdefault(f.dst,[]).append(ident)
    if starts!=expected_s or grants!=expected_r:raise ValueError('endpoint FIFO order changed')
    metrics=dict(delivery_ns=max(events[f.id,'data_ready'] for f in plan.messages)-begins[0],
                 ack_drain_ns=max(events[f.id,'ack_done'] for f in plan.messages)-begins[0],
                 messages=len(plan.messages),payload_bytes=sum(f.bytes for f in plan.messages),
                 peak_send_slots=max_s,peak_receive_slots=max_r)
    if capacity==1:
        old=validate_events(plan,mapping,rows)
        assert all(old[k]==metrics[k] for k in old)
    return metrics

def run(plan,network,root,capacity,timeout_s=120,reference=None,*,build_manifest):
    root=Path(root).resolve()
    if root.exists():raise ValueError('fresh output required')
    manifest=json.loads(Path(build_manifest).read_text())
    if manifest['status']!='built':raise ValueError('backend build incomplete')
    binary=Path(manifest['binary'])
    if digest(binary)!=manifest['binary_sha256']:raise ValueError('backend changed')
    mapping=prepare(plan,network,root)
    env={k:v for k,v in os.environ.items() if not k.startswith('SIMAI_') and k not in
         ('AS_SEND_LAT','NS_ATTRIBUTE_DEFAULT','NS_GLOBAL_VALUE')}
    env.update(SIMAI_NCCL_VERSION='v2.30',SIMAI_NATIVE_FAST_FILE=str(root/'commands.csv'),
               SIMAI_STATIC_ROUTES=str(root/'routes.csv'),AS_SEND_LAT='0',
               SIMAI_RESOURCE_ORDER_CONCURRENCY=str(capacity))
    ports={u:0 for u in network.adjacency};monitored=[]
    for u,v in sorted(network.links):
        if u>=v:continue
        for node in (u,v):
            ports[node]+=1
            if node>=network.gpu_count:monitored.append(f'{node}:{ports[node]}')
    env['SIMAI_NATIVE_WAN_PORTS']=','.join(monitored)
    command=[str(binary),'-t','0','-n',str(root/'topology.txt'),'-c',str(root/'ns3.conf'),
             '-w',str(root/'workload.txt'),'-s',str(root/'schedule.csv')]
    result=dict(status='running',command=command,endpoint_slots=capacity,
                ack_release=True,fifo=True,plan_sha256=digest(root/'plan.json'),
                backend_sha256={str(binary):manifest['binary_sha256'],**manifest['libraries']},
                adapter_sha256=digest(__file__),reference=str(reference) if reference else None)
    started=time.monotonic()
    try:
        with (root/'stdout.log').open('x') as out,(root/'stderr.log').open('x') as err:
            process=subprocess.run(command,cwd=root,env=env,stdout=out,stderr=err,timeout=timeout_s)
        result['returncode']=process.returncode
        if process.returncode:raise ValueError('nonzero backend exit')
        with (root/'commands.csv.events.csv').open() as stream:rows=list(csv.DictReader(stream))
        result.update(audit(plan,mapping,rows,capacity))
        validate_fct(plan,mapping,(root/'fct.txt').read_text().splitlines())
        expected={tuple(map(int,l.split(','))) for l in (root/'routes.csv').read_text().splitlines()}
        applied={tuple(map(int,l.split(','))) for l in (root/'routes.csv.applied.csv').read_text().splitlines()}
        if not applied<=expected:raise ValueError('unexpected route')
        for f in plan.messages:
            for src,dst in ((f.src,f.dst),(f.dst,f.src)):
                if any((u,dst,v) not in applied for u,v in network.path(src,dst)):
                    raise ValueError('missing actual fixed route')
        if reference:
            if capacity!=1:raise ValueError('historical equality only applies to C=1')
            reference=Path(reference);prior=json.loads((reference/'result.json').read_text())
            if prior['status']!='success':raise ValueError('invalid reference')
            for name in ('schedule.csv','commands.csv','topology.txt','routes.csv'):
                if (root/name).read_bytes()!=(reference/name).read_bytes():raise ValueError('reference inputs changed: '+name)
            from resource_order.pairwise_control import normalized_config
            if normalized_config(root/'ns3.conf')!=normalized_config(reference/'ns3.conf'):
                raise ValueError('transport configuration changed')
            for key in ('delivery_ns','ack_drain_ns','messages','payload_bytes'):
                if result[key]!=prior[key]:raise ValueError('historical replay mismatch: '+key)
            if (root/'commands.csv.events.csv').read_bytes()!=(reference/'commands.csv.events.csv').read_bytes():
                raise ValueError('historical event trace mismatch')
            result['historical_exact']=True
        result['status']='success'
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as exc:
        result.update(status='failed',error=repr(exc))
    result['wall_s']=time.monotonic()-started
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
