# Resource-budget algorithm iterations implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix demonstrated handoff and multi-resource deficiencies with small deterministic changes, then retain only experimentally justified behavior.

**Architecture:** Preserve integer R/U, complete matchings, preparation, materialization and event-driven ns-3 execution. Iterate in the existing isolated worktree. Do not add a global optimizer, random search, new scheduler classes or runtime barriers. Compare each change against frozen v1 and no-active-split ablations before combining it.

**Tech Stack:** Python, NumPy/SciPy, unittest/pytest, existing read-only SimAI/ns-3 backend.

**Spec:** User's 2026-11-15 request to prioritize lightweight new-algorithm iterations, including co-critical resources and multiple constrained links on one fixed path; `WAN_BUDGET_STAGE_ANALYSIS.md` is the diagnosed baseline. This plan supersedes only the old spec's single-WAN restriction and frozen v1 policy, not its correctness constraints.

## Global Constraints

- No failed old campaign run is retried. Original workspace/runtime/config remain untouched.
- Preserve all matrix bytes and original source/final destination delivery; virtual bytes never create resource pressure.
- No multipath. A fixed path may contain multiple directed WAN resources; charge every traversed resource once.
- Integer q, legal complete P, nonnegative R/U, discrete progress and finite-stage bound remain mandatory.
- H is planning only; no predetermined data-release times or new barrier.
- Save all new-run outcomes, including regressions/failures; do not tune ns-3 parameters to favor one method.

## Review Focus

1. Shared suffix/prefix paths must not be treated as independent WAN groups.
2. Tied/near-critical resources must not all collapse to a single fixed-ID anchor.
3. A split must not be accepted for a follow-up which is infeasible or silently forgotten.
4. Auxiliary completion and real delivery must stay correct for virtual-only tails and odd byte counts.
5. Transit-WAN accounting must use actual paths, not only source/destination DC labels.

### Task 1: Reproduce and fix unfulfilled handoff

**Files:** `src/simai_a2a/schedulers/resource_budget.py`, `prototype_tests/test_resource_budget.py`, `prototype_tests/test_smoke.py`; experiment driver `tools/run_resource_budget_iterations.py`.

**Interfaces:** Keep `plan(prepared, topology, allow_split=True)` and result fields. Add only internal helpers or an explicit ablation switch if needed. A successful handoff must prevalidate and retain its actual next action; reject the split if no legal progressing follow-up exists. Compare same-work split/unsplit blocks where meaningful; do not pretend the existing proxy already predicts packet-level feedback.

- [x] Add and run failing tests that the existing handoff trace does not immediately serve its promised successor and may repeat the same matching.
- [x] Implement the smallest continuation-aware operation, preserving no-split behavior and all R/U invariants.
- [x] Run original three fixtures against frozen v1, no-split and revised method in ns-3; save plans, all event/transport outcomes and exact delivery checks.
- [x] Retain/revise based on the observed mechanism; record a ruling for any departure from the proposed macro-block interpretation.
- [x] Run task regression tests and commit only scoped code/tests/plan.

### Task 2: Generalize to resource sets and fixed multi-WAN paths

**Files:** same scheduler and tests; `src/simai_a2a/native_analysis.py`, `tests/test_native_analysis.py` for transit accounting only if confirmed.

**Interfaces:** Prepared edges carry all WAN IDs; compatibility of old single-WAN snapshots remains. Group membership is incidence, not disjoint ownership. Every load/budget/anchor decision uses all relevant resources. Transit statistics follow explicit fixed route files when supplied.

- [x] Add failing tests for two directed WANs on one path, paths with a shared nonfirst WAN, and multiple co-critical resource representatives.
- [x] Implement set-based incidence, full shared-resource charging and resource-representative anchors without adding a solver.
- [x] Reproduce transit statistics undercount, then fix and test via literal route/port fixtures.
- [x] Run same-work direct-mesh and chain-topology fixtures; verify route/flow bytes per WAN hop and complete original delivery.
- [x] Run task regressions and commit.

### Task 3: Controlled iteration, counterexamples and final verification

**Files:** existing scheduler only if a new demonstrated defect needs a minimal test-first fix; experiment driver; `WAN_BUDGET_ITERATIONS_REPORT.md` and saved results.

**Interfaces:** Fixed literal mechanism families plus held-out changes in source/receiver skew, payload and WAN bandwidth. No full decomposition search or broad parameter grid. Failed old runs remain untouched.

- [x] Compare each ablation, inspect first differing stage and critical-resource/endpoint events for gains and losses.
- [x] Fix at most the demonstrated additional bottleneck-construction issue if a safe lightweight change exists; otherwise document it rather than add arbitrary search.
- [x] Execute a small held-out validation set, not just the original handoff input; report denominators, regressions and planning time.
- [x] Run the entire project test suite and disclose inherited failures separately from changed-module tests.
- [x] Obtain final fresh-context code review; make test-first important corrections, then record final equations, code locations, limits and next steps.

## Execution notes

User explicitly requested autonomous multi-round implementation and experiments; no additional approval checkpoint. This is the approved lightweight research iteration, not an authorization to merge or push. Existing `WAN_BUDGET_STAGE_ANALYSIS.md` is preexisting untracked work and must be preserved. Old v1 remains recoverable from commit `0ab4a30` and its saved plans/results.
