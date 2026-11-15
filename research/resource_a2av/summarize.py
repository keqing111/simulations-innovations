#!/usr/bin/env python3
"""Read-only replay analysis; writes derived summaries, never launches simulations."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

METHODS=('fast-simai','spreadout','resource-maxq')


def aggregate(cases,rows):
    lookup={(r['case'],r['method']):r for r in rows}
    def valid(row):
        return row.get('success') and row.get('pair_eligible') and (row.get('actual_ms') or 0)>0
    result=dict(conditions=len(cases),complete_triplets=sum(
        all(valid(lookup.get((c['name'],m),{})) for m in METHODS) for c in cases))
    for baseline in METHODS[:2]:
        ratios=[]
        for case in cases:
            a=lookup.get((case['name'],baseline),{});b=lookup.get((case['name'],METHODS[2]),{})
            if valid(a) and valid(b):ratios.append(a['actual_ms']/b['actual_ms'])
        result[baseline]=dict(paired=len(ratios),unpaired=len(cases)-len(ratios),
            geomean_speedup=math.exp(sum(map(math.log,ratios))/len(ratios)) if ratios else None,
            wins=sum(r>1+1e-6 for r in ratios),ties=sum(abs(r-1)<=1e-6 for r in ratios),
            losses=sum(r<1-1e-6 for r in ratios),minimum=min(ratios) if ratios else None,
            maximum=max(ratios) if ratios else None)
    return result


def path_features(case):
    snapshot=json.loads((Path(case['source'])/'planner_topology.json').read_text());n=snapshot['n']
    paths=snapshot['server_paths'];bw=snapshot['bandwidth_gbps'];features={}
    for i,p in enumerate(paths):
        if i//n!=i%n:features[i]=(min(bw[r] for r in p['resource_ids']),p['propagation_us'])
    asymmetric=sum(features[s*n+d]!=features[d*n+s] for s in range(n) for d in range(s+1,n))
    return dict(topology=case['topology'],gpu_count=case['gpu_count'],server_pairs=len(features),
        standalone_bandwidth_gbps=sorted({p[0] for p in features.values()}),
        propagation_us_min=min(p[1] for p in features.values()),
        propagation_us_max=max(p[1] for p in features.values()),
        endpoint_asymmetry_fraction=asymmetric/(n*(n-1)/2),
        shared_bandwidth_gbps=sorted({bw[r] for r in snapshot['shared_resource_ids']}))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('out',type=Path)
    args=parser.parse_args();out=args.out;study=json.loads((out/'study.json').read_text())
    cases,rows=study['cases'],study['runs']
    if not study.get('complete'):raise ValueError('finish the study audit before final summary')
    result=dict(scope='fixed exploratory cohort, one synthetic seed; no confidence interval',
                overall=aggregate(cases,rows),status_counts=dict(Counter(r['status'] for r in rows)))
    result['blocks']={b:aggregate([c for c in cases if c['block']==b],rows) for b in dict.fromkeys(c['block'] for c in cases)}
    primary=[c for c in cases if c['block']=='primary']
    for field in ['topology','family']:
        result['primary_by_'+field]={v:aggregate([c for c in primary if c[field]==v],rows) for v in dict.fromkeys(c[field] for c in primary)}
    unique={}
    for c in cases:unique.setdefault((c['topology'],c['gpu_count']),c)
    result['path_features']=[path_features(c) for c in unique.values()]
    result['timing_ranges']={}
    for m in METHODS:
        subset=[r for r in rows if r['method']==m and r['success']]
        def bounds(field,divisor=1):
            values=[r[field]/divisor for r in subset if r.get(field) is not None]
            return [min(values),max(values)] if values else None
        result['timing_ranges'][m]=dict(communication_ms=bounds('actual_ms'),
            planning_seconds=bounds('planning_cpu_us',1e6),core_seconds=bounds('planner_cpu_us',1e6))
    diagnostics=[]
    for r in rows:
        if not r['success'] or not r.get('attempt_dir'):continue
        directory=Path(r['attempt_dir']);pfc=directory/'pfc.txt'
        epoch=r['input_contract']['epoch'];end=epoch+round(r['actual_ms']*1e6)
        lines=[line.split() for line in pfc.read_text().splitlines() if line.strip()] if pfc.exists() else None
        events=[line for line in lines if epoch<=int(line[0])<=end] if lines is not None else None
        diagnostics.append(dict(case=r['case'],method=r['method'],
            pfc_observed_records_in_foreground=None if events is None else len(events),
            pfc_note='raw hook observations, not unique pause frames or pause duration',
            source_completion_skew_us=r.get('original_source_completion_skew_ns',0)/1000,
            stage_release_skew_mean_us=None if r.get('stage_release_skew_mean_ns') is None else r['stage_release_skew_mean_ns']/1000))
    result['diagnostics']=diagnostics
    (out/'expanded_summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['diagnostics','path_features']},indent=2))


if __name__=='__main__':main()
