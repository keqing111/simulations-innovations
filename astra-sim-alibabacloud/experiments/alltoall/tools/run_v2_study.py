#!/usr/bin/env python3
"""Resumable paired v2 SimAI/ns-3 campaign. No native FAST process or new policy."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from simai_a2a.artifacts import read_matrix_artifact,read_schedule_artifact,write_matrix_artifact,write_schedule_artifact
from simai_a2a.delivery import validate_fast_delivery
from simai_a2a.execution import modeled_scaleup_schedule
from simai_a2a.leafspine import build_leafspine,fixed_routes,server_snapshot,write_fabric
from simai_a2a.metrics import matrix_skew_metrics
from simai_a2a.model import HardwareProfile,LinkModel
from simai_a2a.runner import run_scenario,execution_fingerprint,DEFAULT_CONFIG_TEMPLATE
from simai_a2a.scenario import canonical_sha256,write_micro_workload
from simai_a2a.schedulers.simai_plan import schedule as fast_schedule
from simai_a2a.schedulers.spreadout import SpreadOutScheduler
from simai_a2a.v2_workload import MODELS,catalog,synthetic,trace_input

METHODS=('fast-simai','spreadout','fast-joint-stage-b1','fast-joint-stage-b4',
         'fast-joint-capacity-b1','fast-joint-capacity-b4')
FROZEN=Path('/home/julius/fast/FAST/ds/decomposition/joint_resource.py')
FROZEN_SHA='68b4639d199edb950ce47ecccc54bbf79e87bddb1c1db0dfffda954c89be68d4'
ENV={'NS_ATTRIBUTE_DEFAULT':None,'NS_GLOBAL_VALUE':None}
CONFIG=ROOT/'configs/ns3-leafspine.conf'

def dump(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n');tmp.replace(path)

def case_specs(catalogs):
    cases={}
    def add(dc,family='balanced',seed=1101,mib=4,rho=0.,model=None,window=1,layer=None):
        stem=f'{model}-w{window}-l{layer}-b64' if model else f'{family}-s{seed}-m{mib}'
        name=f'dc{dc}-{stem}-rho{rho:g}'
        cases[name]=dict(name=name,dc=dc,family=family,seed=seed,mib=mib,rho=rho,model=model,window=window,layer=layer)
    for dc in (2,3,4):
        add(dc)
        for family in ('zipf08','zipf12'):
            for seed in (1101,1102,1103):add(dc,family,seed)
    for mib in (1,16):
        add(4,mib=mib)
        for family in ('zipf08','zipf12'):
            for seed in (1101,1102,1103):add(4,family,seed,mib)
    for dc in (2,3,4):
        for model,info in catalogs.items():
            if info['experts']%(32*dc):continue
            for window in range(3):
                for layer in info['layers']:add(dc,model=model,window=window,layer=layer)
    for rho in (.3,.6,.85):
        add(4,rho=rho);add(4,'zipf12',rho=rho)
        for model,info in catalogs.items():add(4,model=model,layer=info['layers'][len(info['layers'])//2],rho=rho)
    return list(cases.values())

def prepare_case(base,c,catalogs):
    directory=base/c['name'];directory.mkdir(parents=True,exist_ok=True)
    hw=HardwareProfile(8*c['dc'],4,LinkModel(100,.0023),LinkModel(700,.02),'v2-leafspine',400,1)
    if (directory/'scenario_manifest.json').exists():return
    matrix=(trace_input(catalogs[c['model']],c['window'],c['layer'],hw.num_devices,64) if c['model'] else
            synthetic(hw.num_devices,c['family'],c['seed'],c['mib']))
    write_matrix_artifact(matrix,directory/'matrix');write_micro_workload(matrix,directory/'workload.csv')
    hardware=dict(schema_version=1,num_servers=hw.num_servers,gpus_per_server=4,
        intra_alpha_ns=100,intra_beta_ns_per_byte=.0023,inter_alpha_ns=700,inter_beta_ns_per_byte=.02,
        topology_id=hw.topology_id,link_bandwidth_gbps=400,oversubscription=1)
    dump(directory/'hardware.json',hardware)
    topology=build_leafspine(hw,c['dc']);routes=fixed_routes(topology);tm=write_fabric(topology,routes,directory)
    tm['foreground_start_ns']=max(1000000,20*2*54400)
    dump(directory/'planner_topology.json',server_snapshot(topology,hw,routes))
    rows=[]
    if c['rho']:
        interval=round(262144*8/(c['rho']*100))
        rows=[f'{a},{b},262144,{interval},1000000000\n' for a in topology.background_ids for b in topology.background_ids if a!=b]
    (directory/'background.csv').write_text(''.join(rows))
    dc=matrix.values.reshape(c['dc'],32,c['dc'],32).sum(axis=(1,3))
    sends=matrix.values.sum(axis=1);receives=matrix.values.sum(axis=0)
    dump(directory/'features.json',dict(**matrix_skew_metrics(matrix,4),logical_bytes=matrix.logical_bytes,
        source_cv=float(np.std(sends)/np.mean(sends)),destination_cv=float(np.std(receives)/np.mean(receives)),
        destination_top10_share=float(sum(sorted(receives,reverse=True)[:int(np.ceil(hw.num_devices*.1))]))/matrix.logical_bytes,
        active_pair_fraction=float(np.count_nonzero(matrix.values))/(hw.num_devices*(hw.num_devices-1)),
        dc_bytes=dc.tolist(),wan_fraction=1-float(np.trace(dc))/matrix.logical_bytes,
        same_server_bytes=sum(int(matrix.values[s*4:(s+1)*4,s*4:(s+1)*4].sum()) for s in range(hw.num_servers))))
    dump(directory/'scenario_manifest.json',dict(schema_version=1,name=c['name'],topology=tm,case=c,
        qp_policy='fresh QP per message; no reusable message API; no QP sensitivity requested',
        background='independent 256KiB RDMA chunks; same DCQCN/priority; periodic offered-rate; horizon1s',
        baseline='FAST family uses integer balance/padding; spreadout sends raw GPU pairs with its round protocol; not native binary',
        pfc='10ms pause lease with explicit early resume; prevents unrefreshed5us lease from expiring before WAN drain'))

def prepare_cases(base,cases,catalogs):
    valid=[];errors=[]
    for case in cases:
        try:prepare_case(base,case,catalogs)
        except Exception as exc:
            row=dict(case=case['name'],method=None,success=False,status='preparation-error',error=repr(exc))
            errors.append(row);dump(base/(case['name']+'.preparation-error.json'),row)
        else:valid.append(case)
    return valid,errors

def execute(job):
    directory,method,binary,timeout=job;directory=Path(directory)
    if hashlib.sha256(FROZEN.read_bytes()).hexdigest()!=FROZEN_SHA:raise RuntimeError('frozen planner changed')
    hwj=json.loads((directory/'hardware.json').read_text());hw=HardwareProfile(hwj['num_servers'],4,
        LinkModel(100,.0023),LinkModel(700,.02),hwj['topology_id'],400,1)
    matrix,mm=read_matrix_artifact(directory/'matrix');source=directory/'schedules'/method
    begin=time.monotonic_ns()
    if not (source/'schedule_manifest.json').exists():
        cpu=time.process_time_ns()
        if method=='spreadout':
            full=SpreadOutScheduler().schedule(matrix,hw,seed=1101)
            actual=np.zeros_like(matrix.values)
            for f in full.flows:actual[f.src,f.dst]+=f.size_bytes
            if not np.array_equal(actual,matrix.values):raise ArithmeticError('spreadout byte loss')
            full.metadata['delivery_chunks']={str(f.flow_id):[[f.src,f.dst,f.size_bytes]] for f in full.flows}
        else:
            full=fast_schedule(matrix,hw,method,json.loads((directory/'planner_topology.json').read_text()),
                               [s//8 for s in range(hw.num_servers)])
            validate_fast_delivery(matrix,full,4)
        full.metadata.update(server_dc_ids=[s//8 for s in range(hw.num_servers)],adapter_process_cpu_us=(time.process_time_ns()-cpu)/1000)
        projected=modeled_scaleup_schedule(full,hw)
        write_schedule_artifact(projected,source,matrix_sha256=mm['matrix_sha256'],hardware_sha256=canonical_sha256(hwj))
    schedule,_=read_schedule_artifact(source)
    signature=execution_fingerprint(directory,method,directory/'topology.txt',binary,CONFIG,0,ns3_env=ENV)
    runs=directory/'runs'/method
    result=None
    for path in sorted(runs.glob('attempt-*/run_manifest.json'),reverse=True):
        candidate=json.loads(path.read_text())
        if candidate.get('success') and candidate.get('input_fingerprint')==signature:result=candidate;break
    if result is None:
        print('START',directory.name,method,flush=True)
        result=run_scenario(directory,method,directory/'topology.txt',binary,timeout_s=timeout,ns3_threads=0,ns3_env=ENV,config_template=CONFIG)
    summary=result.get('summary',{})
    row=dict(case=directory.name,method=method,status=result['status'],success=result['success'],
        attempt_dir=result['attempt_dir'],stage_count=schedule.metadata['n_stages'],
        communication_ns=summary.get('actual_makespan_ns'),logical_bytes=matrix.logical_bytes,network_bytes=schedule.physical_bytes,
        planning_cpu_us=schedule.metadata.get('adapter_process_cpu_us'),elapsed_wall_ns=result.get('elapsed_wall_ns'),
        error=result.get('error'),wan_busy_mean=summary.get('native_wan',{}).get('busy_fraction_mean'))
    if result['success']:
        from simai_a2a.v2_diagnostics import collect
        row.update(collect(schedule,matrix,Path(result['attempt_dir'])))
    dump(source/'v2_result.json',row)
    print('DONE',directory.name,method,result['status'],row['communication_ns'],flush=True)
    return row

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--timeout',type=float,default=600);parser.add_argument('--limit-cases',type=int)
    parser.add_argument('--filter',default='');parser.add_argument('--methods',nargs='+',choices=METHODS,default=METHODS)
    parser.add_argument('--trace-root',type=Path,default=Path('/home/julius/graduation_design/origin_data'))
    parser.add_argument('--binary',type=Path,default=Path('/home/julius/fast/SimAI/bin/SimAI_simulator'))
    parser.add_argument('--synthetic-only',action='store_true')
    args=parser.parse_args();args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=True)
    path=args.out/'trace_catalogs.json'
    if args.synthetic_only:catalogs={}
    elif path.exists():catalogs=json.loads(path.read_text())
    else:
        catalogs={name:catalog(name,args.trace_root) for name in MODELS};dump(path,catalogs)
    cases=case_specs(catalogs)
    if args.filter:cases=[c for c in cases if args.filter in c['name']]
    if args.limit_cases:cases=cases[:args.limit_cases]
    if not cases:parser.error('no cases selected')
    cases,preparation_errors=prepare_cases(args.out,cases,catalogs)
    jobs=[]
    for c in cases:
        jobs.extend((str(args.out/c['name']),m,str(args.binary.resolve()),args.timeout) for m in args.methods)
    meta=dict(complete=False,cases=cases,planned_runs=len(jobs),runs=[],frozen_planner_sha=FROZEN_SHA,
        qp='fresh; no reuse API and no sensitivity grid per user',preparation_errors=preparation_errors,
        execution='event-driven SimAI/ns3; modeled local engine; no global barrier')
    dump(args.out/'study.json',meta)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(execute,j):j for j in jobs}
        for future in as_completed(futures):
            job=futures[future]
            try:row=future.result()
            except Exception as exc:
                row=dict(case=Path(job[0]).name,method=job[1],success=False,status='exception',error=repr(exc))
                dump(args.out/(row['case']+'-'+row['method']+'.error.json'),row)
                print('ERROR',row,flush=True)
            meta['runs'].append(row);dump(args.out/'study.json',meta)
            keys=sorted({k for r in meta['runs'] for k in r})
            with (args.out/'summary.csv').open('w') as stream:
                writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(meta['runs'])
    meta['complete']=not preparation_errors and len(meta['runs'])==len(jobs) and all(r['success'] for r in meta['runs']);dump(args.out/'study.json',meta)

if __name__=='__main__':main()
