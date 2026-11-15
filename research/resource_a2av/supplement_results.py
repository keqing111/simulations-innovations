#!/usr/bin/env python3
"""Complete-cohort summaries and four figure groups; never launches simulations."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import numpy as np
import experiment as e
from paper_mechanism import service_trace
from paper_results import overlap_ns,compare_plans
from simai_a2a.artifacts import read_matrix_artifact
from simai_a2a.schedulers.resource_budget import prepare_resources,pressure

LABELS={'fast-simai':'FAST','spreadout':'Spreadout','pairwise':'Pairwise',
        'volume-maxq':'Volume-MaxQ','resource-maxq':'Resource-MaxQ'}
COLORS=['#94a3b8','#eab308','#8b5cf6','#ea580c','#0891b2']


def summarize(study):
    result={};blocks={c['name']:c['block'] for c in study['cases']}
    for block in ['all']+list(dict.fromkeys(blocks.values())):
        rows=[dict(r,group='all') for r in study['runs'] if block=='all' or blocks[r['case']]==block]
        result[block]=e.audit.paired_summary(rows,study['methods'])['all']
    result['status_counts']=dict(Counter(r['status'] for r in study['runs']))
    return result


def lookup(study):
    return {(r['case'],r['method']):r for r in study['runs']}


def complete_range(samples):
    if not samples or any(v is None for v in samples):return float('nan'),0,0
    mean=float(np.mean(samples));return mean,mean-min(samples),max(samples)-mean


def workload_metrics(matrix,hardware):
    n,g=hardware.num_servers,hardware.gpus_per_server
    prepared=e.iterations.prepare(matrix,hardware)
    intra=sum(int(matrix.values[a,b]) for a in range(n*g) for b in range(a//g*g,(a//g+1)*g))
    sends=matrix.values.sum(axis=1);receives=matrix.values.sum(axis=0)
    cv=lambda x:float(np.std(x)/np.mean(x)) if np.mean(x)>0 else 0.
    return dict(logical_bytes=matrix.logical_bytes,intrinsic_bytes=intra,
        inter_server_bytes=matrix.logical_bytes-intra,balance_bytes=sum(v[2] for v in prepared['raw']),
        sender_cv=cv(sends),receiver_cv=cv(receives),
        server_sender_cv=cv(sends.reshape(n,g).sum(axis=1)),server_receiver_cv=cv(receives.reshape(n,g).sum(axis=1)))


def value(table,case,method,key='actual_ms'):
    row=table.get((case['name'],method),{})
    return row.get(key) if row.get('success') and row.get('pair_eligible') else None


def save_figure(fig,out,name):
    fig.savefig(out/(name+'.png'),dpi=180,bbox_inches='tight')
    fig.savefig(out/(name+'.pdf'),bbox_inches='tight')
    import matplotlib.pyplot as plt
    plt.close(fig)


def real_figure(out,study,table):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,7),layout='constrained')
    for row,top in enumerate(['leaf-bandwidth','multi-cut-mixed']):
        for col,direction in enumerate(['dispatch','combine']):
            ax=axes[row,col]
            for k,method in enumerate(study['methods']):
                means=[];lo=[];hi=[]
                for model in ['deepseek','kimi','llama']:
                    subset=[c for c in study['cases'] if c['block']=='real' and c['topology']==top and c['direction']==direction and c['model']==model]
                    mean,lower,upper=complete_range([value(table,c,method) for c in subset])
                    means.append(mean);lo.append(lower);hi.append(upper)
                ax.bar(np.arange(3)+(k-2)*.16,means,.16,label=LABELS[method],color=COLORS[k],yerr=[lo,hi],capsize=2)
            ax.set(title=top+' / '+direction,ylabel='Complete A2Av time (ms)',xticks=np.arange(3),xticklabels=['DeepSeek','Kimi','Llama'],ylim=(0,None))
            ax.grid(axis='y',alpha=.15)
    axes[0,0].legend(fontsize=8,ncol=2)
    fig.suptitle('Real expert choices: mean and range of two disjoint windows (not a CI)')
    save_figure(fig,out,'figure1_real_moe')


def synthetic_figure(out,study,table):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,3,figsize=(13,7),layout='constrained')
    for row,top in enumerate(['leaf-route-asymmetric','multi-cut-mixed']):
        for col,family in enumerate(['balanced','zipf08','zipf12']):
            ax=axes[row,col];subset=sorted([c for c in study['cases'] if c['block']=='synthetic' and c['gpu_count']==64 and c['topology']==top and c['family']==family],key=lambda c:c['mib'])
            for k,method in enumerate(study['methods']):
                ax.plot([c['mib'] for c in subset],[value(table,c,method) if value(table,c,method) is not None else float('nan') for c in subset],marker='o',label=LABELS[method],color=COLORS[k])
            ax.set(title=top+' / '+family,xlabel='Requested MiB per rank',ylabel='Complete A2Av time (ms)',xticks=[1,4,16],ylim=(0,None));ax.grid(alpha=.15)
    axes[0,0].legend(fontsize=7)
    save_figure(fig,out,'figure2_topology_load')


def mechanism_evidence(out,study):
    cases=[c for c in study['cases'] if c['block']=='mechanism'];table=lookup(study);details=[]
    for c in cases:
        record=dict(case=c['name'],quantum_bytes=c['quantum_bytes'],label_shift=c['label_shift'],methods={})
        for method in ['volume-maxq','resource-maxq']:
            row=table[c['name'],method]
            if not row.get('pair_eligible'):continue
            plan_path=Path(row['attempt_dir']).parents[2]/(method+'.plan.json')
            plan=json.loads(plan_path.read_text());traces=service_trace(row,Path(c['source']))
            first,last=traces[0],traces[-1]
            left=[(a,a+d) for a,d in first['foreground_tx']];right=[(a,a+d) for a,d in last['foreground_tx']]
            record['methods'][method]=dict(coefficients=plan['coefficients'],permutations=plan['permutations'],
                outer_forward_overlap_us=overlap_ns(left,right)/1000,
                left_pending_idle_us=first['idle_pending_us'],right_pending_idle_us=last['idle_pending_us'])
            # Retain full actual-wire intervals, not only summary numbers.
            e.dump(out/(c['name']+'-'+method+'-service.json'),traces)
        a,b=(record['methods'].get(m) for m in ['volume-maxq','resource-maxq'])
        if a and b:
            record.update(same_coefficients=a['coefficients']==b['coefficients'],
                expected_two_round_q=a['coefficients']==b['coefficients']==[c['quantum_bytes']]*2,
                matching_changed=a['permutations']!=b['permutations'])
        details.append(record)
    e.dump(out/'mechanism_evidence.json',details);return details


def mechanism_figure(out,study,table,details):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained');cases=[c for c in study['cases'] if c['block']=='mechanism'];x=np.arange(len(cases))
    for k,method in enumerate(study['methods']):
        axes[0].bar(x+(k-2)*.16,[value(table,c,method) if value(table,c,method) is not None else float('nan') for c in cases],.16,label=LABELS[method],color=COLORS[k])
    for k,method in enumerate(['volume-maxq','resource-maxq']):
        axes[1].bar(x+(k-.5)*.36,[d['methods'].get(method,{}).get('outer_forward_overlap_us',float('nan')) for d in details],.36,label=LABELS[method],color=COLORS[k+3])
    axes[0].set(ylabel='Complete A2Av time (ms)',title='Same controlled support; size and labels vary',ylim=(0,None));axes[0].legend(fontsize=7)
    axes[1].set(ylabel='Outer-cut foreground service overlap (us)',title='Actual ns-3 Tx interval unions',ylim=(0,None));axes[1].legend(fontsize=8)
    labels=[f"{c['quantum_bytes']//1024}KiB / L{c['label_shift']}" for c in cases]
    for ax in axes:ax.set_xticks(x,labels,rotation=35,ha='right');ax.grid(axis='y',alpha=.15)
    save_figure(fig,out,'figure3_mechanism_family')


def overhead_figure(out,study,table):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    cases=[c for c in study['cases'] if c['gpu_count']==128];x=np.arange(len(cases))
    for k,method in enumerate(study['methods']):
        axes[0].bar(x+(k-2)*.16,[value(table,c,method) if value(table,c,method) is not None else float('nan') for c in cases],.16,color=COLORS[k],label=LABELS[method])
        for j,p in enumerate([64,128]):
            vals=[value(table,c,method,'planning_cpu_us')/1000 for c in study['cases'] if c['block']!='mechanism' and c['gpu_count']==p and value(table,c,method,'planning_cpu_us') is not None]
            if vals:axes[1].boxplot([vals],positions=[k+(j-.5)*.28],widths=.23,patch_artist=True,boxprops=dict(facecolor=COLORS[k],alpha=.8 if j else .35),manage_ticks=False)
    names={'leaf-route-asymmetric':'Asym. Leaf','multi-cut-mixed':'Multi-cut'}
    axes[0].set(ylabel='Complete A2Av time (ms)',title='128 GPU / 32 servers / 4 MiB per rank',ylim=(0,None));axes[0].set_xticks(x,[names[c['topology']]+'\n'+c['family'] for c in cases],fontsize=8)
    axes[1].set(yscale='log',ylabel='Method planning CPU time (ms)',title='64 GPU (light) / 128 GPU (dark); excludes common preparation');axes[1].set_xticks(range(5),[LABELS[m] for m in study['methods']],rotation=25,ha='right')
    axes[0].legend(fontsize=7);axes[1].grid(axis='y',alpha=.15)
    save_figure(fig,out,'figure4_scale_planning')


def build(out):
    study=json.loads((out/'study.json').read_text())
    if len(study['runs'])!=study['planned_records']:raise ValueError('cohort incomplete; do not publish partial aggregate')
    for r in study['runs']:
        r.pop('input_contract',None);r.pop('contract_error',None);e.audit.attach_contract(r)
    for c in study['cases']:e.audit.mark_pair_eligibility([r for r in study['runs'] if r['case']==c['name']])
    summary=summarize(study);e.dump(out/'supplement_summary.json',summary)
    failed=[dict(case=r['case'],method=r['method'],status=r['status'],error=r.get('error')) for r in study['runs'] if not r.get('pair_eligible')]
    required={c['name']:c['logical_bytes'] for c in study['cases']}
    bad_delivery=[r['case']+'/'+r['method'] for r in study['runs'] if r.get('success') and r.get('delivered_bytes')!=required[r['case']]]
    # Independently reconstruct every source/final-destination byte count from
    # retained actual delivery events, not only the planner's aggregate field.
    for r in study['runs']:
        if not r.get('success'):continue
        attempt=Path(r['attempt_dir']);expected=np.load(attempt/'matrix.npy');actual=np.zeros_like(expected)
        with (attempt/'original_source_delivery.csv').open() as stream:
            for event in csv.DictReader(stream):actual[int(event['original_source']),int(event['final_destination'])]+=int(event['bytes'])
        if not np.array_equal(actual,expected):bad_delivery.append(r['case']+'/'+r['method'])
    changed=[p for p,h in study['source_hashes'].items() if e.audit.digest(Path(p))!=h]
    verification=dict(total=len(study['runs']),success=sum(r['success'] for r in study['runs']),
        verified_contracts=sum(bool(r.get('input_contract')) for r in study['runs']),
        eligible=sum(bool(r.get('pair_eligible')) for r in study['runs']),failed=failed,
        exact_pair_delivery_checked=sum(r['success'] for r in study['runs']),
        byte_delivery_errors=bad_delivery,changed_registered_inputs=changed)
    e.dump(out/'supplement_verification.json',verification)
    if bad_delivery or changed or verification['verified_contracts']!=verification['success']:
        raise ValueError('delivery, execution contract or frozen-source verification failed')
    table=lookup(study);details=mechanism_evidence(out,study)
    paired_cases=[c for c in study['cases'] if all(table[c['name'],m].get('pair_eligible') for m in ['volume-maxq','resource-maxq'])]
    comparison,states=compare_plans({**study,'cases':paired_cases})
    e.audit.save_csv(out/'plan_comparison.csv',comparison);e.dump(out/'first_differences.json',states)
    verification.update(plan_pairs_checked=len(comparison),changed_plan_cases=sum(not c['same_plan'] for c in comparison),
        same_coefficient_sequence_cases=sum(c['same_coefficients'] for c in comparison),
        same_q_star_first_divergences=len(states))
    e.dump(out/'supplement_verification.json',verification)
    workloads=[];metrics=[]
    for c in study['cases']:
        matrix,_=read_matrix_artifact(Path(c['source'])/'matrix');_,hw=e.fabric(c['topology'],c['gpu_count'])
        snap=json.loads((Path(c['source'])/'planner_topology.json').read_text())
        lower=max(pressure(e.iterations.prepare(matrix,hw)['U'],prepare_resources(snap)),default=0.)
        workloads.append(dict(case=c['name'],block=c['block'],direction=c['direction'],gpu_count=c['gpu_count'],
            topology=c['topology'],model=c['model'],window=c['window'],layer=c['layer'],
            capacity_lower_bound_us=lower,**workload_metrics(matrix,hw)))
        for method in study['methods']:
            r=table[c['name'],method];metric={key:r.get(key) for key in ['case','method','status','pair_eligible','actual_ms','stages','planning_cpu_us','planner_cpu_us','original_source_completion_skew_ns','stage_release_skew_mean_ns','source_q100_max_ns']}
            metric.update(block=c['block'],topology=c['topology'],gpu_count=c['gpu_count'],direction=c['direction'],capacity_lower_bound_us=lower)
            if r.get('success') and r.get('input_contract'):
                pfc=Path(r['attempt_dir'])/'pfc.txt';epoch=r['input_contract']['epoch'];end=epoch+round(r['actual_ms']*1e6)
                metric['pfc_observed_records']=sum(epoch<=int(line.split()[0])<=end for line in pfc.read_text().splitlines() if line.strip()) if pfc.exists() else None
            metrics.append(metric)
    e.audit.save_csv(out/'workload_metrics.csv',workloads);e.audit.save_csv(out/'method_metrics.csv',metrics)
    import matplotlib
    matplotlib.use('Agg')
    real_figure(out,study,table);synthetic_figure(out,study,table);mechanism_figure(out,study,table,details);overhead_figure(out,study,table)
    lines=['# SimAI补充实验结果（完整集合）','','## Material Passport','',
        '- Type: simulation experiment result; fixed-routing All-to-Allv.',
        '- Scope: 52 registered conditions × 5 methods; exploratory extension, no policy tuning.',
        f"- Status: {verification['success']}/{verification['total']} successful; {verification['eligible']} common-contract records.",
        '- Timing: complete operator return (ACK + local dispatch); not application/inference latency.',
        '- Provenance: study.json, source_hashes.json, source_snapshot/, per-attempt native event/Tx traces.','',
        '加速比=对照完成时间/Resource-MaxQ完成时间；>1表示Resource-MaxQ更快。胜/平/负阈值为1e-6。窗口范围不是置信区间；单种子合成结果不用于推断总体显著性。','',
        '| 实验组 | 对照 | 配对/未配对 | 几何均值加速比 | 胜/平/负 | 最差–最好 |',
        '|---|---|---:|---:|---:|---:|']
    for block in ['real','synthetic','mechanism']:
        for method,s in summary[block].items():
            ratio=f"{s['geomean_speedup']:.4f}" if s['geomean_speedup'] is not None else 'NA'
            span=f"{s['worst_speedup']:.4f}–{s['best_speedup']:.4f}" if s['paired'] else 'NA'
            lines.append(f"| {block} | {LABELS[method]} | {s['paired']}/{s['unpaired']} | {ratio}× | {s['wins']}/{s['ties']}/{s['losses']} | {span} |")
    lines+=['','## 四组图','','![真实MoE](figure1_real_moe.png)','','![拓扑与负载](figure2_topology_load.png)','','![机制族](figure3_mechanism_family.png)','','![规模与规划](figure4_scale_planning.png)','',
        '## 边界与复核','',
        '- 真实trace的专家选择实测；源GPU轮转、专家放置及8192字节向量是建模假设。combine为相同dispatch需求的转置。',
        '- Pairwise为rank XOR配对；Spreadout为循环目标序列。二者复用同一rank级异步执行协议。',
        '- 机制族保持机架内重标号和相同支撑结构；是否同q、同轮数和改变匹配见mechanism_evidence.json，不能仅凭速度推断机制。',
        '- plan_comparison.csv和first_differences.json检查Volume/Resource的首次分叉是否来自相同残余、相同q*下的合法匹配；之后残余改变可能使后续q与轮数改变，不能将所有端到端对比说成整条配额序列固定。',
        '- 待发空闲使用有剩余payload下界且无任何物理Tx的区间；重传使其偏保守，不把全部空闲都称为可消除。',
        '- workload_metrics.csv记录真实矩阵的收发倾斜、机内原生通信与balance字节；method_metrics.csv含rank进度错位、PFC原始hook记录数、规划时间和完整通信时间。PFC记录数不是唯一暂停帧数或暂停时长。',
        '- 所有负收益、失败、超时保留；未通过配对检查的记录不参与速度计算。不存在失败后自动重跑。',
        '- 规划时间是当前Python实现的CPU时间，不能称为已经具备在线就绪性能。正文选图应标明适用范围，不以挑选成功样本代替该范围的完整统计。','',
        f"状态计数：`{summary['status_counts']}`。字节守恒错误：{len(bad_delivery)}；冻结输入变化：{len(changed)}。"]
    if failed:lines+=['','未配对记录：']+[f"- {r['case']} / {r['method']}: {r['status']}" for r in failed]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    e.dump(out/'post_analysis_study.json',study)
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,type=Path)
    build(parser.parse_args().out.resolve())
