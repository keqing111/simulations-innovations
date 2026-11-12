#!/usr/bin/env python3
"""Offline single-decision interventions; never an online scheduling policy."""
import argparse
import hashlib
import json
import math
import pathlib
import subprocess

import baseline_control as b
import study


def invoke(data,n,skip=None,resource=3,profile=0):
    command=[str(study.BIN),'--mode','pipeline','--n',str(n),'--g','4','--strategy','1',
             '--topology','1','--scaling','1','--unit-mb','.001024','--repeat','1',
             '--resource-rounds',str(resource)]
    command+=['--wan-profile',str(profile)]
    if skip is not None:command+=['--resource-skip-step',str(skip)]
    raw=json.loads(subprocess.run(command,input=' '.join(map(str,data)),text=True,
                                  capture_output=True,check=True,timeout=60).stdout)
    return raw,b.audit(raw,data,n,4,1024)


def state_before(raw,k,n):
    r=[0]*(n*n);z=[0]*(n*n*4)
    for p,step,payload in zip(raw['permutations'],raw['steps'],raw['payload_units']):
        for s,d in enumerate(p):
            r[s*n+d]+=step['alpha_scaled']
            if s!=d:
                for u in range(4):z[(s*n+d)*4+u]+=payload[s*4+u]
    for p,step,payload in zip(raw['permutations'][:k],raw['steps'][:k],raw['payload_units'][:k]):
        for s,d in enumerate(p):
            r[s*n+d]-=step['alpha_scaled']
            if s!=d:
                for u in range(4):z[(s*n+d)*4+u]-=payload[s*4+u]
    return dict(auxiliary=r,real_pending_units=z)


def current_term(raw,k):
    previous=raw['intrinsic_us'] if k==0 else raw['dispatch_us'][k-1]
    return max(previous,raw['network_stages_us'][k])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=pathlib.Path)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    sources=['simulation/algorithm/all2all.cpp','simulation/algorithm/all2all.h',
             'simulation/bvn_decompose_main.cpp','simulation/scheduler/global.cpp',
             'ds/decomposition/decision_forensics.py','ds/decomposition/baseline_control.py']
    meta=dict(complete=False,binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
              sources={s:hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() for s in sources},
              scope='each intervention changes ONE action then resumes the same resource policy; effects are not additive')
    b.save(args.out/'metadata.json',meta);summary=[]
    for n,kind,profile in [(16,'perturbed',0),(8,'zipf',0),(6,'balanced',2)]:
        name=f'{kind}-n{n}'+(f'-top{profile}' if profile else '')
        data=study.fixed_load_matrix(n,4,kind,'pipeline')
        b.save(args.out/(name+'.input.json'),data)
        base,metrics=invoke(data,n,profile=profile)
        if profile:
            archive=json.loads((study.ROOT/'ds/decomposition/multidc-profiles-20261112'/
                                f'{kind}-p101-top{profile}-unit1024-resource.json').read_text())['raw']
        else:
            archive=json.loads((study.ROOT/'ds/decomposition/resource-certified-20261112'/
                                (f'{kind}-n{n}-unit1024-resource3.json')).read_text())
        for field in ('permutations','payload_units','network_stages_us','total_us','dispatch_us'):
            assert base[field]==archive[field],field
        b.save(args.out/(name+'-baseline.json'),dict(raw=base,metrics=metrics))
        indices=[k for k,step in enumerate(base['steps'])
                 if step['resource_seed']!=base['permutations'][k]]
        for k in indices:
            counter,cm=invoke(data,n,skip=k,profile=profile)
            for field in ('permutations','payload_units','network_stages_us','dispatch_us'):
                assert counter[field][:k]==base[field][:k]
            assert counter['permutations'][k]==base['steps'][k]['resource_seed']
            assert state_before(counter,k,n)==state_before(base,k,n)
            assert counter['steps'][k]['alpha']==base['steps'][k]['alpha']
            immediate=current_term(counter,k)-current_term(base,k)
            delta=counter['total_us']-base['total_us']
            row=dict(case=name,profile=profile,step_zero_based=k,baseline_us=base['total_us'],
                     counterfactual_us=counter['total_us'],counter_minus_base_us=delta,
                     original_immediate_saving_us=immediate,
                     original_suffix_penalty_us=immediate-delta,
                     original_decision_harmful=delta < -1e-8,
                     K_baseline=base['stage_count'],K_counter=counter['stage_count'],
                     network_seed_us=base['steps'][k]['resource_seed_us'],
                     network_chosen_us=base['steps'][k]['resource_chosen_us'],
                     chosen=base['permutations'][k],seed=counter['permutations'][k],
                     q=base['steps'][k]['alpha'])
            summary.append(row)
            b.save(args.out/f'{name}-skip{k}.json',dict(summary=row,raw=counter,metrics=cm,
                                                       same_prefix_state=state_before(base,k,n)))
            print(name,'skip',k,'local save',round(immediate,4),'total counter-base',round(delta,4),
                  'suffix penalty',round(immediate-delta,4),flush=True)
    b.save(args.out/'summary.json',summary)
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest()==meta['binary_sha256']
    assert all(hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest()==h for s,h in meta['sources'].items())
    meta.update(complete=True,interventions=len(summary))
    b.save(args.out/'metadata.json',meta)


if __name__=='__main__':main()
