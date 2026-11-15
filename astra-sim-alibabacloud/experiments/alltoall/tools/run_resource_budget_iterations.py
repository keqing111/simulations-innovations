#!/usr/bin/env python3
"""Small, fixed mechanism families; all attempts retained, no automatic retry."""
import argparse
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import time
import types
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from run_wan_budget_smoke import fixture as original_fixture
from simai_a2a.model import TrafficMatrix
from simai_a2a.leafspine import build_leafspine,fixed_routes,server_snapshot,write_fabric
from simai_a2a.schedulers.simai_plan import prepare,reference,materialize
from simai_a2a.schedulers.resource_budget import plan,prepare_resources,anchored_cap,evaluate_round
from simai_a2a.schedulers.spreadout import SpreadOutScheduler
from simai_a2a.delivery import validate_fast_delivery
from simai_a2a.execution import modeled_scaleup_schedule
from simai_a2a.artifacts import write_matrix_artifact,write_schedule_artifact
from simai_a2a.scenario import canonical_sha256,write_micro_workload,load_hardware
from simai_a2a.runner import run_scenario,backend_shared_libraries
from simai_a2a.v2_diagnostics import collect

BASE='0ab4a30';MODULE='astra-sim-alibabacloud/experiments/alltoall/src/simai_a2a/schedulers/resource_budget.py'
METHODS=('fast-simai','spreadout','legacy-v1','prior-v2','resource-eager','resource-near','resource-full','resource-split','resource-maxq','volume-maxq')


def volume_plan(prepared,topology):
    """Same maximum coefficient and transport, no topology-based choice."""
    started=time.process_time_ns();res=prepare_resources(topology)
    R=[r[:] for r in prepared['R']];U=prepared['U'][:];trace=[]
    while any(v for row in R for v in row):
        q,p=anchored_cap(R);step=evaluate_round(R,U,p,q,res)
        R,U=step.pop('R_next'),step.pop('U_next');step.update(reason='volume_only',fallback=False)
        trace.append(step)
    if any(U):raise ArithmeticError('volume baseline left real bytes')
    return dict(permutations=[s['p'] for s in trace],coefficients=[s['q'] for s in trace],
                trace=trace,remaining_U=U,planner_cpu_us=(time.process_time_ns()-started)/1000)


def fixture(name):
    if name in ('independent-wan','shared-wan','handoff'):return original_fixture(name)
    matrix,hw,top=original_fixture('shared-wan' if name=='shared-dense' else 'handoff')
    units=[[0,8,0,0],[8,0,0,0],[0,6,0,2],[0,0,8,0]]
    if name.startswith('chain'):
        units=[[0,2,0,6],[2,0,6,0],[0,6,0,2],[6,0,2,0]]
        if name=='chain-tied-supported':units[1][2]=units[2][1]=10
        if name=='chain-near':units[1][2]=units[2][1]=9
        gw=top.gateway_ids
        middle=150 if name=='chain-tied' else 200 if name in ('chain-tied-supported','chain-near') else 50 if name=='chain-heldout' else 100
        top=replace(top,links=tuple(replace(e,bandwidth_gbps=(middle if {e.src,e.dst}=={gw[1],gw[2]} else e.bandwidth_gbps))
            for e in top.links if not(e.src in gw and e.dst in gw) or abs(gw.index(e.src)-gw.index(e.dst))==1))
    elif name=='shared-dense':units=[[8*int(s!=d) for d in range(4)] for s in range(4)]
    elif name=='handoff-tied':units[2][1]=8
    elif name=='handoff-small':units=[[v//2 for v in row] for row in units]
    elif name=='handoff-large':units=[[v*2 for v in row] for row in units]
    elif name=='handoff-heavy-witness':units[2][3]=6
    elif name=='handoff-tail':units[2][0]=8
    elif name in ('holdout-delay','holdout-bw50','holdout-access100'):
        gw=top.gateway_ids
        top=replace(top,links=tuple(replace(e,
            latency_ns=100000 if name=='holdout-delay' and e.src in gw and e.dst in gw else e.latency_ns,
            bandwidth_gbps=(50 if e.src in gw and e.dst in gw else e.bandwidth_gbps) if name=='holdout-bw50'
                else 100 if name=='holdout-access100' and e.src not in top.gpu_ids and e.dst not in top.gpu_ids
                else e.bandwidth_gbps) for e in top.links))
    elif name=='holdout-demand':units=[[0,10,0,0],[6,0,0,2],[0,5,0,3],[0,0,10,0]]
    elif name=='handoff-permuted':
        order=[2,1,0,3];units=[[units[s][d] for d in order] for s in order]
    else:raise ValueError('unknown mechanism fixture: '+name)
    values=np.zeros((8,8),dtype=np.int64)
    for s in range(4):
        values[s*2,s*2+1]=(s+1)*4096
        for d in range(4):
            amount=units[s][d]*262144
            values[s*2,d*2+(s+d)%2]+=3*amount//2
            values[s*2+1,d*2+(s+d)%2]+=amount//2
    return TrafficMatrix(values,dict(generator='literal-iteration-fixture',name=name,units=units)),hw,top


def dump(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def run(out,binary,cases,methods,prepare_only=False,source_scenario=None,timeout=90,source_topology=None):
    out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(ROOT/'src/simai_a2a/schedulers/resource_budget.py',out/'planner_snapshot.py')
    shutil.copyfile(Path(__file__),out/'experiment_snapshot.py')
    legacy_source=subprocess.check_output(['git','show',BASE+':'+MODULE],cwd=ROOT,text=True)
    legacy=types.ModuleType('frozen_resource_budget');exec(compile(legacy_source,'frozen:'+BASE,'exec'),legacy.__dict__)
    prior_source=subprocess.check_output(['git','show','51ca9ee:'+MODULE],cwd=ROOT,text=True)
    prior=types.ModuleType('prior_resource_budget');exec(compile(prior_source,'frozen:51ca9ee','exec'),prior.__dict__)
    config=ROOT/'configs/ns3-leafspine.conf'
    study=dict(complete=False,cases=cases,methods=methods,planned_runs=len(cases)*len(methods),runs=[],
        legacy_commit=BASE,legacy_sha256=hashlib.sha256(legacy_source.encode()).hexdigest(),
        prior_commit='51ca9ee',prior_sha256=hashlib.sha256(prior_source.encode()).hexdigest(),
        planner_sha256=hashlib.sha256((ROOT/'src/simai_a2a/schedulers/resource_budget.py').read_bytes()).hexdigest(),
        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        backend_modules={n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in backend_shared_libraries(binary).items()},
        source_scenario=str(source_scenario) if source_scenario else None,
        scope='fixed exploratory cases and explicitly reused inputs; not formal performance evidence')
    for name in cases:
        directory=out/name;directory.mkdir()
        if source_scenario:
            hw,hardware,_=load_hardware(source_scenario/'hardware.json')
            matrix=TrafficMatrix(np.load(source_scenario/'matrix/matrix.npy'),{'source_scenario':str(source_scenario)})
            top=source_topology or build_leafspine(hw,hw.num_servers//8)
            if top.serialize()!=(source_scenario/'topology.txt').read_text():raise ValueError('source topology differs from fixed v2 fabric')
            routes={(int(u),int(d)):int(v) for u,d,v in csv.reader((source_scenario/'routes.csv').open())}
        else:matrix,hw,top=fixture(name);routes=fixed_routes(top)
        tm=write_fabric(top,routes,directory);tm.update(tor_port_capacity_gbps=400,foreground_start_ns=1000000)
        if source_scenario:
            tm.update(json.loads((source_scenario/'scenario_manifest.json').read_text())['topology'])
        dc_ids=tm.get('server_group_ids',[s//(hw.num_servers//max(1,len(top.gateway_ids))) for s in range(hw.num_servers)])
        snapshot=server_snapshot(top,hw,routes)
        if source_scenario and (source_scenario/'planner_topology.json').exists():
            supplied=json.loads((source_scenario/'planner_topology.json').read_text())
            if 'shared_resource_ids' in supplied:
                if any(supplied[k]!=snapshot[k] for k in ('n','g','resource_names','bandwidth_gbps','server_paths')):
                    raise ValueError('source planner topology differs from physical routes')
                snapshot['shared_resource_ids']=supplied['shared_resource_ids']
        dump(directory/'planner_topology.json',snapshot)
        mm=write_matrix_artifact(matrix,directory/'matrix');write_micro_workload(matrix,directory/'workload.csv')
        if not source_scenario:
            hardware=dict(schema_version=1,num_servers=4,gpus_per_server=2,intra_alpha_ns=100,intra_beta_ns_per_byte=.0023,
                inter_alpha_ns=700,inter_beta_ns_per_byte=.02,topology_id=name,link_bandwidth_gbps=400,oversubscription=1)
        dump(directory/'hardware.json',hardware);dump(directory/'scenario_manifest.json',dict(schema_version=1,name=name,topology=tm))
        (directory/'background.csv').write_text('');prepared=prepare(matrix,hw)
        if source_scenario and (source_scenario/'background.csv').exists():shutil.copyfile(source_scenario/'background.csv',directory/'background.csv')
        for method in methods:
            tick=time.process_time_ns();phase='preparation'
            row=dict(case=name,method=method,status='preparation_failed',success=False,actual_ms=None,
                stages=None,planning_cpu_us=None,planner_cpu_us=None,attempt_dir=None,error=None,
                handoffs=0,fallbacks=0,reference_stages=0,relaxed_stages=0)
            try:
                if method=='spreadout':
                    result={};full=SpreadOutScheduler().schedule(matrix,hw,seed=1)
                    full.metadata.update(delivery_chunks={str(f.flow_id):[[f.src,f.dst,f.size_bytes]] for f in full.flows},server_dc_ids=dc_ids)
                else:
                    if method=='fast-simai':
                        result=reference(prepared);ps=result['permutations'];qs=[s['alpha'] for s in result['steps']]
                    else:
                        planner=legacy.plan if method=='legacy-v1' else prior.plan if method=='prior-v2' else plan
                        extra={'split_guard':False} if method=='resource-eager' else {'near_anchors':True} if method=='resource-near' else {'maximum_quantum':True} if method=='resource-maxq' else {}
                        result=volume_plan(prepared,snapshot) if method=='volume-maxq' else planner(prepared,snapshot,allow_split=method!='resource-full',**extra)
                        ps,qs=result['permutations'],result['coefficients']
                    full=materialize(prepared,ps,qs,hw,method,dict(server_dc_ids=dc_ids))
                    validate_fast_delivery(matrix,full,hw.gpus_per_server)
                row.update(planning_cpu_us=(time.process_time_ns()-tick)/1000,planner_cpu_us=result.get('planner_cpu_us'),
                    handoffs=sum(s.get('reason')=='handoff' for s in result.get('trace',[])),
                    fallbacks=sum(s.get('fallback',False) for s in result.get('trace',[])),
                    reference_stages=sum(s.get('reason')=='maximum_reference' for s in result.get('trace',[])),
                    relaxed_stages=sum(s.get('effective_H_us',0)>(s.get('H_us') or 0)+1e-9 for s in result.get('trace',[])))
                dump(directory/(method+'.plan.json'),result)
                schedule=modeled_scaleup_schedule(full,hw);row['stages']=schedule.metadata['n_stages']
                write_schedule_artifact(schedule,directory/'schedules'/method,matrix_sha256=mm['matrix_sha256'],hardware_sha256=canonical_sha256(hardware))
                if prepare_only:row.update(status='prepared',success=True)
                else:
                    phase='execution'
                    outcome=run_scenario(directory,method,directory/'topology.txt',binary,config_template=config,
                        timeout_s=timeout,ns3_threads=0,ns3_env={'NS_ATTRIBUTE_DEFAULT':None,'NS_GLOBAL_VALUE':None})
                    summary=outcome.get('summary',{})
                    row.update(status=outcome['status'],success=outcome['success'],backend_success=outcome['success'],
                        actual_ms=summary.get('actual_makespan_ns',0)/1e6 if outcome['success'] else None,
                        attempt_dir=outcome['attempt_dir'],error=outcome.get('error'))
                    if outcome['success']:
                        phase='validation';row.update(collect(schedule,matrix,Path(outcome['attempt_dir'])))
            except Exception as error:
                unsupported=method=='legacy-v1' and isinstance(error,ValueError) and 'multi-WAN path' in str(error)
                row.update(status='unsupported' if unsupported else phase+'_failed',success=False,
                    actual_ms=None,error=type(error).__name__+': '+str(error))
            study['runs'].append(row);dump(out/'study.json',study)
            print(name,method,row['status'],row['actual_ms'],flush=True)
    study['complete']=not prepare_only and len(study['runs'])==study['planned_runs']
    study['all_success']=all(r['success'] for r in study['runs']) if study['runs'] else False
    dump(out/'study.json',study)
    if study['runs']:
        with (out/'summary.csv').open('w') as f:
            keys=list(dict.fromkeys(k for row in study['runs'] for k in row))
            writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(study['runs'])
    return study


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--binary',type=Path,default=Path('/home/julius/fast/SimAI/bin/SimAI_simulator'))
    p.add_argument('--cases',nargs='+',default=['independent-wan','shared-wan','handoff'])
    p.add_argument('--methods',nargs='+',choices=METHODS,default=list(METHODS));p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--source-scenario',type=Path);p.add_argument('--timeout',type=float,default=90)
    args=p.parse_args()
    cases=[args.source_scenario.name] if args.source_scenario else args.cases
    run(args.out.resolve(),args.binary,cases,args.methods,args.prepare_only,args.source_scenario,args.timeout)
