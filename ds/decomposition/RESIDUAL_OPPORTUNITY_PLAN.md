# Residual opportunity diagnosis implementation plan

> Execute inline with test-driven-development. User approved the preceding diagnostic directions and previously opted out of repeated implementation approval. No commits or pushes in this task.

**Goal:** Test lightweight remaining-work indicators against measured single-decision interventions, and distinguish an intrinsically worse residual from a bad continuation of the current heuristic.

**Architecture:** One offline Python analysis file reuses archived FAST traces and the independent capacity evaluator. A strictly guarded n<=6 equal-edge, no-dummy decomposition oracle supplies exact network cost-to-go. Neither the oracle nor new scores are linked to the online scheduler.

**Spec:** `CAUSAL_AND_MULTIDC_REPORT.md`, sections 2, 7 and 8, plus the user's approval to execute the next-step suggestions.

**Tech stack:** Python standard library; existing FAST JSON and topology snapshots.

## Constraints and alternatives

- Do not alter FAST matching, scaling, Balance, dispatch or the network model this round.
- Compare stage-only, stage+remaining capacity lower bound, stage+endpoint-aware lower bound, and stage+WAN L2 norm. All additive terms are in microseconds; the L2 score is a surrogate, not a lower bound. No coefficient fitting.
- WAN-active-stage count alone is insufficient; the n6 first-LAN decision must not automatically be labelled structurally harmful.
- Full continuation replay is observational evidence about the resumed policy, not proof about the best possible continuation.
- Prefer guarded exact small-case validation to immediately adding a production penalty or blindly increasing price rounds. Exact search is offline only.
- Inputs remain development data. No holdout-performance or novelty claim. If a score fails known cases, report failure rather than deploying it.

## Review focus

1. Dummy demand or unequal edge sizes must be rejected by the exact oracle.
2. Resource totals and nonnegative pending must be preserved on candidate consumption.
3. A cost-to-go bound is not an exact tail, and an exact network optimum is not automatically a FAST pipeline optimum.
4. Same-prefix scoring must use true per-source-GPU pending, not padded auxiliary traffic.
5. Deterministic ties and same-current-cost/different-future choices must remain visible.

## Tasks

### Task 1: Pure scoring and consumption utilities

- [x] Tests first in `simulation/tests/test_residual_opportunity.py`: independent resource and endpoint bounds; true byte consumption; invalid payload rejection.
- [x] Implement `consume(pending, permutation, capacity, n, g)` and `action_features(raw, pending, permutation, capacity, unit)` in `ds/decomposition/residual_opportunity.py`.
- [x] Run the focused tests RED then GREEN.

### Task 2: Guarded equal-edge exact oracle

- [x] Tests first: n2 hand-computed 82us network result; nonuniform and diagonal/dummy case rejection; n6 archived equal-edge optimum 21931.24832us. Broader malformed-input variants remain deferred, see review note below.
- [x] Implement `UniformOracle(raw, unit)` with `full_mask`, `candidates(mask)`, `value(mask)` and `plan(mask)`; require n<=6, uniform positive edge coefficients, exact real payload and regular support.
- [x] Dynamic programming removes one complete matching at a time. Since this restricted model is additive and edge payload is fixed, require each explored matching to cover the first remaining edge, eliminating order duplicates without losing any factorization.
- [x] Compare exact values at every observed uniform prefix; record irreversible regret and remaining policy gap separately.

### Task 3: Paired diagnostic and report

- [x] Score seed/chosen pairs for all 23 previous interventions; record incorrect predictions and ties, not only successes.
- [x] For three WAN profiles x two sizes, inspect all original/maximum-bottleneck/resource uniform prefixes. Check whether tied immediate costs have different exact tails.
- [x] Save immutable inputs/source hashes, exact plans, summaries and metadata in a new output directory; refuse overwrites.
- [x] Write `RESIDUAL_OPPORTUNITY_REPORT.md`, explicitly correct any overly strong causal interpretation in the preceding report.
- [x] Run the focused tests and full `make -C simulation check bvn-check bvn-decompose-build`; verify source and binary unchanged.

## Progress / rulings

- Ruling: Work in the current research branch with existing uncommitted data; add only the research script, its tests and documentation. Do not move or rewrite user work, create unrelated worktrees, or commit accumulated changes.
- Ruling: This is author-approved experimental implementation, not the experiment skill's narrow 'execute an exact user-provided command' service. Its monitoring, provenance and uncertainty safeguards apply; repeated command approval is superseded by explicit user instructions.
- Pre-flight: Task 2 consumes Task 1's exact pending and topology interpretation. Task 3 consumes both. All unit sizes are bytes and all times microseconds.
- Ruling: Exact equal-edge support means no entirely virtual support edges, no diagonal edges and one visit per positive edge; uniform scaling may round capacity upward by one unit while true payload remains fixed. This permits the archived small-load panel without miscounting padded capacity as real bytes.
- Ruling: Add offline 2/3-cycle neighborhood diagnostics after discovering that the large heterogeneous case loses value at stage 2, not stage 1. This distinguishes candidate-generation failure from cost-to-go failure; it does not change the online algorithm.
- Tasks 1–3 complete: 23 interventions, 18 uniform traces/90 prefixes, 24 offline score rollouts. Final output `residual-opportunity-reviewed-20261112`, complete=true. Source/input hashes verified; deterministic outputs exactly match preceding execution.
- Final checks: 7 focused tests and 26 full Python tests passed; integrated topology tests and 5443 independent BvN checks passed. Existing production source/binary hash matches the previous multiDC panel.
- Fresh-context read-only numerical/code review: no Critical/Important issues; independent non-pivot DP agrees on six optima. Provenance now includes `study.py` because the auditor uses it. Deferred Minor: explicit tests for fully virtual off-diagonal support, incomplete GPU channels, repeated support, and rounded-capacity acceptance (implementation guards exist and actual archived rounding inputs pass).
- Declined-scope rulings: general BvN optimality, production optimization, FAST whole-pipeline optimality, holdout performance and unrelated dirty changes remain outside this offline deliverable. No claims made for them.
