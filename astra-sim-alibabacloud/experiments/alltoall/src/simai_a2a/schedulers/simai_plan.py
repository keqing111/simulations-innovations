"""Integer SimAI volume planning. No original FAST simulator or host process.

Only preprocessing/materialization live here; joint_resource.py stays frozen.
"""
from collections import defaultdict,deque
import copy
import hashlib
import importlib
import sys
import time
import numpy as np
from . import _finalize_schedule
from .fast_port import FastAll2All
from .joint import RESEARCH


def _take(hold,owner,dst,amount):
    result=[];queue=hold[owner,dst]
    while amount:
        if not queue:raise ValueError('payload inventory exhausted')
        origin,size=queue.popleft();take=min(amount,size)
        result.append([origin,dst,take]);amount-=take
        if size>take:queue.appendleft((origin,size-take))
    return result


def prepare(matrix,hardware):
    hardware.validate_matrix(matrix);n,g=hardware.num_servers,hardware.gpus_per_server
    hold=defaultdict(deque);raw=[];tags={};S=[[0]*n for _ in range(n)]
    for a in range(n*g):
        for b in range(n*g):
            if (v:=int(matrix.values[a,b])):hold[a,b].append((a,v))
    for s in range(n):
        for d in range(n):
            if s==d:continue
            rows=[sum(size for b in range(d*g,(d+1)*g) for _,size in hold[s*g+u,b]) for u in range(g)]
            total=sum(rows);S[s][d]=(total+g-1)//g
            target=[total//g+int(u<total%g) for u in range(g)]
            for u in range(g):
                if rows[u]<=target[u]:continue
                for v in range(g):
                    amount=min(rows[u]-target[u],target[v]-rows[v])
                    if amount<=0:continue
                    chunks=[];left=amount
                    for b in range(d*g,(d+1)*g):
                        available=sum(x for _,x in hold[s*g+u,b]);take=min(left,available)
                        moved=_take(hold,s*g+u,b,take);chunks+=moved
                        hold[s*g+v,b].extend((o,x) for o,_,x in moved);left-=take
                    assert left==0
                    key=('balance',d,s*g+u,s*g+v);tags[key]=chunks
                    raw.append((s*g+u,s*g+v,amount,0,'balance',d));rows[u]-=amount;rows[v]+=amount
            assert rows==target
    R=copy.deepcopy(S);L=max([sum(row) for row in S]+[sum(col) for col in zip(*S)],default=0)
    rd=[L-sum(row) for row in R];cd=[L-sum(col) for col in zip(*R)]
    for i in range(n):
        for j in range(n):
            amount=min(rd[i],cd[j]);R[i][j]+=amount;rd[i]-=amount;cd[j]-=amount
    U=[sum(x for b in range(d*g,(d+1)*g) for _,x in hold[s*g+u,b]) if s!=d else 0
       for s in range(n) for d in range(n) for u in range(g)]
    assert all(U[(s*n+d)*g+u]<=R[s][d] for s in range(n) for d in range(n) for u in range(g))
    return dict(n=n,g=g,R=R,S=S,U=U,hold=hold,raw=raw,tags=tags,matrix=matrix)


def reference(prepared):
    n,g=prepared['n'],prepared['g'];R=prepared['R']
    app=FastAll2All(np.array(R,dtype=np.int64),g,'ETHER400','H100',1e-6)
    app.SDS_mat=np.array(R,dtype=np.int64);app.sds_info={'max_sum':sum(R[0])}
    app.decompose()
    stages=list(reversed(sorted(app.p_sets,key=lambda p:p['freq'])))
    pending=prepared['U'][:];payload=[]
    for stage in stages:
        row=[0]*(n*g)
        for s,d in enumerate(stage['mp']):
            if s==d:continue
            for u in range(g):
                k=(s*n+d)*g+u;row[s*g+u]=min(stage['freq'],pending[k]);pending[k]-=row[s*g+u]
        payload.append(row)
    if any(pending):raise ValueError('reference failed to serve real bytes')
    return dict(n=n,g=g,permutations=[p['mp'] for p in stages],
                steps=[dict(alpha=p['freq'],alpha_scaled=p['freq']) for p in stages],payload_units=payload)


def materialize(prepared,permutations,coefficients,hardware,algorithm,metadata=None):
    n,g=prepared['n'],prepared['g'];hold=copy.deepcopy(prepared['hold'])
    raw=list(prepared['raw']);tags=copy.deepcopy(prepared['tags']);R=copy.deepcopy(prepared['R']);records=[]
    for a in range(n*g):
        for b in range(a//g*g,(a//g+1)*g):
            amount=sum(x for _,x in hold[a,b])
            if amount:
                key=('intrinsic',0,a,b);tags[key]=_take(hold,a,b,amount)
                raw.append((a,b,amount,0,'intrinsic',0))
    if len(permutations)!=len(coefficients):raise ValueError('plan size mismatch')
    for k,(p,q) in enumerate(zip(permutations,coefficients)):
        if sorted(p)!=list(range(n)) or type(q) is not int or q<=0:raise ValueError('invalid plan')
        records.append(dict(stage_id=k,freq=q,mp=p,quota_per_gpu_bytes=[q]*n))
        for s,d in enumerate(p):
            if R[s][d]<q:raise ValueError('plan exceeds padded residual')
            R[s][d]-=q
            if s==d:continue
            for u in range(g):
                owner=s*g+u;receiver=d*g+u;chunks=[];left=q
                for b in range(d*g,(d+1)*g):
                    take=min(left,sum(x for _,x in hold[owner,b]));moved=_take(hold,owner,b,take)
                    chunks+=moved;left-=take
                    if receiver!=b and take:
                        key=('dispatch',k,receiver,b);tags.setdefault(key,[]).extend(moved)
                amount=q-left
                if amount:
                    raw.append((owner,receiver,amount,0,'inter',k));tags['inter',k,owner,receiver]=chunks
        for (phase,stage,a,b),chunks in list(tags.items()):
            if phase=='dispatch' and stage==k:
                raw.append((a,b,sum(v for _,_,v in chunks),0,phase,k))
    if any(x for row in R for x in row) or any(hold.values()):raise ValueError('incomplete plan delivery')
    full=_finalize_schedule(algorithm,raw,hardware,None,dict(n_stages=len(records),stage_records=records,
        preprocessing='SimAI integer always-balance NW-padding, no scaling',
        simulation_backend='SimAI/ns3; no native FAST simulator process',**(metadata or {})))
    full.metadata['delivery_chunks']={str(f.flow_id):tags[f.phase,f.stage_id,f.src,f.dst] for f in full.flows}
    return full


def schedule(matrix,hardware,algorithm,topology,dc_ids):
    prepared=prepare(matrix,hardware);raw=reference(prepared);raw['topology_snapshot']=topology
    tick=time.process_time_ns();extra={}
    if algorithm=='fast-simai':
        ps=raw['permutations'];qs=[s['alpha'] for s in raw['steps']]
    else:
        if str(RESEARCH) not in sys.path:sys.path.insert(0,str(RESEARCH))
        joint=importlib.import_module('joint_resource')
        objective,budget=algorithm.removeprefix('fast-joint-').rsplit('-b',1)
        plan=joint.plan(raw,1,objective,budget=int(budget))
        ps=plan['permutations'];qs=[s['capacity'] for s in plan['trace']]
        extra=dict(joint_plan=plan,planner_source_sha256=hashlib.sha256((RESEARCH/'joint_resource.py').read_bytes()).hexdigest())
    extra.update(planner_cpu_us=(time.process_time_ns()-tick)/1000,server_dc_ids=dc_ids)
    return materialize(prepared,ps,qs,hardware,algorithm,extra)
