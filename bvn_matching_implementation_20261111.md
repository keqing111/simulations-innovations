# Complete BvN matching implementation plan and report

> **For agentic workers:** execute the approved task inline using tests-first development; do not spawn agents, commit, or push.

**Goal:** add four complete matching policies on the same native padded integer residual, preserving FAST balance/dispatch/pipeline semantics.

**Architecture:** keep no-argument native decomposition unchanged; add a typed overload whose nonnative policies share sorted positive adjacency and the existing extraction method. GlobalScheduler only chooses this decomposition overload and exposes stats; its existing workload/pipeline accounting stays intact.

**Tech Stack:** existing C++11, g++, Make; no external assignment solver or new production files.

**Spec:** explicit user/parent task of 2026-11-11.

## Constraints and review focus

- S0 native unordered traversal and `decompose_with` remain compatible; native stuffing/scaling untouched.
- S1 maximizes feasible bottleneck by binary search on distinct positive residuals.
- S2 samples at most B feasible-value quantiles (minimum and maximum for B>=2; maximum for B=1), uses actual O(n³) Hungarian assignment, rejects forbidden edges, maximizes elimination count then actual bottleneck.
- S3 scans the same bounded samples descending, forcing selected edges incident to maximum-degree vertices to equal t; infeasibility falls back to S1.
- Every output is a perfect matching with actual positive minimum; extraction remains the existing native update.
- Check exact reconstruction, zero/sparse/scaled inputs, threshold budget, exhaustive n<=5 test-only oracle, native parity, pipeline incompatibilities and CPU/wall separation.

## Execution tasks

- [x] Write interface and 3x3 regression tests, run RED, implement public structures and typed overload.
- [x] Write bounded-threshold/Hungarian oracle tests, implement S1/S2/S3, run GREEN.
- [x] Add Global set_decomposition/getter and hook stats; test nondefault policy with pipeline2 and reject conflicting topology modes/overriding hooks.
- [x] Run focused/new/legacy regressions, document interfaces, actual complexity, CPU resolution, RED/GREEN evidence and limitations below.

## Public API (locked for experiment driver)

In `algorithm/all2all.h`: `DecompositionStrategy::{Kuhn,MaximumBottleneck,MaximumElimination,ResidualDegree}`, `DecompositionOptions` (strategy=Kuhn, threshold_budget=8), `DecompositionStepStats`, `DecompositionStats`, and `FastAll2All::decompose(const DecompositionOptions&, DecompositionStats* = nullptr)`.

Step fields: `q`, `q_scaled`, `selected_threshold`, `eliminated`, `degree_before`, `degree_after`, `threshold_probes`, `weighted_calls`, `cpu_us`, `degree_fallback`, `sampled_thresholds`, `sampled_eliminations`.

Aggregate fields: `decomposition_steps`, `threshold_probes`, `weighted_calls`, `cpu_us`, `cpu_tick_us`, `steps`.

Global API: `set_decomposition(const DecompositionOptions&)`, `const DecompositionStats& decomposition_stats() const`, and observation-only `pipeline_hooks_t::decomposition_stats`.

`q` is original-unit native frequency; `q_scaled` and all threshold values are scaled integer units. `eliminated` is the count of selected edges whose pre-consumption residual equals actual q_scaled. Degrees refer to positive residual support, including virtual/self-loop edges. `sampled_eliminations` records S2 weighted objectives per sampled threshold; S1/S3 do not use that field.

CPU timings use `std::clock()` process CPU microseconds, with reported `cpu_tick_us=1e6/CLOCKS_PER_SEC`; tiny steps may read zero. Existing steady_clock fields remain wall time. No physical bandwidth/time reward enters these matching policies.

## Implemented behavior and exact integration boundary

Date: 2026-11-11. Initial HEAD `94949a1`, clean worktree. Core changes are limited to `simulation/algorithm/all2all.{h,cpp}`, `simulation/scheduler/global.{h,cpp}`, and `simulation/tests/test_topology_schedule.cpp`. This worker did not edit Makefile; parent owns the additive experimental executable/targets and experiment script.

S0: the original no-argument `decompose()` and `decompose_with()` bodies are unchanged. The typed Kuhn/no-stats path delegates directly to the original. With stats, the exact original `update_edges()` / `hungarian()` unordered traversal / `update_permutation_sets()` are called, with read-only measurements around them. Native ordering is not normalized or replaced.

S1: each residual step collects sorted distinct positive values. Binary search retains the largest threshold whose eligible support admits a perfect Kuhn matching. Source and positive-neighbor traversal are ascending and deterministic. There is no comparison with the original seed's q and no topology acceptance guard.

S2: feasible distinct values form the prefix through t*. Let L be its size and C=min(B,L). For C=1, choose t*. Otherwise sample indices `floor(i*(L-1)/(C-1))`, i=0..C-1, including minimum/t*. For each threshold, run an actual shortest-augmenting-path Hungarian assignment with integer cost `1-[value==t]`. Entries below threshold never relax slack and therefore cannot be selected; no finite penalty pretends they are legal. A zero equality objective is logged but skipped. Select maximal equality objective Z, then maximal actual alpha. For every accepted positive-Z candidate, at least one selected value equals t and all others are >=t, hence actual alpha=t; the implementation still recomputes the true minimum before extraction.

S3: visit the same sampled pool descending. An edge incident to any current maximum-degree row OR column is eligible only if its value equals t; all other eligible edges must be >=t. The first feasible restricted perfect matching is used. It eliminates exactly one edge incident to every maximum-degree vertex; no vertex can lose more than one matched edge in one step, so maximum degree drops by exactly one. If no sampled threshold is feasible, fall back to the S1 matching and mark `degree_fallback=true`.

All four policies share the original `update_permutation_sets()` extraction. Matching completeness, positive actual minimum, exact extraction, and positive eliminated-entry count are checked. Every step removes at least one positive padded-residual entry, so the finite support gives a termination bound. **Z/Delta refer to auxiliary padded support, including virtual capacity/self loops, not physical network-flow completion counts.** No partial matching, deletion/edit neighborhood, or topology reward was introduced.

Global integration is explicit opt-in: `set_decomposition(options)` selects the typed overload within pipeline2. Without that setter all previous paths remain unchanged. It works with no topology or with `set_schedule(Native,&top)` for existing resource evaluation. Explicit decomposition configuration rejects legacy BandwidthAware/CompletionTimeAware matching overrides in either setter order, and rejects prepare/communication/deferred overrides (observation-only hooks remain legal). To avoid ambiguous trace semantics, this rejection also applies to explicitly configured Kuhn; the old default path without the new setter still supports its old hooks. Explicit decomposition is rejected by pipeline/pipeline3 rather than silently ignored.

Native stuffing, scaling selection, Local balance, per-stage dispatch, intrinsic handling, and pipeline recurrence were not changed. New stats are available both via getter and copied into observation hooks. Existing `native_decompose_us` stays a wall-time field, including padding in the pipeline region; new `cpu_us` covers the typed decomposition only, excluding padding/scaling, Local work, model evaluation and the final copy into hooks.

## Actual online complexity

Per residual step, n is matrix dimension, m positive padded entries, D distinct positive values, and B'<=B the sampled threshold count. Values/degrees/positive neighbors are collected by a dense scan, so this is **O(n²)**, not O(m). Sorting values costs O(m log m). A threshold Kuhn feasibility test uses the cached positive-neighbor lists and costs O(nm), not a dense all-column DFS. The Hungarian weighted solver scans dense columns with O(n³) worst-case cost.

| Policy | Per-step time, including collection but excluding Local/pipeline/model work |
|---|---|
| S0 native | Existing O(n²+nm); instrumented path adds an O(n²) read-only support scan, without unused threshold sorting |
| S1 | O(n² + m log m + nm log(D+1)) |
| S2 | O(n² + m log m + nm log(D+1) + B' n³) |
| S3 | O(n² + m log m + nm log(D+1) + B' nm) |

Scratch memory is O(n²+m)=O(n²), plus native output O(Kn). If requested, step traces use O(KB) additional sampled-threshold/objective storage. No online permutation enumeration, exact global schedule search, or new solver dependency exists.

`threshold_probes` counts real threshold-feasibility calls: S1 binary search plus S3 restricted probes. `weighted_calls` counts all S2 Hungarian calls, including zero-objective candidates. `sampled_thresholds` records the full deterministic pool, descending in S3 even if a successful prefix permits early stopping. Only S2 fills `sampled_eliminations`, one objective per pool entry. Aggregate counters equal sums of step counters.

## CPU versus wall measurements

`std::clock()` measures process CPU time, not elapsed wall time or predicted network time. `cpu_tick_us` reports its nominal unit, not a measured accuracy guarantee; Linux here reports 1us, and very short steps can legitimately quantize to zero. Clock failure/wrap is a controlled error. Aggregate CPU timing includes workspace setup, support statistics, matching, extraction, and trace construction; per-step CPU includes its scan/search/extraction, while final move/copy into external stats/hooks is outside the measured interval. Profiling overhead is not subtracted. The traced native path therefore has the same matching sequence as the original, but is not claimed to have zero observational overhead.

## RED/GREEN evidence

1. Added the specified 3x3 test before implementation. Compile RED: `DecompositionStrategy` / option/stat types undeclared. After adding the API declarations and the test-only exhaustive oracle, link RED: undefined typed `FastAll2All::decompose(...)`.
2. Implemented the full algorithms. Focused GREEN: all S1/S2/S3 outputs for `[[1,1,2],[1,2,1],[2,1,1]]` have K=3, first q=2, first Z=3, first degree3->2. Forcing the original callback's first matching to the diagonal produces K=4, independently reconstructed exactly.
3. Added Global routing/incompatibility tests first. Compile RED: missing setter/getter/hook field. During implementation, the pipeline() rejection test also exposed a missing guard; it failed before the guard was added. Final focused GREEN checks pipeline2 payload conservation for all four strategies, with and without Native topology evaluation, and exact S0 stage/map/frequency/payload/dispatch parity with the legacy path.

## Test coverage and final verification

The test-only oracle enumerates permutations only for n<=5. A fixed seed creates 12 balanced matrices per dimension by summing seven weighted permutations: 60 matrices, four strategies and budgets1/4/8 = 720 bounded decomposition configurations. It independently checks every perfect matching, actual q, padded reconstruction, equality-elimination objective at **each sampled threshold**, S1 maximal bottleneck, S2 Z/actual-alpha ordering, S3 selected threshold or fallback, exact degree drop, counters and budget, and final zero residual.

Additional explicit fixtures cover:

- All-zero input and sparse nonbalanced input using the same native padded reconstruction as reference.
- S3 fallback: `[[1,2,3],[2,2,2],[3,2,1]]` has no sampled all-critical equal-t perfect matching in its first step.
- S2 zero objective: `[[4,2,1],[1,5,1],[2,0,5]]` admits t=2 perfect assignment, but its best equal-t objective is0; that candidate is logged and skipped.
- Native/no-stats delegation, all policies with null stats, invalid enum/zero budget/unpadded input rejection.
- Native scaling factor3: q and q_scaled are checked against original-unit stage frequencies; reconstruction remains exact.
- Default native compatibility and explicit pipeline2 route/payload checks; both incompatible setter orders and all overriding hook kinds are rejected.

Fresh verification (2026-11-11):

```text
make -C simulation /home/julius/fast/FAST/simulation/build/test_topology_schedule
simulation/build/test_topology_schedule
  PASS: Topology schedule tests passed (new complete-policy/oracle tests and existing tests)
make -C simulation bvn-check
  PASS: bvn schedule: 5443 checks passed
make -C simulation check
  PASS: Topology schedule tests passed
  PASS: 3 Python tests, 7.190s
  52 strict native/bandwidth plan+network archive comparisons
  26 corrected time replays (6 previously documented changed plans)
  508 intrinsic-contaminated dispatch slots independently replayed
git diff --check -- <five scoped core/test paths>
  PASS: exit0, no whitespace diagnostics
```

The 5443 count belongs to the existing independent partial-tool suite; it is not being relabeled as the number of complete-strategy oracle tests. No unresolved failure was observed. K reduction, CPU cost or predicted communication improvement is not guaranteed for every input by these bounded heuristics. Parent owns experiment generation, bounded study execution, optional trace availability, model review and final presentation; this report does not invent a real MoE trace or claim an experiment result not run by this worker.
