# 原生FAST论文复现计划 — 2026-10-28

User requests paper reproduction before further optimization. Execute inline with planning/executing-plans/TDD and one fresh final reviewer. Keep all existing dirty code/results; no Git writes, no SimAI/ns3, no topology optimization.

## Design and alternatives

Authoritative paper: NSDI2026 FAST, Figure17/section5.4. Public source commit0712d29fb13a09962425c8a57b951b91d8fc33ba equals remote main (verified read-only2026-10-28). Archive pristine simulation tree directly from git object, never import modified simulation/topology files. New independent harness calls original LocalScheduler,GlobalScheduler::pipeline2,spread_out_baseline. Alternative continuing from modified hooks would not establish pristine reproduction; GPU replication is impossible with the local singleRTX5060, not fourH200/MI300X servers.

Three labels: pristine code-default diagnostics; paper-disclosed-parameter simulation; unavailable physical reproduction. Paper specifies 400Gbps perGPU scale-out,450GB/s scale-up,random50MB perGPU-pair forFig17a; original tables use beta22/2.3us perMB and original benchmark defaults to5000MB/pair and ETHER100. We must not call these identical. Profile wrappers override only link factories via separately compiled symbol rename, preserving original matching/local/pipeline source. Use safe enum constructor; original explicit-link constructor leaves inter/intra enums uninitialized. Override wrapper is declared parameter adapter, not unmodified executable claim. Alpha0.7/0.1us from publiccode because paper gives no exact values; RNGseeds unknown, use20predeclared independentseeds. No fitting/tuning to paper curves.

Metric bytes: matrixuint units from original rand()%10000 (skipdiagonal),MBpu=.01 makes pairmean49.995decimalMB; all physicalbyte totals/ideal bounds calculated in64bit outside pristine core. Algos unchanged. FASTraw predictedµs; pipelineCPU andfullCPU separately measured; FASTall adapter=raw+pipelineCPU(publicbenchscope), fullCPUalternative shown notselectedtofit. Timing on ourCPU is not source-machine reproduction.

Main frozen grid: Fig17a2..40servers step2,8GPU/server,20seeds280001..280020=400runs; Fig17b4servers×8GPU,ratio1,2,...10,12,13,14,15,16,17,18,19,20,22.5,25,27.5,30,35,40,50,60,70,20seeds=560runs. Ratio-only plot omits absolute rate/workload details: choose400Gbps scale-out and50MB/pair as explicitly labelled conditional extension, not exact replication. Code-default diagnostics n2/4/8g8,3seeds=9,original ETHER100/H100 andMBpu1. One seed-specificmatrix per run stored, raw/timing/params/source/binary hashes preserved. No alternative scheduler optimization.

Paper reference: extract vector paths from archived officialUSENIXPDF Figure17; reference labelled digitizednotauthorrawdata. OfficialPDF download succeeded through publicdownloadquery; arxivcopy not used as finalreference. SavePDFhash/calibration/colors/coordinates; clipped idealfirstpoint not fabricated/interpolated. Common5%meanrelativeerror comparison threshold predeclared, reportallerrors; it is our numericalagreementcriterion, not proof authorseeds/models identical. Each conclusion names missingalpha/seed/machine assumptions.

## Review Focus

- Staged sources byte-for-byte from pinnedgitblob, not dirty files; wrapper compiler-only configuration changes disclosed.
- MB/GB vsMiB/Gbps units,perGPU-pair vsperGPUtotal; no bandwidth-factor8 mistakes or totaloverflow.
- PDFcurve extraction/calibration/clipping cannot fabricate points; each curve bound toactualPDFhash.
- Rawpredictedtime vsCPUtime distinct; no choice of timing scope tofit; repeats/seeds not confidence over hardware variability.
- Failed/incomplete/unregisteredruns,stale source/PDF/binary,changedparams/matrices reject; standalonearchive rebuild possible.

## Task1 — pristine harness and numerical tests

Files: reproduce.py,driver.cc,link_profile.h/.cc,tests/test_reproduction.py,results/run-<fingerprint>/upstream/.
Interfaces: prepare snapshots exactpinnedgitandbuild; C++ CLI n,g,seed,profile,MBpu,outGbps,inGbps,alphaScale,matrixIn,matrixOut → JSON originalpipeline/spreadout/ideal/params/cputime/stats.

- [x] RED missingdriver/twogpu100MBhandcost,originalparameterfactories andpayloadunits; implement nativeadapter.
- [x] Archive/verifypristinesourcefiles,compilecodefactoriesrenamedonly; compare native-default results with originalprechangeoracle and rejectinvalidinput/range. Additionalpredeclared alpha0/10 sensitivity atn4/40 with3seeds gives12diagnostics,981totalcases;nonechosenafterobservingplots.

## Task2 — independent paper reference and fixed runs

Files: reference.py,tests/test_reference.py,tests/test_reproduction.py.
Interfaces: decode_line/extract(pdf) returnscalibratedcurveswithrawcoordinates andmetadata; CLIprepare/run/audit/render.

- [x] RED handcalibratedvector/clippedpoint/PDFchanged tests; implement reference extraction without manual y-value fitting.
- [x] Freeze960main-paper-profile+9code+12alpha runs and20seeds beforedata; source-addressedmatrix/rawoutputs. Testreal2runsmoke andtamperrejection.
- [x] Execute/auditallregistration. IndependentPythonGPU-pair/ideal/algbwstats and freshnative semantic replay (timingexcluded) verify every rawresult.

## Task3 — compare and report

Files: REPORT.md,figures andcomparisonCSV inresultdir.

- [x] Overlay paper/reference vs repeatednative means; showrelativeerrors andtiminglimits,configurationgapstable,realhardwareunavailablestatus.
- [x] Onefreshreadonlyreview;ImportantfixesRED→GREEN;fullnative/reproductiontests,archivecheckandfinalrawaudit. Stopoptimizationuntilbaselineagreementassessed. Preserveallwork,noGitintegration.

Outcome: execution/comparison tasks complete, not exact paper replication success. Figure17a FAST raw MARE19.095%, versus SpreadOut.357%/ideal.193%; Figure17b FASTraw.563% under declared conditional parameters. Physical cluster unavailable. Additional post-result diagnostic (72 native calls, separate diagnostics directory) tests existing entry points/scaling on saved inputs; not main-grid replacement, not new optimization. Pipeline3 full-mesh alters resource assumptions and does not uniformly match small/large scales. Baseline mismatch remains unresolved; preserve it rather than tuning configurations after observation.
