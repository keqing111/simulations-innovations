"""Verify the completed cohort and package compact, synthetic-only replay evidence."""
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile

root=Path.cwd();out=root/'astra-sim-alibabacloud/experiments/alltoall/results/heterogeneous-expanded-20261115'
study=json.loads((out/'study.json').read_text());assert study['complete']
assert len(study['runs'])==123
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert all(sha(Path(p))==v for p,v in study['source_hashes'].items())
rows=study['runs'];cases={c['name']:c for c in study['cases']}
success=[r for r in rows if r['success']]
assert all(r['input_contract']['execution_fingerprint_verified'] for r in success)
applied=0;retries=0;files=set();raw_hashes={}
witnesses={'leaf-route-asymmetric-gpu128-balanced-s4101-m4-rho0',
           'leaf-route-asymmetric-gpu128-zipf12-s4101-m4-rho0'}
for r in rows:
    attempt=Path(r['attempt_dir']);retries+=len(list(attempt.parent.glob('attempt-*')))-1
    if r['success']:
        assert r['delivered_bytes']==cases[r['case']]['logical_bytes']
        assert r['background_horizon_valid']
        expected={(int(u),int(d)):int(v) for u,d,v in csv.reader((attempt/'routes.csv').open())}
        with (attempt/'routes.csv.applied.csv').open() as f:
            for x in csv.reader(f):
                node,dst,nxt=map(int,x)
                assert expected[node,dst]==nxt
                applied+=1
    if cases[r['case']]['model']:continue
    raw_names=['run_manifest.json','summary.json','fct.txt','native.csv.events.csv','original_source_delivery.csv']
    for name in raw_names:
        p=attempt/name
        if p.exists():raw_hashes[str(p.relative_to(out))]=sha(p)
    for name in ['v2_diagnostics.json','stage_skew.csv','progress_quantiles.csv',
                 'source_threshold_times.csv','pfc.txt']+(raw_names if r['case'] in witnesses else []):
        p=attempt/name
        if p.exists():files.add(p)
assert retries==0
for c in cases.values():
    if c['model']:continue
    source=Path(c['source'])
    files.update(p for p in source.rglob('*') if p.is_file())
    replay=out/'replays'/c['name']/c['name']
    files.update(replay.glob('*.plan.json'))
(out/'raw_evidence_hashes.json').write_text(json.dumps(raw_hashes,indent=2)+'\n')
for name in ['study.json','source_hashes.json','summary.json','main_summary.json','paired.csv','raw_evidence_hashes.json',
             'all_runs.csv','expanded_summary.json']:
    files.add(out/name)
members={str(p.relative_to(out)):sha(p) for p in sorted(files)}
archive=out/'compact-evidence.tar.gz'
with tarfile.open(archive,'w:gz',compresslevel=6) as tar:
    for p in sorted(files):tar.add(p,arcname=str(p.relative_to(out)),recursive=False)
    data=(json.dumps(members,indent=2)+'\n').encode();info=tarfile.TarInfo('evidence_members.json');info.size=len(data);tar.addfile(info,io.BytesIO(data))
with tarfile.open(archive,'r:gz') as tar:
    for name,digest in members.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==digest
old=root/'astra-sim-alibabacloud/experiments/alltoall/results/resource-heldout-20261115/verification.json'
prior=json.loads(old.read_text())
log=(root/'.superpowers/sdd/2026-11-15-expanded-heterogeneous/full-suite-final.log').read_text()
failures=[line.split(' ',1)[1] for line in log.splitlines() if line.startswith(('FAILED ','ERROR '))]
prior_names=prior['inherited_issues']
normalize=lambda name:'::'.join(name.split('::')[-2:])
assert {normalize(name) for name in failures}=={normalize(name) for name in prior_names}
verification=dict(planned_conditions=41,planned_replays=123,
    actual_backend_runs=study['new_attempts'],status_counts=dict(__import__('collections').Counter(r['status'] for r in rows)),
    execution_fingerprints_verified=len(success),all_successful_original_pair_delivery_verified=True,
    unchanged_registered_source_hashes=True,backend_applied_routes_verified=applied,failed_run_retries=retries,
    paired=dict(fast=study['summary']['all']['fast-simai']['paired'],spreadout=study['summary']['all']['spreadout']['paired']),
    tests=dict(scoped_passed=95,scoped_skipped=6,full_passed=247,full_skipped=11,
               full_failed=10,full_errors=3,full_subtests_passed=128,
               inherited_13_issue_set_unchanged=True,inherited_issue_names=failures),
    independent_review=dict(critical=0,important=0,path_projections=53056,old_pilot_run_records_unchanged=72,
        minor_deferred='plot128G means128GPUs; explicit report clarification'),
    evidence=dict(file=archive.name,bytes=archive.stat().st_size,sha256=sha(archive),members=len(members)+1,
        detailed_event_witness_cases=sorted(witnesses),other_synthetic_raw_files='hash references only; full279MB bundle retained locally',
        member_hashes_verified=True,excludes='raw expert inputs, real matrices/plans/events, full packet traces, executables and credentials'),
    note='Timeouts not converted to speedups. Source-only/archive is not a fresh-clone backend build verification.')
(out/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
print(json.dumps({k:v for k,v in verification.items() if k!='tests'},indent=2))
