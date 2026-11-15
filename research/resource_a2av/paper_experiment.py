#!/usr/bin/env python3
"""Bounded fixed-routing paper cohort; no scheduling/runtime implementation here."""
import argparse
import copy
import json
from pathlib import Path
import shutil
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
import experiment as e
from simai_a2a.artifacts import read_schedule_artifact

METHODS=['fast-simai','spreadout','volume-maxq','resource-maxq']


def paper_cases():
    cases=[]
    for top in ['leaf-uniform','leaf-bandwidth','multi-cut']:
        for family in ['balanced','zipf08']:
            for mib in [1,4]:
                for seed in ([3101] if family=='balanced' else [3101,3102,3103]):
                    name=f'{top}-gpu64-{family}-s{seed}-m{mib}-rho0'
                    cases.append(dict(name=name,group=top+'/'+family,topology=top,gpu_count=64,
                        family=family,mib=mib,seed=seed,rho=0,window=0,layer=None,model=None,
                        block='paper',plot_label=f'{top}/64 GPUs {family}\n{mib}MiB s{seed}'))
    return cases


def replay(out,case,binary,timeout=300):
    source,_=e.prepare_case(out/'inputs',case);case['source']=str(source)
    files=[Path(e.__file__),e.CONFIG_PATH,Path(__file__),
           e.EXPERIMENTS/'src/simai_a2a/schedulers/resource_budget.py',
           e.EXPERIMENTS/'src/simai_a2a/schedulers/simai_plan.py',
           e.EXPERIMENTS/'configs/ns3-leafspine.conf',binary]
    hashes={str(p.resolve()):e.audit.digest(p) for p in files}
    e.dump(out/'source_hashes.json',hashes)
    for p in files[:-1]:shutil.copyfile(p,out/p.name)
    rows=e.execute((str(out/'replay'),str(source),case,timeout,hashes,str(binary),METHODS))
    study=dict(cases=[case],runs=rows,methods=METHODS,planned_records=4,
        scope='first registered natural mechanism condition; later reused without replay',source_hashes=hashes)
    e.audit.finish(out,study)
    return study


def reuse_case(case,old):
    saved=json.loads((old/'study.json').read_text())
    prior=next((c for c in saved['cases'] if c['name']==case['name']),None)
    if prior is None:return None
    core=e.EXPERIMENTS/'src/simai_a2a/schedulers/resource_budget.py'
    if e.audit.digest(old/core.name)!=e.audit.digest(core):raise ValueError('cached planner identity differs')
    source=Path(prior['source']);top,hw=e.fabric(case['topology'],64)
    if top.serialize()!=(source/'topology.txt').read_text():raise ValueError('cached physical topology differs')
    matrix=e.synthetic(64,case['family'],case['seed'],case['mib'])
    if not np.array_equal(matrix.values,np.load(source/'matrix/matrix.npy')):raise ValueError('cached matrix differs')
    actual={(int(a),int(b)):int(c) for a,b,c in __import__('csv').reader((source/'routes.csv').open())}
    expected=e.fixed_routes(top)
    if any(actual[k]!=v for k,v in expected.items() if k[1] in top.gpu_ids):raise ValueError('cached foreground paths differ')
    snap=json.loads((source/'planner_topology.json').read_text());physical=e.server_snapshot(top,hw,actual)
    if any(snap[k]!=physical[k] for k in physical):raise ValueError('cached planner physical paths differ')
    if snap['shared_resource_ids']!=e.shared_ids(top,physical):raise ValueError('cached resource groups differ')
    rows=[copy.deepcopy(r) for r in saved['runs'] if r['case']==case['name']]
    if sorted(r['method'] for r in rows)!=sorted(METHODS):raise ValueError('cached methods missing/duplicated')
    prepared=e.iterations.prepare(matrix,hw)
    reference=e.iterations.reference(prepared)
    plans={'fast-simai':(reference['permutations'],[s['alpha'] for s in reference['steps']])}
    for method in ['volume-maxq','resource-maxq']:
        p=e.iterations.volume_plan(prepared,snap) if method=='volume-maxq' else e.iterations.plan(prepared,snap,maximum_quantum=True)
        plans[method]=p['permutations'],p['coefficients']
    groups=json.loads((source/'scenario_manifest.json').read_text())['topology']['server_group_ids']
    for row in rows:
        if not row['success']:raise ValueError('cached failed run is not eligible for reuse')
        row.pop('input_contract',None);row.pop('contract_error',None);e.audit.attach_contract(row)
        if row['method'] in plans:
            plan_path=Path(row['attempt_dir']).parents[2]/(row['method']+'.plan.json')
            old_plan=json.loads(plan_path.read_text());q=old_plan.get('coefficients',[s['alpha'] for s in old_plan.get('steps',[])])
            if (old_plan['permutations'],q)!=plans[row['method']]:raise ValueError('cached matching/coefficient differs')
        method=row['method']
        if method=='spreadout':
            full=e.iterations.SpreadOutScheduler().schedule(matrix,hw,seed=1)
            full.metadata.update(delivery_chunks={str(f.flow_id):[[f.src,f.dst,f.size_bytes]] for f in full.flows},server_dc_ids=groups)
        else:
            ps,qs=plans[method]
            full=e.iterations.materialize(prepared,ps,qs,hw,method,dict(server_dc_ids=groups))
        fresh=e.iterations.modeled_scaleup_schedule(full,hw)
        retained,_=read_schedule_artifact(Path(row['attempt_dir']))
        if (fresh.flows!=retained.flows or fresh.predicted_makespan_ns!=retained.predicted_makespan_ns
            or json.dumps(fresh.metadata,sort_keys=True)!=json.dumps(retained.metadata,sort_keys=True)):
            raise ValueError('cached materialized schedule differs from current implementation')
        row['materialized_schedule_verified']=True
        row.update(reused=True,group=case['group'],reused_from=str(old))
    e.audit.mark_pair_eligibility(rows)
    if not all(r['pair_eligible'] for r in rows):raise ValueError('cached execution identity/contract differs')
    sends=matrix.values.sum(axis=1);case.update(source=str(source),logical_bytes=matrix.logical_bytes,
        send_bytes_min=int(sends.min()),send_bytes_max=int(sends.max()))
    return source,rows


def run_main(out,binary,mechanism,workers,timeout):
    cases=paper_cases();rows=[];jobs=[]
    caches=[mechanism/'natural',e.EXPERIMENTS/'results/heterogeneous-pilot-20261115']
    files=[Path(__file__),Path(e.__file__),e.CONFIG_PATH,Path(e.iterations.__file__),Path(e.audit.__file__),
           *[e.EXPERIMENTS/'src/simai_a2a'/name for name in ['schedulers/resource_budget.py','schedulers/simai_plan.py',
             'schedulers/fast_port.py','schedulers/spreadout.py','leafspine.py','v2_workload.py','execution.py','runner.py']],
           e.EXPERIMENTS/'configs/ns3-leafspine.conf',binary]
    hashes={str(p.resolve()):e.audit.digest(p) for p in files};e.dump(out/'source_hashes.json',hashes)
    for p in files[:-1]:shutil.copyfile(p,out/p.name)
    for case in cases:
        reused=None
        for old in caches:
            reused=reuse_case(case,old)
            if reused is not None:break
        if reused is not None:rows.extend(reused[1]);continue
        source,_=e.prepare_case(out/'inputs',case);case['source']=str(source)
        jobs.append((str(out/'replays'/case['name']),str(source),case,timeout,hashes,str(binary),METHODS))
    study=dict(complete=False,cases=cases,runs=rows,methods=METHODS,planned_records=96,
        planned_new_attempts=len(jobs)*4,source_hashes=hashes,workers=workers,
        scope='fixed64GPU cohort; eight unique demands across three fabrics; descriptive seed ranges, no CI')
    e.dump(out/'study.json',study)
    print('REGISTERED',len(cases),'conditions;',len(rows),'verified reused records;',len(jobs)*4,'new attempts',flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(e.execute,job):job[2] for job in jobs}
        for future in as_completed(futures):
            case=futures[future]
            try:new=future.result()
            except Exception as error:
                new=e.audit.recover_worker(out/'replays'/case['name'],case['name'],METHODS,error)
                for row in new:row['group']=case['group']
            rows.extend(new);e.dump(out/'study.json',study)
            print('RESULTS',len(rows),'/96',flush=True)
    e.audit.finish(out,study)
    print(json.dumps(study['summary']['all']),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['mechanism','mechanism-audit','shared-mechanism','main'],default='mechanism');parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--binary',type=Path,default=Path('/home/julius/fast/SimAI/bin/SimAI_simulator'))
    parser.add_argument('--mechanism',type=Path);parser.add_argument('--workers',type=int,default=3);parser.add_argument('--timeout',type=float,default=300)
    args=parser.parse_args();out=args.out.resolve()
    if args.phase=='mechanism-audit':
        from paper_mechanism import analyze
        analyze(out);return
    out.mkdir(parents=True,exist_ok=False)
    if args.phase=='main':
        if args.mechanism is None:parser.error('--main needs --mechanism')
        run_main(out,args.binary.resolve(),args.mechanism.resolve(),args.workers,args.timeout)
    elif args.phase=='shared-mechanism':
        if args.mechanism is None:parser.error('--shared-mechanism needs --mechanism')
        from paper_mechanism import run_control
        prior=json.loads((args.mechanism/'natural/study.json').read_text())
        run_control(out/'control',args.binary.resolve(),Path(prior['cases'][0]['source']),'shared-senders')
    else:
        from paper_mechanism import run
        run(out,args.binary.resolve())


if __name__=='__main__':main()
