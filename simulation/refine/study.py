#!/usr/bin/env python3
"""Second-round policy scan; identical native model, private reused audit engine."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
_spec=importlib.util.spec_from_file_location('private_scan_engine',SIM/'scan/study.py')
base=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
# The engine is a private module instance: these bindings do not change scan/
# commands or the frozen first-round archive. Reuse its build/input/replay path.
_original_files=base.files
_original_reconstruct=base.reconstruct
METHODS=['F000','F011','F111','maxmin','spreadout','X10','X01','X11','X11-4','Q75','Q50','B11','B11-4']
FLAGS=[*base.FLAGS,'-DFAST_SCAN_EXTENDED']
FROZEN=SIM/'scan/results/run-7be22345a21c'
sha=base.sha
save=base.save
matrix=base.matrix
specs=base.specs
invoke=base.invoke
semantic=base.semantic


def files():
    return sorted(set(_original_files())|{p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'}|set((ROOT/'tests').glob('*.*')))


def identity():
    record=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,
                compiler=base.subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
                libc=list(platform.libc_ver()),schema=2)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
    return record


def policy(method):
    if method not in METHODS:raise ValueError('unknown declared refinement')
    exact=method in ['maxmin','X10','X01','X11','X11-4','Q75','Q50','B11','B11-4']
    link=method in ['F011','F111','X10','X11','X11-4','Q75','Q50','B11','B11-4']
    residual=method in ['F011','F111','X01','X11','X11-4','Q75','Q50','B11','B11-4']
    numerator,denominator=(3,4) if method=='Q75' else (1,2) if method=='Q50' else (1,1)
    return dict(exact=exact,link=link,residual=residual,tail_score=method.startswith('B'),
                quota_numerator=numerator,quota_denominator=denominator,
                cycle_budget=(4 if method.endswith('-4') else 2) if link else 0)


def reconstruct(case,method,data,raw):
    declared=policy(method)
    if raw.get('policy')!=declared:raise ValueError('raw policy does not match declared method')
    row,totals=_original_reconstruct(case,method,data,raw,cycle_budget=declared['cycle_budget'])
    if declared['exact']:
        for decision in raw['decisions']:
            exact=decision['exact_weight'];native=decision['native_weight']
            if exact<native or native<=0:raise ValueError('invalid exact/native coefficient')
            expected=max(native,(exact*declared['quota_numerator']+declared['quota_denominator']-1)//declared['quota_denominator'])
            if decision['threshold']!=expected:raise ValueError('exact/slack threshold differs from declared fraction')
    row.update(cycle_budget=declared['cycle_budget'],tail_score=declared['tail_score'])
    return row,totals


# Explicit dependency bindings. Root of the engine remains scan/ so its source
# collector and native source paths remain correct; only this wrapper picks the
# output root and policy set. Each process loads exactly one private instance.
base.METHODS=METHODS
base.FLAGS=FLAGS
base.files=files
base.identity=identity
base.reconstruct=reconstruct
build=base.build
prepare=base.prepare
checked=base.checked
run=base.run
audit=base.audit


def audit_baselines(output,smoke=False):
    output=Path(output);study=checked(output,smoke);checks=[]
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text())
        historical=FROZEN/'matrices'/f'{case["id"]}.json'
        if not smoke and json.loads(historical.read_text())!=data:raise ValueError('input differs from first registered scan')
        for method in ['F000','F011','F111','maxmin','spreadout']:
            previous=invoke(FROZEN/'native_scan',case,method,data,1)
            current=json.loads((output/'raw'/f'{case["id"]}-{method}.json').read_text())
            fields=['stages','total_us','balance_us','intrinsic_us','dispatch_us','service','inter_bytes']
            if any(previous[k]!=current[k] for k in fields):raise ValueError(f'old schedule changed {case["id"]}/{method}')
            checks.append(dict(case=case['id'],method=method,fields=fields))
    save(output/'baseline_checks.json',dict(frozen=str(FROZEN),binary_sha256=sha(FROZEN/'native_scan'),checks=checks))
    print('Frozen first-round5-policy equality checks:',len(checks),flush=True)


def render(output,smoke=False):
    output=Path(output);rows=audit(output,smoke)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    gm=lambda values:math.exp(statistics.mean(math.log(x) for x in values))
    fig,axes=plt.subplots(1,2,figsize=(14,4.8),layout='constrained')
    for ax,kind,key,title in [(axes[0],None,'speedup_native','All24 development configs / native FAST'),
                              (axes[1],'zipf','speedup_maxmin','Zipf12 development configs / Max-Min')]:
        values=[gm([r[key] for r in rows if r['method']==m and r['split']=='development' and (kind is None or r['kind']==kind)]) for m in METHODS]
        ax.bar(METHODS,values);ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Communication speedup');ax.set_title(title)
        ax.tick_params(axis='x',rotation=55,labelsize=8);ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'speedup.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),layout='constrained')
    for method in ['F000','F111','maxmin','X10','X01','X11','X11-4','Q75','Q50','B11','B11-4']:
        selected=[r for r in rows if r['method']==method and r['split']=='development'];ns=sorted({r['n'] for r in selected})
        axes[0].plot(ns,[statistics.median(r['planning_us']/1000 for r in selected if r['n']==n) for n in ns],marker='.',label=method)
        axes[1].plot(ns,[gm([r['speedup_maxmin'] for r in selected if r['n']==n and r['kind']=='zipf']) for n in ns],marker='.',label=method)
    axes[0].set_ylabel('Full host planning median (ms)');axes[1].set_ylabel('Zipf communication speedup / Max-Min');axes[1].axhline(1,color='gray',ls='--')
    for ax in axes:ax.set_xlabel('Servers');ax.grid(alpha=.2);ax.legend(ncol=3,fontsize=6)
    fig.savefig(output/'planning.png',dpi=170);plt.close(fig)


def main():
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['prepare','run','audit','render','baselines'])
    p.add_argument('--output',type=Path);p.add_argument('--smoke',action='store_true');args=p.parse_args()
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    if args.phase=='baselines':audit_baselines(output,args.smoke)
    else:globals()[args.phase](output,args.smoke)

if __name__=='__main__':main()
