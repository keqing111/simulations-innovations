#!/usr/bin/env python3
"""Native CPU FAST only: fixed topology model, no SimAI or packet simulator."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent
SIM=ROOT.parent
METHODS=['native','wan-repair','link-cost','link-cost-wan','maxmin','spreadout']
FLAGS=['-std=c++11','-O2','-DNOLOG']
SOURCES=['topology/main.cc','topology/model.cpp','topology/matching.cpp','algorithm/matrix.cpp',
         'algorithm/all2all.cpp','scheduler/config.cpp','scheduler/local.cpp','scheduler/global.cpp','test.cpp']
TIMING={'wall_us','native_decompose_us','extra_planner_us'}


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    return sorted({SIM/'define.h',SIM/'test.h',SIM/'test.cpp',
        *list((SIM/'algorithm').glob('*.cpp')),*list((SIM/'algorithm').glob('*.h')),
        *list((SIM/'scheduler').glob('*.cpp')),*list((SIM/'scheduler').glob('*.h')),
        *list(ROOT.glob('*.cc')),*list(ROOT.glob('*.h')),*list(ROOT.glob('*.cpp')),
        *list(ROOT.glob('*.py')),*list((ROOT/'tests').glob('*.py')),*list((ROOT/'tests').glob('*.cc')),ROOT/'Makefile'})


def identity():
    files={str(p.relative_to(SIM)):sha(p) for p in source_files()}
    compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0]
    record=dict(files=files,compiler=compiler,flags=FLAGS)
    record['digest']=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
    return record


def specs(smoke=False):
    cases=[]
    for n in ([6] if smoke else [6,12]):
        for mib in ([1] if smoke else [16,256]):
            for wan in ([50] if smoke else [50,5000]):
                for kind,seed in ([('zipf',701)] if smoke else [('balanced',0),*[('zipf',s) for s in [701,709,801,809]]]):
                    cases.append(dict(id=f'n{n}-g{1 if smoke else 4}-{kind}-s{seed}-{mib}MiB-w{wan}',
                        n=n,g=1 if smoke else 4,mib=mib,wan_us=wan,kind=kind,seed=seed,
                        split='smoke' if smoke else 'control' if not seed else 'validation' if seed in [801,809] else 'development'))
    return cases


def matrix(case,binary):
    r=case['n']*case['g'];target=r*case['mib']*1024**2
    if case['kind']=='balanced':return [0 if i==j else target//(r*(r-1)) for i in range(r) for j in range(r)]
    weights=json.loads(subprocess.check_output([str(binary),'--zipf',str(case['seed']),str(r*r)]))
    for i in range(r):weights[i*r+i]=0
    total=sum(weights);result=[v*target//total for v in weights]
    order=sorted(range(r*r),key=lambda k:-(weights[k]*target%total))
    for k in order[:target-sum(result)]:result[k]+=1
    return result


def invoke(binary,case,method,data):
    return json.loads(subprocess.check_output([str(binary),str(case['n']),str(case['g']),method,str(case['wan_us'])],
        input=' '.join(map(str,data)).encode(),timeout=120))


def prepare(output,smoke):
    ident=identity();path=output/'study.json';cases=specs(smoke)
    if path.exists():
        old=json.loads(path.read_text())
        if old['identity']!=ident or old['cases']!=cases:raise ValueError('source/grid changed; use new output')
        return old
    output.mkdir(parents=True,exist_ok=True);binary=output/'native_topology'
    cmd=['g++',*FLAGS,'-I'+str(SIM),'-I'+str(SIM/'algorithm'),'-I'+str(SIM/'scheduler'),'-I'+str(ROOT),
         *[str(SIM/s) for s in SOURCES],'-o',str(binary)]
    subprocess.run(cmd,check=True)
    archive=output/'source.tar.gz'
    with tarfile.open(archive,'w:gz') as stream:
        for p in source_files():stream.add(p,arcname=str(p.relative_to(SIM)))
        for name in ['oracle.json','prechange-native-source.tar.gz']:
            stream.add(ROOT/'results'/name,arcname='topology/results/'+name)
    manifests={}
    for case in cases:
        values=matrix(case,binary);path=output/'matrices'/f'{case["id"]}.json';save(path,values)
        manifests[case['id']]=dict(matrix_sha256=sha(path),total_bytes=sum(values))
    study=dict(identity=ident,cases=cases,methods=METHODS,smoke=smoke,
        binary_sha256=sha(binary),archive_sha256=sha(archive),build_command=cmd,inputs=manifests)
    save(output/'study.json',study);print('Prepared native inputs',len(cases),flush=True);return study


def checked_inputs(output,smoke):
    study=json.loads((output/'study.json').read_text())
    if study['identity']!=identity() or study['cases']!=specs(smoke) or study['methods']!=METHODS or study['smoke']!=smoke:
        raise ValueError('registered source/grid mismatch')
    binary=output/'native_topology'
    if sha(binary)!=study['binary_sha256'] or sha(output/'source.tar.gz')!=study['archive_sha256']:raise ValueError('binary/archive changed')
    for case in study['cases']:
        path=output/'matrices'/f'{case["id"]}.json'
        if sha(path)!=study['inputs'][case['id']]['matrix_sha256'] or json.loads(path.read_text())!=matrix(case,binary):
            raise ValueError('registered matrix changed')
    return study


def semantic(raw):return {k:v for k,v in raw.items() if k not in TIMING}


def close(a,b):
    if not math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-7):raise ValueError(f'cost mismatch {a} != {b}')


def reconstruct(case,method,data,raw):
    n,g=case['n'],case['g'];r=n*g;dc=[min(2,s*3//n) for s in range(n)]
    if raw['mode']!=method or not raw['payload_verified']:raise ValueError('algorithm/payload identity mismatch')
    delivered=[[0]*n for _ in range(n)];totals={};costs=[];wan_stages=0
    for k,stage in enumerate(raw['stages']):
        count=r if method=='spreadout' else n
        if sorted(stage['map'])!=list(range(count)) or len(stage['payload'])!=(r if method=='spreadout' else n*g):raise ValueError('invalid stage shape/permutation')
        if method!='spreadout' and stage['freq']<=0:raise ValueError('nonpositive coefficient')
        links={};bandwidths={};alpha=0;local=0
        def add(key,volume,bw):links[key]=links.get(key,0)+volume;bandwidths[key]=bw
        for s,d in enumerate(stage['map']):
            if method=='spreadout':
                if d!=(s+k)%r or stage['payload'][s]!=data[s*r+d]:raise ValueError('SpreadOut cyclic schedule mismatch')
                volume=stage['payload'][s];src,dst=s//g,d//g
                if volume and src==dst:local=max(local,.1+volume*2.3e-6)
            else:
                volume=sum(stage['payload'][s*g:(s+1)*g]);src,dst=s,d
                if any(v<0 or v>stage['freq'] for v in stage['payload'][s*g:(s+1)*g]):raise ValueError('invalid GPU quota')
            if src==dst or not volume:continue
            delivered[src][dst]+=volume
            add(f'server-out:{src}',volume,400);add(f'server-in:{dst}',volume,400)
            if dc[src]!=dc[dst]:
                add(f'gateway-out:{dc[src]}',volume,400);add(f'gateway-in:{dc[dst]}',volume,400)
                add(f'wan:{dc[src]}:{dc[dst]}',volume,100);alpha=max(alpha,case['wan_us']+2.8)
            else:alpha=max(alpha,1.4)
        serial=max((v*.008/bandwidths[key] for key,v in links.items()),default=0)
        if stage['links']!=links or stage['bandwidths']!=bandwidths:raise ValueError('actual path/resource load mismatch')
        close(stage['alpha_us'],alpha);close(stage['serial_us'],serial);close(stage['communication_us'],alpha+serial)
        if method=='spreadout':close(stage['local_us'],local)
        costs.append(max(alpha+serial,local) if method=='spreadout' else alpha+serial)
        wan_stages+=int(any(key.startswith('wan:') for key in links))
        for key,v in links.items():totals[key]=totals.get(key,0)+v
    for s in range(n):
        for d in range(n):
            if s!=d and delivered[s][d]!=sum(data[(s*g+i)*r+d*g+j] for i in range(g) for j in range(g)):
                raise ValueError('server-pair payload mismatch')
    inter=sum(sum(row) for row in delivered)
    if raw['inter_bytes']!=inter:raise ValueError('inter byte total mismatch')
    if method=='spreadout':total=sum(costs)
    else:
        if len(raw['dispatch_us'])!=len(costs):raise ValueError('missing native dispatch phase')
        if costs:
            total=raw['balance_us']+max(raw['intrinsic_us'],costs[0])+sum(max(costs[k],raw['dispatch_us'][k-1]) for k in range(1,len(costs)))+raw['dispatch_us'][-1]
        else:total=raw['balance_us']+raw['intrinsic_us']
    close(raw['total_us'],total)
    if any(not math.isfinite(raw[k]) or raw[k]<0 for k in TIMING):raise ValueError('invalid CPU timing')
    maxwan=max((v for k,v in totals.items() if k.startswith('wan:')),default=0)
    return dict(**case,method=method,total_ms=total/1000,stages=len(costs),wan_stages=wan_stages,
        alpha_ms=sum(s['alpha_us'] for s in raw['stages'])/1000,
        serial_ms=sum(s['serial_us'] for s in raw['stages'])/1000,
        max_wan_payload_bytes=maxwan,model_wan_util=(maxwan*.008/100/total if total else 0),
        decomposition_us=raw['native_decompose_us']+raw['extra_planner_us'],extra_planner_us=raw['extra_planner_us'],
        changed_matches=raw['changed_matches'],repairs=raw['repairs'],inter_bytes=inter),totals


def run(output,smoke):
    study=checked_inputs(output,smoke);binary=output/'native_topology'
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text())
        for method in METHODS:
            path=output/'raw'/f'{case["id"]}-{method}.json'
            if path.exists():
                raw=json.loads(path.read_text())
                if semantic(raw)!=semantic(invoke(binary,case,method,data)):raise ValueError('resume differs from native replay')
            else:raw=invoke(binary,case,method,data);save(path,raw)
            reconstruct(case,method,data,raw)
        print(case['id'],'6 methods complete',flush=True)
    return audit(output,smoke)


def audit(output,smoke):
    study=checked_inputs(output,smoke);rows=[]
    expected={f'{c["id"]}-{m}.json' for c in study['cases'] for m in METHODS}
    if {p.name for p in (output/'raw').glob('*.json')}!=expected:raise ValueError('incomplete/unregistered raw results')
    for case in study['cases']:
        data=json.loads((output/'matrices'/f'{case["id"]}.json').read_text());reference=None
        for method in METHODS:
            raw=json.loads((output/'raw'/f'{case["id"]}-{method}.json').read_text())
            if semantic(raw)!=semantic(invoke(output/'native_topology',case,method,data)):raise ValueError('raw result differs from actual native replay')
            row,totals=reconstruct(case,method,data,raw)
            if reference is not None and totals!=reference:raise ValueError('methods changed routed user bytes')
            reference=totals;rows.append(row)
    index={(r['id'],r['method']):r for r in rows}
    for row in rows:
        row['speedup_native']=index[row['id'],'native']['total_ms']/row['total_ms']
        row['speedup_spreadout']=index[row['id'],'spreadout']['total_ms']/row['total_ms']
    save(output/'comparison.json',rows)
    with (output/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print('Native replay/path/pipeline audited:',len(rows),'results',flush=True);return rows


def render(output,smoke):
    rows=audit(output,smoke)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    for ax,split in zip(axes,['control','validation']):
        for m in METHODS:
            subset=[r for r in rows if r['method']==m and r['split']==split]
            keys=sorted({(r['n'],r['wan_us'],r['mib']) for r in subset})
            values=[math.exp(statistics.mean(math.log(r['speedup_native']) for r in subset if (r['n'],r['wan_us'],r['mib'])==k)) for k in keys]
            ax.plot(range(len(keys)),values,marker='o',label=m)
        ax.set_xticks(range(len(keys)),[f'n{n}\n{w}us\n{b}MiB' for n,w,b in keys],fontsize=7)
        ax.axhline(1,color='gray',ls='--');ax.set_ylabel('Speedup vs native FAST');ax.set_title(split);ax.grid(alpha=.2);ax.legend(fontsize=7)
    fig.savefig(output/'speedup.png',dpi=160);plt.close(fig)
    subset=[r for r in rows if r['split']=='validation' and r['n']==12 and r['wan_us']==5000 and r['mib']==256]
    fig,ax=plt.subplots(figsize=(9,4),layout='constrained')
    for j,m in enumerate(METHODS):
        values=sorted([r for r in subset if r['method']==m],key=lambda r:r['seed'])
        ax.bar([i+(j-2.5)*.12 for i in range(len(values))],[r['total_ms'] for r in values],.12,label=m)
    ax.set_xticks([0,1],['Zipf seed801','Zipf seed809']);ax.set_ylabel('Native model completion (ms)');ax.set_title('12 servers, WAN5ms,256MiB/GPU');ax.legend(fontsize=8);ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'completion.png',dpi=160);plt.close(fig)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['prepare','run','audit','render'],required=True)
    parser.add_argument('--output',type=Path);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    output=(args.output or ROOT/'results'/('run-'+identity()['digest'][:12])).resolve()
    globals()[args.phase](output,args.smoke)


if __name__=='__main__':main()
