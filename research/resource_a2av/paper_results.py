#!/usr/bin/env python3
"""Two paper figures and one small table from completed actual replays only."""
import argparse
import csv
import json
import math
from pathlib import Path
import experiment as e
from simai_a2a.artifacts import read_matrix_artifact
from simai_a2a.schedulers.resource_budget import anchored_cap,evaluate_round,prepare_resources


def sample_range(values):
    return sum(values)/len(values),min(values),max(values),len(values)


def merged(intervals):
    result=[]
    for a,b in sorted(intervals):
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(result[-1][1],b))
        else:result.append((a,b))
    return result


def overlap_ns(a,b):
    a,b=merged(a),merged(b);i=j=total=0
    while i<len(a) and j<len(b):
        total+=max(0,min(a[i][1],b[j][1])-max(a[i][0],b[j][0]))
        if a[i][1]<b[j][1]:i+=1
        else:j+=1
    return total


def ratios(rows,baseline):
    values=[]
    for row in rows:
        a,b=row.get(baseline+'_ms'),row.get('resource-maxq_ms')
        if a is not None and b is not None and math.isfinite(a) and math.isfinite(b) and a>0 and b>0:values.append(a/b)
    return dict(paired=len(values),unpaired=len(rows)-len(values),
        geomean=math.exp(sum(map(math.log,values))/len(values)) if values else None,
        wins=sum(v>1+1e-6 for v in values),ties=sum(abs(v-1)<=1e-6 for v in values),losses=sum(v<1-1e-6 for v in values))


def compare_plans(study):
    lookup={(r['case'],r['method']):r for r in study['runs']};rows=[];states={}
    for case in study['cases']:
        plans=[]
        for method in ['volume-maxq','resource-maxq']:
            run=lookup[case['name'],method]
            plans.append(json.loads((Path(run['attempt_dir']).parents[2]/(method+'.plan.json')).read_text()))
        a,b=plans
        first=next((k for k,(p,q,r,s) in enumerate(zip(a['permutations'],a['coefficients'],b['permutations'],b['coefficients'])) if p!=r or q!=s),None)
        row=dict(case=case['name'],same_plan=a['permutations']==b['permutations'] and a['coefficients']==b['coefficients'],
            same_stages=len(a['coefficients'])==len(b['coefficients']),same_coefficients=a['coefficients']==b['coefficients'],first_difference=first)
        if first is not None:
            source=Path(case['source']);matrix,_=read_matrix_artifact(source/'matrix')
            hw,_,_=e.iterations.load_hardware(source/'hardware.json');pre=e.iterations.prepare(matrix,hw)
            res=prepare_resources(json.loads((source/'planner_topology.json').read_text()));R,U=pre['R'],pre['U']
            for k in range(first):
                step=evaluate_round(R,U,a['permutations'][k],a['coefficients'][k],res);R,U=step['R_next'],step['U_next']
            q,_=anchored_cap(R)
            assert a['coefficients'][first]==b['coefficients'][first]==q
            for p in [a['permutations'][first],b['permutations'][first]]:assert sorted(p)==list(range(len(R))) and min(R[s][d] for s,d in enumerate(p))==q
            states[case['name']]=dict(stage=first,q_star=q,R=R,U=U,volume_matching=a['permutations'][first],resource_matching=b['permutations'][first])
            row['same_q_star_at_first_difference']=True
        rows.append(row)
    return rows,states


def build(out,mechanism):
    study=json.loads((out/'study.json').read_text())
    if not study['complete']:raise ValueError('main cohort is incomplete')
    with (out/'paired.csv').open() as stream:paired=list(csv.DictReader(stream))
    for row in paired:
        for m in ['volume-maxq','resource-maxq','fast-simai','spreadout']:row[m+'_ms']=float(row[m+'_ms']) if row[m+'_ms'] else None
    comparison,states=compare_plans(study);e.audit.save_csv(out/'plan_comparison.csv',comparison);e.dump(out/'first_differences.json',states)
    table=[]
    for top in ['leaf-uniform','leaf-bandwidth','multi-cut']:
        for family in ['balanced','zipf08']:
            subset=[r for r in paired if r['topology']==top and r['family']==family]
            row=dict(topology=top,family=family,conditions=len(subset))
            for m in ['volume-maxq','fast-simai','spreadout']:
                for k,v in ratios(subset,m).items():row[m+'_'+k]=v
            table.append(row)
    e.audit.save_csv(out/'paper_table.csv',table)
    summary=dict(overall={m:ratios(paired,m) for m in ['volume-maxq','fast-simai','spreadout']},
                 conditions=len(paired),changed_matching_cases=sum(not r['same_plan'] for r in comparison),
                 same_coefficient_cases=sum(r['same_coefficients'] for r in comparison),
                 scope='all24conditions; displayed error ranges are three input seeds, not confidence intervals')
    e.dump(out/'paper_summary.json',summary)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    plt.rcParams.update({'font.size':8,'pdf.fonttype':42,'ps.fonttype':42})
    fig,axes=plt.subplots(1,3,figsize=(7.1,2.7),layout='constrained')
    groups=[('balanced',1),('balanced',4),('zipf08',1),('zipf08',4)]
    for ax,top,title in zip(axes,['leaf-uniform','leaf-bandwidth','multi-cut'],['Uniform\nLeaf–Spine','Bandwidth-heterogeneous\nLeaf–Spine','Multi-cut\n100/200/100G']):
        for index,(m,label,color) in enumerate([('volume-maxq','Volume-MaxQ','#94a3b8'),('resource-maxq','Resource-MaxQ','#0891b2'),('fast-simai','FAST-SimAI','#d97706')]):
            stats=[]
            for family,size in groups:
                vals=[r[m+'_ms'] for r in paired if r['topology']==top and r['family']==family and int(r['mib'])==size and r[m+'_ms'] is not None]
                stats.append(sample_range(vals) if vals else (float('nan'),)*4)
            means=[s[0] for s in stats];errors=[[s[0]-s[1] for s in stats],[s[2]-s[0] for s in stats]]
            ax.bar([x+(index-1)*.23 for x in range(4)],means,width=.23,yerr=errors,capsize=2,color=color,label=label)
        ax.set_xticks(range(4),['B/1','B/4','Z/1','Z/4']);ax.set(title=title,ylim=(0,None),xlabel='Distribution / MiB per rank');ax.grid(axis='y',alpha=.15)
    axes[0].set_ylabel('Complete All-to-Allv time (ms)');axes[0].legend(fontsize=6)
    for ext in ['png','pdf']:fig.savefig(out/('figure1_end_to_end.'+ext),dpi=220)
    plt.close(fig)
    traces=json.loads((mechanism/'control/service.json').read_text());runs=json.loads((mechanism/'control/study.json').read_text())['runs']
    times={r['method']:r['actual_ms'] for r in runs};fig,axes=plt.subplots(2,1,figsize=(7.1,3.0),sharex=True,layout='constrained')
    mechanism_stats={}
    for ax,m in zip(axes,['volume-maxq','resource-maxq']):
        selected=traces[m]
        for j,t in enumerate(selected):
            epoch=t['epoch_ns'];foreground=merged([(a,a+b) for a,b in t['foreground_tx']]);control=merged([(a,a+b) for a,b in t['control_tx']])
            for intervals,color,hatch in [(t['idle_pending_lower_bound'],'#fca5a5','//'),(control,'#64748b',None),(foreground,'#0891b2',None)]:
                ax.broken_barh([((a-epoch)/1000,(b-a)/1000) for a,b in intervals],(2-j-.32,.64),facecolors=color,hatch=hatch,linewidth=.15)
        ax.set_yticks([2,1,0],['Left cut100G','Middle cut200G','Right cut100G']);ax.set_title(f'{m}: complete {times[m]*1000:.3f} us',fontsize=9)
        ax.axvline(times[m]*1000,color='#0f172a',linestyle=':',linewidth=.9)
        left=[(a,a+b) for a,b in selected[0]['foreground_tx']];right=[(a,a+b) for a,b in selected[2]['foreground_tx']]
        mechanism_stats[m]=dict(complete_us=times[m]*1000,outer_cut_foreground_overlap_us=overlap_ns(left,right)/1000,
            pending_idle_us={t['resource']:t['idle_pending_us'] for t in selected})
    axes[-1].set_xlabel('Time since collective start (us)')
    axes[0].legend(handles=[Patch(color='#0891b2',label='Foreground wire service'),Patch(color='#64748b',label='Control wire service'),Patch(facecolor='#fca5a5',hatch='//',label='Idle with pending-demand lower bound >0')],loc='lower right',fontsize=6)
    axes[-1].set_xlim(0,max(times['volume-maxq'],times['resource-maxq'])*1000*1.02)
    for ext in ['png','pdf']:fig.savefig(out/('figure2_mechanism.'+ext),dpi=220)
    plt.close(fig);e.dump(out/'mechanism_summary.json',mechanism_stats)
    print(json.dumps(dict(main=summary,mechanism=mechanism_stats),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('out',type=Path);parser.add_argument('mechanism',type=Path)
    args=parser.parse_args();build(args.out,args.mechanism)
