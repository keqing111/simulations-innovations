"""Frozen-condition whole-flow comparisons; one ns-3 run per input/method.

No best-of-simulation selection. A failed method stays failed in the summary.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import time

from .experiments import demand,pilot_network
from .readiness import construct_ready
from .scheduler import baseline,construct,interleaved_ranks
from .simai import run
from .topology import build_topology


def network_for(fabric,scale,profile):
    if scale=='pilot':return pilot_network(fabric)
    servers=1 if scale=='32' else 4
    n=4*servers*8;tor=n+4*servers;upper=tor+4
    overrides={}
    if profile=='heterogeneous':
        if fabric=='leaf-spine':
            overrides={(tor+r,upper+s):(bw,.7) for r,bw in enumerate((400,200,400,200)) for s in range(2)}
        else:
            overrides={(tor+r,upper+r//2):(bw,.7) for r,bw in enumerate((800,400,800,400))}
            overrides.update({(upper,upper+2):(400,.7),(upper+1,upper+2):(800,.7)})
    return build_topology(fabric,racks=4,servers_per_rack=servers,gpus_per_server=8,overrides=overrides)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--methods',nargs='+',default=['spreadout','small-first','large-first','resource','no-resource','ready','ready-isolated','ready-no-align'])
    parser.add_argument('--seeds',nargs='+',type=int,default=[7,11])
    parser.add_argument('--loads',nargs='+',choices=['balanced','zipf','zipf-shared'],default=['balanced','zipf'])
    parser.add_argument('--topologies',nargs='+',choices=['tree','leaf-spine'],default=['tree','leaf-spine'])
    parser.add_argument('--kib',nargs='+',type=int,default=[1024])
    parser.add_argument('--scale',choices=['pilot','32','128'],default='pilot')
    parser.add_argument('--profile',choices=['uniform','heterogeneous'],default='heterogeneous')
    parser.add_argument('--binary',default='/home/julius/fast/SimAI/bin/SimAI_simulator')
    parser.add_argument('--trace',type=Path)
    parser.add_argument('--layer',type=int,default=12)
    parser.add_argument('--tokens-per-rank',type=int,default=16)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--native-bound',type=Path,help='optional exact bound kernel for cover methods')
    parser.add_argument('--resume',action='store_true',help='resume identical frozen inputs from saved cover checkpoints')
    args=parser.parse_args()
    allowed={'spreadout','spreadout-rack','spreadout-server','server-cyclic-resource','server-cyclic-volume','server-cyclic-resource-asc','small-first','large-first','resource','no-resource','ready','ready-isolated','ready-no-align','ready-finish','ready-tail','ready-tail-isolated','ready-tail-no-align','cover-capacity','cover-joint','cover-dependency'}
    allowed.update({'partial-joint-greedy','partial-joint-cyclic','partial-replay-greedy'})
    if set(args.methods)-allowed:parser.error('unknown method')
    if args.output.exists() and not args.resume:parser.error('choose a fresh output directory')
    if args.resume and not (args.output/'manifest.json').exists():parser.error('resume requires an existing manifest')
    if not args.seeds or any(x<=0 for x in args.kib):parser.error('invalid inputs')
    if args.native_bound:args.native_bound=args.native_bound.resolve(strict=True)
    args.output.mkdir(parents=True,exist_ok=args.resume)
    sources={str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in Path(__file__).parent.iterdir() if p.suffix in ('.py','.cpp')}
    manifest=dict(arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items() if k!='resume'},source_sha256=sources)
    if args.native_bound:manifest['native_bound_sha256']=hashlib.sha256(args.native_bound.read_bytes()).hexdigest()
    rows=[]
    if args.resume:
        old=json.loads((args.output/'manifest.json').read_text())
        if old['arguments']!=manifest['arguments']:parser.error('resume arguments differ from original run')
        for name in ('cover.py','model.py','topology.py','experiments.py','simai.py','scheduler.py','dependency_native.cpp'):
            if old['source_sha256'].get(name)!=sources.get(name):parser.error('resume algorithm source changed: '+name)
        if old.get('native_bound_sha256')!=manifest.get('native_bound_sha256'):parser.error('resume native kernel changed')
        with (args.output/'resumes.jsonl').open('a') as f:
            f.write(json.dumps(dict(time_unix=time.time(),**manifest))+'\n')
        if (args.output/'summary.csv').exists():
            with (args.output/'summary.csv').open() as f:rows=list(csv.DictReader(f))
    else:(args.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    finished={r['path'] for r in rows}
    for fabric in args.topologies:
        net=network_for(fabric,args.scale,args.profile)
        n=net.gpu_count
        for load in (['trace'] if args.trace else args.loads):
            for seed in (args.seeds[:1] if load in ('balanced','trace') else args.seeds):
                for kib in ([0] if args.trace else args.kib):
                    metadata=None
                    if args.trace:
                        from .trace_load import load_dispatch
                        flows,metadata=load_dispatch(args.trace,layer=args.layer,ranks=n,tokens_per_rank=args.tokens_per_rank,offset=args.offset)
                    else:flows=demand(n,kib*1024,load,seed)
                    for method in args.methods:
                        dest=args.output/f'{fabric}-{load}-s{seed}-{kib}k-{method}'
                        if str(dest) in finished:continue  # never rerun successful OR failed completed cases
                        if dest.exists():raise ValueError('unrecorded run directory requires inspection: '+str(dest))
                        started=time.perf_counter();diagnostic=None;cover_state=None;prior_s=0.
                        if method.startswith(('cover-','partial-joint-')):
                            from .cover import initial_cover,cover_plan,minimize_joint,minimize_proxy_cached,queue_cover
                            ranks=interleaved_ranks(net,across_racks=False);history=[];start_cover=initial_cover(ranks)
                            partial=method.startswith('partial-')
                            if partial:
                                initial=(construct(flows,net,shared=False) if method.endswith('greedy') else
                                         baseline(flows,'spreadout',n,rank_order=ranks))
                                start_cover=queue_cover(initial,n)
                            checkpoint_path=Path(str(dest)+'.planning.json')
                            if args.resume and checkpoint_path.exists():
                                saved=json.loads(checkpoint_path.read_text())
                                if list(ranks)!=saved['rank_order']:raise ValueError('checkpoint ranks differ')
                                start_cover=tuple(tuple(row) for row in saved['cover'])
                                # A checkpoint is BEFORE its recorded move. Re-evaluate this
                                # state, replacing the last record, not applying it twice.
                                history=saved['history'][:-1];prior_s=saved['planning_s']
                                cover_plan(flows,start_cover,ranks)  # pair/byte validation
                            prefix=len(history)
                            def checkpoint(c,record):
                                nonlocal cover_state
                                record=dict(record,iteration=prefix+record['iteration'])
                                history.append(record)
                                cover_state=dict(status='planning' if record['move'] else 'proxy_stationary',
                                    planning_s=prior_s+time.perf_counter()-started,rank_order=ranks,cover=c,history=history)
                                temporary=checkpoint_path.with_suffix('.json.tmp')
                                temporary.write_text(json.dumps(cover_state));temporary.replace(checkpoint_path)
                            if method=='cover-capacity':
                                c,_=minimize_proxy_cached(flows,start_cover,net,on_step=checkpoint)
                            else:
                                c,_=minimize_joint(flows,start_cover,net,on_step=checkpoint,cache_pairs=True,
                                    native_bound=args.native_bound,dependency_only=method=='cover-dependency',partial=partial)
                            plan=cover_plan(flows,c,ranks)
                        elif method=='partial-replay-greedy':
                            from .cover import queue_cover,cover_plan
                            initial=construct(flows,net,shared=False)
                            plan=cover_plan(flows,queue_cover(initial,n),range(n))
                            if plan.dependency_key()!=initial.dependency_key():raise ValueError('replay changed queues')
                        elif method.startswith('ready'):
                            plan,diagnostic=construct_ready(flows,net,shared=not method.endswith('isolated'),
                                alignment=not method.endswith('no-align'),objective='tail' if 'tail' in method else 'finish' if 'finish' in method else 'span')
                        elif method.startswith('server-cyclic-'):
                            from .cyclic import order_by_work
                            ranks=interleaved_ranks(net,across_racks=False)
                            initial=baseline(flows,'spreadout',n,rank_order=ranks)
                            plan,_=order_by_work(initial,ranks,net,resource=method!='server-cyclic-volume',descending=not method.endswith('asc'))
                        elif method in ('spreadout-rack','spreadout-server'):
                            plan=baseline(flows,'spreadout',n,rank_order=interleaved_ranks(net,across_racks=method=='spreadout-rack'))
                        elif method in ('resource','no-resource'):
                            plan=construct(flows,net,shared=method=='resource')
                        else:plan=baseline(flows,method,n)
                        planning_ms=(prior_s+time.perf_counter()-started)*1000
                        result=run(plan,net,dest,args.binary)
                        if metadata is not None:(dest/'trace-mapping.json').write_text(json.dumps(metadata,indent=2))
                        if diagnostic is not None:(dest/'planning-model.json').write_text(json.dumps(diagnostic,indent=2))
                        if cover_state is not None:(dest/'cover-planning.json').write_text(json.dumps(cover_state))
                        row=dict(topology=fabric,profile=args.profile if args.scale!='pilot' else 'pilot',load=load,seed=seed,gpus=n,kib_per_rank=kib,method=method,
                            status=result['status'],delivery_us=result.get('delivery_ns',0)/1000 if result['status']=='success' else None,
                            planning_ms=planning_ms,predicted_us=diagnostic['predicted_delivery_us'] if diagnostic else None,
                            path=str(dest))
                        rows.append(row);print(json.dumps(row),flush=True)
                        summary_tmp=args.output/'summary.csv.tmp'
                        with summary_tmp.open('w') as out:
                            writer=csv.DictWriter(out,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
                        summary_tmp.replace(args.output/'summary.csv')
    return int(any(r['status']!='success' for r in rows))


if __name__=='__main__':raise SystemExit(main())
