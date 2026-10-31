"""Native timing characterization; never changes frozen paper reproduction."""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
import reproduce as core
MODELS=['spread_out','full_mesh','two_ring','hybrid_cube']
VARIANTS=['pipeline1','pipeline2',*MODELS]

def build(output):
    output=Path(output).resolve();core.build(output/'native')
    sim=output/'native/upstream/simulation'
    include=['-I'+str(sim),'-I'+str(sim/'algorithm'),'-I'+str(sim/'scheduler'),'-I'+str(ROOT)]
    global_object=output/'global.o'
    commands=[['g++',*core.FLAGS,*include,'-Wall','-Wextra','-Wmaybe-uninitialized','-c',str(sim/'scheduler/global.cpp'),'-o',str(global_object)],
        ['g++',*core.FLAGS,*include,*[str(sim/p) for p in ['algorithm/matrix.cpp','algorithm/all2all.cpp','scheduler/local.cpp']],
         str(global_object),str(ROOT/'link_profile.cc'),str(HERE/'audit.cc'),str(output/'native/native-config.o'),'-lpthread','-o',str(output/'audit')]]
    warnings=[]
    for cmd in commands:
        done=subprocess.run(cmd,capture_output=True,text=True);warnings.append(done.stderr)
        if done.returncode:raise RuntimeError(done.stderr)
    (output/'compiler-warnings.txt').write_text('\n'.join(warnings))
    core.save(output/'audit-build.json',dict(commands=commands,binary_sha256=core.sha(output/'audit'),native_build_sha256=core.sha(output/'native/build.json')))
    return output/'audit'

def local(binary,model,data,alpha,beta,mbpu):
    done=subprocess.run([str(binary),'local',model,str(alpha),str(beta),str(mbpu)],input=' '.join(map(str,data)),text=True,capture_output=True,timeout=30)
    if done.returncode:raise RuntimeError(done.stderr)
    return json.loads(done.stdout)['time_us']

def endpoint_bound(data,beta,mbpu):
    return max(max(sum(data[i*8+j] for j in range(8) if i!=j) for i in range(8)),
               max(sum(data[i*8+j] for i in range(8) if i!=j) for j in range(8)))*beta*mbpu

def fixtures():
    result=[dict(name='empty',data=[0]*64),dict(name='dense100',data=[0 if i==j else 100 for i in range(8) for j in range(8)]),
        dict(name='fanout100',data=[100 if i==0 and j else 0 for i in range(8) for j in range(8)]),
        dict(name='self100',data=[100 if i==j else 0 for i in range(8) for j in range(8)])]
    for i in range(8):
        for j in range(8):
            if i!=j:
                d=[0]*64;d[i*8+j]=100;result.append(dict(name=f'edge-{i}-{j}',data=d))
    return result

def run(study_root):
    study_root=Path(study_root).resolve();study=core.checked(study_root,False)
    selected=[c for c in study['cases'] if c['group']=='scale' and c['n'] in [2,4,8,16,32,40]]
    spec=dict(main_study_sha256=core.sha(study_root/'study.json'),source={p.name:core.sha(p) for p in HERE.glob('*') if p.suffix in ['.py','.cc']},
        cases=selected,models=MODELS,variants=VARIANTS,fixtures=fixtures(),rates=dict(out_Gbps=400,in_GBps=450,alpha_us=.1,beta_us_MB=8000/3600,MBpu=1),
        warning='pipeline1 reads uninitialized intra1: diagnostic only, not a valid baseline; equal beta does NOT equal equal aggregate GPU capacity')
    identity=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:12]
    output=HERE/'results'/('run-'+identity);output.mkdir(parents=True,exist_ok=True)
    binary=build(output);core.save(output/'registration.json',spec)
    local_rows=[]
    for fixture in spec['fixtures']:
        for model in MODELS:
            time=local(binary,model,fixture['data'],.1,8000/3600,1)
            doubled=local(binary,model,[2*x for x in fixture['data']],.1,8000/3600,1)
            if doubled<time-1e-8:raise ValueError('native time decreases when bytes double')
            bound=endpoint_bound(fixture['data'],8000/3600,1)
            local_rows.append(dict(fixture=fixture['name'],model=model,time_us=time,doubled_us=doubled,
                endpoint_bound_us=bound,below_same_aggregate_GPU_bound=time+1e-8<bound))
    core.save(output/'local.json',local_rows)
    global_rows=[]
    for k,c in enumerate(selected):
        original=core.audit_one(study_root,c,study,replay=False);matrix=study_root/'matrices'/f'{c["id"]}.bin'
        for variant in VARIANTS:
            args=[str(binary),'global',variant,str(c['n']),str(matrix)]
            done=subprocess.run(args,text=True,capture_output=True,timeout=180)
            row=dict(case=c['id'],gpus=c['n']*8,seed=c['seed'],variant=variant,matrix_sha256=core.sha(matrix),command=args,
                reliable=variant!='pipeline1',returncode=done.returncode)
            if done.returncode:row['error']=done.stderr
            else:
                row.update(json.loads(done.stdout))
                if variant in ['pipeline2','spread_out']:core.close(row['time_us'],original['fast_us'])
            global_rows.append(row)
        if (k+1)%20==0:print('Saved matrix inputs:',k+1,'/',len(selected),flush=True)
    core.save(output/'global.json',global_rows)
    summary=[]
    for n in sorted({r['gpus'] for r in global_rows}):
        for variant in VARIANTS:
            values=[r for r in global_rows if r['gpus']==n and r['variant']==variant and r['returncode']==0]
            summary.append(dict(gpus=n,variant=variant,successful=len(values),reliable=variant!='pipeline1',
                bandwidth_mean=statistics.mean(r['algbw_GB_s'] for r in values) if values else None,
                time_mean_us=statistics.mean(r['time_us'] for r in values) if values else None))
    core.save(output/'summary.json',summary)
    core.save(output/'digests.json',{name:core.sha(output/name) for name in ['registration.json','audit-build.json','compiler-warnings.txt','local.json','global.json','summary.json']})
    print('Timing audit complete:',len(local_rows),'local rows,',len(global_rows),'global calls;',output)
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('study',type=Path);args=parser.parse_args();run(args.study)
