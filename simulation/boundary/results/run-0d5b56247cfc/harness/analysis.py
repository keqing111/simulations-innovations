#!/usr/bin/env python3
"""Audited reoptimized versus frozen-schedule counterfactual analysis."""
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

_spec=importlib.util.spec_from_file_location('boundary_analysis_runner',Path(__file__).with_name('study.py'))
study=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(study)


def schedule_signature(raw):
    value={k:raw.get(k) for k in ['mode','service','balance_us','intrinsic_us','dispatch_us']}
    value['stages']=[{k:s[k] for k in ['freq','map','payload','local_us']} for s in raw['stages']]
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def frozen_cost(raw,case,bandwidths):
    costs=[]
    for stage in raw['stages']:
        links={k:v for k,v in stage['links'].items() if v>0};serial=0
        for key,v in links.items():
            if key not in bandwidths or not math.isfinite(bandwidths[key]) or bandwidths[key]<=0:raise ValueError('unknown/nonpositive frozen resource')
            serial=max(serial,.008*v/bandwidths[key])
        alpha=case['wan_us']+2.8 if any(k.startswith('wan:') for k in links) else 1.4 if links else 0
        costs.append(max(alpha+serial,stage['local_us']) if raw['mode'].startswith('spreadout') else alpha+serial)
    if raw['mode'].startswith('spreadout'):return sum(costs)
    dispatch=raw['dispatch_us']
    if len(dispatch)!=len(costs):raise ValueError('frozen pipeline shape')
    if not costs:return raw['balance_us']+raw['intrinsic_us']
    return raw['balance_us']+max(raw['intrinsic_us'],costs[0])+sum(max(costs[k],dispatch[k-1]) for k in range(1,len(costs)))+dispatch[-1]


def roots_on_grid(fn,xs):
    if len(xs)<2 or any(not math.isfinite(x) for x in xs) or any(b<=a for a,b in zip(xs,xs[1:])):raise ValueError('root grid')
    def value(x):
        y=fn(x)
        if not math.isfinite(y):raise ValueError('nonfinite crossover function')
        return y
    ys=[value(x) for x in xs];eps=1e-7;zeros=[x for x,y in zip(xs,ys) if abs(y)<=eps];roots=[]
    for lo,hi,flo,fhi in zip(xs,xs[1:],ys,ys[1:]):
        if abs(flo)<=eps or abs(fhi)<=eps or flo*fhi>=0:continue
        left,right=lo,hi;fl=flo
        for _ in range(70):
            mid=(left+right)/2;fm=value(mid)
            if abs(fm)<=eps or right-left<1e-9:break
            if (fm>0)==(fl>0):left=mid;fl=fm
            else:right=mid
        roots.append(dict(lo_us=lo,hi_us=hi,root_us=mid))
    return dict(state='sampled_tie' if len(zeros)==len(xs) else 'bracketed' if roots else 'sampled_zero' if zeros else 'no_sampled_crossing',
                roots=roots,sampled_zeros_us=zeros,warning='Not exhaustive for unsampled interior roots; frozen schedules only')


def group_key(c):return tuple(c[k] for k in ['n','g','mib','dc_count','kind','seed'])


def analyze(output,smoke=False):
    output=Path(output);record=study.checked(output,smoke,derived=True);engine=study.load_engine(output)
    rows=json.loads((output/'comparison.json').read_text());index={(r['id'],r['method']):r for r in rows}
    groups={group_key(c) for c in record['cases']};xs=sorted({c['wan_us'] for c in record['cases']})
    anchors={};anchor_cases={}
    for key in sorted(groups):
        c=next(c for c in record['cases'] if group_key(c)==key and c['wan_bw']==100 and c['wan_us']==max(xs));anchor_cases[key]=c
        for method in study.METHODS:anchors[key,method]=json.loads((output/'raw'/f'{study.raw_key(c,method)}.json').read_text())
    result=[]
    for c in record['cases']:
        key=group_key(c);caps=engine.scene.topology(c)['bandwidths'];batch=[]
        for method in study.METHODS:
            raw=json.loads((output/'raw'/f'{study.raw_key(c,method)}.json').read_text());anchor=anchors[key,method]
            frozen=frozen_cost(anchor,c,caps);changed=schedule_signature(raw)!=schedule_signature(anchor)
            if c['id']==anchor_cases[key]['id'] and not math.isclose(frozen,raw['total_us'],rel_tol=1e-10,abs_tol=1e-7):raise ValueError('anchor pipeline repricing mismatch')
            if method in ['F000-opt','maxmin-opt','spreadout-opt']:
                if changed or not math.isclose(frozen,raw['total_us'],rel_tol=1e-10,abs_tol=1e-7):raise ValueError('topology-unaware scheduling/repricing changed')
            row=dict(index[c['id'],method],anchor_case=anchor_cases[key]['id'],frozen_ms=frozen/1000,
                     schedule_changed=changed,repricing_over_reoptimized=frozen/raw['total_us'])
            exposed=row['total_ms']-row['alpha_ms']-row['serial_ms']
            if exposed<-1e-7:raise ValueError('negative exposed local time')
            row['exposed_local_ms']=max(0,exposed);batch.append(row)
        lookup={r['method']:r for r in batch}
        for row in batch:
            for base in ['maxmin','SPM']:row['frozen_speedup_'+base]=lookup[base+'-opt']['frozen_ms']/row['frozen_ms']
        result.extend(batch)
    crossings=[]
    for key in sorted(groups):
        anchor=anchor_cases[key]
        for bw in sorted({c['wan_bw'] for c in record['cases']}):
            for baseline in ['maxmin','SPM']:
                def delta(wan):
                    c=dict(anchor,wan_us=wan,wan_bw=bw);caps=engine.scene.topology(c)['bandwidths']
                    return frozen_cost(anchors[key,'A1-opt'],c,caps)-frozen_cost(anchors[key,baseline+'-opt'],c,caps)
                root=roots_on_grid(delta,xs);actual=[]
                for wan in xs:
                    c=next(c for c in record['cases'] if group_key(c)==key and c['wan_bw']==bw and c['wan_us']==wan)
                    actual.append(index[c['id'],'A1-opt']['total_ms']-index[c['id'],baseline+'-opt']['total_ms'])
                crossings.append(dict(n=anchor['n'],dc_count=anchor['dc_count'],kind=anchor['kind'],seed=anchor['seed'],wan_bw=bw,
                    baseline=baseline,anchor_case=anchor['id'],frozen=root,
                    rerun_sign_change_intervals_us=[[a,b] for a,b,fa,fb in zip(xs,xs[1:],actual,actual[1:]) if fa*fb<0],
                    rerun_sampled_ties_us=[x for x,y in zip(xs,actual) if abs(y)<1e-9],
                    rerun_delta_ms=actual,wan_grid_us=xs))
    summary=[]
    gm=lambda values:math.exp(statistics.mean(math.log(v) for v in values))
    for key in sorted(groups):
        for method in study.METHODS:
            selected=[r for r in result if group_key(r)==key and r['method']==method];first=selected[0]
            value=dict(n=first['n'],dc_count=first['dc_count'],kind=first['kind'],seed=first['seed'],method=method,configs=len(selected))
            for baseline in ['F000','maxmin','spreadout','X10','SPM']:
                values=[r['speedup_'+baseline] for r in selected]
                value[baseline]=dict(gm=gm(values),minimum=min(values),maximum=max(values),
                    wins=sum(v>1.01 for v in values),losses=sum(v<1/1.01 for v in values),near_ties=sum(1/1.01<=v<=1.01 for v in values))
            summary.append(value)
    study.save(output/'analysis.json',result);study.save(output/'crossings.json',crossings);study.save(output/'summary.json',summary)
    with (output/'analysis.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(result[0]));writer.writeheader();writer.writerows(result)
    study.register_derived(output,[output/name for name in ['analysis.json','analysis.csv','crossings.json','summary.json']])
    print('Analyzed',len(result),'frozen/reoptimized pairs;',len(crossings),'fixed-anchor comparisons',flush=True);return result


def render(output,smoke=False):
    output=Path(output);record=study.checked(output,smoke,derived=True)
    if 'analysis.json' not in record['derived']['files']:raise ValueError('analysis not audited')
    rows=json.loads((output/'analysis.json').read_text());selected=[r for r in rows if r['method']=='A1-opt'];index={(r['id'],r['method']):r for r in rows}
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    loads=sorted({(r['kind'],r['seed']) for r in selected});dcs=sorted({r['dc_count'] for r in selected})
    xs=sorted({r['wan_us'] for r in selected});bws=sorted({r['wan_bw'] for r in selected})
    fig,axes=plt.subplots(len(dcs)*2,len(loads),figsize=(5.2*len(loads),3.1*len(dcs)*2),squeeze=False,layout='constrained')
    image=None
    for di,dc in enumerate(dcs):
        for bi,baseline in enumerate(['maxmin','SPM']):
            for li,(kind,seed) in enumerate(loads):
                ax=axes[di*2+bi,li];data=[]
                for bw in bws:
                    data.append([next(r['speedup_'+baseline] for r in selected if (r['dc_count'],r['kind'],r['seed'],r['wan_bw'],r['wan_us'])==(dc,kind,seed,bw,x)) for x in xs])
                image=ax.imshow(data,aspect='auto',cmap='RdYlGn',norm=TwoSlopeNorm(vmin=.85,vcenter=1,vmax=1.25))
                for i,line in enumerate(data):
                    for j,v in enumerate(line):ax.text(j,i,f'{v:.3f}',ha='center',va='center',fontsize=9,color='white' if v<.9 or v>1.18 else 'black')
                ax.set_xticks(range(len(xs)),[str(x/1000) for x in xs]);ax.set_yticks(range(len(bws)),bws)
                ax.set_xlabel('One-way WAN delay (ms)');ax.set_ylabel('WAN base (Gbps)');ax.set_title(f'D{dc} {kind}'+(f' {seed}' if seed else '')+f' / {baseline}')
    fig.colorbar(image,ax=axes.ravel().tolist(),label='Baseline / A1; linear, saturated outside [0.85,1.25]',shrink=.65)
    assets=output/'figures';assets.mkdir(exist_ok=True);fig.savefig(assets/'heatmap.png',dpi=170);plt.close(fig)
    focus=next((r for r in selected if r['dc_count']==6 and r['kind']=='zipf' and r['seed']==760203),next(r for r in selected if r['kind']=='zipf'))
    group=[r for r in selected if group_key(r)==group_key(focus)]
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
    for ax,baseline in zip(axes,['maxmin','SPM']):
        for bw,color in zip(bws,['#1f77b4','#ff7f0e','#2ca02c']):
            values=sorted([r for r in group if r['wan_bw']==bw],key=lambda r:r['wan_us'])
            ax.plot([r['wan_us']/1000 for r in values],[r['speedup_'+baseline] for r in values],'-o',color=color,label=f'{bw}Gbps replan')
            ax.plot([r['wan_us']/1000 for r in values],[r['frozen_speedup_'+baseline] for r in values],'--s',color=color,label=f'{bw}Gbps frozen')
        ax.axhline(1,color='gray',ls=':');ax.set_xlabel('One-way WAN delay (ms)');ax.set_ylabel('Baseline / A1');ax.grid(alpha=.2);ax.legend(fontsize=8,ncol=2)
        ax.set_title(f'D{focus["dc_count"]} Zipf{focus["seed"]} / {baseline}; sampled lines')
    fig.savefig(assets/'frozen_vs_replan.png',dpi=170);plt.close(fig)
    provenance=dict(identity=record['identity'],binary_sha256=study.sha(output/'native_scan'),raw_records_fingerprint=study.record_fingerprint(record),
                    derived_tables={name:digest for name,digest in record['derived']['files'].items() if not name.startswith('figures/') and name.endswith(('.json','.csv'))},
                    warning='Communication only; frozen roots are not global/rerun algorithm thresholds; no CPU inference')
    study.save(assets/'provenance.json',provenance)
    study.register_derived(output,[assets/name for name in ['heatmap.png','frozen_vs_replan.png','provenance.json']])
