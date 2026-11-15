#!/usr/bin/env python3
"""Fixed held-out input validation. No policy tuning or failed-run retries."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import run_resource_budget_iterations as iterations
from run_v2_study import prepare_case,dump
from simai_a2a.runner import execution_fingerprint

ROOT=Path(__file__).resolve().parents[1]
BASE=Path('/home/julius/fast/SimAI/astra-sim-alibabacloud/experiments/alltoall/results/v2-leafspine-20261115')
METHODS=('fast-simai','spreadout','volume-maxq','resource-maxq')
PLANNER=ROOT/'src/simai_a2a/schedulers/resource_budget.py'
REFERENCE_CASES={'dc2-deepseek-w0-l3-b64-rho0','dc4-llama-w0-l25-b64-rho0','dc3-kimi-w0-l31-b64-rho0'}


def validation_cases(catalogs):
    for model,layers in [('deepseek',[3,32,60]),('llama',[1,25,47])]:
        if catalogs[model]['layers']!=layers:raise ValueError('catalog differs from registered layers: '+model)
    cases=[]
    for model,dc in [('deepseek',2),('llama',4),('kimi',3)]:
        for window in range(3):
            for layer in ([31] if model=='kimi' else catalogs[model]['layers']):
                name=f'dc{dc}-{model}-w{window}-l{layer}-b64-rho0'
                cases.append(dict(name=name,group=model,dc=dc,model=model,window=window,layer=layer,
                                  family='real',seed=1101,mib=4,rho=0.))
    for dc in (2,4):
        for seed in (2101,2102,2103):
            cases.append(dict(name=f'dc{dc}-zipf12-s{seed}-m4-rho0',group=f'zipf-dc{dc}',dc=dc,
                              model=None,window=0,layer=None,family='zipf12',seed=seed,mib=4,rho=0.))
    return cases


def source_baselines(study,case):
    result=[]
    for method in METHODS[:2]:
        matches=[r for r in study['runs'] if r['case']==case and r['method']==method]
        if len(matches)>1:raise ValueError('ambiguous original baseline')
        old=matches[0] if matches else dict(status='unrecorded',success=False)
        result.append(dict(old,case=case,method=method,reused=True,
            actual_ms=old['communication_ns']/1e6 if old['success'] else None,
            stages=old.get('stage_count'),planner_cpu_us=None))
    return result


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def contract(attempt):
    attempt=Path(attempt);manifest=json.loads((attempt/'run_manifest.json').read_text())
    summary=json.loads((attempt/'summary.json').read_text())
    binary=Path(manifest['command'][0]).resolve()
    fingerprint=execution_fingerprint(attempt.parents[2],manifest['algorithm'],attempt.parents[2]/'topology.txt',
        binary,ROOT/'configs/ns3-leafspine.conf',manifest['ns3_threads'],ns3_env=manifest['ns3_environment'])
    if fingerprint!=manifest['input_fingerprint']:raise ValueError('execution fingerprint mismatch')
    config=[s for s in (attempt/'ns3.conf').read_text().splitlines() if s.strip()
            and not s.lstrip().startswith('#') and not any(x in s for x in ('_FILE ','TRACE_OUTPUT_FILE '))]
    return dict(matrix=digest(attempt/'matrix.npy'),topology=digest(attempt/'topology.txt'),
        routes=digest(attempt/'routes.csv'),background=digest(attempt/'background.csv'),
        hardware=json.loads((attempt/'hardware.json').read_text()),epoch=summary['collective_epoch_ns'],
        config=config,semantics=manifest['runtime_semantics_version'],
        libraries=manifest['backend_shared_library_sha256'],environment=manifest['ns3_environment'],
        binary_sha256=digest(binary),threads=manifest['ns3_threads'],execution_fingerprint_verified=True)


def verify_contract(row,expected):
    if row['success'] and row['input_contract']!=expected:raise ValueError('replay contract mismatch')


def attach_contract(row):
    row.setdefault('backend_attempted',bool(row.get('attempt_dir')))
    if row['success'] and 'input_contract' not in row:
        try:row['input_contract']=contract(Path(row['attempt_dir']))
        except Exception as error:row['contract_error']=repr(error)


def mark_pair_eligibility(rows):
    """Retain execution outcomes; pair each control against this method only."""
    ours=next((r for r in rows if r['method']=='resource-maxq'),{})
    expected=ours.get('input_contract') if ours.get('success') else None
    for row in rows:
        row['pair_eligible']=bool(row['success'] and expected is not None and row.get('input_contract')==expected)
        row['pairing_error']=None if row['pair_eligible'] else row.get('contract_error','no successful common execution contract')


def recover_worker(out,case,methods,error):
    """Read persisted work, never rerun a method after a worker exception."""
    path=out/'study.json'
    try:saved=json.loads(path.read_text())['runs']
    except (OSError,ValueError):saved=[]
    result=[]
    for method in methods:
        row=next((dict(r) for r in saved if r['method']==method),None)
        if row is None:
            row=dict(case=case,method=method,status='worker_aborted_before_method',success=False,
                     actual_ms=None,error=repr(error))
            attempts=sorted((out/case/'runs'/method).glob('attempt-*'))
            if attempts and (attempts[-1]/'run_manifest.json').exists():
                manifest=json.loads((attempts[-1]/'run_manifest.json').read_text())
                row.update(status='worker_aborted_after_backend',attempt_dir=manifest['attempt_dir'],
                           backend_success=manifest.get('success'))
            elif attempts:
                row.update(status='worker_aborted_attempt_unknown',attempt_dir=str(attempts[-1]),backend_attempted=None)
        row['reused']=False;attach_contract(row);result.append(row)
    return result


def paired_summary(rows):
    result={}
    for group in ['all']+sorted({r['group'] for r in rows}):
        subset=[r for r in rows if group=='all' or r['group']==group]
        cases={r['case'] for r in subset};lookup={(r['case'],r['method']):r for r in subset}
        result[group]={}
        for method in METHODS[:-1]:
            values=[]
            for case in sorted(cases):
                a=lookup.get((case,'resource-maxq'),{});b=lookup.get((case,method),{})
                if (a.get('success') and b.get('success') and a.get('pair_eligible',True) and b.get('pair_eligible',True)
                    and a.get('actual_ms',0)>0 and b.get('actual_ms',0)>0):
                    values.append(b['actual_ms']/a['actual_ms'])
            result[group][method]=dict(paired=len(values),unpaired=len(cases)-len(values),
                geomean_speedup=math.exp(sum(map(math.log,values))/len(values)) if values else None,
                wins=sum(v>1+1e-6 for v in values),ties=sum(abs(v-1)<=1e-6 for v in values),
                losses=sum(v<1-1e-6 for v in values),worst_speedup=min(values) if values else None,
                best_speedup=max(values) if values else None)
    return result


def execute(job):
    out,source,methods,binary,timeout,expected_hash=job
    if digest(PLANNER)!=expected_hash:raise ValueError('frozen planner changed')
    result=iterations.run(Path(out),Path(binary),[Path(source).name],methods,
                          source_scenario=Path(source),timeout=timeout)
    for row in result['runs']:
        row['reused']=False
        attach_contract(row)
    if digest(PLANNER)!=expected_hash:raise ValueError('frozen planner changed during replay')
    return result['runs']


def save_csv(path,rows):
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(rows)


def finish(out,study):
    rows=study['runs'];paired=[]
    for case in study['cases']:
        group=[r for r in rows if r['case']==case['name']]
        mark_pair_eligibility(group)
        record=dict(case=case['name'],group=case['group'],window=case['window'],layer=case['layer'],seed=case['seed'])
        for method in METHODS:
            matches=[r for r in group if r['method']==method]
            if len(matches)!=1:raise ValueError('planned method missing or duplicate: '+case['name']+' '+method)
            row=matches[0];record[method+'_ms']=row.get('actual_ms') if row['pair_eligible'] else None
            record[method+'_status']=row['status'];record[method+'_stages']=row.get('stages')
            record[method+'_pair_eligible']=row['pair_eligible'];record[method+'_pairing_error']=row['pairing_error']
            record[method+'_core_cpu_ms']=row['planner_cpu_us']/1000 if row.get('planner_cpu_us') is not None else None
        paired.append(record)
    matrices={}
    for case in study['cases']:
        matrices.setdefault(digest(Path(case['source'])/'matrix/matrix.npy'),[]).append(case['name'])
    study.update(complete=len(rows)==study['planned_records'],all_success=all(r['success'] and r['pair_eligible'] for r in rows),
        new_attempts=sum(not r['reused'] and r.get('backend_attempted') is True for r in rows),
        new_attempts_unknown=sum(not r['reused'] and r.get('backend_attempted') is None for r in rows),
        new_method_records=sum(not r['reused'] for r in rows),summary=paired_summary(rows),
        duplicate_matrices=[v for v in matrices.values() if len(v)>1])
    study['previously_unreported_summary']=paired_summary([r for r in rows if r['case'] not in REFERENCE_CASES])['all']
    study['reference_conditions']=sorted(REFERENCE_CASES)
    dump(out/'study.json',study);save_csv(out/'paired.csv',paired)
    save_csv(out/'all_runs.csv',[{k:v for k,v in r.items() if k!='input_contract'} for r in rows])
    dump(out/'summary.json',study['summary'])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(13,8),layout='constrained')
    labels=[p['case'].replace('-b64-rho0','').replace('-m4-rho0','') for p in paired]
    for ax,method in zip(axes,['volume-maxq','spreadout']):
        ratios=[p[method+'_ms']/p['resource-maxq_ms'] if p[method+'_ms'] and p['resource-maxq_ms'] else float('nan') for p in paired]
        ax.bar(range(len(paired)),ratios,color=['#0891b2' if v>=1 else '#e07b39' for v in ratios])
        ax.axhline(1,color='#334155',linewidth=1)
        ax.set(title=f'Reference: {method}; above 1 = Resource-MaxQ faster',ylabel='Speedup (baseline / new)',
               ylim=(0,max([1]+[v for v in ratios if math.isfinite(v)])*1.12))
        ax.set_xticks(range(len(labels)),labels,rotation=75,ha='right',fontsize=7)
    fig.savefig(out/'heldout_speedup.png',dpi=160);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--timeout',type=float,default=180);parser.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--analyze-only',action='store_true',help='audit persisted artifacts and rebuild summaries; never replay')
    args=parser.parse_args();out=args.out.resolve()
    if args.analyze_only:
        study=json.loads((out/'study.json').read_text())
        for row in study['runs']:
            row.pop('input_contract',None);row.pop('contract_error',None);attach_contract(row)
        study['analysis_sha256']=digest(Path(__file__))
        shutil.copyfile(Path(__file__),out/'analysis_snapshot.py')
        finish(out,study)
        print('AUDITED',len(study['runs']),'records; paired',study['all_success'],flush=True)
        return
    out.mkdir(parents=True,exist_ok=False)
    catalogs=json.loads((BASE/'trace_catalogs.json').read_text());old=json.loads((BASE/'study.json').read_text())
    cases=validation_cases(catalogs);rows=[];jobs=[];frozen=digest(PLANNER)
    expected=subprocess.check_output(['git','show','cc76a17:'+iterations.MODULE],cwd=ROOT)
    if hashlib.sha256(expected).hexdigest()!=frozen:raise ValueError('planner differs from registered revision')
    shutil.copyfile(PLANNER,out/'planner_snapshot.py');shutil.copyfile(Path(__file__),out/'experiment_snapshot.py')
    binary=Path('/home/julius/fast/SimAI/bin/SimAI_simulator').resolve()
    for case in cases:
        source=BASE/case['name'] if case['model'] else out/'new_inputs'/case['name']
        if case['model']:
            if not(source/'scenario_manifest.json').exists():raise ValueError('original real input missing: '+str(source))
            for row in source_baselines(old,case['name']):
                row['group']=case['group']
                attach_contract(row)
                rows.append(row)
            methods=list(METHODS[2:])
        else:prepare_case(out/'new_inputs',case,catalogs);methods=list(METHODS)
        case['source']=str(source)
        jobs.append((str(out/'replays'/case['name']),str(source),methods,str(binary),args.timeout,frozen))
    study=dict(complete=False,cases=cases,runs=rows,planned_new_attempts=66,planned_records=108,
        planner_sha256=frozen,binary_sha256=digest(binary),workers=args.workers,
        scope='fixed held-out layers/windows/seeds; descriptive only; old failures not retried',
        note='core CPU sampled under parallel jobs; not an isolated wall-clock latency benchmark')
    dump(out/'study.json',study);dump(out/'jobs.json',jobs)
    print('PREFLIGHT',len(cases),'cases;',len(rows),'old baselines;',sum(len(j[2]) for j in jobs),'new attempts',flush=True)
    if args.preflight_only:return
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(execute,job):case for job,case in zip(jobs,cases)}
        for future in as_completed(futures):
            case=futures[future]
            try:completed=future.result()
            except Exception as error:
                methods=METHODS[2:] if case['model'] else METHODS
                completed=recover_worker(out/'replays'/case['name'],case['name'],methods,error)
            for row in completed:row['group']=case['group']
            study['runs'].extend(completed);dump(out/'study.json',study)
            print('REGISTERED',case['name'],len(study['runs']),'/',108,flush=True)
    finish(out,study)
    print(json.dumps(study['summary'],indent=2),flush=True)


if __name__=='__main__':main()
