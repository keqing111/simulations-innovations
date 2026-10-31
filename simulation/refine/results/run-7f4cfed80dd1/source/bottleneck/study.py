#!/usr/bin/env python3
"""Shared-resource native study; no packet-simulator or GPU performance claims."""
import argparse,csv,hashlib,json,math,platform,statistics,subprocess,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
METHODS=['native','wan-repair','reference','cached','bounded','maxmin','spreadout']
FLAGS=['-std=c++11','-O3','-DNOLOG','-include','cstring']
TIMING={'wall_us','native_decompose_us','extra_planner_us'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(v,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(p)
def files():
    return sorted({SIM/'define.h',SIM/'test.h',SIM/'test.cpp',
        *list((SIM/'algorithm').glob('*.cpp')),*list((SIM/'algorithm').glob('*.h')),
        *list((SIM/'scheduler').glob('*.cpp')),*list((SIM/'scheduler').glob('*.h')),
        *[SIM/'topology'/p for p in ['main.cc','model.h','model.cpp','matching.h','matching.cpp']],
        SIM/'reproduction/link_profile.h',SIM/'reproduction/link_profile.cc',SIM/'reproduction/assets/oracle.json',
        *[p for p in ROOT.glob('*') if p.suffix in ['.cc','.cpp','.h','.py'] or p.name=='Makefile'],*list((ROOT/'tests').glob('*.*'))})
def identity():
    value=dict(files={str(p.relative_to(SIM)):sha(p) for p in files()},flags=FLAGS,
        compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0],libc=list(platform.libc_ver()))
    value['digest']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest();return value
def build(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    ident=identity();archive=output/'source.tar.gz'
    with tarfile.open(archive,'w:gz') as stream:
        for name in ident['files']:stream.add(SIM/name,arcname=name)
    source=output/'source'
    with tarfile.open(archive) as stream:stream.extractall(source,filter='data')
    include=['-I'+str(source),'-I'+str(source/'algorithm'),'-I'+str(source/'scheduler'),'-I'+str(source/'topology'),'-I'+str(source/'bottleneck'),'-I'+str(source/'reproduction')]
    config=output/'config.o';profile=output/'profile.o';binary=output/'native_bottleneck'
    commands=[['g++',*FLAGS,*include,'-Dget_inter_link_info=upstream_get_inter_link_info','-Dget_intra_link_info=upstream_get_intra_link_info',
        '-c',str(source/'scheduler/config.cpp'),'-o',str(config)],
        ['g++',*FLAGS,*include,'-Dget_inter_link_info=profile_get_inter_link_info','-Dget_intra_link_info=profile_get_intra_link_info',
         '-c',str(source/'reproduction/link_profile.cc'),'-o',str(profile)],
        ['g++',*FLAGS,*include,*[str(source/p) for p in ['algorithm/matrix.cpp','algorithm/all2all.cpp','scheduler/local.cpp','scheduler/global.cpp','test.cpp',
         'topology/model.cpp','topology/matching.cpp','bottleneck/cached.cpp','bottleneck/driver.cc']],str(config),str(profile),'-lpthread','-o',str(binary)]]
    for cmd in commands:
        result=subprocess.run(cmd,capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)
    save(output/'build.json',dict(identity=ident,commands=commands,source_archive_sha256=sha(archive),binary_sha256=sha(binary)))
    return binary
def specs(smoke=False):
    cases=[]
    for n in ([6] if smoke else [6,12,24]):
        for mib in ([1] if smoke else [16,256]):
            for wan in ([50] if smoke else [50,5000]):
                choices=[('balanced',0),('zipf',280101)] if smoke else [('balanced',0),*[('zipf',s) for s in [280101,280103,280201,280203]]]
                for kind,seed in choices:
                    cases.append(dict(id=f'n{n}-g{1 if smoke else 8}-{kind}-s{seed}-{mib}MiB-w{wan}',n=n,g=1 if smoke else 8,
                        mib=mib,wan_us=wan,kind=kind,seed=seed,split='control' if not seed else 'validation' if seed>=280200 else 'development'))
    return cases
def matrix(case,binary):
    r=case['n']*case['g'];target=r*case['mib']*1024**2
    if case['kind']=='balanced':return [0 if i==j else target//(r*(r-1)) for i in range(r) for j in range(r)]
    weights=json.loads(subprocess.check_output([str(binary),'--zipf',str(case['seed']),str(r*r)],text=True))
    for i in range(r):weights[i*r+i]=0
    total=sum(weights);values=[v*target//total for v in weights]
    order=sorted(range(r*r),key=lambda k:-(weights[k]*target%total))
    for k in order[:target-sum(values)]:values[k]+=1
    return values
def selection(method):
    if method in ['reference','cached','bounded']:return 'link-cost',method
    if method=='legacy-profile':return 'legacy',method
    return method,'reference'
def command(binary,case,method):
    mode,choice=selection(method)
    profile='upstream-400' if choice=='legacy-profile' else 'paper'
    if choice=='legacy-profile':choice='reference'
    return ['env','FAST_BOTTLENECK_VARIANT='+choice,'FAST_BOTTLENECK_PROFILE='+profile,str(binary),str(case['n']),str(case['g']),mode,str(case['wan_us'])]
def invoke(binary,case,method,data):
    result=subprocess.run(command(binary,case,method),input=' '.join(map(str,data)),text=True,capture_output=True,timeout=180)
    if result.returncode:raise RuntimeError(result.stderr)
    return json.loads(result.stdout)
def semantic(raw):return {k:v for k,v in raw.items() if k not in TIMING and not k.startswith('_')}
def close(a,b):
    if not math.isfinite(a) or not math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-7):raise ValueError(f'numeric mismatch {a} != {b}')
def reconstruct(case,method,data,raw):
    n,g=case['n'],case['g'];r=n*g;dc=[min(2,s*3//n) for s in range(n)]
    if raw['mode']!=selection(method)[0] or not raw['payload_verified']:raise ValueError('method identity or payload unchecked')
    delivered=[[0]*n for _ in range(n)];totals={};costs=[];wan_stages=0
    for k,stage in enumerate(raw['stages']):
        size=r if method=='spreadout' else n
        if sorted(stage['map'])!=list(range(size)) or len(stage['payload'])!=r:raise ValueError('stage shape')
        if method!='spreadout' and stage['freq']<=0:raise ValueError('nonpositive quota')
        links={};bandwidths={};delay=0;local=0
        def add(key,volume,bw):links[key]=links.get(key,0)+volume;bandwidths[key]=bw
        for s,d in enumerate(stage['map']):
            if method=='spreadout':
                if d!=(s+k)%r or stage['payload'][s]!=data[s*r+d]:raise ValueError('SpreadOut stage mismatch')
                volume=stage['payload'][s];src,dst=s//g,d//g
                if volume and src==dst:local=max(local,.1+volume*(8000/3600)*1e-6)
            else:
                values=stage['payload'][s*g:(s+1)*g]
                if any(v<0 or v>stage['freq'] for v in values):raise ValueError('GPU quota')
                volume=sum(values);src,dst=s,d
            if src==dst or not volume:continue
            delivered[src][dst]+=volume;add(f'server-out:{src}',volume,400);add(f'server-in:{dst}',volume,400)
            if dc[src]!=dc[dst]:
                add(f'gateway-out:{dc[src]}',volume,400);add(f'gateway-in:{dc[dst]}',volume,400)
                add(f'wan:{dc[src]}:{dc[dst]}',volume,100);delay=max(delay,case['wan_us']+2.8)
            else:delay=max(delay,1.4)
        serial=max((.008*value/bandwidths[key] for key,value in links.items()),default=0)
        if stage['links']!=links or stage['bandwidths']!=bandwidths:raise ValueError('routed link bytes/capacity')
        close(stage['alpha_us'],delay);close(stage['serial_us'],serial);close(stage['communication_us'],delay+serial)
        if method=='spreadout':close(stage['local_us'],local)
        costs.append(max(delay+serial,local) if method=='spreadout' else delay+serial)
        wan_stages+=int(any(key.startswith('wan:') for key in links))
        for key,volume in links.items():totals[key]=totals.get(key,0)+volume
    for s in range(n):
        for d in range(n):
            if s!=d and delivered[s][d]!=sum(data[(s*g+i)*r+d*g+j] for i in range(g) for j in range(g)):raise ValueError('delivered server pair bytes')
    inter=sum(map(sum,delivered));close(raw['inter_bytes'],inter)
    if method=='spreadout':total=sum(costs)
    else:
        if len(raw['dispatch_us'])!=len(costs):raise ValueError('dispatch count')
        total=raw['balance_us']+(max(raw['intrinsic_us'],costs[0])+sum(max(costs[k],raw['dispatch_us'][k-1]) for k in range(1,len(costs)))+raw['dispatch_us'][-1] if costs else raw['intrinsic_us'])
    close(raw['total_us'],total)
    if any(not math.isfinite(raw[k]) or raw[k]<0 for k in TIMING):raise ValueError('elapsed timing')
    if method=='bounded' and raw['cycle_trials']>2*len(costs):raise ValueError('attempt budget')
    maxwan=max((v for key,v in totals.items() if key.startswith('wan:')),default=0)
    planning=raw['native_decompose_us']+raw['extra_planner_us']
    return dict(**case,method=method,total_ms=total/1000,stages=len(costs),wan_stages=wan_stages,
        alpha_ms=sum(v['alpha_us'] for v in raw['stages'])/1000,serial_ms=sum(v['serial_us'] for v in raw['stages'])/1000,
        max_wan_bytes=maxwan,model_wan_util=maxwan*.008/100/total if total else 0,
        planning_us=planning,extra_planner_us=raw['extra_planner_us'],wall_us=raw['wall_us'],
        modeled_plus_planning_ms=(total+planning)/1000,cycle_trials=raw['cycle_trials'],changed_matches=raw['changed_matches'],inter_bytes=inter),totals
def prepare(output,smoke):
    ident=identity();registered=specs(smoke);manifest=output/'study.json'
    if manifest.exists():return checked(output,smoke)
    binary=build(output);inputs={}
    for case in registered:
        data=matrix(case,binary);path=output/'matrices'/f'{case["id"]}.json';save(path,data)
        inputs[case['id']]=dict(sha256=sha(path),actual_bytes=sum(data))
    study=dict(identity=ident,cases=registered,methods=METHODS,smoke=smoke,inputs=inputs,execution_root=str(output.resolve()),records={},
        binary_sha256=sha(binary),build_sha256=sha(output/'build.json'),archive_sha256=sha(output/'source.tar.gz'),host=dict(platform=platform.platform(),python=platform.python_version()))
    save(manifest,study);print('Frozen inputs:',len(registered),'methods:',len(METHODS),flush=True);return study
def checked(output,smoke):
    study=json.loads((output/'study.json').read_text())
    if study['identity']!=identity() or study['cases']!=specs(smoke) or study['methods']!=METHODS or study['smoke']!=smoke:raise ValueError('source/grid identity')
    for name,key in [('native_bottleneck','binary_sha256'),('build.json','build_sha256'),('source.tar.gz','archive_sha256')]:
        if sha(output/name)!=study[key]:raise ValueError('binary/build/archive changed')
    for name,digest in study['identity']['files'].items():
        if sha(output/'source'/name)!=digest:raise ValueError('staged source changed')
    for case in study['cases']:
        if sha(output/'matrices'/f'{case["id"]}.json')!=study['inputs'][case['id']]['sha256']:raise ValueError('matrix hash changed')
    return study
def audit_one(output,study,case,method,replay=True):
    key=case['id']+'-'+method;path=output/'raw'/f'{key}.json'
    if study['records'].get(key)!=sha(path):raise ValueError('raw execution digest changed')
    raw=json.loads(path.read_text());data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text())
    if raw['_command']!=command(Path(study['execution_root'])/'native_bottleneck',case,method):raise ValueError('execution command mismatch')
    if replay and semantic(raw)!=semantic(invoke(output/'native_bottleneck',case,method,data)):raise ValueError('native replay mismatch')
    return reconstruct(case,method,data,raw)
def run(output,smoke):
    study=checked(output,smoke)
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text())
        for method in METHODS:
            key=case['id']+'-'+method;path=output/'raw'/f'{key}.json'
            if path.exists():audit_one(output,study,case,method)
            else:
                raw=invoke(output/'native_bottleneck',case,method,data);raw['_command']=command(output/'native_bottleneck',case,method)
                reconstruct(case,method,data,raw);save(path,raw);study['records'][key]=sha(path);save(output/'study.json',study)
        print(case['id'],'7methods complete',flush=True)
    return audit(output,smoke)
def audit(output,smoke):
    study=checked(output,smoke);keys={c['id']+'-'+m for c in study['cases'] for m in METHODS}
    if set(study['records'])!=keys or {p.stem for p in (output/'raw').glob('*.json')}!=keys:raise ValueError('incomplete records')
    rows=[]
    for case in study['cases']:
        reference=None;cache_semantic=None
        for method in METHODS:
            row,totals=audit_one(output,study,case,method)
            if reference is not None and totals!=reference:raise ValueError('policies changed routed bytes')
            reference=totals;rows.append(row)
            if method in ['reference','cached']:
                value=semantic(json.loads((output/'raw'/f'{case["id"]}-{method}.json').read_text()))
                if cache_semantic is not None and value!=cache_semantic:raise ValueError('cache changed schedule or modeled time')
                cache_semantic=value
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        row['speedup_native']=index[row['id'],'native']['total_ms']/row['total_ms']
        row['speedup_with_planning']=index[row['id'],'native']['modeled_plus_planning_ms']/row['modeled_plus_planning_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Audited native replay/routed bytes/pipeline:',len(rows),flush=True);return rows
def render(output,smoke):
    rows=audit(output,smoke)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for ax,split in zip(axes,['control','validation']):
        for method in ['native','wan-repair','cached','bounded','maxmin','spreadout']:
            subset=[r for r in rows if r['method']==method and r['split']==split]
            keys=sorted({(r['n'],r['wan_us'],r['mib']) for r in subset})
            values=[math.exp(statistics.mean(math.log(r['speedup_native']) for r in subset if (r['n'],r['wan_us'],r['mib'])==key)) for key in keys]
            ax.plot(range(len(keys)),values,marker='.',label=method)
        ax.set_xticks(range(len(keys)),[f'n{n}\n{w}us\n{b}MiB' for n,w,b in keys],fontsize=6);ax.axhline(1,color='gray',ls='--');ax.set_title(split);ax.set_ylabel('Speedup vs native FAST');ax.grid(alpha=.2);ax.legend(fontsize=7)
    fig.savefig(output/'speedup.png',dpi=170);plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4),layout='constrained')
    for method in ['reference','cached','bounded','maxmin']:
        subset=[r for r in rows if r['method']==method]
        ns=sorted({r['n'] for r in subset});ax.plot(ns,[statistics.median(r['extra_planner_us']/1000 for r in subset if r['n']==n) for n in ns],marker='o',label=method)
    ax.set_xlabel('Servers');ax.set_ylabel('Extra planning elapsed time (ms), median');ax.set_title('Native host diagnostic; not GPU timing');ax.grid(alpha=.2);ax.legend();fig.savefig(output/'planning.png',dpi=170);plt.close(fig)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['prepare','run','audit','render'],required=True);parser.add_argument('--output',type=Path);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12]+('-smoke' if args.smoke else ''))).resolve()
    globals()[args.phase](output,args.smoke)
if __name__=='__main__':main()
