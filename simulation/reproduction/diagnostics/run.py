"""Post-main-result diagnosis, never substitutes for the frozen main study."""
import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import reproduce as core

def main():
    study_root=Path(sys.argv[1]).resolve();study=core.checked(study_root,False)
    output=ROOT/'diagnostics/results'/('main-'+study['identity']['digest'][:12]);output.mkdir(parents=True,exist_ok=True)
    sim=study_root/'upstream/simulation';binary=output/'variants'
    variants=['pipeline2','pipeline2_scaling','pipeline3_spread','pipeline3_mesh']
    cases=[c for c in study['cases'] if c['group']=='scale' and c['n'] in [2,4,8,16,32,40] and c['seed']<=280003]
    cmd=['g++',*core.FLAGS,'-I'+str(sim),'-I'+str(sim/'algorithm'),'-I'+str(sim/'scheduler'),'-I'+str(ROOT),
         *[str(sim/p) for p in ['algorithm/matrix.cpp','algorithm/all2all.cpp','scheduler/local.cpp','scheduler/global.cpp']],
         str(ROOT/'link_profile.cc'),str(ROOT/'diagnostics/variant.cc'),str(study_root/'native-config.o'),'-lpthread','-o',str(binary)]
    subprocess.run(cmd,check=True,capture_output=True)
    record=dict(status='post-main-result diagnostic; not preregistered main experiment',
        main_study_sha256=core.sha(study_root/'study.json'),driver_sha256=core.sha(ROOT/'diagnostics/variant.cc'),
        script_sha256=core.sha(Path(__file__)),binary_sha256=core.sha(binary),command=cmd,cases=cases,variants=variants)
    registration=output/'registration.json'
    if registration.exists() and json.loads(registration.read_text())!=record:raise ValueError('diagnostic registration changed')
    core.save(registration,record)  # fixed before observing any variant output
    rows=[]
    for c in cases:
        original=core.audit_one(study_root,c,study,replay=False)
        matrix=study_root/'matrices'/f'{c["id"]}.bin';results={}
        for variant in variants:
            args=[str(binary),str(c['n']),str(c['g']),str(c['mbpu']),variant,str(matrix)]
            value=json.loads(subprocess.check_output(args,text=True,timeout=180));results[variant]=value
            rows.append(dict(case=c['id'],gpus=c['n']*c['g'],seed=c['seed'],variant=variant,matrix_sha256=core.sha(matrix),command=args,**value))
        core.close(results['pipeline2']['fast_us'],original['fast_us'])
        core.close(results['pipeline3_spread']['fast_us'],original['fast_us'])
    core.save(output/'results.json',rows)
    core.save(output/'result_digest.json',dict(registration_sha256=core.sha(registration),results_sha256=core.sha(output/'results.json')))
    print('Diagnostic variants complete:',len(rows),output)

if __name__=='__main__':main()
