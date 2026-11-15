"""Audit completed paper evidence without running a simulator."""
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

root=Path.cwd();sys.path.insert(0,str(root/'research/resource_a2av'));import experiment as e
out=e.EXPERIMENTS/'results/paper-fixed-routing-20261115';study=json.loads((out/'study.json').read_text())
assert study['complete'] and study['all_success'] and len(study['runs'])==96
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
changed=[p for p,h in study['source_hashes'].items() if sha(Path(p))!=h]
assert changed==[str((root/'research/resource_a2av/paper_experiment.py').resolve())]
for p,h in study['source_hashes'].items():
    snapshot=out/Path(p).name
    assert sha(snapshot if snapshot.exists() else Path(p))==h
post=json.loads((out/'post_review_reuse_audit.json').read_text());assert post['verified_count']==36 and post['backend_replays']==0
assert sha(root/'research/resource_a2av/paper_experiment.py')==post['cache_guard_sha256']
sources={c['name']:c for c in study['cases']};files={};applied=0;retries=0
for index,row in enumerate(study['runs']):
    attempt=Path(row['attempt_dir']);assert e.audit.contract(attempt)==row['input_contract']
    assert row['delivered_bytes']==sources[row['case']]['logical_bytes']
    retries+=len(list(attempt.parent.glob('attempt-*')))-1
    routes={(int(a),int(b)):int(c) for a,b,c in csv.reader((attempt/'routes.csv').open())}
    for a,b,c in csv.reader((attempt/'routes.csv.applied.csv').open()):assert routes[int(a),int(b)]==int(c);applied+=1
    for name in ['fct.txt','v2_diagnostics.json','stage_skew.csv','source_threshold_times.csv']:
        files[f'main/{row["case"]}/{row["method"]}/{name}']=attempt/name
    plan=attempt.parents[2]/(row['method']+'.plan.json')
    if plan.exists():files[f'main/{row["case"]}/{row["method"]}.plan.json']=plan
    if (index+1)%12==0:print('AUDITED',index+1,'/96',flush=True)
for case in study['cases']:
    source=Path(case['source'])
    for p in source.rglob('*'):
        if p.is_file():files[f'inputs/{case["name"]}/{p.relative_to(source)}']=p
controls=[]
for folder in ['paper-mechanism-20261115','paper-shared-mechanism-20261115']:
    control=e.EXPERIMENTS/'results'/folder/'control';data=json.loads((control/'study.json').read_text());controls+=data['runs']
    files[f'{folder}/study.json']=control/'study.json';files[f'{folder}/service.json']=control/'service.json'
    for row in data['runs']:
        attempt=Path(row['attempt_dir']);retries+=len(list(attempt.parent.glob('attempt-*')))-1
        if row['success']:assert e.audit.contract(attempt)==row['input_contract']
        for name in ['run_manifest.json','summary.json','fct.txt','native.csv','native.csv.events.csv',
                     'native_wan.csv','v2_diagnostics.json','schedule.csv','schedule_metadata.csv','schedule_manifest.json']:
            p=attempt/name
            if p.exists():files[f'{folder}/{row["method"]}/{name}']=p
        plan=attempt.parents[2]/(row['method']+'.plan.json')
        if plan.exists():files[f'{folder}/{row["method"]}.plan.json']=plan
    for p in (control/'input').rglob('*'):
        if p.is_file():files[f'{folder}/input/{p.relative_to(control/"input")}']=p
assert retries==0
assert sum(r['success'] for r in controls)==7 and len(controls)==8
shared=e.EXPERIMENTS/'results/paper-shared-mechanism-20261115/control/replay/controlled-cut'
resource=json.loads((shared/'resource-maxq.plan.json').read_text());hand=json.loads((shared/'hand-overlap.plan.json').read_text())
assert resource['permutations']==hand['permutations'] and resource['coefficients']==hand['coefficients']==[262144,262144]
for name in ['study.json','source_hashes.json','paired.csv','all_runs.csv','paper_table.csv','paper_summary.json',
             'plan_comparison.csv','first_differences.json','mechanism_summary.json','post_review_reuse_audit.json',
             'figure_analysis_snapshot.py','post_review_controller_snapshot.py']:
    files[name]=out/name
members={name:sha(p) for name,p in files.items()}
archive=out/'paired-evidence.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for name,p in sorted(files.items()):tar.add(p,arcname=name,recursive=False)
    blob=(json.dumps(members,indent=2)+'\n').encode();info=tarfile.TarInfo('members_sha256.json');info.size=len(blob);tar.addfile(info,io.BytesIO(blob))
with tarfile.open(archive) as tar:
    for name,h in members.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==h
prior=json.loads((e.EXPERIMENTS/'results/resource-heldout-20261115/verification.json').read_text())['inherited_issues']
log=(root/'.superpowers/sdd/2026-11-15-paper-fixed-routing/full-suite.log').read_text()
issues=[line.split(' ',1)[1] for line in log.splitlines() if line.startswith(('FAILED ','ERROR '))]
normalize=lambda s:'::'.join(s.split('::')[-2:]);assert set(map(normalize,issues))==set(prior)
assert not subprocess.check_output(['git','diff','c576a55','HEAD','--','astra-sim-alibabacloud/experiments/alltoall/src/simai_a2a/schedulers/resource_budget.py',
    'astra-sim-alibabacloud/experiments/alltoall/src/simai_a2a/schedulers/simai_plan.py','astra-sim-alibabacloud/experiments/alltoall/configs/ns3-leafspine.conf'])
verification=dict(main_conditions=24,main_records=96,main_success=96,new_main_attempts=60,
    reused_records=36,reused_pilot_records=32,reused_natural_mechanism_records=4,
    exact_main_contracts_recomputed=96,original_pair_delivery_verified=96,applied_routes_verified=applied,
    control_records=8,control_success=7,control_timeouts=1,control_timeout_excluded_from_speedups=True,
    unique_records_including_extra_controls=104,total_new_backend_attempts=72,failed_run_retries=retries,
    registered_source_snapshot_hashes_verified=True,post_review_controller_changes=changed,
    post_review_cached_full_schedule_regenerations=36,post_review_backend_replays=0,
    core_balance_dispatch_congestion_configuration_unchanged=True,
    actual_resource_matches_hand_full_plan=True,shared_control_q_per_gpu_bytes=262144,shared_control_stages=2,
    tests=dict(scoped_passed=106,scoped_skipped=6,full_passed=258,full_skipped=11,full_failed=10,full_errors=3,
        subtests_passed=128,inherited_issue_set_unchanged=True,inherited_issues=issues),
    evidence=dict(file=archive.name,bytes=archive.stat().st_size,sha256=sha(archive),members=len(members)+1,member_hashes_verified=True,
        scope='synthetic inputs/plans/FCT/diagnostics; detailed event and port evidence for both controls; no binary or secrets'),
    caution='Observed controlled1.8135x is exploratory, not representative of main24 neutral conditions. No fresh-clone backend build or whole-project green-test claim.')
e.dump(out/'verification.json',verification);print(json.dumps(verification,indent=2))
