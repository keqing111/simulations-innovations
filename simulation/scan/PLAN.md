# FAST G/L/R Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Track evidence in PROGRESS.md. User chose continuous inline execution, no Git changes or additional approval rounds.

**Goal:** Implement independent G/L/R factors with a single decomposition path and run the approved 260-result development scan plus 21-repeat planning diagnostics.

**Architecture:** A backward-compatible deferred-decomposition hook leaves all old callers unchanged. A new scan/ planner operates inside FAST's existing Birkhoff loop and dispatch path; scan/ driver uses the same topology and factory profile. Python snapshots sources, registers inputs, independently reconstructs payload/routes/pipeline time, replays outputs and renders results.

**Tech Stack:** Existing C++11, g++, Python3 standard library, installed matplotlib. No new solver or packages.

**Spec:** ../../research/topology_aware/OPTIMIZATION_SCAN.md

## Global Constraints

- Original FAST pipeline2, intra SpreadOut, true byte delivery, fixed routes and network model remain unchanged; no new barriers.
- Eight F000..F111 methods plus exact Max-Min and SpreadOut. G at most6 threshold feasibility probes/stage; L at most2 force-edge attempts/stage; R only uses current real residual.
- 26 development/diagnostic cases, no new validation claims. Native is primary baseline; no Direct.
- Preserve dirty workspace, old source snapshots/results, no Git commits/push/cleanup. New files in scan/, only backward-compatible fields/branches in scheduler/global.*.
- Measure actual one-pass planning, preprocessing separately, no modeled plus planning claim without serial/no-overlap qualifier.

## Review Focus

- Empty, intra-only and virtual-only residuals must not become WAN service; Task2 tests.
- Threshold feasibility/cycle validity/coefficient floor must hold with uint32 boundary values; Task2 tests.
- Disabled factors must preserve exact original permutation/coefficient/pipeline output; Task1 and Task3 tests.
- Raw tampering, incomplete runs and replays must fail audit; Task3 tests.
- Timing includes needed setup but excludes JSON export and simulated network evaluation; Task1/3 integration assertions and explicit report boundaries.

### Task1: Single decomposition hook

**Files:** Modify scheduler/global.h and global.cpp; create scan/Makefile and tests/entry_test.cc.
**Interfaces:** pipeline_hooks_t::defer_decomposition bool (defaultfalse), planning_us double (default0). When deferred, prepare receives an undecomposed app and must produce its schedule. All other semantics unchanged.
- [x] RED: compile entry_test requiring new hook; run and retain expected missing-interface failure.
- [x] GREEN: defer only when explicitly enabled with a prepare callback; time planning before analytic cost evaluation; default path untouched.
- [x] Verify: entry_test compares stage maps/frequencies, payload, dispatch and pipeline result between old and deferred-native paths, and rejects missing prepare.

### Task2: G/L/R planner

**Files:** Create scan/planner.h, planner.cpp, tests/planner_test.cc.
**Interfaces:** Options::parse(method); Planner(top, rows, method); decompose_into(FastAll2All&); stats() with stage/probe/cycle/floor counters; remaining_inter_bytes(). ResourceCache provides real-payload cost and pressure priorities.
- [x] RED: tests for all8 combinations, native/bounded/Max-Min identity, exact conservation, support decomposition reconstruction, threshold bounds, coefficient-guard rejection and R's real-only ordering.
- [x] GREEN: reuse existing force_edge, use native neighbor iteration when R disabled; at most6 binary power threshold probes; L accepts only strict stage-time/real-byte improvement with coefficient floor; update shadow residual exactly.
- [x] Verify: deterministic random small matrices and hand-built shared-WAN example; no permutation enumeration.

### Task3: Snapshotted driver and auditable harness

**Files:** Create scan/main.cc, study.py, tests/test_study.py.
**Interfaces:** driver N G METHOD WAN_US [REPEATS]; --zipf follows existing generator. JSON includes stages, pipeline costs, one-pass/setup/preprocess timing, operation counters and per-repeat planning samples. study.py --phase prepare/run/audit/render --output PATH [--smoke].
- [x] RED: unittest checks grid26/methods10, F000 baseline identity, actual all-method smoke, routed byte/pipeline reconstruction and raw timing tamper rejection.
- [x] GREEN: snapshot exact source/binary/build; reuse existing generator and independent reconstruction with explicit method identity; actual21 repetitions in interleaved deterministic method order, not21 workloads.
- [x] Verify: make -C simulation/scan check; compare F000 to frozen previous binary on26 inputs, and F010 to prior bounded, Max-Min and SpreadOut to their old equivalents.

### Task4: Run and report

**Files:** Create scan/results/run-*/; scan/REPORT.md; update research index/status.
- [x] Freeze inputs and implementation before timing/results; run all260 methods and full native replay audit.
- [x] Render linear-axis communication/CPU plots, factor interaction and losses; report exact timing scope and all negative cases, development status and best-within-grid wording.
- [x] Run existing bottleneck/topology/reproduction tests; no old artifact replacement.

### Task5: Independent review and final verification

- [x] Obtain one read-only fresh-context review of new code/core/data; report scope reviewed, final result text self-checked after reviewer usage limit (explicitly disclosed in ledger).
- [x] Important findings: reproducing RED test, minimal fix, whole suite GREEN. Preserve run source identity if fixes change implementation; rerun rather than amend old raw.
- [x] Final audit, report/results prepared for handoff and2–3 next-step suggestions; no Git integration or cleanup.
