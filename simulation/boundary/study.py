#!/usr/bin/env python3
"""Frozen native FAST communication-only WAN boundary scan."""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
FROZEN=SIM/'planning/results/final-96b18da3da36'
METHODS=[m+'-opt' for m in ['F000','maxmin','spreadout','X10','SPM','A1']]
LATENCIES=[50,1000,2500,3000,5000]
BANDWIDTHS=[25,100,400]
_ENGINES={}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(path)


def files():
    return sorted([*ROOT.glob('*.py'),ROOT/'DESIGN.md',ROOT/'Makefile',*(ROOT/'tests').glob('*.py')])


def identity():
    value={str(p.relative_to(ROOT)):sha(p) for p in files()}
    return dict(files=value,digest=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest())


def case_id(c):return f'n{c["n"]}g{c["g"]}-d{c["dc_count"]}-{c["kind"]}-s{c["seed"]}-b{c["wan_bw"]}-w{c["wan_us"]}'


def specs(smoke=False):
    cases=[];n,g,mib=(6,1,1) if smoke else (24,8,64)
    for d in ([3] if smoke else [3,6]):
        for kind,seed in ([('balanced',0),('zipf',810203)] if smoke else [('balanced',0),('zipf',760203),('zipf',810203)]):
            for bw in ([100,400] if smoke else BANDWIDTHS):
                for wan in ([50,3000] if smoke else LATENCIES):
                    c=dict(n=n,g=g,mib=mib,dc_count=d,dc=[s*d//n for s in range(n)],kind=kind,seed=seed,
                           layout_seed=seed,profile='asymmetric',wan_bw=bw,wan_us=wan,
                           split='balanced-control' if kind=='balanced' else 'known-counterexample' if seed==760203 else 'new-seed')
                    c['id']=case_id(c);cases.append(c)
    return cases


def capacities(case):return 400,400,case['wan_bw']


def command(binary,case,method,repeats=1):
    return [str(binary),str(case['n']),str(case['g']),method,str(case['wan_us']),str(repeats),
            ','.join(map(str,case['dc'])),*map(str,capacities(case)),'asymmetric']


def load_engine(output):
    output=Path(output);build=json.loads((output/'engine-build.json').read_text())
    if not build['identity']['digest'].startswith('96b18da3da36'):raise ValueError('wrong frozen engine')
    if sha(output/'native_scan')!=build['binary_sha256'] or sha(output/'engine-source.tar.gz')!=build['source_archive_sha256']:raise ValueError('engine binary/archive changed')
    for name,digest in build['identity']['files'].items():
        if sha(output/'engine-source'/name)!=digest:raise ValueError('frozen engine source changed')
    key=str(output.resolve())
    if key not in _ENGINES:
        path=output/'engine-source/planning/study.py';spec=importlib.util.spec_from_file_location('boundary_frozen_engine',path)
        engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
        # Private module only; all native/independent path calculations see the
        # requested capacity. The C++ binary and scheduler are never modified.
        engine.scene.capacities=capacities;_ENGINES[key]=engine
    return _ENGINES[key]


def invoke(binary,case,method,data):
    result=subprocess.run(command(binary,case,method),input=' '.join(map(str,data)),capture_output=True,text=True,timeout=180)
    if result.returncode:raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def matrix_id(c):return f'n{c["n"]}g{c["g"]}-{c["kind"]}-s{c["seed"]}-{c["mib"]}MiB'


def raw_key(c,method):return c['id']+'-'+method


def record_fingerprint(record):return hashlib.sha256(json.dumps(record['records'],sort_keys=True).encode()).hexdigest()


def prepare(output,smoke=False):
    output=Path(output).resolve()
    if (output/'study.json').exists():return checked(output,smoke)
    output.mkdir(parents=True,exist_ok=True)
    for source,destination in [('native_scan','native_scan'),('source.tar.gz','engine-source.tar.gz'),('build.json','engine-build.json')]:shutil.copy2(FROZEN/source,output/destination)
    with tarfile.open(output/'engine-source.tar.gz') as stream:stream.extractall(output/'engine-source',filter='data')
    engine=load_engine(output);ident=identity()
    with tarfile.open(output/'harness-source.tar.gz','w:gz') as stream:
        for name in ident['files']:stream.add(ROOT/name,arcname=name)
    with tarfile.open(output/'harness-source.tar.gz') as stream:stream.extractall(output/'harness',filter='data')
    cases=specs(smoke);matrices={};anchors={}
    for c in cases:
        mid=matrix_id(c)
        if mid not in matrices:
            data=engine.scene.matrix(c,output/'native_scan');path=output/'matrices'/f'{mid}.json';save(path,data)
            matrices[mid]=dict(sha256=sha(path),actual_bytes=sum(data))
    if not smoke:
        old=SIM/'planning/results/run-600edb9c2a2c';prior=json.loads((old/'study.json').read_text())
        for c in cases:
            if c['wan_bw']!=100 or c['wan_us'] not in [50,5000] or c['seed'] not in [0,760203]:continue
            match=next(p for p in prior['cases'] if all(p[k]==c[k] for k in ['n','g','kind','seed','dc','wan_us','mib']))
            for method in METHODS:
                source=FROZEN/'raw'/f'{match["id"]}-{method}.json';key=raw_key(c,method);dest=output/'anchors'/f'{key}.json'
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
                anchors[key]=dict(sha256=sha(dest),original=str(source),original_sha256=sha(source))
    record=dict(identity=ident,cases=cases,methods=METHODS,smoke=smoke,repeats=1,matrices=matrices,anchors=anchors,
                records={},execution_root=str(output),engine_build_sha256=sha(output/'engine-build.json'),
                harness_archive_sha256=sha(output/'harness-source.tar.gz'),
                timing_warning='One warmup + one execution per policy, communication determinism only; CPU timings excluded from derived evidence')
    save(output/'study.json',record);print('Frozen',len(cases),'configs x',len(METHODS),'policies;',len(matrices),'rank matrices',flush=True);return record


def checked(output,smoke=False,derived=False):
    output=Path(output);record=json.loads((output/'study.json').read_text())
    if record['identity']!=identity() or record['cases']!=specs(smoke) or record['methods']!=METHODS or record['smoke']!=smoke or record['repeats']!=1:raise ValueError('source/grid/protocol changed')
    if sha(output/'engine-build.json')!=record['engine_build_sha256'] or sha(output/'harness-source.tar.gz')!=record['harness_archive_sha256']:raise ValueError('registered archive changed')
    for name,digest in record['identity']['files'].items():
        if sha(output/'harness'/name)!=digest:raise ValueError('harness source changed')
    engine=load_engine(output);generated={}
    for c in record['cases']:
        mid=matrix_id(c)
        if mid in generated:continue
        data=engine.scene.matrix(c,output/'native_scan');path=output/'matrices'/f'{mid}.json';entry=record['matrices'][mid]
        if sha(path)!=entry['sha256'] or json.loads(path.read_text())!=data or sum(data)!=entry['actual_bytes']:raise ValueError('matrix/generator changed')
        generated[mid]=data
    expected={raw_key(c,m) for c in record['cases'] for m in METHODS}
    present={p.stem for p in (output/'raw').glob('*.json')}
    if set(record['records'])!=present or not present<=expected:raise ValueError('unregistered raw set')
    for key,digest in record['records'].items():
        if sha(output/'raw'/f'{key}.json')!=digest:raise ValueError('raw digest changed')
    for key,entry in record['anchors'].items():
        if sha(output/'anchors'/f'{key}.json')!=entry['sha256']:raise ValueError('anchor changed')
    if derived:
        entry=record.get('derived',{})
        if present!=expected or entry.get('records_fingerprint')!=record_fingerprint(record):raise ValueError('stale derived record set')
        for name,digest in entry.get('files',{}).items():
            if sha(output/name)!=digest:raise ValueError('derived table changed')
        if 'comparison.json' not in entry.get('files',{}):raise ValueError('missing audited tables')
    return record


def communication_row(engine,c,method,data,raw):
    row,totals=engine.reconstruct(c,method,data,raw)
    fields=['method','strategy','implementation','total_ms','stages','wan_stages','alpha_ms','serial_ms','resource_lb_ms',
            'serial_gap_ms','model_wan_util','cycle_trials','construction_trials','construction_accepted','matching_calls','changed_matches','inter_bytes']
    result={**c,**{key:row[key] for key in fields}};caps=engine.scene.topology(c)['bandwidths']
    work={k:.008*v/caps[k] for k,v in totals.items()};lb=max(work.values(),default=0)
    result['lb_resources']=sorted(k for k,v in work.items() if math.isclose(v,lb,rel_tol=1e-10,abs_tol=1e-9))
    result['lb_families']=','.join(sorted({k.split(':')[0].split('-')[0] for k in result['lb_resources']}))
    return result,totals


def run(output,smoke=False):
    output=Path(output);record=checked(output,smoke);engine=load_engine(output)
    for c in record['cases']:
        data=json.loads((output/'matrices'/f'{matrix_id(c)}.json').read_text())
        for method in METHODS:
            key=raw_key(c,method)
            if key in record['records']:continue
            raw=invoke(output/'native_scan',c,method,data);communication_row(engine,c,method,data,raw)
            if key in record['anchors']:
                old=json.loads((output/'anchors'/f'{key}.json').read_text())
                if engine.engine.semantic(raw)!=engine.engine.semantic(old):raise ValueError('previous anchor not reproduced')
            raw['_command']=command(Path(record['execution_root'])/'native_scan',c,method)
            path=output/'raw'/f'{key}.json';save(path,raw);record['records'][key]=sha(path);save(output/'study.json',record)
        print(c['id'],'6 policies complete',flush=True)
    return audit(output,smoke,replay=False)


def register_derived(output,paths):
    output=Path(output);record=json.loads((output/'study.json').read_text())
    entry=record.setdefault('derived',dict(files={},records_fingerprint=record_fingerprint(record)))
    if entry['records_fingerprint']!=record_fingerprint(record):raise ValueError('cannot bind stale derivation')
    for path in paths:entry['files'][str(Path(path).relative_to(output))]=sha(path)
    save(output/'study.json',record)


def audit(output,smoke=False,replay=False):
    output=Path(output);record=checked(output,smoke);engine=load_engine(output);rows=[]
    if len(record['records'])!=len(record['cases'])*6:raise ValueError('incomplete result grid')
    for c in record['cases']:
        data=json.loads((output/'matrices'/f'{matrix_id(c)}.json').read_text());totals_reference=None
        for method in METHODS:
            key=raw_key(c,method);raw=json.loads((output/'raw'/f'{key}.json').read_text())
            if raw['_command']!=command(Path(record['execution_root'])/'native_scan',c,method) or len(raw['timing_samples'])!=1:raise ValueError('wrong command/repeats')
            if replay and engine.engine.semantic(raw)!=engine.engine.semantic(invoke(output/'native_scan',c,method,data)):raise ValueError('native replay mismatch')
            if key in record['anchors'] and engine.engine.semantic(raw)!=engine.engine.semantic(json.loads((output/'anchors'/f'{key}.json').read_text())):raise ValueError('old anchor mismatch')
            row,totals=communication_row(engine,c,method,data,raw)
            if totals_reference is not None and totals!=totals_reference:raise ValueError('routed byte totals differ by strategy')
            totals_reference=totals;rows.append(row)
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        for method in METHODS:row['speedup_'+method[:-4]]=index[row['id'],method]['total_ms']/row['total_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    register_derived(output,[output/'comparison.json',output/'comparison.csv'])
    print('Audited',len(rows),'native outputs;',len(record['anchors']),'previous anchors; CPU excluded',flush=True);return rows


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',required=True,choices=['prepare','run','audit','analyze','render'])
    parser.add_argument('--output',type=Path);parser.add_argument('--smoke',action='store_true');parser.add_argument('--replay',action='store_true');args=parser.parse_args()
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    if args.phase=='audit':audit(output,args.smoke,args.replay)
    elif args.phase in ['analyze','render']:
        spec=importlib.util.spec_from_file_location('boundary_analysis',ROOT/'analysis.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
        getattr(a,args.phase)(output,args.smoke)
    else:globals()[args.phase](output,args.smoke)


if __name__=='__main__':main()
