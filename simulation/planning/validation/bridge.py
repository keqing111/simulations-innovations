#!/usr/bin/env python3
"""Rebuild final resource-only implementation; prove equality to frozen data.

Bridge timings are intentionally NOT performance measurements. All historical
diagnostic fields are independently audited before removing export-only fields.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
SIM=ROOT.parent


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def canonical(raw):
    value=copy.deepcopy({k:v for k,v in raw.items() if k not in ['wall_us','timing_samples','native_decompose_us','extra_planner_us'] and not k.startswith('_')})
    if value['mode'].endswith(('-ref','-opt')):value['mode']=value['mode'][:-4]
    for name in ['initial_rows','support_diagnostics']:value.pop(name,None)
    for name in ['optimized','static_path','row_order']:value['policy'].pop(name,None)
    for decision in value.get('decisions',[]):
        for name in ['row_order','row_pressure']:decision.pop(name,None)
    return value


def require_equal(expected,actual):
    if canonical(expected)!=canonical(actual):raise ValueError('full semantic bridge differs')


def run(output=None):
    module=load(ROOT/'study.py','current_planning_bridge');module.SUITE='confirm'
    isolated=load(Path(__file__).with_name('isolated.py'),'bridge_binary_validator')
    ident=module.identity();out=(output or ROOT/'results'/('final-'+ident['digest'][:12])).resolve()
    if (out/'checks.json').exists():raise ValueError('completed bridge exists; preserve it and use a new output')
    if (out/'build.json').exists():
        if json.loads((out/'build.json').read_text())['identity']!=ident:raise ValueError('build source changed')
        binary=isolated.validate_binary(out)
    else:binary=module.build(out)
    with tarfile.open(out/'validation-source.tar.gz','w:gz') as stream:
        for path in sorted(Path(__file__).parent.glob('*.py')):stream.add(path,arcname=path.name)
    checks=[];sources={}
    for name,suite in [('run-45a8ce2df36d','explore'),('run-600edb9c2a2c','confirm')]:
        archive=ROOT/'results'/name;frozen=load(archive/'source/planning/study.py','frozen_'+suite);frozen.SUITE=suite
        record=frozen.checked(archive);sources[name]=dict(study_sha256=module.sha(archive/'study.json'),binary_sha256=module.sha(archive/'native_scan'))
        for case in record['cases']:
            data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text());actual=module.invoke(binary,case,None,data,1)
            for method in module.METHODS:
                key=case['id']+'-'+method;path=archive/'raw'/f'{key}.json'
                if module.sha(path)!=record['records'][key]:raise ValueError('frozen raw changed')
                expected=json.loads(path.read_text());frozen.reconstruct(case,method,data,expected);module.reconstruct(case,method,data,actual[method])
                require_equal(expected,actual[method]);module.save(out/'raw'/f'{key}.json',actual[method]);checks.append(dict(archive=name,case=case['id'],method=method,equal=True))
            for strategy in module.STRATEGIES:module.check_pair(actual[strategy+'-ref'],actual[strategy+'-opt'])
            print('Bridge',case['id'],'12 policies equal',flush=True)
    historical=[]
    for name,suite in [('run-5f741f907916','explore'),('run-fd58e408d88f','confirm')]:
        archive=SIM/'ordering/results'/name;old=load(archive/'source/ordering/study.py','old_order_'+suite);old.SUITE=suite
        record=old.checked(archive);sources[name]=dict(study_sha256=module.sha(archive/'study.json'),binary_sha256=module.sha(archive/'native_scan'))
        for case in record['cases']:
            data=json.loads((archive/'matrices'/f'{case["id"]}.json').read_text());previous=module.invoke(archive/'native_scan',case,'A1',data,1)
            path=archive/'raw'/f'{case["id"]}-A1.json'
            if module.sha(path)!=record['records'][case['id']+'-A1']:raise ValueError('historical raw changed')
            stored=json.loads(path.read_text())
            if module.engine.semantic(previous)!=module.engine.semantic(stored):raise ValueError('historical replay changed')
            old.reconstruct(case,'A1',data,previous)
            current=module.invoke(binary,case,'A1-opt',data,1);module.reconstruct(case,'A1-opt',data,current);require_equal(previous,current)
            historical.append(dict(archive=name,case=case['id'],equal=True,stages=len(current['stages'])))
        print('Historical bridge',name,len(record['cases']),'equal',flush=True)
    module.save(out/'checks.json',dict(source_identity=ident,binary_sha256=module.sha(binary),
                validation_source_sha256=module.sha(out/'validation-source.tar.gz'),sources=sources,checks=checks,historical=historical,
                timing_warning='all timings here are semantic replay only, NOT performance evidence; use separate isolated final run'))
    print('Final build:',out,'; full semantic checks',len(checks),'; historical',len(historical),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args();run(args.output)
