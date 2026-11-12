#!/usr/bin/env python3
"""Fixed-budget resource-assignment development panel; no best-of-budget selection."""
import argparse
import hashlib
import json
import pathlib
import statistics
import subprocess

import baseline_control as b
import study


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=pathlib.Path)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    names=['simulation/algorithm/all2all.cpp','simulation/algorithm/all2all.h',
           'simulation/scheduler/global.cpp','simulation/bvn_decompose_main.cpp',
           'ds/decomposition/resource_control.py','ds/decomposition/baseline_control.py',
           'ds/decomposition/study.py']
    meta=dict(complete=False,budget=3,scaling=True,seed=101,repeats=3,
              binary_sha256=hashlib.sha256(study.BIN.read_bytes()).hexdigest(),
              sources={p:hashlib.sha256((study.ROOT/p).read_bytes()).hexdigest() for p in names})
    b.save(args.out/'metadata.json',meta);rows=[]
    for n in (8,16):
        for kind in ('balanced','perturbed','zipf'):
            data=study.fixed_load_matrix(n,4,kind,'pipeline')
            b.save(args.out/f'{kind}-n{n}.input.json',data)
            for unit in (16,1024):
                expected=None
                for label,mode,strategy,rounds in [('native','pipeline',0,0),('s1','pipeline',1,0),
                                                   ('resource3','pipeline',1,3),('spreadout','spreadout',0,0)]:
                    command=[str(study.BIN),'--mode',mode,'--n',str(n),'--g','4','--strategy',str(strategy),
                             '--topology','1','--scaling',str(int(mode=='pipeline')),'--resource-rounds',str(rounds),
                             '--unit-mb',str(unit/1e6),'--repeat','3']
                    raw=json.loads(subprocess.run(command,input=' '.join(map(str,data)),text=True,
                                                  capture_output=True,check=True,timeout=90).stdout)
                    metrics=b.audit(raw,data,n,4,unit)
                    if expected is None:expected=metrics['resource_totals']
                    assert expected==metrics['resource_totals']
                    row=dict(case=f'{kind}-n{n}-unit{unit}',n=n,kind=kind,unit=unit,label=label,
                             total_us=raw['total_us'],K=raw.get('stage_count',raw.get('active_rounds')),
                             proposals=raw.get('resource_proposals',0),accepts=raw.get('resource_accepts',0),**metrics)
                    if mode=='pipeline':
                        row.update(decomposition_cpu_us=statistics.median(raw['cpu_samples_us']),
                                   planning_wall_us=statistics.median(raw['planning_wall_samples_us']))
                    rows.append(row)
                    b.save(args.out/(row['case']+'-'+label+'.json'),raw)
                    print(row['case'],label,'K',row['K'],'us',round(row['total_us'],4),
                          'accept',row['accepts'],flush=True)
    b.save(args.out/'summary.json',rows)
    assert hashlib.sha256(study.BIN.read_bytes()).hexdigest()==meta['binary_sha256']
    assert all(hashlib.sha256((study.ROOT/p).read_bytes()).hexdigest()==h for p,h in meta['sources'].items())
    meta.update(complete=True,outputs=len(rows),scope='development only; one synthetic seed and one 3DC topology')
    b.save(args.out/'metadata.json',meta)


if __name__=='__main__':main()
