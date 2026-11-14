#!/usr/bin/env python3
"""Paired SimAI/ns-3 deployment check, not a tuned paper benchmark campaign."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from simai_a2a.artifacts import write_matrix_artifact,write_schedule_artifact
from simai_a2a.delivery import validate_fast_delivery
from simai_a2a.execution import modeled_scaleup_schedule
from simai_a2a.metrics import matrix_skew_metrics
from simai_a2a.model import TrafficMatrix,HardwareProfile,LinkModel,Schedule
from simai_a2a.runner import run_scenario
from simai_a2a.scenario import canonical_sha256,write_micro_workload
from simai_a2a.schedulers import get_scheduler
from simai_a2a.schedulers.joint import topology_snapshot,research_modules,FAST
from simai_a2a.topology import build_multidc_topology,write_topology
from simai_a2a.trace_workload import trace_matrix

METHODS=('fast-native-nvidia','spreadout','fast-joint-stage-b1','fast-joint-stage-b4',
         'fast-joint-capacity-b1','fast-joint-capacity-b4')
MODELS={
 'deepseek':('cognitivecomputations/DeepSeek-R1-AWQ',256),
 'kimi':('moonshotai/Kimi-K2-Thinking',384),
 'qwen':('Qwen/Qwen3-235B-A22B-FP8',128),
 'llama':('meta-llama/Llama-4-Maverick-17B-128E-Instruct',128)}


def dump(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def inputs(args,hardware):
    c,_=research_modules();n,g=hardware.num_servers,hardware.gpus_per_server;r=n*g
    for family in args.families:
        counts,meta=c.traffic(n,g,family,args.tokens//r,1101)
        meta['vector_bytes']=args.vector_bytes
        # Conversion before native ceil/padding is essential.
        yield family,TrafficMatrix(np.array(counts,dtype=np.int64).reshape(r,r)*args.vector_bytes,meta)
    for name in args.models:
        if name=='none':continue
        folder,experts=MODELS[name];files=sorted((args.trace_root/folder).rglob('*.json'))
        if not files:raise ValueError(f'no trace files for {name}')
        path=files[0];records=json.loads(path.read_bytes())
        if isinstance(records,dict):records=[records]
        layers=sorted({int(k) for record in records for k,v in record.items()
                       if k.isdigit() and isinstance(v,list) and v})
        if not layers:raise ValueError(f'no route layers for {name}')
        for layer in sorted({layers[0],layers[len(layers)//2],layers[-1]}):
            yield f'{name}-layer{layer}',trace_matrix(path,layer=layer,num_devices=r,expert_count=experts,
                token_limit=args.tokens,vector_bytes=args.vector_bytes)


def prepare_case(root,name,matrix,hardware,top_config,options):
    root.mkdir(parents=True)
    mm=write_matrix_artifact(matrix,root/'matrix')
    hw=dict(schema_version=1,num_servers=hardware.num_servers,gpus_per_server=hardware.gpus_per_server,
        intra_alpha_ns=hardware.intra.alpha_ns,intra_beta_ns_per_byte=hardware.intra.beta_ns_per_byte,
        inter_alpha_ns=hardware.inter.alpha_ns,inter_beta_ns_per_byte=hardware.inter.beta_ns_per_byte,
        topology_id=hardware.topology_id,link_bandwidth_gbps=hardware.link_bandwidth_gbps,
        oversubscription=hardware.oversubscription)
    dump(root/'hardware.json',hw);write_micro_workload(matrix,root/'workload.csv')
    top=write_topology(hardware,root/'topology.txt',topology_config=top_config)
    dump(root/'scenario_manifest.json',dict(schema_version=1,name=name,topology=top))
    planning={}
    for method in METHODS:
        tick=time.process_time_ns()
        full=get_scheduler(method,fast_options=options).schedule(matrix,hardware,seed=1101)
        wall_cpu=(time.process_time_ns()-tick)/1000
        validation={}
        if method.startswith('fast'):
            validation=validate_fast_delivery(matrix,full,hardware.gpus_per_server)
        else:
            actual=np.zeros_like(matrix.values)
            for flow in full.flows:actual[flow.src,flow.dst]+=flow.size_bytes
            if not np.array_equal(actual,matrix.values):raise ArithmeticError('spreadout delivery failed')
        full=Schedule(full.algorithm,full.flows,full.predicted_makespan_ns,
                      {**full.metadata,'server_dc_ids':options['server_dc_ids']})
        schedule=modeled_scaleup_schedule(full,hardware)
        write_schedule_artifact(schedule,root/'schedules'/method,matrix_sha256=mm['matrix_sha256'],
            hardware_sha256=canonical_sha256(hw),manifest_fields={'delivery_validation':validation})
        planning[method]=dict(stage_count=schedule.metadata['n_stages'],
            planner_cpu_us=full.metadata.get('planner_cpu_us'),adapter_process_cpu_us=wall_cpu,
            full_physical_bytes=full.physical_bytes,network_bytes=schedule.physical_bytes,
            local_times_ns=schedule.metadata['local_times_ns'])
    dump(root/'planning.json',planning)
    return dict(name=name,features=matrix_skew_metrics(matrix,hardware.gpus_per_server),
                logical_bytes=matrix.logical_bytes,traffic_metadata=matrix.metadata,planning=planning)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--data-centers',type=int,nargs='+',choices=(2,4),default=[2])
    parser.add_argument('--models',nargs='+',choices=(*MODELS,'none'),default=list(MODELS))
    parser.add_argument('--families',nargs='*',choices=('balanced','token_uniform','token_zipf'),
                        default=['balanced','token_uniform','token_zipf'])
    parser.add_argument('--trace-root',type=Path,default=FAST.parent.parent/'graduation_design/origin_data')
    parser.add_argument('--tokens',type=int,default=256)
    parser.add_argument('--vector-bytes',type=int,default=8192)
    parser.add_argument('--wan-delay-ns',type=int,default=50000)
    parser.add_argument('--wan-gbps',type=float,default=100.)
    parser.add_argument('--timeout',type=float,default=45.)
    parser.add_argument('--binary',type=Path,default=ROOT.parents[2]/'bin/SimAI_simulator')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):parser.error('output must be new/empty')
    if args.tokens<16 or args.tokens%64 or args.vector_bytes<=0:parser.error('tokens must be positive multiple of64')
    args.out.mkdir(parents=True,exist_ok=True)
    meta=dict(schema_version=1,prepared=False,complete=False,methods=METHODS,cases=[],runs=[],
        input_selection='lexicographically first file per model; first/middle/last nonempty layer; first tokens',
        trace_warning='expert choices measured; source ranks, expert placement, rebatching and bytes modeled',
        modeled_scaleup='per-server serialized alpha/beta engine; ns-3 network; no global barrier',
        planner_sha256=hashlib.sha256((FAST/'ds/decomposition/joint_resource.py').read_bytes()).hexdigest(),
        config={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()})
    for dcs in args.data_centers:
        n,g=dcs*2,2
        hw=HardwareProfile(n,g,LinkModel(100,.0023),LinkModel(700,.02),f'{dcs}dc',400,1)
        top_config=dict(kind='multi-dc-full-mesh',data_centers=dcs,servers_per_data_center=2,
            intra_dc_bandwidth_gbps=400.,intra_dc_latency_ns=700.,
            inter_dc_bandwidth_gbps=args.wan_gbps,inter_dc_latency_ns=args.wan_delay_ns)
        topology=build_multidc_topology(hw,**{k:v for k,v in top_config.items() if k!='kind'})
        options=dict(server_dc_ids=[s//2 for s in range(n)],topology_snapshot=topology_snapshot(topology,hw))
        for label,matrix in inputs(args,hw):
            name=f'dc{dcs}-{label}'
            meta['cases'].append(prepare_case(args.out/name,name,matrix,hw,top_config,options))
            dump(args.out/'study.json',meta)
            print('prepared',name,flush=True)
    meta['prepared']=True;dump(args.out/'study.json',meta)
    if args.prepare_only:return
    for case in meta['cases']:
        scenario=args.out/case['name']
        for method in METHODS:
            result=run_scenario(scenario,method,scenario/'topology.txt',args.binary,timeout_s=args.timeout,
                                ns3_threads=0,ns3_env={'NS_ATTRIBUTE_DEFAULT':None,'NS_GLOBAL_VALUE':None})
            meta['runs'].append(dict(case=case['name'],method=method,success=result['success'],
                status=result['status'],attempt_dir=result['attempt_dir'],summary=result.get('summary'),
                error=result.get('error')))
            dump(args.out/'study.json',meta)
            print(case['name'],method,result['status'],
                  result.get('summary',{}).get('actual_makespan_ns'),flush=True)
            if not result['success']:raise RuntimeError(f'failed run, no auto retry: {result["attempt_dir"]}')
    meta['complete']=True;dump(args.out/'study.json',meta)


if __name__=='__main__':main()
