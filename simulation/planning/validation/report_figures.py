#!/usr/bin/env python3
"""Report-only derived figures; refuse mixed-version CPU provenance."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

METHODS=['F000','maxmin','spreadout','X10','SPM','A1']


def gm(values):return math.exp(statistics.mean(math.log(v) for v in values))


def require_final_binary(bridge,registration):
    if bridge['binary_sha256']!=registration['identity']['binary_sha256']:raise ValueError('CPU evidence is not from final binary')


def render(formal,isolated,final,out):
    read=lambda path:json.loads(path.read_text())
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    save=lambda path,value:path.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    bridge=read(final/'checks.json');registration=read(isolated/'registration.json');require_final_binary(bridge,registration)
    if len(bridge['checks'])!=480 or len(bridge['historical'])!=116 or not all(c['equal'] for c in bridge['checks']+bridge['historical']):raise ValueError('incomplete final semantic proof')
    communication=read(formal/'comparison.json');cpu=read(isolated/'comparison.json');out.mkdir(parents=True,exist_ok=True)
    opt=[r for r in cpu if r['implementation']=='opt'];index={(r['id'],r['method']):r for r in cpu}
    metrics={}
    for kind in ['all','balanced','zipf']:
        metrics[kind]={}
        for method in METHODS:
            cc=[r for r in communication if r['method']==method+'-opt' and (kind=='all' or r['kind']==kind)]
            ss=[r for r in opt if r['strategy']==method and (kind=='all' or r['kind']==kind)]
            refs=[index[r['id'],method+'-ref'] for r in ss]
            gains=[r['planning_speedup'] for r in ss]
            metrics[kind][method]=dict(communication={b:gm([r['speedup_'+b] for r in cc]) for b in METHODS},
                planning_gm=gm(gains),planning_min=min(gains),planning_max=max(gains),
                ref_median_ms=statistics.median(r['planning_us']/1000 for r in refs),
                opt_median_ms=statistics.median(r['planning_us']/1000 for r in ss),
                p95_median_ms=statistics.median(r['planning_p95_us']/1000 for r in ss),
                with_planning={b:gm([r['with_planning_'+b] for r in ss]) for b in METHODS})
    save(out/'metrics.json',metrics)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for ax,baseline in zip(axes,['maxmin','SPM']):
        for offset,kind in enumerate(['balanced','zipf']):
            values=[metrics[kind][method]['communication'][baseline] for method in METHODS]
            ax.bar([i+(offset-.5)*.35 for i in range(6)],values,.35,label=kind)
        ax.set_xticks(range(6),METHODS);ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Communication speedup / '+baseline)
        ax.set_title('N24/48, confirm; equal load-stratum weights');ax.legend();ax.grid(axis='y',alpha=.2)
    fig.savefig(out/'communication.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,4.5),layout='constrained')
    selected=[r for r in opt if r['strategy']=='A1'];xs=list(range(len(selected)))
    for offset,impl in enumerate(['ref','opt']):
        values=[index[r['id'],'A1-'+impl]['planning_us']/1000 for r in selected]
        axes[0].bar([i+(offset-.5)*.36 for i in xs],values,.36,label=impl)
    axes[0].set_xticks(xs,[f'D{r["dc_count"]} {r["kind"]}\n{r["wan_us"]}us' for r in selected],rotation=35,ha='right',fontsize=8)
    axes[0].set_ylabel('A1 full planning median (ms)');axes[0].set_title('N48: 24 samples/config; final binary');axes[0].legend()
    values=[metrics['all'][method]['planning_gm'] for method in METHODS]
    bars=axes[1].bar(METHODS,values,color=['#999999']*5+['#1f77b4'])
    for bar,value in zip(bars,values):axes[1].text(bar.get_x()+bar.get_width()/2,value+.015,f'{value:.3f}',ha='center',fontsize=9)
    axes[1].axhline(1,color='gray',ls='--');axes[1].set_ylim(0,max(values)*1.15);axes[1].set_ylabel('Paired planning speedup ref / opt (GM)')
    axes[1].set_title('Gray: unchanged-kernel controls; blue: pooled A1')
    for ax in axes:ax.grid(axis='y',alpha=.2)
    fig.savefig(out/'planning_final.png',dpi=170);plt.close(fig)
    save(out/'provenance.json',dict(script_sha256=digest(Path(__file__)),test_sha256=digest(Path(__file__).with_name('test_figures.py')),
                communication_sha256=digest(formal/'comparison.json'),cpu_sha256=digest(isolated/'comparison.json'),
                final_checks_sha256=digest(final/'checks.json'),binary_sha256=bridge['binary_sha256'],
                cpu_warning='Only isolated final-binary CPU; no V1 timing is used in these figures. No confidence intervals implied.'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ['formal','isolated','final','output']:parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();render(args.formal.resolve(),args.isolated.resolve(),args.final.resolve(),args.output.resolve())
