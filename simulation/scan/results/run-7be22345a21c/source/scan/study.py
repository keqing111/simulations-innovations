#!/usr/bin/env python3
"""Development-only factorial scan in native FAST's analytic topology model."""
import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
_spec=importlib.util.spec_from_file_location('bottleneck_reconstruction',SIM/'bottleneck/study.py')
legacy=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
METHODS=['F000','F100','F010','F001','F110','F101','F011','F111','maxmin','spreadout']
FLAGS=legacy.FLAGS
sha=legacy.sha
save=legacy.save
matrix=legacy.matrix
FROZEN=SIM/'bottleneck/results/run-33e58e040218/native_bottleneck'


def files():
    return sorted(set(legacy.files())|{p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'}|set((ROOT/'tests').glob('*.*')))


def identity():
    record=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,
                compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
                libc=list(platform.libc_ver()),schema=1)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
    return record


def build(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    ident=identity();archive=output/'source.tar.gz'
    with tarfile.open(archive,'w:gz') as stream:
        for name in ident['files']:stream.add(SIM/name,arcname=name)
    source=output/'source'
    with tarfile.open(archive) as stream:stream.extractall(source,filter='data')
    include=['-I'+str(source/p) for p in ['.','algorithm','scheduler','topology','bottleneck','scan','reproduction']]
    config=output/'config.o';profile=output/'profile.o';binary=output/'native_scan'
    commands=[['g++',*FLAGS,*include,'-Dget_inter_link_info=upstream_get_inter_link_info','-Dget_intra_link_info=upstream_get_intra_link_info',
               '-c',str(source/'scheduler/config.cpp'),'-o',str(config)],
              ['g++',*FLAGS,*include,'-Dget_inter_link_info=profile_get_inter_link_info','-Dget_intra_link_info=profile_get_intra_link_info',
               '-c',str(source/'reproduction/link_profile.cc'),'-o',str(profile)],
              ['g++',*FLAGS,*include,*[str(source/p) for p in ['algorithm/matrix.cpp','algorithm/all2all.cpp',
                'scheduler/local.cpp','scheduler/global.cpp','test.cpp','topology/model.cpp','topology/matching.cpp','scan/planner.cpp','scan/main.cc']],
               str(config),str(profile),'-lpthread','-o',str(binary)]]
    for command in commands:
        result=subprocess.run(command,capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    save(output/'build.json',dict(identity=ident,commands=commands,source_archive_sha256=sha(archive),binary_sha256=sha(binary)))
    return binary


def specs(smoke=False):
    cases=[]
    for n in ([6] if smoke else [6,12,24]):
        for mib in ([1] if smoke else [16,256]):
            for wan in ([50] if smoke else [50,5000]):
                for kind,seed in [('balanced',0),('zipf',280101)]:
                    g=1 if smoke else 8
                    cases.append(dict(id=f'n{n}-g{g}-{kind}-s{seed}-{mib}MiB-w{wan}',n=n,g=g,mib=mib,
                                      wan_us=wan,kind=kind,seed=seed,split='development'))
    if not smoke:
        for wan in [50,5000]:
            cases.append(dict(id=f'n24-g8-zipf-s280203-256MiB-w{wan}',n=24,g=8,mib=256,wan_us=wan,
                              kind='zipf',seed=280203,split='diagnostic'))
    return cases


def command(binary,case,method=None,repeats=1):
    if method is None:return [str(binary),'--batch',str(case['n']),str(case['g']),str(case['wan_us']),str(repeats)]
    return [str(binary),str(case['n']),str(case['g']),method,str(case['wan_us']),str(repeats)]


def invoke(binary,case,method,data,repeats=1):
    result=subprocess.run(command(binary,case,method,repeats),input=' '.join(map(str,data)),text=True,capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def semantic(raw):
    return {k:v for k,v in raw.items() if k not in ['timing_samples','wall_us','native_decompose_us','extra_planner_us'] and not k.startswith('_')}


def reconstruct(case,method,data,raw):
    if method not in METHODS or raw['mode']!=method:raise ValueError('factor identity mismatch')
    adapted=dict(raw);adapted['mode']='spreadout' if method=='spreadout' else 'native'
    row,totals=legacy.reconstruct(case,adapted['mode'],data,adapted);row['method']=method
    samples=raw['timing_samples']
    if not samples:raise ValueError('missing measured samples')
    for sample in samples:
        if set(sample)!={'planning_us','setup_us','decompose_us','preprocess_us'}:raise ValueError('timing schema')
        if any(not math.isfinite(v) or v<0 for v in sample.values()):raise ValueError('negative/nonfinite timing')
        if sample['setup_us']>sample['planning_us'] or sample['decompose_us']>sample['planning_us'] or sample['preprocess_us']>sample['decompose_us']:
            raise ValueError('timing containment')
    if raw['native_decompose_us']!=0 or raw['extra_planner_us']!=0:raise ValueError('legacy duplicate timings in one-pass driver')
    if method!='spreadout':
        if raw['decomposition_runs']!=1:raise ValueError('not single decomposition')
        n=case['n'];stages=raw['stages']
        if len(stages)>n*n or raw['cycle_trials']>2*len(stages):raise ValueError('operation budget')
        if method.startswith('F') and method[2]=='0' and raw['cycle_trials']:raise ValueError('L disabled but cycles used')
        if method.startswith('F') and method[1]=='0' and raw['threshold_probes']:raise ValueError('G disabled but threshold probes used')
        decisions=raw['decisions']
        if method=='F000':
            if decisions or raw['changed_matches']:raise ValueError('native factors changed matching')
        elif len(decisions)!=len(stages):raise ValueError('missing stage decisions')
        if raw['threshold_probes']!=sum(d['probes'] for d in decisions):raise ValueError('probe count mismatch')
        for stage,d in zip(stages,decisions):
            if d['weight']!=stage['freq'] or d['weight']<d['threshold'] or d['weight']<d['seed_weight'] or d['threshold']<1:
                raise ValueError('coefficient/threshold guard violated')
            if method.startswith('F') and method[1]=='1' and d['probes']>6:raise ValueError('G probe budget')
        # Independently reconstruct a legal padded support matrix from permutations.
        service=raw['service']
        if len(service)!=n*n:raise ValueError('service matrix shape')
        padded=[0]*(n*n)
        for stage in stages:
            for s,d in enumerate(stage['map']):padded[s*n+d]+=stage['freq']
        maximum=max([sum(service[s*n:(s+1)*n]) for s in range(n)]+[sum(service[s*n+d] for s in range(n)) for d in range(n)])
        if any(padded[k]<service[k] for k in range(n*n)) or any(sum(padded[s*n:(s+1)*n])!=maximum for s in range(n)):
            raise ValueError('decomposition failed to cover service matrix')
    med=lambda key:statistics.median(s[key] for s in samples)
    timings=sorted(s['planning_us'] for s in samples)
    row.update(planning_us=med('planning_us'),planning_p95_us=timings[math.ceil(.95*len(timings))-1],
               decompose_us=med('decompose_us'),setup_us=med('setup_us'),preprocess_us=med('preprocess_us'),
               timing_repeats=len(samples),threshold_probes=raw['threshold_probes'],matching_calls=raw['matching_calls'],
               lower_coefficient_rejections=raw['lower_coefficient_rejections'],
               modeled_plus_planning_ms=row['total_ms']+med('planning_us')/1000)
    return row,totals


def prepare(output,smoke=False):
    output=Path(output)
    if (output/'study.json').exists():return checked(output,smoke)
    binary=build(output);cases=specs(smoke);inputs={};generated={}
    for case in cases:
        key=tuple(case[k] for k in ['n','g','mib','kind','seed'])
        if key not in generated:generated[key]=matrix(case,binary)
        data=generated[key];path=output/'matrices'/f'{case["id"]}.json';save(path,data)
        inputs[case['id']]=dict(sha256=sha(path),actual_bytes=sum(data))
    record=dict(identity=identity(),methods=METHODS,cases=cases,smoke=smoke,repeats=3 if smoke else 21,
                inputs=inputs,records={},execution_root=str(output.resolve()),binary_sha256=sha(binary),
                build_sha256=sha(output/'build.json'),archive_sha256=sha(output/'source.tar.gz'),
                host=dict(platform=platform.platform(),python=platform.python_version()),
                timing_protocol='one warm-up per method; deterministic rotating round-robin; instrumented host full planning, no analytic evaluation or export')
    save(output/'study.json',record);print('Frozen',len(cases),'development cases x',len(METHODS),'methods',flush=True)
    return record


def checked(output,smoke=False):
    output=Path(output);study=json.loads((output/'study.json').read_text())
    if study['identity']!=identity() or study['cases']!=specs(smoke) or study['methods']!=METHODS or study['smoke']!=smoke:
        raise ValueError('source/grid changed; choose new run directory')
    for file,key in [('native_scan','binary_sha256'),('build.json','build_sha256'),('source.tar.gz','archive_sha256')]:
        if sha(output/file)!=study[key]:raise ValueError('binary/build/archive changed')
    for name,digest in study['identity']['files'].items():
        if sha(output/'source'/name)!=digest:raise ValueError('snapshotted source changed')
    generated={};paired={}
    for case in study['cases']:
        path=output/'matrices'/f'{case["id"]}.json';entry=study['inputs'][case['id']]
        if sha(path)!=entry['sha256']:raise ValueError('registered input changed')
        key=tuple(case[k] for k in ['n','g','mib','kind','seed'])
        if key not in generated:generated[key]=matrix(case,output/'native_scan')
        data=json.loads(path.read_text())
        if data!=generated[key] or sum(data)!=entry['actual_bytes']:raise ValueError('matrix does not match registered generator/seed')
        if key in paired and paired[key]!=entry['sha256']:raise ValueError('WAN settings did not reuse input')
        paired[key]=entry['sha256']
    return study


def run(output,smoke=False):
    output=Path(output);study=checked(output,smoke)
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text())
        keys=[case['id']+'-'+m for m in METHODS]
        if all(key in study['records'] for key in keys):continue
        raw=invoke(output/'native_scan',case,None,data,study['repeats'])
        if set(raw)!=set(METHODS):raise ValueError('incomplete batch')
        for method in METHODS:
            value=raw[method];reconstruct(case,method,data,value)
            if len(value['timing_samples'])!=study['repeats']:raise ValueError('repeat protocol')
            key=case['id']+'-'+method;path=output/'raw'/f'{key}.json'
            if key in study['records']:
                if sha(path)!=study['records'][key] or semantic(json.loads(path.read_text()))!=semantic(value):raise ValueError('resume changed result')
                continue
            value['_command']=command(Path(study['execution_root'])/'native_scan',case,None,study['repeats'])
            save(path,value);study['records'][key]=sha(path);save(output/'study.json',study)
        print(case['id'],'10methods complete',flush=True)
    return audit(output,smoke)


def audit(output,smoke=False,replay=True):
    output=Path(output);study=checked(output,smoke);rows=[]
    keys={c['id']+'-'+m for c in study['cases'] for m in METHODS}
    if set(study['records'])!=keys or {p.stem for p in (output/'raw').glob('*.json')}!=keys:raise ValueError('incomplete/unregistered raw')
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text());totals_reference=None
        if replay:replayed=invoke(output/'native_scan',case,None,data,1)
        for method in METHODS:
            key=case['id']+'-'+method;path=output/'raw'/f'{key}.json'
            if sha(path)!=study['records'][key]:raise ValueError('raw timing/semantic digest changed')
            raw=json.loads(path.read_text())
            if raw['_command']!=command(Path(study['execution_root'])/'native_scan',case,None,study['repeats']):raise ValueError('wrong execution command')
            if len(raw['timing_samples'])!=study['repeats']:raise ValueError('wrong timing repetition count')
            if replay and semantic(raw)!=semantic(replayed[method]):raise ValueError('native replay mismatch')
            row,totals=reconstruct(case,method,data,raw)
            if totals_reference is not None and totals!=totals_reference:raise ValueError('methods changed routed byte totals')
            totals_reference=totals;rows.append(row)
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        base=index[row['id'],'F000'];strong=index[row['id'],'maxmin']
        row['speedup_native']=base['total_ms']/row['total_ms']
        row['speedup_with_planning']=base['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
        row['speedup_maxmin']=strong['total_ms']/row['total_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Audited one-pass native replay/routes/payload/pipeline:',len(rows),flush=True)
    return rows


def audit_baselines(output,smoke=False):
    output=Path(output);study=checked(output,smoke);checks=[]
    if not FROZEN.is_file():raise FileNotFoundError('frozen previous baseline binary unavailable')
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text())
        for method,old in [('F000','native'),('F010','bounded'),('maxmin','maxmin'),('spreadout','spreadout')]:
            previous=legacy.invoke(FROZEN,case,old,data)
            current=json.loads((output/'raw'/f'{case["id"]}-{method}.json').read_text())
            fields=['total_us','stages','inter_bytes'] if method=='spreadout' else ['total_us','balance_us','intrinsic_us','dispatch_us','stages','inter_bytes']
            if any(current[k]!=previous[k] for k in fields):raise ValueError(f'frozen baseline changed: {case["id"]}/{method}')
            checks.append(dict(case=case['id'],method=method,old=old,fields=fields))
    save(output/'baseline_checks.json',dict(binary=str(FROZEN),sha256=sha(FROZEN),checks=checks))
    print('Frozen baseline semantic checks:',len(checks),flush=True)


def render(output,smoke=False):
    output=Path(output);rows=audit(output,smoke)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    names=METHODS
    gm=lambda values:math.exp(statistics.mean(math.log(v) for v in values))
    axes[0].bar(names,[gm([r['speedup_native'] for r in rows if r['method']==m]) for m in names]);axes[0].axhline(1,color='gray',ls='--')
    axes[0].set_ylabel('Communication speedup vs native FAST');axes[0].set_title('Development + known counterexamples')
    axes[1].bar(names,[gm([r['speedup_with_planning'] for r in rows if r['method']==m]) for m in names]);axes[1].axhline(1,color='gray',ls='--')
    axes[1].set_ylabel('Speedup incl. measured planning (serial assumption)');axes[1].set_title('Instrumented host diagnostic, not GPU measurement')
    for ax in axes:ax.tick_params(axis='x',rotation=45);ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'speedup.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for m in names:
        selected=[r for r in rows if r['method']==m and r['split']=='development'];ns=sorted({r['n'] for r in selected})
        axes[0].plot(ns,[statistics.median(r['planning_us']/1000 for r in selected if r['n']==n) for n in ns],marker='o',label=m)
        axes[1].plot(ns,[gm([r['speedup_native'] for r in selected if r['n']==n]) for n in ns],marker='o',label=m)
    axes[0].set_ylabel('Full planning median (ms)');axes[1].set_ylabel('Communication speedup vs native FAST');axes[1].axhline(1,color='gray',ls='--')
    for ax in axes:ax.set_xlabel('Servers');ax.grid(alpha=.2);ax.legend(fontsize=7,ncol=2)
    fig.savefig(output/'scale.png',dpi=170);plt.close(fig)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',required=True,choices=['prepare','run','audit','render','baselines'])
    parser.add_argument('--output',type=Path);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    if args.phase=='baselines':audit_baselines(output,args.smoke)
    else:globals()[args.phase](output,args.smoke)

if __name__=='__main__':main()
