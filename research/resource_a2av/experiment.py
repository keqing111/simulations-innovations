#!/usr/bin/env python3
"""Fixed topology x demand experiments, reusing the existing four-method replay."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor,as_completed
import itertools
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[2]
EXPERIMENTS=ROOT/'astra-sim-alibabacloud/experiments/alltoall'
sys.path[:0]=[str(EXPERIMENTS/'tools'),str(EXPERIMENTS/'src')]
import run_resource_budget_iterations as iterations
import run_resource_budget_validation as audit
from simai_a2a.artifacts import write_matrix_artifact
from simai_a2a.leafspine import fixed_routes,server_snapshot,write_fabric
from simai_a2a.model import HardwareProfile,LinkModel
from simai_a2a.scenario import write_micro_workload
from simai_a2a.topology import Ns3Topology,TopologyLink
from simai_a2a.v2_workload import synthetic,catalog,trace_input
from run_v2_study import dump

CONFIG_PATH=Path(__file__).with_name('experiment_matrix.json')
CONFIG=json.loads(CONFIG_PATH.read_text())


def case_specs(phase):
    cfg=CONFIG['phases'][phase];cases=[]
    allowed={t['id']:t['gpu_counts'] for t in CONFIG['topologies']}
    for block in cfg.get('blocks',[dict(cfg,block=phase)]):
        families=block.get('families',block.get('models',[]));real='models' in block
        for kind,p,family,mib,seed,rho in itertools.product(block['topologies'],block['gpu_counts'],families,
                block.get('mib_per_rank',[4]),block.get('seeds',[1101]),block['rho']):
            if p not in allowed[kind] or family=='balanced' and seed!=block.get('seeds',[1101])[0]:continue
            for window in (block['windows'] if real else [0]):
                model=family if real else None
                stem=f'{model}-w{window}-lmid-b64' if model else f'{family}-s{seed}-m{mib}'
                name=f'{kind}-gpu{p}-{stem}-rho{rho:g}'
                short={'leaf-uniform':'U','leaf-mixed':'M','leaf-route-asymmetric':'A','pod-mixed':'P','multi-cut-mixed':'C'}.get(kind,kind)
                label=f'{short}/{p}G {family}\n'+(f'w{window} b64' if real else f'{mib}MiB r{rho:g}')
                cases.append(dict(name=name,group=kind+'/'+family,topology=kind,gpu_count=p,family=family,
                    mib=None if real else mib,seed=seed,rho=rho,window=window,layer=None,model=model,
                    block=block['block'],plot_label=label))
    if len({c['name'] for c in cases})!=len(cases):raise ValueError('duplicate experiment conditions')
    if 'planned_conditions' in cfg and len(cases)!=cfg['planned_conditions']:raise ValueError('registered case count changed')
    return cases


def fabric(kind,p):
    spec=next((t for t in CONFIG['topologies'] if t['id']==kind),None)
    if spec is None or p not in spec['gpu_counts']:raise ValueError('unsupported fabric size')
    n=p//4;racks=n//4
    hw=HardwareProfile(n,4,LinkModel(100,.0023),LinkModel(700,.02),kind,400,1)
    bg=tuple(range(p,p+racks));nv=tuple(range(p+racks,p+racks+n));start=p+racks+n
    tor=tuple(range(start,start+racks));start+=racks;links=[]
    def link(a,b,bw,delay=700):links.append(TopologyLink(a,b,bw,delay))
    for rank in range(p):link(rank,nv[rank//4],8/hw.intra.beta_ns_per_byte,hw.intra.alpha_ns)
    for server in range(n):link(nv[server],tor[server//4],400)
    for host,leaf in zip(bg,tor):link(host,leaf,400)
    if kind.startswith('leaf-'):
        spine=tuple(range(start,start+4));switches=tor+spine
        for rack,leaf in enumerate(tor):
            for k,sp in enumerate(spine):
                if 'rack_spine_gbps' in spec:
                    bw,delay=spec['rack_spine_gbps'][rack%4][k],spec['rack_spine_delay_ns'][rack%4][k]
                else:bw,delay=spec['rack_uplink_gbps'][rack%4],spec['rack_delay_ns'][rack%4]
                link(leaf,sp,bw,delay)
    elif kind.startswith('multi-cut'):
        agg=tuple(range(start,start+racks));switches=tor+agg;spine=agg
        for leaf,a in zip(tor,agg):link(leaf,a,400)
        for k,(a,b) in enumerate(zip(agg,agg[1:])):
            link(a,b,spec['cut_gbps'][k%len(spec['cut_gbps'])],spec.get('cut_delay_ns',[700])[k%len(spec.get('cut_delay_ns',[700]))])
    else:
        pods=racks//2;agg=tuple(range(start,start+2*pods));cores=tuple(range(start+2*pods,start+2*pods+2));switches=tor+agg+cores;spine=agg+cores
        for pod in range(pods):
            for leaf in tor[2*pod:2*pod+2]:
                for a in agg[2*pod:2*pod+2]:link(leaf,a,1600)
            for a,core in zip(agg[2*pod:2*pod+2],cores):
                link(a,core,spec['pod_uplink_gbps'][pod%4],spec.get('pod_delay_ns',[700])[pod%len(spec.get('pod_delay_ns',[700]))])
    top=Ns3Topology(tuple(range(p)),nv,tor[0],tuple(links),4,switch_ids=switches,tor_ids=tor,
                    spine_ids=spine,background_ids=bg)
    return top,hw


def shared_ids(top,snapshot):
    switches=set(top.regular_switch_ids);ids=[]
    for r,name in enumerate(snapshot['resource_names']):
        a,b=map(int,name.removeprefix('link:').split('->'))
        if a in switches and b in switches:ids.append(r)
    return ids


def background(top,routes,snapshot,rho):
    if not 0<=rho<1:raise ValueError('background rho must be in [0,1)')
    if not rho:return '',dict(interval_ns=None,max_configured_resource_rho=0.)
    groups=set(shared_ids(top,snapshot));names={name:r for r,name in enumerate(snapshot['resource_names'])}
    pairs=[(a,b) for a in top.background_ids for b in top.background_ids if a!=b];work=Counter();chunk=262144
    for a,b in pairs:
        node=a
        for _ in range(top.node_count):
            nxt=routes[node,b];resource=names.get(f'link:{node}->{nxt}')
            if resource in groups:work[resource]+=chunk
            node=nxt
            if node==b:break
        else:raise ValueError('background route loop')
    if not work:raise ValueError('background has no shared-resource work')
    required=max(v*8/snapshot['bandwidth_gbps'][r]/rho for r,v in work.items())
    interval=math.ceil(required)
    actual=max(v*8/snapshot['bandwidth_gbps'][r]/interval for r,v in work.items())
    return ''.join(f'{a},{b},{chunk},{interval},1000000000\n' for a,b in pairs),dict(
        interval_ns=interval,max_configured_resource_rho=actual,bytes_per_chunk=chunk,
        normalization='maximum offered load over physical shared switch links; not measured utilization')


def prepare_case(base,case,trace_root=None):
    directory=base/case['name'];directory.mkdir(parents=True,exist_ok=False)
    top,hw=fabric(case['topology'],case['gpu_count'])
    routes=fixed_routes(top,route_classes={h:i for i,h in enumerate(top.background_ids)})
    snapshot=server_snapshot(top,hw,routes)
    snapshot['shared_resource_ids']=shared_ids(top,snapshot)
    if case['model']:
        info=catalog(case['model'],trace_root);layer=info['layers'][1];case['layer']=layer
        matrix=trace_input(info,case['window'],layer,case['gpu_count'],64)
    else:matrix=synthetic(case['gpu_count'],case['family'],case['seed'],case['mib'])
    sends=matrix.values.sum(axis=1)
    case.update(logical_bytes=matrix.logical_bytes,send_bytes_min=int(sends.min()),send_bytes_max=int(sends.max()))
    write_matrix_artifact(matrix,directory/'matrix');write_micro_workload(matrix,directory/'workload.csv')
    hardware=dict(schema_version=1,num_servers=hw.num_servers,gpus_per_server=4,intra_alpha_ns=100,
        intra_beta_ns_per_byte=.0023,inter_alpha_ns=700,inter_beta_ns_per_byte=.02,topology_id=case['topology'],
        link_bandwidth_gbps=400,oversubscription=1)
    dump(directory/'hardware.json',hardware);dump(directory/'planner_topology.json',snapshot)
    meta=write_fabric(top,routes,directory);meta.update(kind=case['topology'],server_group_ids=[s//4 for s in range(hw.num_servers)],
        foreground_start_ns=max(1000000,math.ceil(40000*max(p['propagation_us'] for p in snapshot['server_paths']))),
        monitored_resources='all directed switch-switch links; legacy WAN filenames do not imply DC')
    # Retain the runtime's existing trace ABI, selecting physical shared ports.
    ports=Counter();monitored=[];switches=set(top.regular_switch_ids)
    for e in top.links:
        ports[e.src]+=1;ports[e.dst]+=1
        if e.src in switches and e.dst in switches:monitored.extend([[e.src,ports[e.src]],[e.dst,ports[e.dst]]])
    meta['gateway_wan_ports']=monitored
    tasks,pressure=background(top,routes,snapshot,case['rho']);(directory/'background.csv').write_text(tasks)
    dump(directory/'scenario_manifest.json',dict(schema_version=1,name=case['name'],topology=meta,case=case,
        pressure=pressure,group_policy='all physical directed switch-switch resources, overlapping path incidence',
        excluded_metrics=['legacy scalar100G normalized utilization'],qp_policy='existing fresh-QP policy unchanged'))
    return directory,top


def execute(job):
    output,source,case,timeout,hashes,binary,methods=job
    for path,expected in hashes.items():
        if audit.digest(Path(path))!=expected:raise ValueError('registered source changed')
    top,_=fabric(case['topology'],case['gpu_count'])
    result=iterations.run(Path(output),Path(binary),
        [case['name']],methods,source_scenario=Path(source),source_topology=top,timeout=timeout)
    for row in result['runs']:
        row.update(group=case['group'],reused=False);audit.attach_contract(row)
    return result['runs']


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--phase',choices=list(CONFIG['phases']),default='pilot');parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--timeout',type=float,default=180);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--binary',type=Path,default=Path('/home/julius/fast/SimAI/bin/SimAI_simulator'))
    parser.add_argument('--trace-root',type=Path,default=Path('/home/julius/graduation_design/origin_data'))
    parser.add_argument('--analyze-only',action='store_true')
    args=parser.parse_args();out=args.out.resolve()
    if args.analyze_only:
        study=json.loads((out/'study.json').read_text())
        for row in study['runs']:
            row.pop('input_contract',None);row.pop('contract_error',None);audit.attach_contract(row)
        phase=study['phase'];study.update(result_figure=f'{phase}_speedup.png',
            scope=f"fixed factorial; phase={phase}; configured background rho={sorted({c['rho'] for c in study['cases']})}; no online or general superiority claim",
            analysis_sha256=audit.digest(Path(__file__)))
        identities={r['input_contract']['binary_sha256'] for r in study['runs'] if r.get('input_contract')}
        study['backend_identity']=dict(sha256=next(iter(identities))) if len(identities)==1 else None
        shutil.copyfile(Path(__file__),out/'analysis_snapshot.py')
        shutil.copyfile(audit.__file__,out/'analysis_audit_snapshot.py')
        audit.finish(out,study);(out/'heldout_speedup.png').replace(out/study['result_figure']);return
    out.mkdir(parents=True,exist_ok=False);cases=case_specs(args.phase)
    methods=CONFIG['phases'][args.phase].get('methods',list(audit.METHODS))
    binary=args.binary.resolve()
    paths=[Path(__file__),CONFIG_PATH,EXPERIMENTS/'src/simai_a2a/schedulers/resource_budget.py',
           EXPERIMENTS/'configs/ns3-leafspine.conf',
           EXPERIMENTS/'src/simai_a2a/leafspine.py',EXPERIMENTS/'src/simai_a2a/v2_workload.py',
           EXPERIMENTS/'src/simai_a2a/schedulers/simai_plan.py',
           EXPERIMENTS/'tools/run_resource_budget_iterations.py',EXPERIMENTS/'tools/run_resource_budget_validation.py']
    hashes={str(p):audit.digest(p) for p in paths}
    if not args.prepare_only:hashes[str(binary)]=audit.digest(binary)
    dump(out/'source_hashes.json',hashes)
    for p in paths:shutil.copyfile(p,out/p.name)
    jobs=[]
    for case in cases:
        source,_=prepare_case(out/'inputs',case,args.trace_root);case['source']=str(source)
        jobs.append((str(out/'replays'/case['name']),str(source),case,args.timeout,hashes,str(binary),methods))
    planned=len(cases)*len(methods)
    if CONFIG['phases'][args.phase].get('planned_new_replays',planned)!=planned:raise ValueError('registered replay count changed')
    study=dict(complete=False,all_success=False,cases=cases,runs=[],phase=args.phase,methods=methods,planned_records=planned,
        source_hashes=hashes,planned_new_attempts=planned,workers=args.workers,
        backend_identity=None if args.prepare_only else dict(path=str(binary),sha256=hashes[str(binary)]),
        result_figure=f'{args.phase}_speedup.png',
        scope=f"fixed factorial; phase={args.phase}; configured background rho={sorted({c['rho'] for c in cases})}; no online or general superiority claim")
    dump(out/'study.json',study);print('REGISTERED',len(cases),'conditions;',planned,'methods',flush=True)
    if args.prepare_only:return
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(execute,job):case for job,case in zip(jobs,cases)}
        for future in as_completed(futures):
            case=futures[future]
            try:rows=future.result()
            except Exception as error:
                rows=audit.recover_worker(out/'replays'/case['name'],case['name'],methods,error)
                for row in rows:row['group']=case['group']
            study['runs'].extend(rows);dump(out/'study.json',study)
            print('REGISTERED RESULTS',len(study['runs']),'/',study['planned_records'],flush=True)
    audit.finish(out,study);(out/'heldout_speedup.png').replace(out/study['result_figure'])
    print(json.dumps(study['summary'],indent=2),flush=True)


if __name__=='__main__':main()
