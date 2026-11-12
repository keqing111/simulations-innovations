#!/usr/bin/env python3
"""Diagnose a fixed 3DC x 2-server topology without changing the scheduler."""
import argparse
import collections
import hashlib
import json
import math
import random
import statistics
import subprocess
import sys
import unittest
import itertools

import baseline_control as b
import study


def diagnose(raw,n,g,unit,metrics):
    topology=raw.get('topology_snapshot')
    capacities=dict(zip(topology['resource_names'],topology['bandwidth_gbps'])) if topology else {}
    if raw['mode']=='spreadout':
        flows=[s['flows'] for s in raw['rounds']]
    else:
        flows=[[[s*g+u,p[s]*g+u,payload[s*g+u]] for s in range(n) if s!=p[s] for u in range(g)]
               for p,payload in zip(raw['permutations'],raw['payload_units'])]
    work=lambda loads:{name:value*.008/(capacities[name] if topology else (100 if name.startswith('wan:') else 400))
                       for name,value in loads.items()}
    total=metrics['resource_totals'];remaining=total.copy();allwork=work(total)
    bound=max(allwork.values(),default=0)
    critical=[e for e,t in allwork.items() if math.isclose(t,bound,abs_tol=1e-9)]
    absent={e:0.0 for e in critical};trace=[];previous=None;switches=0
    for index,stage in enumerate(flows):
        loads,alpha,serial=b.resource_cost(stage,n,g,unit,topology)
        bottlenecks=sorted(e for e,t in work(loads).items() if math.isclose(t,serial,abs_tol=1e-9))
        if bottlenecks and previous is not None and bottlenecks!=previous:switches+=1
        if bottlenecks:previous=bottlenecks
        before=max(work(remaining).values(),default=0)
        for e in critical:
            if not loads.get(e):absent[e]+=alpha+serial
        for e,amount in loads.items():
            remaining[e]-=amount
            assert remaining[e]>=0
        trace.append(dict(index=index,alpha_us=alpha,serial_us=serial,
                          resource_bytes=loads,bottlenecks=bottlenecks,
                          remaining_bound_before_us=before,
                          remaining_bound_after_us=max(work(remaining).values(),default=0)))
    assert not any(remaining.values())
    gap=metrics['serial_sum_us']-bound
    assert gap>=-1e-8
    return dict(resource_work_bound_us=bound,serialization_gap_us=gap,
                dominant_resources=critical,dominant_absent_stage_us=absent,
                bottleneck_set_changes=switches,
                wan_stages=sum(any(e.startswith('wan:') for e in t['resource_bytes']) for t in trace),
                stages=trace)


def cases():
    n,g=6,4;r=n*g
    for kind in ('balanced','perturbed','zipf'):
        data=study.fixed_load_matrix(n,g,'balanced' if kind=='perturbed' else kind,'pipeline')
        if kind=='perturbed':
            for s in range(r):
                data[s*r+(s+g)%r]+=1
                data[s*r+(s+2*g)%r]-=1
        for placement in ((101,) if kind=='balanced' else (101,102,103)):
            order=list(range(n))
            if placement!=101:random.Random(placement).shuffle(order)
            actual=[data[(order[s//g]*g+s%g)*r+order[d//g]*g+d%g]
                    for s in range(r) for d in range(r)]
            assert all(sum(actual[s*r:s*r+r])==39060 for s in range(r))
            yield kind,placement,actual


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=__import__('pathlib').Path)
    parser.add_argument('--profiles',type=int,nargs='+',choices=(0,1,2),default=[0])
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    names=['ds/decomposition/typical_topology.py','ds/decomposition/baseline_control.py',
           'ds/decomposition/study.py','simulation/algorithm/all2all.cpp',
           'simulation/algorithm/all2all.h','simulation/scheduler/global.cpp',
           'simulation/bvn_decompose_main.cpp','simulation/topology/model.cpp']
    meta=dict(complete=False,n=6,g=4,dc=[0,0,1,1,2,2],generator_seed=101,profiles=args.profiles,
              placement_seeds=[101,102,103],repeats=3,resource_rounds=3,
              source_sha256={s:hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest() for s in names},
              binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
              scope='fixed topology development diagnosis; placements are not independent traffic seeds')
    b.save(args.out/'metadata.json',meta);rows=[]
    for (kind,placement,data),profile in itertools.product(list(cases()),args.profiles):
        prefix=f'{kind}-p{placement}-top{profile}'
        b.save(args.out/(prefix+'.input.json'),data)
        for unit in (16,1024):
            expected=None
            for label,mode,strategy,budget in [('native','pipeline',0,0),('bottleneck','pipeline',1,0),
                                             ('resource','pipeline',1,3),('spreadout','spreadout',0,0)]:
                command=[str(study.BIN),'--mode',mode,'--n','6','--g','4','--strategy',str(strategy),
                         '--resource-rounds',str(budget),'--scaling',str(int(mode=='pipeline')),
                         '--wan-profile',str(profile),
                         '--unit-mb',str(unit/1e6),'--topology','1','--repeat','3']
                raw=json.loads(subprocess.run(command,input=' '.join(map(str,data)),text=True,
                                              capture_output=True,check=True,timeout=90).stdout)
                metrics=b.audit(raw,data,6,4,unit)
                if expected is None:expected=metrics['resource_totals']
                assert metrics['resource_totals']==expected
                diagnostic=diagnose(raw,6,4,unit,metrics)
                name=f'{prefix}-unit{unit}-{label}'
                b.save(args.out/(name+'.json'),dict(raw=raw,diagnostic=diagnostic))
                row=dict(case=f'{prefix}-unit{unit}',kind=kind,placement=placement,unit=unit,label=label,profile=profile,
                         total_us=raw['total_us'],K=raw.get('stage_count',raw.get('active_rounds')),
                         **metrics,**{k:v for k,v in diagnostic.items() if k!='stages'})
                if mode=='pipeline':
                    row.update(cpu_us=statistics.median(raw['cpu_samples_us']),
                               planning_wall_us=statistics.median(raw['planning_wall_samples_us']),
                               factors=sorted({s['alpha']//s['alpha_scaled'] for s in raw['steps']}))
                rows.append(row)
                print(name,'K',row['K'],'us',round(row['total_us'],4),
                      'gap',round(row['serialization_gap_us'],4),flush=True)
    b.save(args.out/'summary.json',rows)
    assert all(hashlib.sha256((study.ROOT/s).read_bytes()).hexdigest()==h for s,h in meta['source_sha256'].items())
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest()==meta['binary_sha256']
    meta.update(complete=True,outputs=len(rows),conditions=len(rows)//4)
    b.save(args.out/'metadata.json',meta)


class DiagnosticsTests(unittest.TestCase):
    def test_witness_gap_and_conservation(self):
        import paper_counterexample as ce
        for plan,want in [(ce.NATIVE,80),(ce.ALTERNATIVE,0)]:
            raw=dict(mode='pipeline',permutations=[s['permutation'] for s in plan],payload_units=[[1]*6]*2)
            totals,_,_=b.resource_cost([[s,d,x] for s in range(6) for d,x in enumerate(ce.DEMAND[s*6:s*6+6]) if x],6,1,1000000)
            result=diagnose(raw,6,1,1000000,dict(resource_totals=totals,serial_sum_us=240+want))
            self.assertEqual(result['resource_work_bound_us'],240)
            self.assertEqual(result['serialization_gap_us'],want)
            self.assertEqual(result['dominant_absent_stage_us'],{'wan:2:1':0})

    def test_all_sources_fixed_budget(self):
        self.assertEqual(len(list(cases())),7)


if __name__=='__main__':
    if '--self-test' in sys.argv:unittest.main(argv=[sys.argv[0]],verbosity=2)
    else:main()
