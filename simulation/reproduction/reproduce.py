#!/usr/bin/env python3
"""Pristine, pinned author source reproduction; never imports topology/SimAI."""
import hashlib
import io
import json
import argparse
import csv
import math
import platform
import statistics
import struct
import tempfile
from pathlib import Path
import subprocess
import tarfile
from reference import extract,RATIOS

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
COMMIT='0712d29fb13a09962425c8a57b951b91d8fc33ba'
FLAGS=['-std=c++11','-O3','-DNOLOG','-include','cstring']
UPSTREAM_SHA='c9ccfd164b88466f2bfc587e4522979060cc3edd5b3a9388f9fb5a8212e9eafe'
PAPER_SHA='21af19770b9f28c07ea3f16e70c56420771459f46331b27a421865df1cb41f13'
TIME_FIELDS={'pipeline_cpu_us','full_cpu_us','all_gbps_bytes','all_full_gbps_bytes'}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');temporary.replace(path)


def build(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    data=(ROOT/'assets/upstream.tar').read_bytes()
    if hashlib.sha256(data).hexdigest()!=UPSTREAM_SHA:raise ValueError('pinned upstream archive changed')
    (output/'upstream.tar').write_bytes(data)
    source=output/'upstream';source.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as stream:
        for m in stream.getmembers():
            if not (m.name.startswith('simulation/') or m.name=='simulation' and m.isdir()) or '..' in Path(m.name).parts or not (m.isfile() or m.isdir()):raise ValueError('unsafe source archive')
        stream.extractall(source)
    sim=source/'simulation';includes=['-I'+str(sim),'-I'+str(sim/'algorithm'),'-I'+str(sim/'scheduler'),'-I'+str(ROOT)]
    config=output/'native-config.o'
    commands=[['g++',*FLAGS,*includes,'-Dget_inter_link_info=upstream_get_inter_link_info',
        '-Dget_intra_link_info=upstream_get_intra_link_info','-c',str(sim/'scheduler/config.cpp'),'-o',str(config)]]
    binary=output/'native_paper'
    paths=[sim/'algorithm/matrix.cpp',sim/'algorithm/all2all.cpp',sim/'scheduler/local.cpp',sim/'scheduler/global.cpp',ROOT/'driver.cc',ROOT/'link_profile.cc']
    commands.append(['g++',*FLAGS,*includes,*map(str,paths),str(config),'-lpthread','-o',str(binary)])
    for cmd in commands:
        done=subprocess.run(cmd,capture_output=True,text=True)
        if done.returncode:raise RuntimeError(done.stderr)
    original={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
    save(output/'build.json',dict(commit=COMMIT,commands=commands,source_files=original,
        compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
        archive_sha256=sha(output/'upstream.tar'),binary_sha256=sha(binary)))
    return binary


def identity():
    paths=sorted([*ROOT.glob('*.py'),*ROOT.glob('*.cc'),*ROOT.glob('*.h'),*ROOT.glob('tests/*.py'),*ROOT.glob('assets/*')])
    record=dict(files={str(p.relative_to(ROOT)):sha(p) for p in paths if p.is_file()},
        flags=FLAGS,commit=COMMIT,compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0],libc=platform.libc_ver())
    if record['files']['assets/upstream.tar']!=UPSTREAM_SHA or record['files']['assets/paper.pdf']!=PAPER_SHA:raise ValueError('pinned asset changed')
    # JSON normalizes tuples, so normalize before comparison after disk roundtrip.
    record=json.loads(json.dumps(record));record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest();return record


def cases(smoke=False):
    result=[]
    def add(group,n,g,seed,profile='paper',mbpu=.01,out=400,intra=3600,alpha=1,ratio=None):
        name=f'{group}-n{n}-g{g}-s{seed}'
        if ratio is not None:name+=f'-r{ratio:g}'
        if group=='alpha':name+=f'-a{alpha:g}'
        result.append(dict(id=name,group=group,n=n,g=g,seed=seed,profile=profile,mbpu=mbpu,
            out_gbps=out,in_gbps=intra,alpha_scale=alpha,ratio=ratio))
    if smoke:
        add('smoke-paper',2,1,280001);add('smoke-default',2,1,280001,'upstream-default',1,out=100)
        return result
    for n in range(2,41,2):
        for seed in range(280001,280021):add('scale',n,8,seed)
    for ratio in RATIOS:
        for seed in range(280001,280021):add('ratio',4,8,seed,intra=400*ratio,ratio=ratio)
    for n in [2,4,8]:
        for seed in range(280001,280004):add('defaults',n,8,seed,'upstream-default',1,out=100,intra=8000/2.3)
    for n in [4,40]:
        for alpha in [0,10]:
            for seed in range(280001,280004):add('alpha',n,8,seed,alpha=alpha)
    return result


def command(binary,case,matrix_out):
    return [str(binary),str(case['n']),str(case['g']),str(case['seed']),case['profile'],str(case['mbpu']),
        str(case['out_gbps']),str(case['in_gbps']),str(case['alpha_scale']),'-',str(matrix_out)]


def prepare(output,smoke):
    ident=identity();registered=cases(smoke);path=output/'study.json'
    if path.exists():
        old=json.loads(path.read_text())
        if old['identity']!=ident or old['cases']!=registered or old['smoke']!=smoke:raise ValueError('source/grid changed; new output required')
        checked(output,smoke);return old
    binary=build(output);save(output/'paper_reference.json',extract(ROOT/'assets/paper.pdf'))
    with tarfile.open(output/'adapter-source.tar.gz','w:gz') as stream:
        for name in ident['files']:stream.add(ROOT/name,arcname=name)
    model=next((x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name')),'unknown')
    study=dict(identity=ident,cases=registered,smoke=smoke,execution_root=str(output.resolve()),binary_sha256=sha(binary),
        build_sha256=sha(output/'build.json'),adapter_archive_sha256=sha(output/'adapter-source.tar.gz'),
        hardware=dict(cpu=model,platform=platform.platform(),python=platform.python_version()),reference_sha256=sha(output/'paper_reference.json'),record_hashes={})
    save(path,study);print('Prepared pinned native cases',len(registered),flush=True);return study


def checked(output,smoke):
    study=json.loads((output/'study.json').read_text())
    if study['identity']!=identity() or study['cases']!=cases(smoke) or study['smoke']!=smoke:raise ValueError('registered source/grid changed')
    for name,key in [('native_paper','binary_sha256'),('build.json','build_sha256'),('adapter-source.tar.gz','adapter_archive_sha256'),('paper_reference.json','reference_sha256')]:
        if sha(output/name)!=study[key]:raise ValueError(f'{name} changed')
    info=json.loads((output/'build.json').read_text())
    if info['commit']!=COMMIT or sha(output/'upstream.tar')!=UPSTREAM_SHA:raise ValueError('upstream archive identity')
    for name,digest in info['source_files'].items():
        if sha(output/'upstream'/name)!=digest:raise ValueError('pristine staged source changed')
    if json.loads((output/'paper_reference.json').read_text())!=extract(ROOT/'assets/paper.pdf'):raise ValueError('digitized reference mismatch')
    return study


def close(a,b):
    if not math.isfinite(a) or not math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-7):raise ValueError(f'numeric mismatch {a} != {b}')


def source_legacy_bound(data,n,g,mbpu,inter_beta,intra_beta):
    """Literal test.cpp fabric_speed_benchmark bound, NOT paper-text bound."""
    r=n*g
    maxcross=max(max(
        sum(data[(s*g+i)*r+d] for i in range(g) for d in range(r) if d//g!=s),
        sum(data[d*r+s*g+i] for i in range(g) for d in range(r) if d//g!=s)) for s in range(n))
    intra=sum(data[i*r+j] for i in range(r) for j in range(r) if i//g==j//g)
    return max(maxcross*inter_beta,intra*intra_beta)*mbpu/g


def audit_values(case,raw,matrix):
    n,g=case['n'],case['g'];r=n*g;content=matrix.read_bytes()
    if len(content)!=4*r*r:raise ValueError('matrix length mismatch')
    data=struct.unpack('<'+'I'*(r*r),content)
    if any(data[i*r+i] for i in range(r)):raise ValueError('matrix diagonal changed')
    if max(data)>9999:raise ValueError('matrix outside native random generator range')
    total=sum(data);maxcross=0
    for s in range(n):
        sent=sum(data[(s*g+i)*r+d] for i in range(g) for d in range(r) if d//g!=s)
        recv=sum(data[d*r+s*g+i] for i in range(g) for d in range(r) if d//g!=s)
        maxcross=max(maxcross,sent,recv)
    if (raw['profile'],raw['servers'],raw['gpus'],raw['seed'])!=(case['profile'],n,g,case['seed']):raise ValueError('native configuration mismatch')
    close(raw['mbpu'],case['mbpu'])
    expected=(.7*case['alpha_scale'],8000/case['out_gbps'],.1*case['alpha_scale'],8000/case['in_gbps']) if case['profile']=='paper' else (.7,84,.1,2.3)
    for name,value in zip(['inter_alpha','inter_beta','intra_alpha','intra_beta'],expected):close(raw[name],value)
    if raw['total_units']!=total or raw['max_cross_units']!=maxcross or raw['max_cell']!=max(data):raise ValueError('matrix accounting mismatch')
    ideal=maxcross*case['mbpu']*raw['inter_beta']/g;close(raw['ideal_us'],ideal)
    if raw['full_cpu_us']<raw['pipeline_cpu_us'] or raw['pipeline_cpu_us']<0:raise ValueError('invalid scheduling times')
    for name,time in [('fast_gbps_bytes',raw['fast_us']),('spread_gbps_bytes',raw['spread_us']),('ideal_gbps_bytes',ideal),
                      ('all_gbps_bytes',raw['fast_us']+raw['pipeline_cpu_us']),('all_full_gbps_bytes',raw['fast_us']+raw['full_cpu_us'])]:
        close(raw[name],total*case['mbpu']*1000/(r*time))
    if raw['fast_us']<ideal*(1-1e-10):raise ValueError('FAST faster than endpoint bound')
    legacy=source_legacy_bound(data,n,g,case['mbpu'],raw['inter_beta'],raw['intra_beta'])
    return dict(**case,**{k:raw[k] for k in ['fast_us','spread_us','ideal_us','pipeline_cpu_us','full_cpu_us',
        'fast_gbps_bytes','spread_gbps_bytes','ideal_gbps_bytes','all_gbps_bytes','all_full_gbps_bytes']},
        source_legacy_ideal_us=legacy,source_legacy_ideal_gb_s=total*case['mbpu']*1000/(r*legacy),
        actual_pair_mean_MB=total*case['mbpu']/(r*(r-1)),actual_per_gpu_MB=total*case['mbpu']/r,
        scaleout_gb_s=1000/raw['inter_beta'])


def invoke(binary,case,matrix_out):
    matrix_out.parent.mkdir(parents=True,exist_ok=True);cmd=command(binary,case,matrix_out)
    done=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
    if done.returncode:raise RuntimeError(f'{case["id"]}: {done.stderr}')
    return json.loads(done.stdout),cmd


def audit_one(output,case,study,replay=True):
    path=output/'raw'/f'{case["id"]}.json'
    if study['record_hashes'].get(case['id'])!=sha(path):raise ValueError('raw record differs from independent execution digest')
    raw=json.loads(path.read_text());matrix=output/'matrices'/f'{case["id"]}.bin'
    origin=Path(study['execution_root'])
    if raw['_case']!=case or raw['_command']!=command(origin/'native_paper',case,origin/'matrices'/matrix.name) or raw['_binary_sha256']!=study['binary_sha256']:
        raise ValueError('execution registration mismatch')
    if raw['_matrix_sha256']!=sha(matrix):raise ValueError('matrix hash mismatch')
    row=audit_values(case,raw,matrix)
    if replay:
        with tempfile.TemporaryDirectory(prefix='fast-paper-replay-') as temp:
            again,_=invoke(output/'native_paper',case,Path(temp)/'matrix.bin')
            if sha(Path(temp)/'matrix.bin')!=raw['_matrix_sha256']:raise ValueError('matrix differs from native replay')
            semantic={k:v for k,v in raw.items() if k not in TIME_FIELDS and not k.startswith('_')}
            if semantic!={k:v for k,v in again.items() if k not in TIME_FIELDS}:raise ValueError('native result differs on replay')
    return row


def run(output,smoke):
    study=checked(output,smoke)
    for k,case in enumerate(study['cases']):
        path=output/'raw'/f'{case["id"]}.json';matrix=output/'matrices'/f'{case["id"]}.bin'
        if path.exists():audit_one(output,case,study)
        else:
            raw,cmd=invoke(output/'native_paper',case,matrix)
            raw.update(_case=case,_command=cmd,_binary_sha256=study['binary_sha256'],_matrix_sha256=sha(matrix));save(path,raw)
            audit_values(case,raw,matrix)
            study['record_hashes'][case['id']]=sha(path);save(output/'study.json',study)
        if (k+1)%20==0 or smoke:print('Native cases',k+1,'/',len(study['cases']),flush=True)
    return audit(output,smoke)


def summarize(rows,reference):
    groups={};metrics=['fast_gbps_bytes','spread_gbps_bytes','ideal_gbps_bytes','source_legacy_ideal_gb_s','all_gbps_bytes','all_full_gbps_bytes','pipeline_cpu_us','full_cpu_us']
    for row in rows:
        if row['group'] not in ['scale','ratio']:continue
        key=(row['group'],row['n']*row['g'] if row['group']=='scale' else row['ratio']);groups.setdefault(key,[]).append(row)
    summary=[];mapping={'fast_raw':'fast_gbps_bytes','fast_all':'all_gbps_bytes','ideal':'ideal_gbps_bytes','spreadout':'spread_gbps_bytes'}
    for (group,x),values in sorted(groups.items()):
        item=dict(group=group,x=x,trials=len(values),metrics={m:dict(mean=statistics.mean(v[m] for v in values),sd=statistics.stdev(v[m] for v in values) if len(values)>1 else 0) for m in metrics},paper={})
        bw=values[0]['scaleout_gb_s']
        for curve,field in mapping.items():
            points={v['x']:v['y'] for v in reference[group].get(curve,[])}
            if x in points:
                predicted=item['metrics'][field]['mean']/(bw if group=='ratio' else 1)
                item['paper'][curve]=dict(reference=points[x],reproduced=predicted,relative_error=(predicted/points[x]-1))
        summary.append(item)
    return summary


def audit(output,smoke):
    study=checked(output,smoke);expected={c['id']+'.json' for c in study['cases']}
    if {p.name for p in (output/'raw').glob('*.json')}!=expected:raise ValueError('incomplete/unregistered results')
    if set(study['record_hashes'])!={c['id'] for c in study['cases']}:raise ValueError('incomplete execution digest registry')
    rows=[audit_one(output,c,study) for c in study['cases']]
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    summary=summarize(rows,json.loads((output/'paper_reference.json').read_text()));save(output/'summary.json',summary)
    print('Pristine native replay/units audited:',len(rows),flush=True);return rows,summary


def render(output,smoke):
    rows,summary=audit(output,smoke)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    ref=json.loads((output/'paper_reference.json').read_text())
    mapping={'fast_raw':'fast_gbps_bytes','fast_all':'all_gbps_bytes','ideal':'ideal_gbps_bytes','spreadout':'spread_gbps_bytes'}
    colors={'fast_raw':'tab:purple','fast_all':'tab:blue','ideal':'black','spreadout':'tab:pink'}
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained')
    errors={}
    for ax,group in zip(axes,['scale','ratio']):
        subset=[r for r in summary if r['group']==group]
        for curve,field in mapping.items():
            if group=='ratio' and curve=='fast_all':continue
            bw=50 if group=='ratio' else 1
            ax.plot([r['x'] for r in subset],[r['metrics'][field]['mean']/bw for r in subset],color=colors[curve],marker='.',label=curve+' reproduced')
            points=ref[group][curve];ax.plot([v['x'] for v in points],[v['y'] for v in points],color=colors[curve],ls='--',alpha=.6,label=curve+' paper')
            values=[abs(r['paper'][curve]['relative_error']) for r in subset if curve in r['paper']]
            if values:errors[group+'_'+curve]=dict(mean_relative_error=statistics.mean(values),max_relative_error=max(values),points=len(values),mean_within_predeclared_5pct=statistics.mean(values)<=.05)
        if group=='ratio':
            ax.plot([r['x'] for r in subset],[r['metrics']['source_legacy_ideal_gb_s']['mean']/50 for r in subset],color='tab:orange',ls=':',label='source-legacy ideal (finite intra)')
        ax.set_xlabel('GPU count' if group=='scale' else 'Scale-up / scale-out bandwidth ratio')
        ax.set_ylabel('Algorithmic bandwidth (GB/s)' if group=='scale' else 'Bandwidth / scale-out bandwidth')
        ax.set_title('Figure17a: disclosed parameters' if group=='scale' else 'Figure17b: conditional absolute parameters')
        ax.grid(alpha=.2);ax.legend(fontsize=7)
    fig.savefig(output/'paper-overlay.png',dpi=180);plt.close(fig);save(output/'agreement.json',errors)
    fig,ax=plt.subplots(figsize=(8,4),layout='constrained');scale=[s for s in summary if s['group']=='scale']
    for field,label in [('pipeline_cpu_us','pipeline2 elapsed'),('full_cpu_us','full native setup + pipeline elapsed')]:
        ax.plot([s['x'] for s in scale],[s['metrics'][field]['mean']/1000 for s in scale],marker='.',label=label)
    ax.set_xlabel('GPU count');ax.set_ylabel('Scheduling elapsed wall time (ms)');ax.set_title('Local host; not original paper machine');ax.grid(alpha=.2);ax.legend()
    fig.savefig(output/'cpu-overhead.png',dpi=180);plt.close(fig)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['prepare','run','audit','render'],required=True)
    parser.add_argument('--output',type=Path);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    globals()[args.phase](output,args.smoke)


if __name__=='__main__':main()
