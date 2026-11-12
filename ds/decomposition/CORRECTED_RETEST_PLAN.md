# Corrected workload and FAST host-planner retest

> **For agentic workers:** Use superpowers:executing-plans and test-driven-development. Execute inline. No commit/push; preserve dirty changes and historical data.

**Goal:** Re-run the existing joint BvN algorithms only after repairing the workload protocol and anchoring the reference to unmodified upstream NVIDIA CPU planning code.

**Spec:** WORKLOAD_AND_BASELINE_AUDIT_20261113.md and the user's request to repair, rerun and analyze.

**Architecture:** A thin CPU harness compiles the existing NVIDIA matrix/algorithm/local/global translation units unchanged. Baseline calls the original `run_scheduler()` (always balance, no simulator quantization, descending coefficients). A replay composer calls the same original balance/restore helpers and is checked byte-for-byte against the original scheduler's volume plan. All policies share explicit analytical network/local/pipeline pricing; no claim of GPU/chunk runtime reproduction. Existing Python joint heuristics remain frozen.

## Constraints and review focus

- Native source files remain unchanged and are pinned to local upstream 0712d29 content. CUDA headers are available; unused GPU functions are linker-discarded, not stubbed.
- Native compile limits apply: <=10 servers, <=8 GPUs/server. Main panel is 3DC x2server x4GPU; 8GPU configuration is tested separately.
- Generation requires actual-min coefficient; execution replay permits any positive coefficient <= selected residual, with full final reconstruction.
- Source-origin token provenance is checked through native `channel` metadata, not just pairwise total bytes.
- Preserve same-server off-diagonal demand; own-GPU token routes are recorded as local no-network work, not silently redistributed remotely.
- Synthetic workloads are not called real traces. Balanced control, sampled edge-size Zipf, independent shared-hotspot token routing, source-affinity routing and uniform token routing are separate families.
- Top-2 sampling selects distinct experts; one expert/GPU; token vectors are 8192B. Source batch and expert placement are explicit. No forced positive balance or discarded unfavorable cases.
- Freeze workload/rules before observing results; use paired inputs/seeds and archive complete source/protocol fingerprints. No post-result tuning.

## Files and tasks

### 1. Native CPU reference / replay

- Create `ds/decomposition/runtime_host.cpp`, add `runtime-host-build` target to simulation/Makefile.
- Baseline invokes original `init_global_scheduler` and `run_scheduler`; extra direct original BvN invocation exports coefficients/padding and is checked against native stage maps.
- Replay uses original `balance_one_server`, `get_buffer_size`, `restore_one_server`, `schedule_this_gpu`; baseline-plan replay must match native scheduling_result_t bytes.
- Export balance matrices, intrinsic matrices, restore matrices, per-GPU actual payload, source-origin delivered matrix, construction and execution plans, and original CPU profiler result.
- Fix existing optional replay capacity check to <=minimum; keep generation tests strict.
- Tests first: missing host build, native plan/replay equality, reordered legal plan accepted, overflowing/incomplete plans rejected, full original-source delivery, profile recurrence, empty demand and g=8.

### 2. Workload protocol and independent audits

- Create `ds/decomposition/corrected_study.py`, tests `simulation/tests/test_corrected_study.py`.
- Main n=6,g=4; seeds1101/1102/1103; token batches480/3840, Top-K=2, one expert/GPU, vector8192B. Strict uniform control has40/320 assignments per expert including self, with self removed only from communication matrix and retained in metadata.
- Edge-size Zipf exponent0.8 truncated at10000, independently sampled cells, normalized only globally to balanced communication budget; token interpretation not claimed for that family.
- Token families: uniform probabilities, shared receiver Zipf0.9, and source-domain mixture (0.5 global +0.5 domain, four domains), independently sampled per source with distinct Top-2 destinations. Retain local-server traffic.
- Report source/destination marginals, tile source imbalance, local fraction, self routes, repeated rows, balance/intrinsic/restore times. Fixed source token count is allowed, copied destination counts are not.
- Compare analytical evaluator in endpoint/no-startup mode to native CPU profiler. Main pricing uses existing H100 alpha0.1us/beta2.3us/MB, existing three WAN profiles from spreadout CLI, shared by all methods.

### 3. Frozen paired retest and analysis

- Main panel: balanced control once +4 stochastic families x3 seeds =13 inputs per batch, two batches, three profiles =78 conditions.
- Methods: native-host descending schedule, joint-stage construction order, joint-capacity construction order, joint-capacity descending-order ablation, spreadout-like. Same native balance/restore for all BvN methods.
- Save all inputs and traces; assert exact per-original-GPU delivery, real payload and resource-byte conservation. Evaluate full recurrence, not network-only ranking. Report Python planning cost separately.
- Run full suite; perform fresh read-only review; final new report includes all negatives, generator statistics and old/new claim boundaries. Do not merge old synthetic panel with this panel.

## Ledger

- Ruling: keep current isolated research branch and additive harness files; no upstream edits or destructive worktree operations.
- Ruling: reference is the project CPU host volume plan, not a multi-GPU runtime measurement. Analytical timing is separately validated against native profiler then extended with common WAN capacity model.
- Ruling: no new algorithm tuning in this retest. Failure or lack of speedup is a result, not a reason to change the workload.
- [x] Native reference, replay and tests (originally seven RED tests; now ten corrected-study tests GREEN).
- [x] Workload generator/audits and independent pricing tests.
- [x] Frozen paired experiments: 78 conditions/390 outputs; final byte-unit run `corrected-byte-host-20261113`; analysis in `CORRECTED_RETEST_REPORT.md`.

### Execution receipts

- Native/replay interface: uint64 native matrix versus uint native coefficient update; bounded adapter to total input units <= UINT_MAX before native calls. Actual experimental inputs are far below that bound. This is an adapter restriction, not an upstream algorithm fix.
- Generation versus execution: the new descending-replay regression failed with `replay coefficient is not actual residual minimum`, then passed after the capacity-only replay check; strict greedy generation remains unchanged.
- Fresh independent read-only review: native source identity, all26 regenerated inputs, original390 outputs, all78 native profiler values and resource/time conservation independently checked; no remaining scoped correctness blocker. Unsafe n2/g8 all-cross-edge1e9 reproduced SIGSEGV, then RED→GREEN after input guard.
- Reporting ruling: review described Python timing as wall time, but direct source inspection confirms `time.process_time()`. Report both costs as process CPU with different scopes/languages, not comparable end-to-end planner times. Spreadout planner_us=0 is an unmeasured placeholder, not a cost claim.
- Scientific ruling: baseline is FAST-Host (unmodified project CPU volume planner), not CUDA/chunk runtime. All stochastic cases trigger balance; no real-trace claim. Old66-condition evidence remains archived and explicitly superseded for workload/baseline claims.
- Initial panel retained at `corrected-host-20261113`; hardening changed source/binary fingerprints, so the entire panel was rerun to a distinct final directory. No algorithm/traffic parameters were tuned between runs; analytical results are identical (CPU samples vary).
- Final unit audit: native integer ceil(balance_sum/g) does not commute with scaling vector counts by8192. Small-case native physical estimates changed by up to2.7%, so token-grid runs above are intermediate evidence, NOT final reference. Added RED→GREEN byte-before-initialization parity test and `host_vectors()`; all native/candidate coefficients and payloads now use bytes, while input archives retain vector counts. Third full frozen run `corrected-byte-host-20261113` supersedes both preliminary directories. Same26 demand matrices and frozen joint algorithms, no tuned seeds/formulas. Capacity wins78/78, GM1.249x; stage76/78, GM1.239x; capacity-descending77/78, GM1.234x. Source/binary hashes frozen anew.
- Final verification: `make -C simulation check bvn-check runtime-host-build bvn-decompose-build` exit0; 60 Python tests, topology schedule tests, 5443 BvN checks pass. Full log archived in final result directory. Analysis revalidates every case, actual-min generation for unsorted candidates, per-stage predicted/native payload and time agreement, and resource/provenance/padded-matrix conservation. No commit/push or upstream source edits.
