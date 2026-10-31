#!/usr/bin/env python3
"""Frozen same-process implementation benchmark and same-model SPM control."""
import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
_spec=importlib.util.spec_from_file_location('private_planning_scenario',SIM/'scenario/study.py')
scene=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(scene)
engine=scene.base
_files=scene.files
_prepare=scene._prepare
_checked=scene._checked
_reconstruct=scene.reconstruct
SUITE='explore'
STRATEGIES=['F000','maxmin','spreadout','X10','SPM','A1']
METHODS=[s+'-'+i for s in STRATEGIES for i in ['ref','opt']]
FLAGS=[*scene.FLAGS,'-DFAST_PLANNING_SCAN']
sha=scene.sha
save=scene.save
command=scene.command
invoke=engine.invoke


def files():
    return sorted(set(_files())|{p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'}|set((ROOT/'tests').glob('*.*')))


def identity():
    record=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,suite=SUITE,
                compiler=engine.subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
                libc=list(platform.libc_ver()),schema=1)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest();return record


def specs(smoke=False,suite=None):
    suite=suite or SUITE;cases=[]
    for n in ([6] if smoke else [24,48] if suite=='confirm' else [12]):
        for d in ([3] if smoke else [3,6]):
            for kind in ['balanced','zipf']:
                for wan in ([50] if smoke else [50,5000]):
                    for seed in ([760201,760203] if suite=='confirm' and not smoke else [760101]):
                        c=dict(n=n,g=1 if smoke else 8,mib=1 if smoke else 64,dc_count=d,
                               dc=[s*d//n for s in range(n)],profile='asymmetric',kind=kind,
                               seed=seed if kind=='zipf' else 0,layout_seed=seed,wan_us=wan,split=suite)
                        c['id']=f'n{n}g{c["g"]}-d{d}-asymmetric-{kind}-s{c["seed"]}-l{seed}-w{wan}'
                        cases.append(c)
    return cases


def policy(method):
    if method not in METHODS:raise ValueError('unknown implementation label')
    strategy,implementation=method.rsplit('-',1)
    return {**scene.policy('A1' if strategy=='SPM' else strategy),
            'optimized':implementation=='opt','static_path':strategy=='SPM'}


def normalized(raw):
    value=copy.deepcopy(engine.semantic(raw));value['mode']=value['mode'].rsplit('-',1)[0]
    value['policy'].pop('optimized')
    return value


def check_pair(reference,optimized):
    if reference['mode'].rsplit('-',1)[-1]!='ref' or optimized['mode'].rsplit('-',1)[-1]!='opt':raise ValueError('pair labels')
    if reference['policy']['optimized'] or not optimized['policy']['optimized']:raise ValueError('pair implementation metadata')
    if normalized(reference)!=normalized(optimized):raise ValueError('implementation changed full schedule/counters/payload/cost')


def reconstruct(case,method,data,raw):
    if raw['mode']!=method or raw['policy']!=policy(method):raise ValueError('strategy/implementation policy')
    strategy,implementation=method.rsplit('-',1);mapped='A1' if strategy=='SPM' else strategy
    adapted=dict(raw,mode=mapped,policy=scene.policy(mapped))
    row,totals=_reconstruct(case,mapped,data,adapted)
    row.update(method=method,strategy=strategy,implementation=implementation)
    return row,totals


def prepare(output,smoke=False):
    output=Path(output)
    if (output/'study.json').exists():return checked(output,smoke)
    record=_prepare(output,smoke);record.update(suite=SUITE,repeats=3 if smoke else 24)
    save(output/'study.json',record);return record


def checked(output,smoke=False):
    record=_checked(output,smoke)
    if record.get('suite')!=SUITE or record['repeats']!=(3 if smoke else 24):raise ValueError('suite/repeat registration')
    return record


def audit(output,smoke=False,replay=True):
    output=Path(output);study=checked(output,smoke);rows=[]
    keys={c['id']+'-'+m for c in study['cases'] for m in METHODS}
    if set(study['records'])!=keys or {p.stem for p in (output/'raw').glob('*.json')}!=keys:raise ValueError('incomplete/unregistered raw')
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text());totals_reference=None;batch={}
        if replay:replayed=invoke(output/'native_scan',case,None,data,1)
        for method in METHODS:
            key=case['id']+'-'+method;path=output/'raw'/f'{key}.json'
            if sha(path)!=study['records'][key]:raise ValueError('raw timing/semantic digest changed')
            raw=json.loads(path.read_text());batch[method]=raw
            if raw['_command']!=command(Path(study['execution_root'])/'native_scan',case,None,study['repeats']):raise ValueError('wrong execution command')
            if len(raw['timing_samples'])!=study['repeats']:raise ValueError('wrong timing repetition count')
            if replay and engine.semantic(raw)!=engine.semantic(replayed[method]):raise ValueError('native replay mismatch')
            row,totals=reconstruct(case,method,data,raw)
            if totals_reference is not None and totals!=totals_reference:raise ValueError('methods changed routed byte totals')
            totals_reference=totals;rows.append(row)
        for strategy in STRATEGIES:check_pair(batch[strategy+'-ref'],batch[strategy+'-opt'])
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        impl=row['implementation'];reference=index[row['id'],row['strategy']+'-ref']
        row['planning_speedup']=reference['planning_us']/row['planning_us']
        for baseline in STRATEGIES:
            base=index[row['id'],baseline+'-'+impl]
            row['speedup_'+baseline]=base['total_ms']/row['total_ms']
            row['with_planning_'+baseline]=base['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Audited full ref/opt equality, replay, routes, payload and native pipeline:',len(rows),flush=True)
    return rows


engine.METHODS=METHODS;engine.FLAGS=FLAGS;engine.files=files;engine.identity=identity;engine.specs=specs
engine.matrix=scene.matrix;engine.input_key=scene.input_key;engine.command=command
engine.reconstruct=reconstruct;engine.checked=checked;engine.audit=audit
build=engine.build;run=engine.run


def summarize(rows):
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs));result=[]
    groups=[('all',lambda r:True)]
    groups += [('load:'+k,lambda r,k=k:r['kind']==k) for k in sorted({r['kind'] for r in rows})]
    groups += [('N:'+str(n),lambda r,n=n:r['n']==n) for n in sorted({r['n'] for r in rows})]
    for group,include in groups:
        for method in METHODS:
            ss=[r for r in rows if r['method']==method and include(r)]
            record=dict(group=group,method=method,configs=len(ss),planning_speedup=gm([r['planning_speedup'] for r in ss]),
                        planning_median_ms=statistics.median(r['planning_us']/1000 for r in ss),
                        planning_p95_median_ms=statistics.median(r['planning_p95_us']/1000 for r in ss))
            for base in STRATEGIES:
                ratios=[r['speedup_'+base] for r in ss]
                record[base]=dict(gm=gm(ratios),minimum=min(ratios),maximum=max(ratios),
                                  wins=sum(v>1+1e-9 for v in ratios),losses=sum(v<1-1e-9 for v in ratios),
                                  with_planning_gm=gm([r['with_planning_'+base] for r in ss]))
            result.append(record)
    return result


def render(output,smoke=False):
    output=Path(output);rows=audit(output,smoke);save(output/'summary.json',summarize(rows))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs));ns=sorted({r['n'] for r in rows})
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for strategy in ['F000','maxmin','SPM','A1']:
        for impl,style in [('ref','--'),('opt','-')]:
            ss=[r for r in rows if r['strategy']==strategy and r['implementation']==impl]
            axes[0].plot(ns,[statistics.median(r['planning_us']/1000 for r in ss if r['n']==n) for n in ns],style,marker='o',label=strategy+'-'+impl)
    ss=[r for r in rows if r['implementation']=='opt']
    axes[1].bar(STRATEGIES,[gm([r['planning_speedup'] for r in ss if r['strategy']==s]) for s in STRATEGIES]);axes[1].axhline(1,color='gray',ls='--')
    axes[0].set_xlabel('Servers');axes[0].set_ylabel('Full planning median (ms)');axes[0].legend(fontsize=7,ncol=2)
    axes[1].set_ylabel('Paired planning speedup ref / opt (GM)')
    for ax in axes:ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'planning.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for ax,key,title in zip(axes,['speedup_maxmin','with_planning_maxmin'],['Communication only','Communication + serial host planning']):
        for offset,kind in enumerate(['balanced','zipf']):
            values=[gm([r[key] for r in ss if r['strategy']==s and r['kind']==kind]) for s in STRATEGIES]
            ax.bar([i+(offset-.5)*.35 for i in range(6)],values,.35,label=kind)
        ax.set_xticks(range(6),STRATEGIES);ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Speedup / optimized Max-Min');ax.set_title(title);ax.legend();ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'comparison.png',dpi=170);plt.close(fig)


def historical(output):
    output=Path(output);study=checked(output);checks=[]
    for name in ['run-5f741f907916','run-fd58e408d88f']:
        old=SIM/'ordering/results'/name;registered=json.loads((old/'study.json').read_text())
        for case in registered['cases']:
            data=json.loads((old/'matrices'/f'{case["id"]}.json').read_text())
            previous=invoke(old/'native_scan',case,'A1',data,1)
            stored=json.loads((old/'raw'/f'{case["id"]}-A1.json').read_text())
            if engine.semantic(previous)!=engine.semantic(stored):raise ValueError('old archive replay changed')
            current=invoke(output/'native_scan',case,'A1-opt',data,1)
            prior=copy.deepcopy(engine.semantic(previous));prior.pop('initial_rows');prior.pop('support_diagnostics');prior['policy'].pop('row_order')
            for d in prior['decisions']:d.pop('row_order');d.pop('row_pressure')
            now=normalized(current);now['policy'].pop('static_path')
            if prior!=now:raise ValueError('historical A1 schedule changed: '+case['id'])
            checks.append(dict(archive=name,case=case['id'],stages=len(current['stages']),equal=True))
    save(output/'historical_checks.json',dict(checks=checks,binaries={name:sha(SIM/'ordering/results'/name/'native_scan') for name in ['run-5f741f907916','run-fd58e408d88f']}))
    print('Historical archived binary/input/full schedule equality:',len(checks),flush=True)


def main():
    global SUITE
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['prepare','run','audit','render','historical'])
    p.add_argument('--suite',choices=['explore','confirm'],default='explore');p.add_argument('--output',type=Path);p.add_argument('--smoke',action='store_true');args=p.parse_args();SUITE=args.suite
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    if args.phase=='historical':historical(output)
    else:globals()[args.phase](output,args.smoke)


if __name__=='__main__':main()
