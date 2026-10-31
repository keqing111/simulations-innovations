# Native FAST topology-aware study — 2026-10-27

Approved direction: native CPU simulator only; no SimAI/ns-3 or GPU-runtime changes. Execute inline using writing-plans/executing-plans/TDD; one fresh final review. Preserve existing dirty sources, no commits/push/worktree cleanup.

## Design and alternatives

Keep original C++ preprocessing, balance, dispatch, execution order and pipeline2 max-overlap. Add opt-in callbacks to observe real per-GPU transfers, replace decomposition and evaluate stage communication. Default calls remain original. New standalone C++ model and runner live here, import no SimAI code. Alternative Python replay would recreate alignment risk; a new packet/event simulator would exceed scope. Opt-in native hooks are chosen.

Routed resource model: directed GPU-lane/server/DC gateway/WAN resources, fixed full-mesh DC paths; sum actual payload per resource, stage approximation max(path alpha)+max(resource payload/bandwidth). No packet congestion or RTT/ACK addition. Preserve microsecond/decimal-MB native units. Native freq is per GPU; payload is measured before/after actual native dispatch, including GPUi→GPUi data absent from dispatch matrices. Virtual slots create no traffic. Empty communication costs zero.

Use original decomposition, WAN alternating-cycle repair, bounded one-cycle resource-cost search, combined cost+WAN repair, and known Max-Min reference. Search at most N candidate alternating cycles per stage; no permutation enumeration/global optimizer. Every extraction zeros a positive residual edge; <=N² stages. All methods share exact evaluator; no execution batching or schedule selection oracle.

Compatibility has two distinct checks: callback returning original stage formula reproduces original pipeline exactly (including padding charges); actual-payload homogeneous resource model reproduces fully-real regular fixtures. Universal equality is impossible when legacy charges virtual slots but real-payload model deliberately does not. Document this rather than hide mismatch.

Freeze grid before results: 6/12 servers,4 GPUs/server,3 DCs;16/256MiB/GPU;WAN50us/5ms;400Gbps server and gateway,100Gbps WAN,700ns per internal hop. Balanced and native truncated-inverse-CDF Zipf0.9 with development seeds701/709, held-out801/809; fixed-total bytes. 40 inputs ×6 methods =240 model runs. SpreadOut uses original cyclic rounds and same network model, original intra-link costing. No Direct. Save matrices, actual stage/GPU payload, link loads, decomposition timing, source snapshot/compiler/binary hashes and figures. No claim of real WAN utilization or hardware speedup.

## Review focus

1. Native self-match/virtual accounting differs from real payload; validate no user WAN bytes fabricated or omitted.
2. Native units and per-GPU vs server volume; hand-calculated shared-WAN and homogeneous tests.
3. Full original pipeline alignment vs custom adapter; pre-change oracle binary and original stage formula tests.
4. Every method shares evaluator and preserves preprocessing; no hidden execution changes or chosen-result filtering.
5. Malformed route/matrix/32-bit range, empty matrices, stage coverage and incomplete artifacts reject or handle explicitly.

## Task1 — opt-in native hooks and compatibility

Files: modify scheduler/global.h/.cpp,local.h/.cpp,algorithm/all2all.h/.cpp; create topology/tests/oracle.cc,tests/test_native.py,native_hooks.h.
Interfaces: pipeline2(MBpu,fn,hooks=nullptr), hooks prepare(service,balanced_gpu_rows,app),stage(stage,per_gpu_payload),phase reports; optional decompose matching callback. Pending row accessor read-only.

- [x] Compile pre-change native oracle and archive source; tests baseline native+SpreadOut on deterministic fixture grid.
- [x] RED tests for opt-in hooks; add minimal hooks, preserve default calculations, compare baseline oracle values. Native matching implementation/order unchanged; pre-change binary exposes aggregate oracle, not serialized stages.
- [x] Test exact real payload/full completion, g1/g4,padding,self-matches,zero workload; run native test suite.

## Task2 — topology model and lightweight matching

Files: topology/model.h/.cpp,matching.h/.cpp,main.cc,tests/model_test.cc,tests/test_native.py.
Interfaces: Topology.stage(payload)->Cost; Planner callback uses original positive residual support, returns legal perfect match; executable consumes matrix and topology parameters and emits JSON.

- [x] RED hand-calculated shared100Gbps WAN,full-duplex,virtual/empty,homogeneous and invalid-input tests; implement model.
- [x] RED forced cycle,matching reconstruction,finite-stage bound tests; implement bounded cycle search/Max-Min. WAN objective checks existence, not maximum cardinality; homogeneous compatibility checked on evaluator/native pipeline, not an all-policies equality promise.
- [x] Compare actual native dispatch payload with planner shadow rows and aggregate delivery per destination; verify no changed execution semantics.

## Task3 — frozen experiments and report

Files: topology/study.py,REPORT.md,tests/test_study.py,results/run-<sourcehash>/.
Interfaces: CLI prepare/run/audit/render; canonical registered cases and all6method rows, recompute costs from raw stage/link data.

- [x] RED driver/schema/tampering tests; build source-addressed driver with source archive and fixed input grid.
- [x] Execute240 native model cases; include original oracle and controlled feasible same-matrix counterexample, no seed retuning. Audit all outputs and render linear plots.
- [x] One fresh final reviewer; fix Important issues RED→GREEN; full native suite + compatibility checks. Report relative-to-native and SpreadOut, matching CPU time,negative cases and no hardware claims. Preserve branch/source state.
