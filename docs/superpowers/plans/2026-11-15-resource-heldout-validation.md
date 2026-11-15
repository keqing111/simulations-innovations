# Resource-MaxQ Held-out Validation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; run inline, without changing the scheduler. Steps use checkbox syntax.

**Goal:** Execute a fixed layer/window/seed validation and document heterogeneous topology designs and planning latency.

**Architecture:** One experiment driver reuses the existing scenario preparation, frozen planners and actual replay entry. Original successful baselines are read-only; new synthetic cases run all four methods. One compact table/JSON/plot result set records every planned condition.

**Tech Stack:** Python, existing numpy/matplotlib/pytest, SimAI/ns-3 binary.

**Spec:** `HETEROGENEOUS_TOPOLOGY_VALIDATION.md`.

## Global Constraints

- Freeze planner at cc76a17; no algorithm or network runtime/config changes.
- Use the existing isolated worktree; no push, old campaign restart or failed-run retry.
- Preserve foreground epoch and exact source inputs; admit reused baselines only after fingerprints/config/library comparison.
- Preselect 27 conditions:21 real and6 synthetic; no selection by measured performance.
- Keep strict balanced seed duplicates out of sample counts; descriptive statistics only.

## Review Focus

- Old incomplete baseline: retain missing status, never retry or silently discard condition.
- Changed configuration/runtime: reject pairing, not just matrix mismatch.
- Identical “random” input: detect duplicate matrix digest and disclose sample dependence.
- Worker crash: preserve parent ledger with case-specific failure, do not drop other cases.
- Planned versus actual counts: require every planned method, not merely exited futures.

### Task 1: Driver correctness

**Files:** Create `experiments/alltoall/tools/run_resource_budget_validation.py` and `prototype_tests/test_validation.py` (under astra-sim-alibabacloud).

**Interfaces:** `validation_cases(catalogs)->list[dict]`; `paired_summary(rows)->dict`; `source_baselines(study,case)->list[dict]`; `run` reuses existing iteration driver.

- [ ] Write tests for fixed cross-product/unseen seeds, absent/failed baseline, per-pair timing with missing data, and fingerprint mismatch rejection.
- [ ] Run tests RED; implement minimal driver; run GREEN and changed-scope suite.
- [ ] Snapshot planner, run source preflight and commit script/tests before replay.

### Task 2: Execute and report

**Consumes:** frozen27case driver, original successful real baselines and existing runtime.
**Produces:** per-case study artifacts, paired.csv, summary.json, linear-speedup plot and report section5.

- [ ] Execute66new replay attempts,3workers, per-method timeout180s; parent persists each completed/failed future; no automatic retries.
- [ ] Verify exact input/epoch/numerical config/runtime semantics/libraries for reused results; validate byte delivery for new results.
- [ ] Report every condition, group geometric means, win/tie/loss and worst loss; label input dependence and absent CIs.
- [ ] Recheck scoped and full suite, report inherited failures, self-review experiment boundaries and commit compact results/docs. No merge/push.
