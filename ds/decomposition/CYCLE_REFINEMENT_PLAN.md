# Shared-resource alternating-cycle refinement implementation plan

> Execute inline, test first. User requested continued algorithm development and a consolidated progress/effect report, and opted out of routine approvals. No commit/push or worktree migration of existing dirty research files.

**Goal:** Add an opt-in, same-q 2/3-cycle local minimizer of the actual topology stage cost, and measure it separately from resource pricing and future-cost research.

**Architecture:** Extend existing `DecompositionOptions`, stage statistics and `FastAll2All` implementation; reuse cached per-edge true resource contributions. Global only passes topology/pending, CLI only exposes flags/stats. No new production class or source file. Use a single new paired Python experiment driver.

**Spec:** `RESIDUAL_OPPORTUNITY_REPORT.md`, sections 4, 5 and 8; latest user instruction to continue and explain current directions/results.

**Tech stack:** Existing C++11 and Python standard library.

## Design and alternatives

- Increasing price rounds does not address the demonstrated symmetric linear-objective limitation. Full minimax/global assignment or exhaustive permutations would violate the lightweight online goal.
- Chosen: steepest strict descent over valid 2-row swaps and both 3-row cyclic destination rotations. Stop at a local minimum rather than a fixed iteration count. This is not a global optimum and does not protect complete-plan time.
- `resource_cycle=0|2|3`, default0. Nonzero requires MaximumBottleneck, real topology and pending; 3 includes 2-swaps. Can run alone (`resource_rounds=0`) or after pricing. Disallow combining with single-step skip intervention to preserve its old meaning.
- Objective: unchanged `Topology::evaluate_gpu()` total stage time. Do not add remaining-work penalties, delete edges, relax q, change scaling/padding, or add barriers.
- Cache per-pair propagation plus sparse work. Candidate trials change at most six pair contributions. Use sparse deltas and sorted baseline resource/propagation maxima; re-evaluate every accepted candidate with the canonical evaluator. No full route reconstruction per trial.
- Bound each pass by O(n^3 L + n^2 + E log E), with L the maximum constant number of sparse resource entries involved in a three-cycle, E resource count. Whole stage multiplies by actual strictly improving moves+one final pass; no general polynomial bound on the number of moves is claimed.

## Review focus

1. Never count virtual edges as real traffic; preserve q, legal support and exact original delivery.
2. Incremental max propagation/resource cost must equal the canonical evaluator, including removing the current maximum and numerical cancellation.
3. Resource pricing off+cycles on must still collect/consume true pending; defaults must reproduce archived plans exactly.
4. Current-stage descent does not guarantee lower total time or unchanged K. Report negative results, CPU and full planning wall time.
5. Independent BvN partial module stays unchanged; all performance here uses FAST pipeline2.

## Task 1: CLI, core and regressions

- [x] Add failing CLI tests for default-off parity, cycle-only known n6 counterexample, invalid combinations, per-stage strict descent and certified 2/3-neighborhood local minima.
- [x] Add `resource_cycle`, `cycle_evaluations/passes/accepts` and `cycle_costs_us` statistics; cache `edge_alpha_us` beside existing edge loads.
- [x] Implement `FastAll2All::match_resource_cycles(top,q,selected,max_length,stats,step)` using sparse work deltas and exact accepted-cost verification. Reuse current decomposition/pending path.
- [x] Extend existing Global setter/require checks and CLI flag/output. Default untouched, skip+cycle rejected.
- [x] Run focused tests and full suite.

## Task 2: Frozen paired experiments and progress report

- [x] New `cycle_control.py`: reuse the seven fixed-budget demands, three WAN profiles, two sizes. Compare native, maximum-bottleneck, pricing3, cycle3, pricing3+cycle3 and spreadout-like; all FAST scaling on.
- [x] Verify unchanged methods against archived physical plans/times, every output's exact delivery and topology/pipeline replay; preserve all negatives.
- [x] Freeze source hashes before two new receiver-Zipf seeds202/303; repeat the same topology/size/method rules without tuning.
- [x] Record full predicted A2A, K, alpha/serialization, cycle evaluations/moves/passes, decomposition CPU, planning wall and simple serialized CPU+communication budget (not measured end-to-end time).
- [x] Write `CYCLE_REFINEMENT_REPORT.md` and a compact current progress map covering decomposition, static mapping, resource pricing, cycle correction and remaining-cost diagnostics. Do not mix static-only time with complete FAST time, or scaling-off historical gains with current native controls.
- [x] Final fresh-context review and full `make -C simulation check bvn-check bvn-decompose-build`; record unresolved issues. No arbitrary rule tuning after seeing new seeds.

## Ledger

- Pre-flight: core option controls both Global pending collection and FastAll2All pending consumption; both must change together. CLI statistics consume the same shared structs. Experiment evaluator imports existing audited topology snapshots.
- Ruling: Retain all user comments and unrelated edits. Existing branch is a research branch, not main/master. Only explicit current-task paths are edited.
- Ruling: The small-matrix exact-q test compares against original integer demand, so it must disable native scaling. Scaling-on is independently checked for true delivery and pipeline conservation; a rounded original-unit capacity is not the original-matrix max-min coefficient.
- Ruling: Add two size-only n32/n64 uniform checks and the n8/n16 legacy failures to expose online cost and regressions, not to tune parameters. After the first run exposed high CPU, cache each resource's constant us/byte factor once rather than recomputing division per neighbor. Matching objective/neighborhood/stop/tie rules stay frozen; rerun final data and compare physical outputs to the first batch.
- Review fix: A legal 1e6-ratio scaling fixture exposed cancellation in subtractive work deltas; a tiny finite bandwidth also invalidated reciprocal caching. Added both API regressions, observed the original exception, then cache per-edge work in canonical multiplication/division order and use positive interval-tree sums when most old work is removed. Canonical evaluation still authorizes every strict acceptance. Per-pass worst case is now O(n^3 L log(n+1) + E n + E log E + n^2 L), with O(n^2 L + E n) extra memory; ordinary well-conditioned deltas retain the fast path.
- Review provenance fix: Expand source hashes to Local/Matrix/Config, relevant headers, Makefile and new tests, in addition to executable and research/evaluator sources. Final batch will be `cycle-refinement-reviewed-20261112`; earlier data remain intermediate.
- Task 1 complete: CLI/core tests RED→GREEN, cancellation regression RED→GREEN, 34 full Python tests and integrated C++ plus5443 independent BvN checks pass. No change to objective, neighborhood, stop rule or original default plans during numerical repair.
- Task 2 complete: 408 outputs/68 paired conditions, complete=true,216 archived plan/time checks. Final numerical repair preserves all408 physical plans/times relative to the initial frozen-rule run. CPU only from final reviewed batch.
- Final review: one Important cancellation/reciprocal boundary fixed with integrated RED→GREEN fixtures; no fresh re-review performed after repair. Expanded dependency hashes address the provenance note. No other Critical/Important findings. Unrelated dirty state, general BvN optimality and manuscript judgement remain outside scope.
- Remaining research limits are explicit, not hidden blockers: seven regressions against original FAST; n64 full neighborhood CPU exceeds communication savings; remaining-cost scores not yet reliable. No commits or pushes.
