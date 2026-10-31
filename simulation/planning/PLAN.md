# A1规划实现与拓扑对照 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans inline with TDD; user opted out of pauses and Git mutations. One final independent read-only review.

**Goal:** Reduce planning cost without changing A1 schedules, and add a transparent same-model static-path matching baseline.
**Architecture:** Keep reference paths, introduce optimized implementation selection and pooled matcher workspace; exact weighted assignment adds SPM control. Private scenario engine freezes12 method/implementation pairs and independently audits them.
**Tech Stack:** C++11, Python3, existing gprof/matplotlib; no new dependency.
**Spec:** DESIGN.md

## Global Constraints

- Preserve all A1 semantic fields, q* search/DFS order and floating scores; no model/pipeline/padding/route/barrier change, no new heuristic combination.
- No fast-math, trace omission, moved-out preprocessing or reuse across independent planning instances to manufacture gains.
- Six strategies×ref/opt;40configs×24 formal repeats,11520 samples. Freeze before outcomes.
- Preserve dirty/user changes and old archives, no Git mutation/destructive cleanup. SPM is adapted control, not faithful paper reproduction.

## Review Focus

- Workspace reuse after failed augmentation, recursively displaced rows and zero/virtual demand must match reference exactly.
- Nonallocating sort must reproduce stable ties; fall back on nonfinite scoring, don't change floating arithmetic.
- Weighted matching must exclude forbidden edges, conserve payload, achieve exact assignment objective; no dummy reward.
- Same-process round-robin and cache/preprocess/trace costs must be charged symmetrically; paired semantics cannot compare only totals.
- Historical equality uses archived binary/input; no claiming fully self-contained past dependencies or external solver reproduction.

### Task1: Equivalent optimized matching

Files: scan/planner.h/.cpp; planning/Makefile,tests/planner_test.cc.
Interfaces: Options.optimized via-ref/-opt; ResourceCache::balanced_matching(...,bool optimized=false); per-instance lazy scratch; internal threshold matcher.
- [x] RED real finite matcher allocation bound fails on reference-like optimized stub; literal tie/long displacement/failed-search reuse and random exact equivalence.
- [x] GREEN cached volume/load/inverse/seen/candidate buffers, exact stable-tie nonallocating sort. Direct threshold recursion tried then withdrawn after isolated regressions; see ledger ruling.
- [x] Verify same schedules/weights and fewer deterministic allocation events, native regression.

### Task2: Static-path maximum-weight control

Files: scan/planner.h/.cpp, scan/main.cc; tests/planner_test.cc,tests/test_study.py.
Interfaces: maximum_weight_matching(a,n,q,weights); ResourceCache::individual_utilities(q,real); SPM exact q* and common full-cost acceptance; FAST_PLANNING_SCAN labels12 variants.
- [x] RED literal masked/tied weighted assignment, tiny random exact objective, real-vs-virtual/path capacity utility tests.
- [x] GREEN masked Hungarian shortest augmenting solver, static path cache, SPM planner integration and12-policy CLI.
- [x] Verify complete native flow conservation, no forbidden edges, old defaults/JSON unchanged outside new entry.

### Task3: Execute and publish local evidence

Files: planning/study.py,tests/test_study.py,REPORT.md,PAPER_OUTLINE.md.
- [x] RED40case grid, ref/opt full semantic checks, strategy/metadata tamper guards and real smoke archive.
- [x] GREEN private scenario engine reuse, parameterized12-label audit and plotting; freeze40cases then480 results×24 timings. Final-version CPU uses separate8config isolation, not mixed with V1.
- [x] Audit all pairs,116 historical A1 inputs, archive-only build, all old suites, one read-only review. No blocking findings;2minor improvements deferred and disclosed in REVIEW.md.
- [x] Report CPU vs communication separately, honest external baseline boundary,2–3 next suggestions; preserve workspace.
