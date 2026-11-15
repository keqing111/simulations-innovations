"""Rebuild descriptive tables/figures from all retained iteration studies.

No scheduling, simulation, source edits, failed-run retries or best-run selection.
The primary variant is explicitly resource-maxq; adaptive/eager stay separate.
"""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
OLD=Path('/home/julius/fast/SimAI/astra-sim-alibabacloud/experiments/alltoall/results/v2-leafspine-20261115')
sys.path.insert(0,str(ROOT/'src'))
from simai_a2a.schedulers.resource_budget import _components
from simai_a2a.schedulers.simai_plan import prepare
from simai_a2a.scenario import load_hardware
from simai_a2a.model import TrafficMatrix


def load(path):return json.loads(path.read_text())
def dump(path,data):path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
def save_csv(name,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(rows)


all_rows=[];studies={};latest={};validation=[]
for path in sorted((ROOT/'results').glob('resource-budget-*/study.json'),key=lambda p:p.stat().st_mtime_ns):
    study=load(path);studies[path.parent.name]=study
    for run in study['runs']:
        row=dict(campaign=path.parent.name,**run)
        if run.get('success') and run.get('attempt_dir'):
            attempt=Path(run['attempt_dir']);summary=load(attempt/'summary.json')
            assert abs(run['actual_ms']-summary['actual_makespan_ns']/1e6)<1e-10
            assert summary['native_runtime']['dependency_violations']==0
            assert run.get('background_horizon_valid',True)
            expected=int(np.load(path.parent/run['case']/'matrix/matrix.npy').sum())
            assert run['delivered_bytes']==expected
            row['pfc_count']=summary['pfc_count']
            row['foreground_epoch_ns']=summary['collective_epoch_ns']
            if study.get('source_scenario'):
                source_meta=load(Path(study['source_scenario'])/'scenario_manifest.json')['topology']
                row['source_epoch_matches']=row['foreground_epoch_ns']==source_meta['foreground_start_ns']
            ports=[r for r in summary.get('native_wan',{}).get('ports',[]) if r['eligible_payload_bytes']]
            row['active_wan_wire_busy_mean']=float(np.mean([p['busy_fraction'] for p in ports])) if ports else None
            row['dependency_idle_sum_us']=sum(p['dependency_only_idle_ns'] for p in ports)/1000
            row['transport_idle_sum_us']=sum(p['transport_or_upstream_idle_ns'] for p in ports)/1000
            row['peak_wan_queue_bytes']=max((p['peak_queue_bytes'] for p in ports),default=0)
            plan_file=path.parent/run['case']/(run['method']+'.plan.json')
            plan=load(plan_file)
            row['core_cpu_us']=plan.get('planner_cpu_us')
            latest[run['case'],run['method']]=row
        all_rows.append(row)
save_csv('all_runs.csv',all_rows)

old=load(OLD/'study.json');old_by={(r['case'],r['method']):r for r in old['runs']}
cases=[
 ('dc2-balanced-s1101-m4-rho0','2 DC / balanced'),
 ('dc2-zipf12-s1101-m4-rho0','2 DC / Zipf1.2'),
 ('dc2-deepseek-w0-l3-b64-rho0','2 DC / DeepSeek'),
 ('dc2-llama-w0-l25-b64-rho0','2 DC / Llama'),
 ('dc3-kimi-w0-l31-b64-rho0','3 DC / Kimi'),
 ('dc4-balanced-s1101-m4-rho0','4 DC / balanced'),
 ('dc4-llama-w0-l25-b64-rho0','4 DC / Llama'),
 ('dc4-balanced-s1101-m4-rho0.3','4 DC / balanced + BG0.3')]
primary=[];freedom_rows=[];forks=[]
for case,label in cases:
    ours=latest.get((case,'resource-maxq'));volume=latest.get((case,'volume-maxq'))
    if ours is None or volume is None:continue
    a=Path(ours['attempt_dir']);v=Path(volume['attempt_dir']);source=OLD/case
    # Exact common inputs and transport semantics, not just same case names.
    assert np.array_equal(np.load(a/'matrix.npy'),np.load(source/'matrix/matrix.npy'))
    for filename in ('topology.txt','routes.csv'):
        assert (a/filename).read_bytes()==(source/filename).read_bytes()
    assert (a/'background.csv').read_bytes()==(source/'background.csv').read_bytes()
    assert load(a/'hardware.json')==load(source/'hardware.json')
    manifests=[load(a/'run_manifest.json'),load(v/'run_manifest.json')]
    for method in ('fast-simai','spreadout'):
        base=old_by[case,method];assert base['success']
        manifests.append(load(Path(base['attempt_dir'])/'run_manifest.json'))
    assert len({m['runtime_semantics_version'] for m in manifests})==1
    assert all(m['backend_shared_library_sha256']==manifests[0]['backend_shared_library_sha256'] for m in manifests)
    def normalized_config(path):
        return [line for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')
                and not any(x in line for x in ('_FILE ','TRACE_OUTPUT_FILE '))]
    assert all(normalized_config(Path(m['attempt_dir'])/'ns3.conf')==normalized_config(a/'ns3.conf') for m in manifests)
    assert len({load(Path(m['attempt_dir'])/'summary.json')['collective_epoch_ns'] for m in manifests})==1
    fast=old_by[case,'fast-simai']['communication_ns']/1e6
    spread=old_by[case,'spreadout']['communication_ns']/1e6
    row=dict(case=case,label=label,fast_ms=fast,spreadout_ms=spread,
        volume_maxq_ms=volume['actual_ms'],resource_maxq_ms=ours['actual_ms'],
        speedup_vs_fast=fast/ours['actual_ms'],speedup_vs_spreadout=spread/ours['actual_ms'],
        speedup_vs_volume=volume['actual_ms']/ours['actual_ms'],
        volume_stages=volume['stages'],resource_stages=ours['stages'],
        core_cpu_ms=ours['core_cpu_us']/1000,reference_stages=ours.get('reference_stages'),
        campaign=ours['campaign'],volume_campaign=volume['campaign'])
    hw,_,_=load_hardware(a/'hardware.json');prepared=prepare(TrafficMatrix(np.load(a/'matrix.npy')),hw)
    plans={method:load(Path(record['attempt_dir']).parents[2]/(method+'.plan.json'))
           for method,record in [('volume-maxq',volume),('resource-maxq',ours)]}
    for method,plan in plans.items():
        R=[r[:] for r in prepared['R']];U=prepared['U'][:];n,g=prepared['n'],prepared['g']
        for k,step in enumerate(plan['trace']):
            p,q=step['p'],step['q'];component,_=_components(R,q,p,{})
            inverse={d:s for s,d in enumerate(p)};degrees=[];allowed=0
            for s in range(n):
                choices=0
                for d in range(n):
                    if R[s][d]>=q and component[s]==component[inverse[d]]:
                        allowed+=1
                        if any(U[(s*n+d)*g:(s*n+d+1)*g]):choices+=1
                if any(U[(s*n)*g:(s*n+n)*g]):degrees.append(choices)
            freedom_rows.append(dict(case=case,method=method,stage=k,real_bytes=step['bytes'],
                allowed_support_edges=allowed,unique_perfect_matching=allowed==n,
                mean_real_targets_per_active_sender=float(np.mean(degrees)) if degrees else 0.,
                max_real_targets_per_sender=max(degrees,default=0)))
            for s,d in enumerate(p):
                R[s][d]-=q
                for u in range(g):U[(s*n+d)*g+u]=max(0,U[(s*n+d)*g+u]-q)
        assert not any(U) and not any(v for r in R for v in r)
        selected=[r for r in freedom_rows if r['case']==case and r['method']==method and r['real_bytes']]
        row[method+'_weighted_real_targets']=sum(r['real_bytes']*r['mean_real_targets_per_active_sender'] for r in selected)/sum(r['real_bytes'] for r in selected)
        row[method+'_real_stages']=len(selected)
    pa,pb=plans['volume-maxq'],plans['resource-maxq']
    forks.append(dict(case=case,first_matching_difference=next((i for i,(x,y) in enumerate(zip(pa['permutations'],pb['permutations'])) if x!=y),None),
                      first_coefficient_difference=next((i for i,(x,y) in enumerate(zip(pa['coefficients'],pb['coefficients'])) if x!=y),None),
                      equal_coefficient_sum=sum(pa['coefficients'])==sum(pb['coefficients'])))
    primary.append(row);validation.append(dict(case=case,exact_common_input=True,common_runtime_and_config=True))
save_csv('primary.csv',primary)
save_csv('matching_freedom.csv',freedom_rows);dump(OUT/'first_plan_differences.json',forks)

# Explain a same-q, same-stage-count mechanism rather than attributing all
# FAST speedups to topology. These are PLANNED logical stages, not barriers.
case='dc4-balanced-s1101-m4-rho0'
ours=latest[case,'resource-maxq'];volume=latest[case,'volume-maxq']
directory=Path(ours['attempt_dir']).parents[2]
top=load(directory/'planner_topology.json');wan_ids=[i for i,n in enumerate(top['resource_names']) if n.startswith('wan:')]
names=[top['resource_names'][i] for i in wan_ids];mechanism={};stage_rows=[]
for method,row in [('volume-maxq',volume),('resource-maxq',ours)]:
    directory=Path(row['attempt_dir']).parents[2];plan=load(directory/(method+'.plan.json'))
    work=np.array([[step['resource_bytes'][i] for step in plan['trace']] for i in wan_ids],dtype=np.int64)
    counts=(work>0).sum(axis=0).tolist()
    mechanism[method]=dict(coefficients=plan['coefficients'],matrix_mib=(work/1048576).tolist(),
        active_wans_by_stage=counts,mean_active_wans=float(np.mean(counts)),
        peak_server_pairs_per_wan=[int(round(max(work[:,k])/(plan['coefficients'][k]*4))) for k in range(len(counts))],
        initial_lan_only_stages=next((i for i,n in enumerate(counts) if n),len(counts)),
        sum_stage_proxy_us=sum(s['time_us'] for s in plan['trace']),
        dependency_idle_sum_us=row['dependency_idle_sum_us'],transport_idle_sum_us=row['transport_idle_sum_us'],
        peak_wan_queue_bytes=row['peak_wan_queue_bytes'],pfc_count=row['pfc_count'],
        source_completion_skew_us=row['original_source_completion_skew_ns']/1000,
        mean_stage_release_skew_us=row['stage_release_skew_mean_ns']/1000)
    for k,step in enumerate(plan['trace']):
        stage_rows.append(dict(method=method,stage=k,q_bytes=step['q'],real_bytes=step['bytes'],
            active_wans=counts[k],time_proxy_us=step['time_us'],phi_after_us=step['phi_after_us']))
assert mechanism['volume-maxq']['coefficients']==mechanism['resource-maxq']['coefficients']
assert load(Path(volume['attempt_dir'])/'schedule_manifest.json')['metadata']['local_times_ns']==load(Path(ours['attempt_dir'])/'schedule_manifest.json')['metadata']['local_times_ns']
save_csv('dc4_stages.csv',stage_rows);dump(OUT/'dc4_mechanism.json',mechanism)

# Audit foreground WAN bytes using recorded route-based eligible totals. Transit
# hops are counted separately, not called application byte amplification.
for case,label in cases:
    row=latest.get((case,'resource-maxq'))
    if not row:continue
    a=Path(row['attempt_dir']);summary=load(a/'summary.json');actual=Counter()
    with (a/'native_wan.csv').open() as f:
        for p in csv.DictReader(f):
            if int(p['kind'])==0:actual[int(p['node_id']),int(p['port_id'])]+=int(p['payload_bytes'])
    expected={(p['node_id'],p['port_id']):p['eligible_payload_bytes'] for p in summary['native_wan']['ports']}
    assert all(actual[k]>=v for k,v in expected.items())
    validation.append(dict(case=case,wan_expected_payload_bytes=sum(expected.values()),
                           observed_payload_bytes=sum(actual.values()),delivery_and_dependencies_valid=True))

by_case=defaultdict(list)
for r in old['runs']:by_case[r['case']].append(r)
verification=dict(unique_recorded_runs=len(all_rows),status_counts=dict(Counter(r['status'] for r in all_rows)),
    studies=len(studies),all_studies_registered=all(s.get('complete') for s in studies.values()),
    paired_primary_conditions=len(primary),validation=validation,
    old_campaign=dict(complete=old['complete'],recorded=len(old['runs']),planned=old['planned_runs'],
        statuses=dict(Counter(r['status'] for r in old['runs'])),
        complete_six_method_conditions=sum(len(rs)==6 and all(r['success'] for r in rs) for rs in by_case.values()),
        restart_policy='killed by system restart; no restart or failed-run retry'),
    excluded_metrics=['legacy scalar-capacity utilization for heterogeneous50/100/200Gbps WANs'],
    historical_successes_with_unmatched_source_epoch=sum(r.get('source_epoch_matches') is False for r in all_rows),
    scope='descriptive exploratory simulations, no population inference or significance testing')
dump(OUT/'verification.json',verification)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig,axes=plt.subplots(2,4,figsize=(16,7),layout='constrained')
for ax,row in zip(axes.flat,primary):
    values=[row[k] for k in ('fast_ms','spreadout_ms','volume_maxq_ms','resource_maxq_ms')]
    bars=ax.bar(['FAST','Spreadout','MaxQ only','Resource\nMaxQ'],values,color=['#94a3b8','#64748b','#f59e0b','#0891b2'])
    ax.bar_label(bars,fmt='%.2f',fontsize=8,padding=3)
    ax.set(title=row['label'],ylabel='Full operation time (ms)',ylim=(0,max(values)*1.22))
    ax.tick_params(axis='x',labelsize=8)
for ax in list(axes.flat)[len(primary):]:ax.axis('off')
fig.savefig(OUT/'primary_fct.png',dpi=170);plt.close(fig)

fig,axes=plt.subplots(2,1,figsize=(12,7),layout='constrained',sharex=True)
maximum=max(max(row) for m in mechanism.values() for row in m['matrix_mib'])
for ax,method in zip(axes,['volume-maxq','resource-maxq']):
    m=mechanism[method]
    im=ax.imshow(m['matrix_mib'],aspect='auto',vmin=0,vmax=maximum,cmap='YlOrRd')
    ax.set_yticks(range(len(names)),[n.replace('wan:','') for n in names],fontsize=8)
    ax.set(title=f'{method}: same 31 stages and coefficients; mean active WANs={m["mean_active_wans"]:.2f}',ylabel='Directed WAN')
axes[-1].set(xlabel='Logical stage index (not a global barrier)')
fig.colorbar(im,ax=list(axes),label='Foreground payload / stage (MiB)',shrink=.8)
fig.savefig(OUT/'dc4_resource_heatmap.png',dpi=170);plt.close(fig)

fig,ax=plt.subplots(figsize=(10,4),layout='constrained')
for method,row in [('volume-maxq',volume),('resource-maxq',ours)]:
    bins=defaultdict(float)
    with (Path(row['attempt_dir'])/'wan_bins_100us.csv').open() as f:
        for r in csv.DictReader(f):bins[int(r['relative_start_ns'])]+=float(r['foreground_gbps'])/100
    xs=sorted(bins);ax.step([x/1e6 for x in xs],[bins[x] for x in xs],where='post',label=method)
ax.set(xlabel='Actual time since operation epoch (ms)',ylabel='Equivalent fully utilized 100 Gbps WANs',ylim=(0,12.5),title='4 DC balanced: packet-derived foreground service, 100 us bins')
ax.legend();fig.savefig(OUT/'dc4_actual_wan_service.png',dpi=170);plt.close(fig)
print(json.dumps(dict(runs=len(all_rows),statuses=verification['status_counts'],primary=primary,old=verification['old_campaign']),indent=2,ensure_ascii=False))
