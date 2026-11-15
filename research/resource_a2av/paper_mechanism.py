"""Literal opportunity control and actual-wire service diagnostics; not a planner."""
import csv
import json
from pathlib import Path
import shutil
import time
import numpy as np
import experiment as e
from simai_a2a.model import TrafficMatrix
from simai_a2a.schedulers.simai_plan import prepare,materialize
from simai_a2a.artifacts import read_schedule_artifact,write_schedule_artifact
from simai_a2a.scenario import canonical_sha256
from simai_a2a.execution import modeled_scaleup_schedule
from simai_a2a.delivery import validate_fast_delivery
from simai_a2a.runner import run_scenario
from simai_a2a.v2_diagnostics import collect


def controlled_case(kind='independent'):
    active=[0,1,4,5,8,9,12,13];q=262144
    local={a:b for a,b in [(0,1),(4,5),(8,9),(12,13)] for a,b in [(a,b),(b,a)]}
    cross={a:b for a,b in [(0,4),(1,5),(8,12),(9,13)] for a,b in [(a,b),(b,a)]}
    values=np.zeros((64,64),dtype=np.int64)
    for s in active:
        for d in [local[s],cross[s]]:
            for u in range(4):values[4*s+u,4*d+u]=q
    matrix=TrafficMatrix(values,dict(generator='literal-cut-opportunity',active_servers=active,
        per_gpu_pair_bytes=q,meaning='hand-controlled support, not real/sampled MoE'))
    plans={}
    for name,choose in [('hand-sequential',set([0,1,4,5])),('hand-overlap',set([0,5,8,13]))]:
        p=list(range(16));other=p[:]
        for s in active:
            p[s]=cross[s] if s in choose else local[s]
            other[s]=local[s] if s in choose else cross[s]
        plans[name]=dict(permutations=[p,other],coefficients=[q,q])
    if kind=='shared-senders':
        edges={0:[1,4],1:[0,5],4:[0,12],5:[1,13],12:[4,13],13:[5,12]}
        values[:]=0
        for s,ds in edges.items():
            for d in ds:
                for u in range(4):values[4*s+u,4*d+u]=q
        matrix=TrafficMatrix(values,dict(generator='literal-shared-sender-cut-opportunity',
            active_servers=list(edges),per_gpu_pair_bytes=q,
            meaning='six active servers; middle senders4/5 must alternate left/right bottlenecks'))
        for name,targets in [('hand-sequential',[4,5,0,1,13,12]),('hand-overlap',[1,5,0,13,4,12])]:
            p=list(range(16));other=p[:]
            for s,d in zip(edges,targets):p[s]=d;other[s]=next(t for t in edges[s] if t!=d)
            plans[name]=dict(permutations=[p,other],coefficients=[q,q])
    elif kind!='independent':raise ValueError('unknown controlled variant')
    top,hw=e.fabric('multi-cut',64)
    return matrix,top,hw,plans


def pending_idle(total_bytes,packets,start,end):
    """Conservative: duplicate payload reduces the bound; all wire Tx exclude idle."""
    if total_bytes<=0:return []
    busy=sorted((max(start,p['start_ns']),min(end,p['start_ns']+p['duration_ns']))
                for p in packets if p['start_ns']<end and p['start_ns']+p['duration_ns']>start)
    gaps=[];cursor=start
    for a,b in busy:
        if a>cursor:gaps.append((cursor,a))
        cursor=max(cursor,b)
    if cursor<end:gaps.append((cursor,end))
    sent=sorted((p['start_ns']+p['duration_ns'],p['payload_bytes']) for p in packets if p['kind']==0)
    index=amount=0;result=[]
    for a,b in gaps:
        while index<len(sent) and sent[index][0]<=a:amount+=sent[index][1];index+=1
        if amount<total_bytes:result.append((a,b))
    return result


def service_trace(row,source):
    attempt=Path(row['attempt_dir']);top,hw=e.fabric('multi-cut',64)
    routes={(int(a),int(b)):int(c) for a,b,c in csv.reader((source/'routes.csv').open())}
    schedule,_=read_schedule_artifact(attempt)
    ports={};counts={}
    for link in top.links:
        for a,b in [(link.src,link.dst),(link.dst,link.src)]:
            counts[a]=counts.get(a,0)+1;ports[a,b]=counts[a]
    cuts=[link for link in top.links if link.src in top.spine_ids and link.dst in top.spine_ids]
    keys={(link.src,ports[link.src,link.dst]):link for link in cuts}
    demand={key:0 for key in keys};flow_sets={key:set() for key in keys}
    for flow in schedule.flows:
        u=flow.src
        while u!=flow.dst:
            v=routes[u,flow.dst];key=(u,ports[u,v])
            if key in demand:demand[key]+=flow.size_bytes;flow_sets[key].add(flow.flow_id)
            u=v
    packets={key:[] for key in keys}
    with (attempt/'native_wan.csv').open() as stream:
        for packet in csv.DictReader(stream):
            p={k:int(v) for k,v in packet.items()};key=p['node_id'],p['port_id']
            if key in packets:
                if p['kind']==0 and p['flow_id'] not in flow_sets[key]:raise ValueError('Tx flow not on planned path')
                packets[key].append(p)
    epoch=row['input_contract']['epoch'];end=epoch+round(row['actual_ms']*1e6);result=[]
    for key,link in keys.items():
        idle=pending_idle(demand[key],packets[key],epoch,end)
        result.append(dict(resource=f'{link.src}->{link.dst}',node=key[0],port=key[1],
            rate_gbps=link.bandwidth_gbps,required_bytes=demand[key],epoch_ns=epoch,end_ns=end,
            foreground_tx=[(p['start_ns'],p['duration_ns']) for p in packets[key] if p['kind']==0],
            control_tx=[(p['start_ns'],p['duration_ns']) for p in packets[key] if p['kind']!=0],
            idle_pending_lower_bound=idle,idle_pending_us=sum(b-a for a,b in idle)/1000))
    return result


def run(out,binary):
    from paper_experiment import paper_cases,replay
    case=next(c for c in paper_cases() if c['topology']=='multi-cut' and c['family']=='balanced' and c['mib']==1)
    natural=out/'natural';natural.mkdir();study=replay(natural,case,binary)
    if not study['all_success']:raise RuntimeError('natural mechanism failed; retained, no retry')
    source=Path(case['source']);run_control(out/'control',binary,source)
    analyze(out)


def run_control(control,binary,source,kind='independent'):
    matrix,top,hw,manual=controlled_case(kind)
    control.mkdir();inputs=control/'input';inputs.mkdir()
    for name in ['hardware.json','topology.txt','routes.csv','background.csv','planner_topology.json','scenario_manifest.json']:
        shutil.copyfile(source/name,inputs/name)
    e.write_matrix_artifact(matrix,inputs/'matrix');e.write_micro_workload(matrix,inputs/'workload.csv')
    manifest=json.loads((inputs/'scenario_manifest.json').read_text());manifest.update(name='controlled-cut',case=dict(family='literal-cut-opportunity',logical_bytes=matrix.logical_bytes));e.dump(inputs/'scenario_manifest.json',manifest)
    raw=e.iterations.run(control/'replay',binary,['controlled-cut'],['volume-maxq','resource-maxq'],source_scenario=inputs,source_topology=top,timeout=300)
    rows=raw['runs'];directory=control/'replay/controlled-cut';prepared=prepare(matrix,hw)
    hardware=json.loads((inputs/'hardware.json').read_text());mm=json.loads((inputs/'matrix/matrix_manifest.json').read_text())
    for method,result in manual.items():
        tick=time.process_time_ns();full=materialize(prepared,result['permutations'],result['coefficients'],hw,method,dict(server_dc_ids=[s//4 for s in range(16)]))
        validate_fast_delivery(matrix,full,4);schedule=modeled_scaleup_schedule(full,hw)
        cpu=(time.process_time_ns()-tick)/1000
        write_schedule_artifact(schedule,directory/'schedules'/method,matrix_sha256=mm['matrix_sha256'],hardware_sha256=canonical_sha256(hardware))
        e.dump(directory/(method+'.plan.json'),result)
        outcome=run_scenario(directory,method,directory/'topology.txt',binary,config_template=e.EXPERIMENTS/'configs/ns3-leafspine.conf',
            timeout_s=300,ns3_threads=0,ns3_env={'NS_ATTRIBUTE_DEFAULT':None,'NS_GLOBAL_VALUE':None})
        row=dict(case='controlled-cut',method=method,status=outcome['status'],success=outcome['success'],
            actual_ms=outcome.get('summary',{}).get('actual_makespan_ns',0)/1e6 if outcome['success'] else None,
            stages=2,planning_cpu_us=cpu,attempt_dir=outcome['attempt_dir'],reused=False)
        if outcome['success']:row.update(collect(schedule,matrix,Path(outcome['attempt_dir'])))
        rows.append(row);e.dump(control/'study.json',dict(runs=rows,scope='manual controls are not algorithm output'))
        print('CONTROL',method,row['status'],row['actual_ms'],flush=True)
    for row in rows:e.audit.attach_contract(row)
    e.audit.mark_pair_eligibility(rows)
    e.dump(control/'service.json',{r['method']:service_trace(r,inputs) for r in rows if r['pair_eligible']})
    e.dump(control/'study.json',dict(runs=rows,variant=kind,scope='manual controls are not algorithm output',
        complete=True,all_success=all(r['pair_eligible'] for r in rows)))
    print('MECHANISM COMPLETE',[(r['method'],r['actual_ms']) for r in rows],flush=True)


def analyze(out):
    study=json.loads((out/'natural/study.json').read_text());source=Path(study['cases'][0]['source'])
    trace={r['method']:service_trace(r,source) for r in study['runs'] if r['method'] in ['volume-maxq','resource-maxq'] and r['success']}
    e.dump(out/'natural_service.json',trace)
    control=out/'control';raw=json.loads((control/'study.json').read_text());rows=raw['runs']
    for row in rows:e.audit.attach_contract(row)
    e.audit.mark_pair_eligibility(rows)
    e.dump(control/'service.json',{r['method']:service_trace(r,control/'input') for r in rows if r['pair_eligible']})
    raw.update(complete=True,all_success=all(r['pair_eligible'] for r in rows));e.dump(control/'study.json',raw)
