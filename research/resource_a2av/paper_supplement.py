#!/usr/bin/env python3
"""Frozen supplemental cohort: common baselines, real traces and mechanism family."""
import argparse
import copy
import json
from pathlib import Path
import shutil
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
import experiment as e
from paper_mechanism import controlled_case
from simai_a2a.model import TrafficMatrix

METHODS=['fast-simai','spreadout','pairwise','volume-maxq','resource-maxq']


def cases():
    result=[]
    def add(block,top,p,family,**kw):
        c=dict(block=block,topology=top,gpu_count=p,family=family,seed=5101,
               rho=0,window=0,layer=None,model=None,mib=4,direction='dispatch')
        c.update(kw)
        detail=(f"{c['model']}-w{c['window']}-{c['direction']}" if block=='real' else
                f"q{c['quantum_bytes']}-label{c['label_shift']}" if block=='mechanism' else f"{family}-m{c['mib']}-s{c['seed']}")
        c.update(name=f"supp-{block}-{top}-gpu{p}-{detail}",group=block+'/'+top,
                 plot_label=f"{top}/{p}G {detail}")
        result.append(c)
    for top in ['leaf-bandwidth','multi-cut-mixed']:
        for model in ['deepseek','kimi','llama']:
            for window in [0,1]:
                for direction in ['dispatch','combine']:
                    add('real',top,64,model,model=model,window=window,direction=direction,mib=None)
    for top in ['leaf-route-asymmetric','multi-cut-mixed']:
        for family in ['balanced','zipf08','zipf12']:
            for mib in [1,4,16]:add('synthetic',top,64,family,mib=mib)
        for family in ['balanced','zipf12']:add('synthetic',top,128,family)
    for quantum in [65536,1048576]:
        for shift in [0,1,2]:
            add('mechanism','multi-cut',64,'literal-shared-senders',quantum_bytes=quantum,label_shift=shift)
    if len(result)!=52 or len({c['name'] for c in result})!=52:raise ValueError('registered cohort changed')
    return result


def as_direction(matrix,direction):
    if direction not in ('dispatch','combine'):raise ValueError('unknown MoE communication direction')
    return TrafficMatrix((matrix.values if direction=='dispatch' else matrix.values.T).copy(),
        {**copy.deepcopy(matrix.metadata),'direction':direction,
         'combine_assumption':'transpose of dispatch, same per-expert vector bytes; communication only'})


def demand(case,trace_root):
    if case['block']=='real':
        info=e.catalog(case['model'],trace_root)
        return as_direction(e.trace_input(info,case['window'],info['layers'][1],case['gpu_count'],64),case['direction'])
    if case['block']=='synthetic':
        return e.synthetic(case['gpu_count'],case['family'],case['seed'],case['mib'])
    matrix,_,_,_=controlled_case('shared-senders');q=case['quantum_bytes'];shift=case['label_shift']
    old_to_new=[(rank//16)*16+(((rank%16)//4+shift)%4)*4+rank%4 for rank in range(64)]
    values=np.zeros_like(matrix.values)
    values[np.ix_(old_to_new,old_to_new)]=(matrix.values//262144)*q
    return TrafficMatrix(values,dict(generator='literal-shared-sender-mechanism-family',
        per_gpu_pair_bytes=q,label_shift=shift,rank_old_to_new=old_to_new,
        meaning='rack-preserving relabeling and size sweep; exploratory controlled support'))


def prepare_case(out,case,trace_root):
    # Reuse physical input construction, then replace ONLY this new cohort's demand.
    placeholder={**case,'model':None,'family':'balanced','mib':1}
    source,top=e.prepare_case(out/'inputs',placeholder,trace_root)
    matrix=demand(case,trace_root)
    if case['block']=='real':case['layer']=matrix.metadata['layer']
    sends=matrix.values.sum(axis=1)
    case.update(source=str(source),logical_bytes=matrix.logical_bytes,
                send_bytes_min=int(sends.min()),send_bytes_max=int(sends.max()))
    e.write_matrix_artifact(matrix,source/'matrix');e.write_micro_workload(matrix,source/'workload.csv')
    manifest=json.loads((source/'scenario_manifest.json').read_text());manifest['case']=copy.deepcopy(case)
    e.dump(source/'scenario_manifest.json',manifest)
    return source,top


def run(out,binary,workers,timeout,trace_root):
    out.mkdir(parents=True,exist_ok=False);registered=cases()
    files=[Path(__file__),Path(e.__file__),e.CONFIG_PATH,Path(e.iterations.__file__),Path(e.audit.__file__),
           Path(__file__).with_name('paper_mechanism.py'),e.EXPERIMENTS/'configs/ns3-leafspine.conf',
           *[e.EXPERIMENTS/'src/simai_a2a'/name for name in ['schedulers/resource_budget.py',
             'schedulers/pairwise.py','schedulers/spreadout.py','schedulers/simai_plan.py','schedulers/fast_port.py',
             'execution.py','leafspine.py','v2_workload.py','runner.py','v2_diagnostics.py']],binary]
    hashes={str(p.resolve()):e.audit.digest(p) for p in files}
    snapshots=out/'source_snapshot';snapshots.mkdir()
    for p in files[:-1]:shutil.copyfile(p,snapshots/p.name)
    jobs=[];inputs={}
    for case in registered:
        source,_=prepare_case(out,case,trace_root)
        for p in source.rglob('*'):
            if p.is_file():inputs[str(p)]=e.audit.digest(p)
        jobs.append((str(out/'replays'/case['name']),str(source),case,timeout,hashes,str(binary),METHODS))
    hashes.update(inputs);e.dump(out/'source_hashes.json',hashes)
    study=dict(complete=False,all_success=False,phase='paper-supplement',cases=registered,runs=[],methods=METHODS,
        planned_records=260,planned_new_attempts=260,source_hashes=hashes,workers=workers,
        timeout_s=timeout,backend_identity=dict(path=str(binary),sha256=hashes[str(binary)]),
        scope='frozen 52-condition supplement; no tuning, retry, backend rebuild or global barrier')
    e.dump(out/'study.json',study)
    print('REGISTERED 52 conditions; 260 new replays; 3 figure groups + shared overhead ablation',flush=True)
    jobs.sort(key=lambda j:{'mechanism':0,'real':1,'synthetic':2}[j[2]['block']])
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(e.execute,j):j[2] for j in jobs}
        for future in as_completed(pending):
            case=pending[future]
            try:rows=future.result()
            except Exception as error:
                rows=e.audit.recover_worker(out/'replays'/case['name'],case['name'],METHODS,error)
                for row in rows:row.update(group=case['group'],reused=False)
            study['runs'].extend(rows);e.dump(out/'study.json',study)
            print('COLLECTED',len(study['runs']),'/260',case['name'],flush=True)
    changed=[p for p,h in hashes.items() if e.audit.digest(Path(p))!=h]
    if changed:raise ValueError('registered source/input changed: '+str(changed))
    e.audit.finish(out,study)
    print('COMPLETE',sum(r['success'] for r in study['runs']),'/260 successful',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--binary',type=Path,default=Path('/home/julius/fast/SimAI/bin/SimAI_simulator'))
    parser.add_argument('--trace-root',type=Path,default=Path('/home/julius/graduation_design/origin_data'))
    parser.add_argument('--workers',type=int,default=3);parser.add_argument('--timeout',type=float,default=300)
    args=parser.parse_args();run(args.out.resolve(),args.binary.resolve(),args.workers,args.timeout,args.trace_root)


if __name__=='__main__':main()
