#!/usr/bin/env python3
"""Three tiny functional fixtures, not a tuned performance sweep."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from simai_a2a.model import TrafficMatrix,HardwareProfile,LinkModel
from simai_a2a.topology import build_multidc_topology
from simai_a2a.leafspine import fixed_routes,server_snapshot,write_fabric
from simai_a2a.schedulers.simai_plan import prepare,reference,materialize
from simai_a2a.schedulers.resource_budget import plan
from simai_a2a.delivery import validate_fast_delivery
from simai_a2a.execution import modeled_scaleup_schedule
from simai_a2a.artifacts import write_matrix_artifact,write_schedule_artifact
from simai_a2a.scenario import canonical_sha256,write_micro_workload
from simai_a2a.runner import run_scenario,backend_shared_libraries
from simai_a2a.v2_diagnostics import collect

METHODS=('fast-simai','wan-budget-full','wan-budget-split')

def fixture(name):
    if name=='shared-wan':
        units=[[0,2,8,0],[2,0,0,8],[8,0,0,2],[0,8,2,0]];dcs,servers=2,2
    elif name=='independent-wan':
        units=[[0,8,0,0],[8,0,0,0],[0,0,0,8],[0,0,8,0]];dcs,servers=4,1
    elif name=='handoff':
        units=[[0,8,0,0],[8,0,0,0],[0,6,0,2],[0,0,8,0]];dcs,servers=4,1
    else:raise ValueError('unknown functional fixture')
    hw=HardwareProfile(4,2,LinkModel(100,.0023),LinkModel(700,.02),name,400,1)
    values=np.zeros((8,8),dtype=np.int64)
    for s in range(4):
        values[s*2,s*2+1]=(s+1)*4096
        for d in range(4):
            amount=units[s][d]*262144
            if amount:
                values[s*2,d*2+(s+d)%2]+=3*amount//2
                values[s*2+1,d*2+(s+d)%2]+=amount//2
    matrix=TrafficMatrix(values,dict(generator='literal-functional-fixture',name=name,server_channel_units=units,
        unit_bytes=262144,source_channel_split='3:1 before real balance',not_randomly_searched=True))
    topology=build_multidc_topology(hw,data_centers=dcs,servers_per_data_center=servers,
        intra_dc_bandwidth_gbps=400,intra_dc_latency_ns=700,inter_dc_bandwidth_gbps=100,inter_dc_latency_ns=50000)
    return matrix,hw,topology

def dump(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')

def run(out,binary,prepare_only=False):
    out.mkdir(parents=True,exist_ok=False)
    config=ROOT/'configs/ns3-leafspine.conf'
    meta=dict(complete=False,scope='three fixed tiny functional cases, not paper performance evidence',runs=[],
        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),binary=str(binary.resolve()),
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        backend_modules={n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in backend_shared_libraries(binary).items()},
        planner_sha256=hashlib.sha256((ROOT/'src/simai_a2a/schedulers/resource_budget.py').read_bytes()).hexdigest(),
        qp='same existing fresh-QP transport; no new QP or global barrier',cases=[])
    for name in ('independent-wan','shared-wan','handoff'):
        directory=out/name;directory.mkdir();matrix,hw,top=fixture(name);routes=fixed_routes(top)
        tm=write_fabric(top,routes,directory);tm.update(tor_port_capacity_gbps=400,foreground_start_ns=1000000)
        servers_per_dc=2 if name=='shared-wan' else 1
        dc_ids=[s//servers_per_dc for s in range(4)]
        snapshot=server_snapshot(top,hw,routes);dump(directory/'planner_topology.json',snapshot)
        mm=write_matrix_artifact(matrix,directory/'matrix');write_micro_workload(matrix,directory/'workload.csv')
        hardware=dict(schema_version=1,num_servers=4,gpus_per_server=2,intra_alpha_ns=100,intra_beta_ns_per_byte=.0023,
            inter_alpha_ns=700,inter_beta_ns_per_byte=.02,topology_id=name,link_bandwidth_gbps=400,oversubscription=1)
        dump(directory/'hardware.json',hardware);dump(directory/'scenario_manifest.json',dict(schema_version=1,name=name,topology=tm))
        (directory/'background.csv').write_text('');prepared=prepare(matrix,hw)
        meta['cases'].append(dict(name=name,logical_bytes=matrix.logical_bytes))
        for method in METHODS:
            tick=time.process_time_ns()
            if method=='fast-simai':
                result=reference(prepared);ps=result['permutations'];qs=[step['alpha'] for step in result['steps']]
            else:
                result=plan(prepared,snapshot,allow_split=method.endswith('split'));ps=result['permutations'];qs=result['coefficients']
            cpu=(time.process_time_ns()-tick)/1000
            dump(directory/(method+'.plan.json'),result)
            full=materialize(prepared,ps,qs,hw,method,dict(server_dc_ids=dc_ids,planner_cpu_us=cpu))
            validate_fast_delivery(matrix,full,2);schedule=modeled_scaleup_schedule(full,hw)
            write_schedule_artifact(schedule,directory/'schedules'/method,matrix_sha256=mm['matrix_sha256'],hardware_sha256=canonical_sha256(hardware))
            if prepare_only:continue
            outcome=run_scenario(directory,method,directory/'topology.txt',binary,config_template=config,
                timeout_s=90,ns3_threads=0,ns3_env={'NS_ATTRIBUTE_DEFAULT':None,'NS_GLOBAL_VALUE':None})
            row=dict(case=name,method=method,status=outcome['status'],success=outcome['success'],
                actual_ms=outcome.get('summary',{}).get('actual_makespan_ns',0)/1e6,
                stages=len(ps),planning_cpu_us=cpu,attempt_dir=outcome['attempt_dir'],error=outcome.get('error'),
                chosen_handoff=sum(step.get('reason')=='handoff' for step in result.get('trace',[])),
                real_only_progress_stages=sum(step.get('Z_R')==0 and step.get('Z_U',0)>0 for step in result.get('trace',[])),
                fallbacks=sum(step.get('fallback',False) for step in result.get('trace',[])))
            if outcome['success']:row.update(collect(schedule,matrix,Path(outcome['attempt_dir'])))
            meta['runs'].append(row);dump(out/'study.json',meta)
            print(name,method,outcome['status'],row['actual_ms'],flush=True)
            if not outcome['success']:raise RuntimeError(f'no retry: {row}')
    meta['complete']=not prepare_only and len(meta['runs'])==9 and all(r['success'] for r in meta['runs'])
    dump(out/'study.json',meta)
    if meta['runs']:
        with (out/'summary.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(meta['runs'][0]));writer.writeheader();writer.writerows(meta['runs'])
    return meta

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--binary',type=Path,default=Path('/home/julius/fast/SimAI/bin/SimAI_simulator'))
    parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    run(args.out.resolve(),args.binary,args.prepare_only)
