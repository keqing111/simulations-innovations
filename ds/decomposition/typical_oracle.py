#!/usr/bin/env python3
"""Offline n=6 stage oracle ONLY. Never linked into an online scheduler."""
import argparse
import hashlib
import itertools
import json
import math
import pathlib

import baseline_control as b


def inspect(raw,unit):
    n,g=6,4
    auxiliary=[0]*36;pending=[0]*(36*g)
    for p,step,payload in zip(raw['permutations'],raw['steps'],raw['payload_units']):
        for s,d in enumerate(p):
            auxiliary[s*n+d]+=step['alpha_scaled']
            if s!=d:
                for u in range(g):pending[(s*n+d)*g+u]+=payload[s*g+u]
    records=[]
    for index,(chosen,step,payload) in enumerate(zip(raw['permutations'],raw['steps'],raw['payload_units'])):
        q,capacity=step['alpha_scaled'],step['alpha']
        assert min(auxiliary[s*n+d] for s,d in enumerate(chosen))==q
        def cost(p):
            flows=[[s*g+u,d*g+u,min(capacity,pending[(s*n+d)*g+u])]
                   for s,d in enumerate(p) if s!=d for u in range(g)]
            _,a,t=b.resource_cost(flows,n,g,unit,raw.get('topology_snapshot'))
            return a+t
        current=cost(chosen);best=math.inf;best_match=None;count=0
        for p in itertools.permutations(range(n)):
            if any(auxiliary[s*n+d]<q for s,d in enumerate(p)):continue
            count+=1;value=cost(p)
            if value<best:best,best_match=value,list(p)
        assert count>0 and best<=current+1e-8
        assert math.isclose(current,raw['network_stages_us'][index],abs_tol=1e-8,rel_tol=1e-12)
        records.append(dict(index=index,q=q,capacity=capacity,feasible_matchings=count,
                            actual_us=current,stage_optimum_us=best,
                            immediate_gap_us=max(0,current-best),best_match=best_match))
        for s,d in enumerate(chosen):
            auxiliary[s*n+d]-=q
            if s==d:continue
            for u in range(g):
                k=(s*n+d)*g+u;amount=min(capacity,pending[k]);pending[k]-=amount
                assert amount==payload[s*g+u]
    assert not any(auxiliary) and not any(pending)
    return records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=pathlib.Path)
    parser.add_argument('--out',required=True,type=pathlib.Path)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    meta=json.loads((args.input/'metadata.json').read_text());assert meta['n']==6 and meta['g']==4 and meta['complete']
    rows=[];hashes={}
    for row in json.loads((args.input/'summary.json').read_text()):
        if row['label'] not in ('bottleneck','resource'):continue
        name=row['case']+'-'+row['label'];path=args.input/(name+'.json')
        hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        records=inspect(json.loads(path.read_text())['raw'],row['unit'])
        b.save(args.out/(name+'.json'),records)
        result=dict(case=row['case'],label=row['label'],K=len(records),
                    unique_stages=sum(r['feasible_matchings']==1 for r in records),
                    improvable_stages=sum(r['immediate_gap_us']>1e-8 for r in records),
                    immediate_gaps_sum_us=sum(r['immediate_gap_us'] for r in records),
                    max_matching_count=max(r['feasible_matchings'] for r in records))
        rows.append(result);print(result,flush=True)
    b.save(args.out/'summary.json',rows)
    b.save(args.out/'metadata.json',dict(complete=True,input_metadata=meta,input_sha256=hashes,
                                        oracle_source_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
                                        scope='exact current-stage oracle at observed prefixes, NOT a whole-plan optimum'))


if __name__=='__main__':main()
