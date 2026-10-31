# 精确粒度与残余瓶颈评分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans inline, TDD and PROGRESS.md. No Git operations or redundant approval requests; user explicitly delegated continuous execution.

**Goal:** Scan13 fixed low-overhead strategies on the same26 development inputs without changing the simulator model.
**Architecture:** Extend existing native planner policies, compile an opt-in extended method list, use a private instance of the tested scan harness with explicit policy/identity bindings. Preserve all historical results.
**Tech Stack:** Existing C++11/Python3/matplotlib only.
**Spec:** DESIGN.md

## Global Constraints

- Same Topology evaluator/pipeline/routes/data and21-repeat protocol; no newbarrier or model verification prerequisite.
- Exact q* matching remains conventional Max-Min; L/R and residual-bound objective are experimental modifications.
- Candidate budget2 or4; exact real byte delivery, no fictitious WAN benefit, finite positive-edge elimination.
- 26 development/diagnostic inputs×13 methods=338 outcomes, balanced/Zipf reported separately, not validation claims.
- Preserve dirty in-place workspace, snapshots isolate builds; no commits/push/deletion.

## Review Focus

- Residual-bound units, self/virtual exclusion and zero demands: Task1 literal hand tests.
- Exact/relaxed thresholds and coefficient guard under pressure ordering: Task1 traces/random matrix tests.
- Policy budget must be derived from declared method, not trusted raw metadata: Task2 tamper tests.
- All5 shared methods and every generated input must match first frozen run: Task2/3 independent comparison.
- Historical archive reproducibility despite shared planner extensions: Task3 archive audit + rebuild.

### Task1: Strategy options and model-consistent residual score

Files: scan/planner.h/.cpp, refine/tests/planner_test.cc, refine/Makefile.
Interfaces: Options adds tail_score, quota_numerator/denominator, cycle_budget; ResourceCache::remaining_bound(m,w,rows); Decision includes exact/native coefficients for audit.
- [x] RED: new method parsing and literal bound tests; Q75/Q50 threshold fixtures; allnew methods exact conservation and support reconstruction.
- [x] GREEN: exact q* with optional floor relaxation; unchanged old options; bounded2/4 cycles; B scores currenttime+residualbound.
- [x] Verify: new C++ tests +existing scan C++ tests; baseline model/scheduler files unchanged.

### Task2: Extended CLI and independent results harness

Files: scan/main.cc opt-in method list/trace output, scan/study.py optional explicit cycle budget; refine/study.py and tests/test_study.py.
Interfaces: opt-in compile FAST_SCAN_EXTENDED;13-method list, exact declared thresholds/budgets in wrapper audit; reuse snapshot-build/source registration/seed regeneration/round-robin phases.
- [x] RED: real CLI smoke26 outcomes; old5 policy equality; seed-generation equivalence; forged threshold/budget rejection.
- [x] GREEN: private harness bindings; exact policy-derived budgets; new archive includes current-code dependencies and tests (historical baseline artifact remains external, explicitly disclosed).
- [x] Verify: make -C simulation/refine check and existing scan full suite.

### Task3: Freeze, execute, render and review

- [x] Freeze inputs/methods/source then run all338 outcomes×21 measured repeats.
- [x] Replay audit,130 frozen old-method comparisons,26 saved input equalities; render all/Zipf/CPU charts and report negative cases.
- [x] Run all existing suites and archive-only rebuild smoke; completed one read-only independent code/data/report review (no implementer agents).
- [x] Report evidence, source/model boundaries and2–3 next steps. No experiment method changes after observation or Git mutations.
