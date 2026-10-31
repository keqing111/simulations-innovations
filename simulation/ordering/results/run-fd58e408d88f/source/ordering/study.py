#!/usr/bin/env python3
"""q* freedom diagnosis and fixed-budget row-order ablations; native timing unchanged."""
import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import random
import statistics

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
_spec=importlib.util.spec_from_file_location('private_order_scenario',SIM/'scenario/study.py')
scene=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(scene)
engine=scene.base
_files=scene.files
_prepare=scene._prepare
_checked=scene._checked
_audit=engine.audit
_reconstruct=scene.reconstruct
SUITE='explore'
METHODS=['F000','maxmin','spreadout','A1','A1D','A1P','A1DP']
ORDER={'A1':0,'A1D':1,'A1P':2,'A1DP':3}
FLAGS=[*scene.FLAGS,'-DFAST_ORDER_SCAN']
sha=scene.sha
save=scene.save
close=scene.close
invoke=engine.invoke


def files():
    return sorted(set(_files())|{p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'}|set((ROOT/'tests').glob('*.*')))


def identity():
    record=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,suite=SUITE,
                compiler=engine.subprocess.check_output(['g++','--version'],text=True).splitlines()[0],libc=list(platform.libc_ver()),schema=1)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest();return record


def permutation(case):
    p=list(range(case['n']))
    if case['renumber']:
        random.Random(case['layout_seed']).shuffle(p)
        if p==list(range(case['n'])):p=p[1:]+p[:1]
    return p


def dc_labels(case):
    n=case['n'];p=permutation(case);dc=[0]*n
    for s in range(n):dc[p[s]]=s*case['dc_count']//n
    return dc


def specs(smoke=False,suite=None):
    suite=suite or SUITE;cases=[]
    ns=[6] if smoke else [24,48] if suite=='confirm' else [12,24,48]
    for n in ns:
        for d in ([3] if smoke else [3,6]):
            for profile in (['shared100','asymmetric'] if smoke or suite=='explore' else ['asymmetric']):
                for kind in ['balanced','zipf']:
                    for wan in ([50] if smoke else [50,5000]):
                        for seed in ([640201,640203] if suite=='confirm' else [640101]):
                            for renumber in ([False,True] if suite=='confirm' else [False]):
                                c=dict(n=n,g=1 if smoke else 8,mib=1 if smoke else 64,dc_count=d,profile=profile,kind=kind,
                                       seed=seed if kind=='zipf' else 0,layout_seed=seed,wan_us=wan,renumber=renumber,split=suite)
                                cases.append(c)
    if not smoke and suite=='explore':
        for d in [3,6]:
            for wan in [50,5000]:cases.append(dict(n=24,g=8,mib=64,dc_count=d,profile='shared100',kind='block',seed=0,
                                                 layout_seed=640101,wan_us=wan,renumber=False,split=suite))
    for c in cases:
        c['problem_id']=f'n{c["n"]}g{c["g"]}-d{c["dc_count"]}-{c["profile"]}-{c["kind"]}-s{c["seed"]}-l{c["layout_seed"]}-w{c["wan_us"]}'
        c['id']=c['problem_id']+('-permuted' if c['renumber'] else '-original');c['dc']=dc_labels(c)
    return cases


def input_key(case):
    key=(case['n'],case['g'],case['mib'],case['kind'],case['seed'])
    if case['kind']=='block':key+=(case['dc_count'],case['layout_seed'])
    return key+(tuple(permutation(case)),)


def matrix(case,binary):
    canonical={**case,'dc':[s*case['dc_count']//case['n'] for s in range(case['n'])]}
    a=scene.matrix(canonical,binary)
    if not case['renumber']:return a
    n,g=case['n'],case['g'];r=n*g;p=permutation(case);b=[0]*len(a)
    for i in range(r):
        for j in range(r):b[(p[i//g]*g+i%g)*r+p[j//g]*g+j%g]=a[i*r+j]
    return b


command=scene.command


def support_summary(a,n,q,m,real,g):
    if len(a)!=n*n or len(real)!=n*n*g or sorted(m)!=list(range(n)) or q<=0:raise ValueError('support dimensions')
    inverse={d:s for s,d in enumerate(m)};graph=[[] for _ in range(n)];reverse=[[] for _ in range(n)];degrees=[0]*n
    for s in range(n):
        if a[s*n+m[s]]<q:raise ValueError('unsupported diagnostic matching')
        for d in range(n):
            if a[s*n+d]>=q:
                degrees[s]+=1
                if d!=m[s]:v=inverse[d];graph[s].append(v);reverse[v].append(s)
    # Independent Kosaraju pass (C++ uses Tarjan).
    seen=set();finish=[]
    def forward(s):
        seen.add(s)
        for v in graph[s]:
            if v not in seen:forward(v)
        finish.append(s)
    for s in range(n):
        if s not in seen:forward(s)
    component=[-1]*n;sizes=[]
    def backward(s,k):
        component[s]=k;count=1
        for v in reverse[s]:
            if component[v]<0:count+=backward(v,k)
        return count
    for s in reversed(finish):
        if component[s]<0:sizes.append(backward(s,len(sizes)))
    has_real=lambda s,d:s!=d and any(real[(s*n+d)*g+u]>0 for u in range(g))
    movable=realrows=edges=0
    for s in range(n):
        if sizes[component[s]]<=1:continue
        movable+=1;change=has_real(s,m[s])
        for v in graph[s]:
            if component[v]==component[s]:edges+=1;change=change or has_real(s,m[v])
        realrows+=int(change)
    return dict(edges=sum(degrees),flexible_edges=edges,movable_rows=movable,real_movable_rows=realrows,degrees=degrees)


def feasible(a,n,q):
    inverse=[-1]*n
    def dfs(s,seen):
        for d in range(n):
            if a[s*n+d]>=q and d not in seen:
                seen.add(d)
                if inverse[d]<0 or dfs(inverse[d],seen):inverse[d]=s;return True
        return False
    return all(dfs(s,set()) for s in range(n))


def row_pressure(case,a,q,real):
    n,g=case['n'],case['g'];dc=case['dc'];caps=scene.topology(case)['bandwidths'];loads={};routes={};volumes={}
    for s in range(n):
        for d in range(n):
            if s==d:continue
            keys=[f'server-out:{s}',f'server-in:{d}']
            if dc[s]!=dc[d]:keys += [f'gateway-out:{dc[s]}',f'gateway-in:{dc[d]}',f'wan:{dc[s]}:{dc[d]}']
            routes[s,d]=keys;v=sum(real[(s*n+d)*g:(s*n+d+1)*g]);volumes[s,d]=v
            for key in keys:loads[key]=loads.get(key,0)+v
    h={key:v/caps[key] for key,v in loads.items()};maximum=max(h.values(),default=0);result=[0.]*n
    if maximum:
        for (s,d),v in volumes.items():
            if a[s*n+d]>=q:result[s]+=v*sum(h[key]/maximum/caps[key] for key in routes[s,d])
    return result


def policy(method):
    mapped='A1' if method in ORDER else method
    return {**scene.policy(mapped),'row_order':ORDER.get(method,0)}


def reconstruct(case,method,data,raw):
    if method not in METHODS or raw['mode']!=method or raw['policy']!=policy(method):raise ValueError('declared order policy')
    mapped='A1' if method in ORDER else method
    adapted=dict(raw);adapted['mode']=mapped;adapted['policy']=scene.policy(mapped)
    row,totals=_reconstruct(case,mapped,data,adapted);row['method']=method
    n,g=case['n'],case['g'];r=n*g;frames=raw['stages'];diagnostics=raw['support_diagnostics'];initial=raw['initial_rows']
    if method=='spreadout':
        if initial or diagnostics:raise ValueError('SpreadOut has no decomposition diagnostic')
        row.update(diagnosed_stages=0,free_stages=0,real_free_stages=0,movable_row_fraction=0.,real_movable_row_fraction=0.,order_changed_stages=0)
        return row,totals
    if len(initial)!=n*n*g or any(not isinstance(v,int) or v<0 for v in initial):raise ValueError('initial real rows')
    for s in range(n):
        for d in range(n):
            if s!=d and sum(initial[(s*n+d)*g:(s*n+d+1)*g])!=sum(data[(s*g+u)*r+d*g+v] for u in range(g) for v in range(g)):
                raise ValueError('initial real server-pair demand')
    a=[0]*(n*n)
    for frame in frames:
        for s,d in enumerate(frame['map']):a[s*n+d]+=frame['freq']
    real=list(initial);free=real_free=movable=real_movable=changed=0
    if len(diagnostics)!=(0 if method=='F000' else len(frames)):raise ValueError('diagnostic count')
    for k,frame in enumerate(frames):
        m=frame['map'];q=frame['freq']
        if min(a[s*n+m[s]] for s in range(n))!=q:raise ValueError('actual coefficient')
        if method!='F000':
            if feasible(a,n,q+1):raise ValueError('not exact maximum bottleneck coefficient')
            info=support_summary(a,n,q,m,real,g)
            if info!=diagnostics[k]:raise ValueError('support freedom diagnostic')
            free+=info['movable_rows']>0;real_free+=info['real_movable_rows']>0;movable+=info['movable_rows'];real_movable+=info['real_movable_rows']
            decision=raw['decisions'][k];order=decision['row_order'];pressure=decision['row_pressure'];kind=ORDER.get(method,0)
            expected_pressure=row_pressure(case,a,q,real) if kind>=2 else []
            if len(pressure)!=len(expected_pressure):raise ValueError('row pressure shape')
            for x,y in zip(pressure,expected_pressure):close(x,y)
            if method in ORDER:
                degrees=info['degrees']
                # Values are independently checked above. Sort the exported exact
                # doubles: equivalent summation orders can differ by one ULP in
                # mathematically tied rows, and must not falsely reject a trace.
                expected_order=sorted(range(n),key=lambda s:((degrees[s] if kind in [1,3] else 0),(-pressure[s] if kind>=2 else 0),s))
                if order!=expected_order:raise ValueError('row visit order')
                changed+=order!=list(range(n))
            elif order or pressure:raise ValueError('nonconstructive trace')
        for s,d in enumerate(m):
            a[s*n+d]-=q
            for u in range(g):
                idx=(s*n+d)*g+u;sent=min(q,real[idx])
                if frame['payload'][s*g+u]!=sent:raise ValueError('real GPU payload shadow')
                real[idx]-=sent
    if any(a) or any(real[(s*n+d)*g+u] for s in range(n) for d in range(n) if s!=d for u in range(g)):raise ValueError('residual demand left')
    diagnosed=len(diagnostics)
    row.update(diagnosed_stages=diagnosed,free_stages=free,real_free_stages=real_free,
               movable_row_fraction=movable/(n*diagnosed) if diagnosed else 0.,real_movable_row_fraction=real_movable/(n*diagnosed) if diagnosed else 0.,order_changed_stages=changed)
    return row,totals


def prepare(output,smoke=False):
    output=Path(output)
    if (output/'study.json').exists():return checked(output,smoke)
    value=_prepare(output,smoke);value.update(suite=SUITE,repeats=3 if smoke else 21 if SUITE=='confirm' else 5)
    save(output/'study.json',value);return value


def checked(output,smoke=False):
    value=_checked(output,smoke)
    if value.get('suite')!=SUITE or value['repeats']!=(3 if smoke else 21 if SUITE=='confirm' else 5):raise ValueError('suite/timing protocol')
    return value


def audit(output,smoke=False,replay=True):
    output=Path(output);rows=_audit(output,smoke,replay);index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        ref=index[row['id'],'A1'];row['speedup_A1']=ref['total_ms']/row['total_ms'];row['speedup_A1_with_planning']=ref['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return rows


engine.METHODS=METHODS;engine.FLAGS=FLAGS;engine.files=files;engine.identity=identity;engine.specs=specs
engine.matrix=matrix;engine.input_key=input_key;engine.command=command;engine.reconstruct=reconstruct;engine.checked=checked;engine.audit=audit
build=engine.build;run=engine.run


def summarize(rows):
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs));result=[]
    groups=[('all',lambda r:True)]
    groups += [('load:'+k,lambda r,k=k:r['kind']==k) for k in sorted({r['kind'] for r in rows})]
    groups += [('N:'+str(n),lambda r,n=n:r['n']==n) for n in sorted({r['n'] for r in rows})]
    groups += [('profile:'+p,lambda r,p=p:r['profile']==p) for p in sorted({r['profile'] for r in rows})]
    for name,include in groups:
        for method in METHODS:
            ss=[r for r in rows if r['method']==method and include(r)];ratios=[r['speedup_A1'] for r in ss]
            result.append(dict(group=name,method=method,configs=len(ss),vs_A1=gm(ratios),vs_MaxMin=gm([r['speedup_maxmin'] for r in ss]),
                               vs_native=gm([r['speedup_native'] for r in ss]),with_planning_vs_A1=gm([r['speedup_A1_with_planning'] for r in ss]),
                               wins_A1=sum(v>1+1e-9 for v in ratios),losses_A1=sum(v<1-1e-9 for v in ratios),min_A1=min(ratios),max_A1=max(ratios),
                               planning_median_ms=statistics.median(r['planning_us']/1000 for r in ss),
                               free_stage_fraction=sum(r['free_stages'] for r in ss)/max(1,sum(r['diagnosed_stages'] for r in ss)),
                               real_free_stage_fraction=sum(r['real_free_stages'] for r in ss)/max(1,sum(r['diagnosed_stages'] for r in ss))))
    return result


def render(output,smoke=False):
    output=Path(output);rows=audit(output,smoke);save(output/'summary.json',summarize(rows))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs));kinds=sorted({r['kind'] for r in rows})
    fig,axes=plt.subplots(1,len(kinds),figsize=(5*len(kinds),4),squeeze=False,layout='constrained')
    for ax,kind in zip(axes[0],kinds):
        ax.bar(METHODS,[gm([r['speedup_A1'] for r in rows if r['kind']==kind and r['method']==m]) for m in METHODS])
        ax.axhline(1,color='gray',ls='--');ax.set_title(f'{SUITE}: {kind}');ax.set_ylabel('Communication speedup / A1');ax.tick_params(axis='x',rotation=30);ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'speedup.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained');ns=sorted({r['n'] for r in rows})
    for method in ['maxmin','A1','A1D','A1P','A1DP']:
        for ax,key in zip(axes,['planning_us','real_free_stages']):
            values=[]
            for n in ns:
                ss=[r for r in rows if r['method']==method and r['n']==n]
                values.append(statistics.median(r[key]/1000 for r in ss) if key=='planning_us' else sum(r[key] for r in ss)/max(1,sum(r['diagnosed_stages'] for r in ss)))
            ax.plot(ns,values,marker='o',label=method)
    axes[0].set_ylabel('Full host planning median (ms)');axes[1].set_ylabel('Stages with real matching freedom / all stages')
    for ax in axes:ax.set_xlabel('Servers');ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(output/'planning_freedom.png',dpi=170);plt.close(fig)
    index={(r['problem_id'],r['renumber'],r['method']):r for r in rows};pairs=[]
    for r in rows:
        if r['renumber']:
            original=index[r['problem_id'],False,r['method']]
            pairs.append(dict(problem_id=r['problem_id'],method=r['method'],permuted_over_original=r['total_ms']/original['total_ms']))
    save(output/'renumbering.json',pairs)
    if pairs:
        fig,ax=plt.subplots(figsize=(9,4),layout='constrained')
        ax.boxplot([[p['permuted_over_original'] for p in pairs if p['method']==m] for m in METHODS],tick_labels=METHODS,showfliers=True)
        ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Permuted / original communication time');ax.set_title('Same physical problem, changed server IDs');ax.grid(axis='y',alpha=.2)
        fig.savefig(output/'renumbering.png',dpi=170);plt.close(fig)


def main():
    global SUITE
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['prepare','run','audit','render'])
    p.add_argument('--suite',choices=['explore','confirm'],default='explore');p.add_argument('--output',type=Path);p.add_argument('--smoke',action='store_true');args=p.parse_args();SUITE=args.suite
    out=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve();globals()[args.phase](out,args.smoke)

if __name__=='__main__':main()
