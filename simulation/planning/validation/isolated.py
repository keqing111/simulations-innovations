#!/usr/bin/env python3
"""Diagnostic only: warmed single-policy processes, reversed block order.

Uses an already frozen binary/input; does not change the formal study or tune
algorithms. Each independent planning instance still pays all cache/trace costs.
"""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import tarfile


def orders(methods):return [list(methods),list(reversed(methods))]


def select(cases):return [c for c in cases if c['n']==48 and c['layout_seed']==760201]


def load(archive):
    path=archive/'source/planning/study.py'
    spec=importlib.util.spec_from_file_location('frozen_planning',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.SUITE='confirm'
    return module


def validate_binary(directory):
    def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    if not (directory/'build.json').is_file() or not (directory/'native_scan').is_file():raise ValueError('missing registered build')
    build=json.loads((directory/'build.json').read_text())
    if digest(directory/'native_scan')!=build['binary_sha256'] or digest(directory/'source.tar.gz')!=build['source_archive_sha256']:raise ValueError('registered build digest changed')
    for name,sha in build['identity']['files'].items():
        if digest(directory/'source'/name)!=sha:raise ValueError('registered build source changed')
    return directory/'native_scan'


def run(archive,out,binary_run=None):
    module=load(archive);formal=module.checked(archive);cases=select(formal['cases']);repeats=12
    target=binary_run or archive;binary=validate_binary(target)
    if json.loads((target/'build.json').read_text())['identity']['flags']!=formal['identity']['flags']:raise ValueError('different compiler flags')
    if len(cases)!=8:raise ValueError('wrong diagnostic subset')
    ident=dict(source_sha256=module.sha(Path(__file__)),test_sha256=module.sha(Path(__file__).with_name('test_isolated.py')),
               binary_sha256=module.sha(binary),archive_sha256=module.sha(archive/'source.tar.gz'),
               binary_source_archive_sha256=module.sha(target/'source.tar.gz'),build_sha256=module.sha(target/'build.json'),binary_run=str(target),
               cases=cases,orders=orders(module.METHODS),repeats_per_block=repeats,blocks=2,
               protocol='new process per policy block, one warm-up; all preprocessing charged per instance; reversed second block',
               selection='N48, layout_seed760201, both loads/DC counts/WAN latencies; chosen after noticing negative-control timing bias; diagnostic, not new independent algorithm confirmation')
    out.mkdir(parents=True,exist_ok=True);registration=out/'registration.json'
    if registration.exists():
        record=json.loads(registration.read_text())
        if record['identity']!=ident:raise ValueError('diagnostic source/grid changed')
    else:
        with tarfile.open(out/'source.tar.gz','w:gz') as stream:
            for name in ['isolated.py','test_isolated.py']:stream.add(Path(__file__).with_name(name),arcname=name)
        record=dict(identity=ident,records={},script_archive_sha256=module.sha(out/'source.tar.gz'));module.save(registration,record)
    if module.sha(out/'source.tar.gz')!=record['script_archive_sha256']:raise ValueError('diagnostic script archive changed')
    for case in cases:
        data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text())
        for block,methods in enumerate(ident['orders']):
            for method in methods:
                key=f'{case["id"]}-block{block}-{method}';path=out/'raw'/f'{key}.json'
                if key in record['records']:
                    if module.sha(path)!=record['records'][key]:raise ValueError('diagnostic raw changed')
                    continue
                raw=module.invoke(binary,case,method,data,repeats)
                module.reconstruct(case,method,data,raw)
                expected=json.loads((archive/'raw'/f'{case["id"]}-{method}.json').read_text())
                if module.engine.semantic(raw)!=module.engine.semantic(expected):raise ValueError('isolated semantics changed')
                raw['_command']=module.command(binary,case,method,repeats)
                module.save(path,raw);record['records'][key]=module.sha(path);module.save(registration,record)
            print(case['id'],'isolated block',block,'complete',flush=True)
    rows=[]
    for case in cases:
        data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text())
        for method in module.METHODS:
            samples=[]
            for block in range(2):
                key=f'{case["id"]}-block{block}-{method}';path=out/'raw'/f'{key}.json'
                if module.sha(path)!=record['records'][key]:raise ValueError('diagnostic digest changed')
                raw=json.loads(path.read_text());module.reconstruct(case,method,data,raw)
                if len(raw['timing_samples'])!=repeats:raise ValueError('diagnostic repeat count')
                samples.extend(raw['timing_samples'])
            row,_=module.reconstruct(case,method,data,dict(raw,timing_samples=samples));rows.append(row)
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        reference=index[row['id'],row['strategy']+'-ref']
        row['planning_speedup']=reference['planning_us']/row['planning_us']
        for baseline in module.STRATEGIES:
            ref=index[row['id'],baseline+'-'+row['implementation']]
            row['with_planning_'+baseline]=ref['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
    module.save(out/'comparison.json',rows)
    with (out/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Isolated diagnostic:',len(rows),'outcomes,',len(rows)*24,'measured samples',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--archive',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--binary-run',type=Path)
    args=parser.parse_args();run(args.archive.resolve(),args.output.resolve(),args.binary_run.resolve() if args.binary_run else None)
