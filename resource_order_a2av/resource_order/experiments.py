"""Reproducible transport acceptance and small exploratory ns-3 comparisons."""
import argparse
import csv
import json
from pathlib import Path
import random
import time

from .model import Message, Plan
from .scheduler import baseline, construct
from .simai import run
from .topology import build_topology


def demand(n,bytes_per_rank,kind,seed):
    """Fixed row totals; independently permuted destination ranks for Zipf 1.2.

    This is synthetic pair-size skew, NOT a measured MoE routing distribution.
    Independent row permutations need not produce a hot receiver. Integer
    largest-remainder rounding preserves exact totals and positive edges.
    """
    if n<2 or bytes_per_rank<n-1 or kind not in ('balanced','zipf','zipf-shared'):raise ValueError('invalid demand')
    rng=random.Random(seed);flows=[]
    popularity=list(range(n))
    if kind=='zipf-shared':rng.shuffle(popularity)
    global_weights={dst:(k+1)**-1.2 for k,dst in enumerate(popularity)}
    for src in range(n):
        dsts=[j for j in range(n) if j!=src]
        weights=[1 if kind=='balanced' else (j+1)**-1.2 for j in range(n-1)]
        if kind=='zipf':rng.shuffle(dsts)
        if kind=='zipf-shared':weights=[global_weights[dst] for dst in dsts]
        remaining=bytes_per_rank-(n-1);raw=[remaining*w/sum(weights) for w in weights]
        sizes=[1+int(x) for x in raw]
        for j in sorted(range(n-1),key=lambda j:(-(raw[j]-int(raw[j])),j))[:bytes_per_rank-sum(sizes)]:sizes[j]+=1
        flows += [Message(len(flows)+j,src,dst,size) for j,(dst,size) in enumerate(zip(dsts,sizes))]
    return flows


def pilot_network(fabric):
    overrides=({(24,28):(100,2),(25,28):(200,.7),(26,29):(100,1),(27,29):(400,.7)}
               if fabric=='tree' else {(24,28):(100,2),(24,29):(100,2),
                                       (25,28):(200,.7),(25,29):(200,.7)})
    return build_topology(fabric,racks=4,servers_per_rack=2,gpus_per_server=2,overrides=overrides)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('smoke','target-smoke','pilot','orthogonal','mechanism'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--binary',default='/home/julius/fast/SimAI/bin/SimAI_simulator')
    args=parser.parse_args()
    if args.output.exists():parser.error('choose a new output directory')
    args.output.mkdir(parents=True)
    if args.mode=='mechanism':
        from .search import optimize
        net=build_topology('leaf-spine',racks=2,servers_per_rack=2,gpus_per_server=2)
        # Two independent rack-local transfers; only 0->4 crosses racks.
        flows=[Message(0,0,2,2*1024*1024),Message(1,0,4,128*1024),Message(2,6,4,128*1024)]
        seed=Plan.from_order(flows,[0,1,2])
        result=optimize(seed,net,args.output/'gap',args.binary,neighborhood='gap',max_evaluations=16)
        print(json.dumps({k:v for k,v in result.items() if k not in ('evaluations','accepted','passes')},indent=2))
        return 0 if result['status']=='completed' else 1
    if args.mode=='smoke':
        net=build_topology('leaf-spine',racks=2,servers_per_rack=1,gpus_per_server=2)
        flows=[Message(0,0,2,1048576),Message(1,1,3,32768),Message(2,1,0,16384),Message(3,2,3,8192)]
        result=run(Plan.from_order(flows,[0,1,2,3]),net,args.output/'transport',args.binary)
        print(result['status'],result.get('delivery_ns'),flush=True)
        return 0 if result['status']=='success' else 1
    if args.mode=='target-smoke':
        net=build_topology('leaf-spine')
        flows=[]
        for src in range(128):
            for dst,size in ((src^1,32768),((src+32)%128,131072),((src+64)%128,131072)):
                flows.append(Message(len(flows),src,dst,size))
        result=run(baseline(flows,'spreadout',128),net,args.output/'transport',args.binary)
        print(result['status'],result.get('delivery_ns'),result.get('error'),flush=True)
        return 0 if result['status']=='success' else 1
    rows=[]
    methods=('spreadout','small-first','large-first','resource','no-alignment','no-resource')
    if args.mode=='orthogonal':methods=('anchor-only','fill-only')
    for fabric in ('tree','leaf-spine'):
        # 16 GPU pilot: 4 racks, 2 servers/rack, 2 GPUs/server.
        # Same physical NIC and local link rates as the target-scale topology.
        net=pilot_network(fabric)
        for load in ('balanced','zipf'):
            flows=demand(16,1024*1024,load,7)
            for method in methods:
                started=time.perf_counter()
                if method in ('anchor-only','fill-only'):
                    plan=construct(flows,net,anchor_shared=method=='anchor-only',fill_shared=method=='fill-only')
                else:
                    plan=(baseline(flows,method,16) if method in ('spreadout','small-first','large-first') else
                          construct(flows,net,alignment=method!='no-alignment',shared=method!='no-resource'))
                planning_ms=(time.perf_counter()-started)*1000
                result=run(plan,net,args.output/f'{fabric}-{load}-{method}',args.binary)
                row=dict(topology=fabric,load=load,seed=7,gpus=16,bytes_per_rank=1024*1024,method=method,
                         status=result['status'],delivery_us=result.get('delivery_ns',0)/1000 if result['status']=='success' else None,
                         ack_drain_us=result.get('ack_drain_ns',0)/1000 if result['status']=='success' else None,
                         planning_ms=planning_ms)
                rows.append(row);print(json.dumps(row),flush=True)
                with (args.output/'summary.csv').open('w') as f:
                    writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
    return 0 if all(row['status']=='success' for row in rows) else 1


if __name__=='__main__':raise SystemExit(main())
