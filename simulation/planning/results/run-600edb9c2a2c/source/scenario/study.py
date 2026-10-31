#!/usr/bin/env python3
"""Pre-registered topology domain scan; unchanged native FAST timing semantics."""
import argparse
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
_spec=importlib.util.spec_from_file_location('private_scenario_engine',SIM/'scan/study.py')
base=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(base)
_files=base.files
_prepare=base.prepare
_checked=base.checked
SUITE='explore'
METHODS=['F000','maxmin','spreadout','X10','X11','A1','A2']
FLAGS=[*base.FLAGS,'-DFAST_SCENARIO_SCAN']
sha=base.sha
save=base.save
invoke=base.invoke
close=base.legacy.close


def files():
    return sorted(set(_files())|{p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'}|set((ROOT/'tests').glob('*.*')))


def identity():
    record=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,suite=SUITE,
                compiler=base.subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
                libc=list(platform.libc_ver()),schema=1)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest();return record


def specs(smoke=False,suite=None):
    suite=suite or SUITE;cases=[]
    seeds=[520201,520203] if suite=='confirm' else [520101]
    for dc_count in ([3] if smoke else [3,6] if suite=='confirm' else [2,3,4,6]):
        for profile in (['shared100','gateway100','asymmetric'] if smoke else ['shared100','gateway100'] if suite=='confirm'
                        else ['endpoint_control','shared100','shared25','gateway100']+(['asymmetric'] if dc_count>2 else [])):
            for kind in (['block'] if smoke else ['balanced','zipf']+(['block'] if dc_count>2 else [])):
                for wan in ([50] if smoke else [50,5000]):
                    for layout_seed in seeds:
                        n,g,mib=(6,1,1) if smoke else (24,8,64)
                        dc=[s*dc_count//n for s in range(n)]
                        if suite=='confirm':random.Random(layout_seed).shuffle(dc)
                        seed=layout_seed if kind=='zipf' else 0
                        name=f'n{n}g{g}-d{dc_count}-{profile}-{kind}-s{seed}-l{layout_seed}-w{wan}'
                        cases.append(dict(id=name,n=n,g=g,mib=mib,dc_count=dc_count,dc=dc,profile=profile,
                                          kind=kind,seed=seed,layout_seed=layout_seed,wan_us=wan,split=suite))
    return cases


def input_key(case):
    key=(case['n'],case['g'],case['mib'],case['kind'],case['seed'])
    return key+(tuple(case['dc']),case['layout_seed']) if case['kind']=='block' else key


def matrix(case,binary):
    n,g=case['n'],case['g'];r=n*g
    q=case['mib']*1024**2//(4*(r-1))*(4*(r-1));u=q//(r-1)
    if case['kind']=='zipf':
        weights=json.loads(base.subprocess.check_output([str(binary),'--zipf',str(case['seed']),str(r*r)],text=True))
        for i in range(r):weights[i*r+i]=0
        total=sum(weights);target=r*q;values=[v*target//total for v in weights]
        order=sorted(range(r*r),key=lambda k:-(weights[k]*target%total))
        for k in order[:target-sum(values)]:values[k]+=1
        return values
    if case['kind'] not in ['balanced','block']:raise ValueError('unknown demand kind')
    groups=sorted(set(case['dc']));cycle=list(groups);random.Random(case['layout_seed']).shuffle(cycle)
    hot={cycle[i]:cycle[(i+1)%len(cycle)] for i in range(len(cycle))}
    values=[]
    for i in range(r):
        a=case['dc'][i//g]
        for j in range(r):
            b=case['dc'][j//g]
            v=0 if i==j else u
            if case['kind']=='block' and a!=b:v=u//4+(3*(len(groups)-1)*u//4 if b==hot[a] else 0)
            values.append(v)
    return values


def capacities(case):
    n=case['n'];profile=case['profile'];sb,gb,wb=400,400,100
    if profile=='endpoint_control':gb=wb=n*400
    elif profile=='shared25':wb=25
    elif profile=='gateway100':gb=100
    elif profile not in ['shared100','asymmetric']:raise ValueError('unknown profile')
    return sb,gb,wb


def topology(case):
    n=case['n'];dc=case['dc'];sb,gb,wb=capacities(case);groups=sorted(set(dc));bandwidths={}
    pairs=[(a,b) for a in groups for b in groups if a<b]
    mean=statistics.mean([(.5,1,2)[(a+b)%3] for a,b in pairs]) if case['profile']=='asymmetric' else 1
    for s in range(n):
        for d in range(n):
            if s==d:continue
            bandwidths[f'server-out:{s}']=sb;bandwidths[f'server-in:{d}']=sb
            if dc[s]!=dc[d]:
                bandwidths[f'gateway-out:{dc[s]}']=gb;bandwidths[f'gateway-in:{dc[d]}']=gb
                bandwidths[f'wan:{dc[s]}:{dc[d]}']=wb*(.5,1,2)[(dc[s]+dc[d])%3]/mean if case['profile']=='asymmetric' else wb
    return dict(dc=dc,bandwidths=bandwidths)


def command(binary,case,method=None,repeats=1):
    prefix=[str(binary),'--batch',str(case['n']),str(case['g'])] if method is None else [str(binary),str(case['n']),str(case['g']),method]
    return prefix+[str(case['wan_us']),str(repeats),','.join(map(str,case['dc'])),*map(str,capacities(case)),
                   'asymmetric' if case['profile']=='asymmetric' else 'uniform']


def policy(method):
    if method not in METHODS:raise ValueError('unknown method')
    return dict(exact=method in ['maxmin','X10','X11','A1','A2'],link=method in ['X10','X11'],
                residual=method=='X11',tail_score=False,quota_numerator=1,quota_denominator=1,
                cycle_budget=2 if method in ['X10','X11'] else 0,
                constructive_budget=1 if method=='A1' else 2 if method=='A2' else 0)


def reconstruct(case,method,data,raw):
    n,g=case['n'],case['g'];r=n*g;dc=case['dc'];declared=policy(method);expected=topology(case)
    if raw['mode']!=method or not raw['payload_verified'] or raw['policy']!=declared:raise ValueError('identity/policy')
    if raw['topology']['dc']!=dc or set(raw['topology']['bandwidths'])!=set(expected['bandwidths']):raise ValueError('topology labels/resources')
    for key,bw in expected['bandwidths'].items():close(raw['topology']['bandwidths'][key],bw)
    delivered=[[0]*n for _ in range(n)];totals={};costs=[];wan_stages=0
    for k,stage in enumerate(raw['stages']):
        size=r if method=='spreadout' else n
        if sorted(stage['map'])!=list(range(size)) or len(stage['payload'])!=r:raise ValueError('stage shape')
        if method!='spreadout' and stage['freq']<=0:raise ValueError('nonpositive coefficient')
        links={};delay=0;local=0
        for s,d in enumerate(stage['map']):
            if method=='spreadout':
                if d!=(s+k)%r or stage['payload'][s]!=data[s*r+d]:raise ValueError('SpreadOut pairing/payload')
                volume=stage['payload'][s];src,dst=s//g,d//g
                if volume and src==dst:local=max(local,.1+volume*(8000/3600)*1e-6)
            else:
                values=stage['payload'][s*g:(s+1)*g]
                if any(not isinstance(v,int) or v<0 or v>stage['freq'] for v in values):raise ValueError('GPU quota')
                volume=sum(values);src,dst=s,d
            if src==dst or not volume:continue
            delivered[src][dst]+=volume;keys=[f'server-out:{src}',f'server-in:{dst}']
            if dc[src]!=dc[dst]:
                keys += [f'gateway-out:{dc[src]}',f'gateway-in:{dc[dst]}',f'wan:{dc[src]}:{dc[dst]}']
                delay=max(delay,case['wan_us']+2.8)
            else:delay=max(delay,1.4)
            for key in keys:links[key]=links.get(key,0)+volume
        bandwidths={key:expected['bandwidths'][key] for key in links}
        if stage['links']!=links or set(stage['bandwidths'])!=set(bandwidths):raise ValueError('routed resources/bytes')
        for key,bw in bandwidths.items():close(stage['bandwidths'][key],bw)
        serial=max((.008*v/bandwidths[key] for key,v in links.items()),default=0)
        close(stage['alpha_us'],delay);close(stage['serial_us'],serial);close(stage['communication_us'],delay+serial)
        close(stage['local_us'],local)
        costs.append(max(delay+serial,local) if method=='spreadout' else delay+serial)
        wan_stages+=int(any(key.startswith('wan:') for key in links))
        for key,v in links.items():totals[key]=totals.get(key,0)+v
    for s in range(n):
        for d in range(n):
            if s!=d and delivered[s][d]!=sum(data[(s*g+i)*r+d*g+j] for i in range(g) for j in range(g)):raise ValueError('server-pair conservation')
    inter=sum(map(sum,delivered));close(raw['inter_bytes'],inter)
    dispatch=raw['dispatch_us']
    if any(not math.isfinite(v) or v<0 for v in [raw['balance_us'],raw['intrinsic_us'],*dispatch]):raise ValueError('local timing')
    if method=='spreadout':
        if len(costs)!=r or dispatch or raw['service'] or raw['decisions'] or raw['decomposition_runs']:raise ValueError('SpreadOut native path')
        total=sum(costs)
    else:
        if len(dispatch)!=len(costs) or len(costs)>n*n or raw['decomposition_runs']!=1:raise ValueError('one-pass finite decomposition')
        total=raw['balance_us']+(max(raw['intrinsic_us'],costs[0])+sum(max(costs[k],dispatch[k-1]) for k in range(1,len(costs)))+dispatch[-1] if costs else raw['intrinsic_us'])
        decisions=raw['decisions']
        if method=='F000':
            if decisions or raw['changed_matches']:raise ValueError('native matching modified')
        elif len(decisions)!=len(costs):raise ValueError('missing decisions')
        for stage,decision in zip(raw['stages'],decisions):
            if decision['weight']!=stage['freq'] or decision['weight']<decision['seed_weight'] or decision['threshold']<=0:raise ValueError('coefficient guard')
            if decision['threshold']!=decision['exact_weight'] or decision['weight']!=decision['exact_weight'] or decision['exact_weight']<decision['native_weight'] or decision['native_weight']<=0:raise ValueError('exact threshold')
            if decision['constructions']!=declared['constructive_budget']:raise ValueError('constructive budget')
        padded=[0]*(n*n)
        for stage in raw['stages']:
            for s,d in enumerate(stage['map']):padded[s*n+d]+=stage['freq']
        service=raw['service']
        if len(service)!=n*n or any(not isinstance(v,int) or v<0 for v in service):raise ValueError('service shape')
        maximum=max([sum(service[s*n:(s+1)*n]) for s in range(n)]+[sum(service[s*n+d] for s in range(n)) for d in range(n)])
        if any(padded[k]<service[k] for k in range(n*n)) or any(sum(padded[s*n:(s+1)*n])!=maximum for s in range(n)):raise ValueError('padded service conservation')
    close(raw['total_us'],total)
    if raw['cycle_trials']>declared['cycle_budget']*len(costs) or raw['construction_trials']!=sum(d['constructions'] for d in raw['decisions']):raise ValueError('operation budget/count')
    if raw['construction_accepted']>raw['construction_trials'] or raw['threshold_probes'] or raw['lower_coefficient_rejections']:raise ValueError('operation guards')
    if raw['native_decompose_us']!=0 or raw['extra_planner_us']!=0:raise ValueError('duplicate planning timing')
    samples=raw['timing_samples']
    if not samples:raise ValueError('missing measured samples')
    for sample in samples:
        if set(sample)!={'planning_us','setup_us','decompose_us','preprocess_us'} or any(not math.isfinite(v) or v<0 for v in sample.values()):raise ValueError('timing schema')
        if sample['setup_us']>sample['planning_us'] or sample['decompose_us']>sample['planning_us'] or sample['preprocess_us']>sample['decompose_us']:raise ValueError('timing containment')
    if not math.isfinite(raw['wall_us']) or raw['wall_us']<0:raise ValueError('wall timing')
    med=lambda key:statistics.median(s[key] for s in samples)
    timings=sorted(s['planning_us'] for s in samples)
    lb=max((.008*v/expected['bandwidths'][key] for key,v in totals.items()),default=0)
    wan_work=max((.008*v/expected['bandwidths'][key] for key,v in totals.items() if key.startswith('wan:')),default=0)
    serial=sum(s['serial_us'] for s in raw['stages']);alpha=sum(s['alpha_us'] for s in raw['stages'])
    if total+1e-7<lb or serial+1e-7<lb:raise ValueError('resource work bound')
    row=dict(**case,method=method,total_ms=total/1000,stages=len(costs),wan_stages=wan_stages,
             alpha_ms=alpha/1000,serial_ms=serial/1000,resource_lb_ms=lb/1000,serial_gap_ms=(serial-lb)/1000,
             model_wan_util=wan_work/total if total else 0,planning_us=med('planning_us'),
             planning_p95_us=timings[math.ceil(.95*len(timings))-1],decompose_us=med('decompose_us'),
             setup_us=med('setup_us'),preprocess_us=med('preprocess_us'),timing_repeats=len(samples),
             modeled_plus_planning_ms=total/1000+med('planning_us')/1000,
             cycle_trials=raw['cycle_trials'],construction_trials=raw['construction_trials'],construction_accepted=raw['construction_accepted'],
             matching_calls=raw['matching_calls'],changed_matches=raw['changed_matches'],inter_bytes=inter)
    return row,totals


def prepare(output,smoke=False):
    output=Path(output)
    if (output/'study.json').exists():return checked(output,smoke)
    record=_prepare(output,smoke);record.update(suite=SUITE,repeats=3 if smoke else 21 if SUITE=='confirm' else 5)
    save(output/'study.json',record);return record


def checked(output,smoke=False):
    record=_checked(output,smoke)
    if record.get('suite')!=SUITE or record['repeats']!=(3 if smoke else 21 if SUITE=='confirm' else 5):raise ValueError('suite/repeat registration')
    return record


base.METHODS=METHODS; base.FLAGS=FLAGS; base.files=files; base.identity=identity
base.specs=specs; base.matrix=matrix; base.input_key=input_key; base.command=command
base.reconstruct=reconstruct; base.checked=checked
build=base.build; run=base.run; audit=base.audit


def summarize(rows):
    index={(r['id'],r['method']):r for r in rows}
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs))
    result=[]
    # Groups are descriptive strata, not a retrospective winner selector.
    groups=[('all',lambda r:True)]
    groups += [('load:'+k,lambda r,k=k:r['kind']==k) for k in sorted({r['kind'] for r in rows})]
    groups += [('profile:'+p,lambda r,p=p:r['profile']==p) for p in sorted({r['profile'] for r in rows})]
    groups += [('D:'+str(d),lambda r,d=d:r['dc_count']==d) for d in sorted({r['dc_count'] for r in rows})]
    groups += [('WAN:'+str(w),lambda r,w=w:r['wan_us']==w) for w in sorted({r['wan_us'] for r in rows})]
    for name,include in groups:
        for method in METHODS:
            selected=[r for r in rows if r['method']==method and include(r)]
            record=dict(group=name,method=method,configs=len(selected),planning_median_ms=statistics.median(r['planning_us']/1000 for r in selected),
                        planning_p95_config_ms=statistics.quantiles([r['planning_us']/1000 for r in selected],n=20,method='inclusive')[18] if len(selected)>1 else selected[0]['planning_us']/1000)
            for baseline in ['F000','maxmin','spreadout','X11']:
                ratios=[index[r['id'],baseline]['total_ms']/r['total_ms'] for r in selected]
                all_ratios=[index[r['id'],baseline]['modeled_plus_planning_ms']/r['modeled_plus_planning_ms'] for r in selected]
                record[baseline]=dict(gm=gm(ratios),minimum=min(ratios),maximum=max(ratios),
                                      wins=sum(v>1+1e-9 for v in ratios),losses=sum(v<1-1e-9 for v in ratios),with_planning_gm=gm(all_ratios))
            result.append(record)
    return result


def render(output,smoke=False):
    output=Path(output);rows=audit(output,smoke)
    save(output/'summary.json',summarize(rows))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    gm=lambda vs:math.exp(statistics.mean(math.log(v) for v in vs))
    kinds=sorted({r['kind'] for r in rows});fig,axes=plt.subplots(1,len(kinds),figsize=(5*len(kinds),4.5),squeeze=False,layout='constrained')
    for ax,kind in zip(axes[0],kinds):
        selected=[r for r in rows if r['kind']==kind]
        ax.bar(METHODS,[gm([r['speedup_maxmin'] for r in selected if r['method']==m]) for m in METHODS])
        ax.axhline(1,color='gray',ls='--');ax.set_title(f'{SUITE}: {kind}');ax.set_ylabel('Communication speedup / Max-Min');ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'speedup.png',dpi=170);plt.close(fig)
    profiles=['endpoint_control','shared100','shared25','gateway100','asymmetric'];groups=sorted({(r['dc_count'],r['kind'],r['wan_us']) for r in rows})
    fig,axes=plt.subplots(1,2,figsize=(16,max(5,len(groups)*.3)),layout='constrained')
    for ax,method in zip(axes,['A1','A2']):
        values=[]
        for group in groups:
            values.append([gm([r['speedup_maxmin'] for r in rows if r['method']==method and (r['dc_count'],r['kind'],r['wan_us'])==group and r['profile']==p])
                           if any(r['method']==method and (r['dc_count'],r['kind'],r['wan_us'])==group and r['profile']==p for r in rows) else float('nan') for p in profiles])
        ax.imshow(values,aspect='auto',vmin=.75,vmax=2,cmap='RdYlGn')
        ax.set_xticks(range(len(profiles)),profiles,rotation=30,ha='right');ax.set_yticks(range(len(groups)),[f'D{d} {k} {w}us' for d,k,w in groups],fontsize=8)
        ax.set_title(f'{method}: speedup / Max-Min (linear)')
        for i,line in enumerate(values):
            for j,v in enumerate(line):
                if math.isfinite(v):ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=7)
    fig.savefig(output/'domain.png',dpi=170);plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4),layout='constrained')
    values=[[r['planning_us']/1000 for r in rows if r['method']==m] for m in METHODS]
    ax.boxplot(values,tick_labels=METHODS,showfliers=False);ax.set_ylabel('Full host planning median per config (ms)');ax.set_title(f'{SUITE}: excludes analytic evaluation');ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'planning.png',dpi=170);plt.close(fig)


def main():
    global SUITE
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True,choices=['prepare','run','audit','render'])
    p.add_argument('--suite',choices=['explore','confirm'],default='explore');p.add_argument('--output',type=Path);p.add_argument('--smoke',action='store_true');args=p.parse_args();SUITE=args.suite
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    globals()[args.phase](output,args.smoke)

if __name__=='__main__':main()
