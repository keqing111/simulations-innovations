# 匹配自由度与行排序 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: execute inline with superpowers:executing-plans and TDD. User opted out of pauses and Git mutations; one final independent read-only review.

**Goal:** Diagnose q* freedom and fairly test topology-related row ordering at one-construction budget.
**Architecture:** Extend existing planner with support summary/order functions; opt-in CLI exports offline diagnostics; private scenario audit engine drives separately frozen suites.
**Tech Stack:** C++11, Python3, existing matplotlib; no new dependencies.
**Spec:** DESIGN.md

## Global Constraints

- No changes to native timing, padding, pipeline, routing, q*, real demand or barriers; no neural model/permutation enumeration.
- Seven policies F000/maxmin/spreadout/A1/A1D/A1P/A1DP; all constructive policies exactly one extra construction/stage.
- Explore52configs×5 repeats, confirm64configs×21 repeats; both frozen before outcome inspection. Whole-server renumbering preserves physical problem.
- In-place authorized dirty checkout; preserve user changes and old results, no Git mutation/cleanup.

## Review Focus

- q* support includes virtual edges: distinguish graph freedom from real dispatch freedom.
- SCC diagnostic must not change schedules or leak outside-loop work into/away from policy timings.
- Degree-only policy must not pay pressure computation; ties deterministic; pressure ignores dummy/self demand.
- Renumber both demand endpoints and DC labels; don't generate different hot cycles or count paired cases as independent.
- Reused legacy audit must validate new policy metadata/order/diagnostics, not silently accept forged budgets or fall back to old topology.

### Task1: Planner diagnostics and row ordering

Files: scan/planner.h/.cpp, ordering/tests/planner_test.cc, ordering/Makefile.
Interfaces: SupportInfo support_summary(a,n,q,matching,real,g); constructive_order(a,n,q,pressure,kind); Options.row_order=0/1/2/3; ResourceCache.row_priorities(a,q,rows).
- [x] RED literal unique/cyclic/virtual freedom, scarce-row and pressure order, topology capacity swap, random support matching tests.
- [x] GREEN SCC alternating-cycle diagnostic, stable lexicographic order, A1D/P/DP one construction using existing matcher/score.
- [x] Verify native conservation/q*/budget and old suite compatibility.

### Task2: Opt-in trace, independent audit and study

Files: scan/main.cc; ordering/study.py, tests/test_study.py.
Interfaces: FAST_ORDER_SCAN builds alongside FAST_SCENARIO_SCAN; extra policy.row_order, decisions.row_order/row_pressure, initial_rows, support_diagnostics exported only ORDER; diagnostic computed after measured repeats.
- [x] RED52/64 grids, whole-server permutation invariants, all7 realCLI policies and independent diagnostic/order tamper guards.
- [x] GREEN private existing build/run engine, per-stage support/payload/q*/order audit, input cache keys include permutation, files/identity registration.
- [x] Verify old four-policy semantics vs frozen scenario; all tests, fixed model sources.

### Task3: Execute and report

- [x] Freeze both suites, run364 exploration+448 confirmation outcomes without concurrent CPU-heavy work.
- [x] Full replay/data/route/pipeline/diagnostic audit; linear plots, results vs A1/Max-Min/native, renumber robustness and online cost.
- [x] Archive-only rebuild; one independent read-only review, one TDD fix pass if needed.
- [x] Report actual gains/negative findings with2–3 next suggestions; no extra algorithm sweep or Git action.
