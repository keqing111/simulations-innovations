# Native shared-bottleneck implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. User opted out of intermediate approval and Git operations. One fresh final reviewer, no implementer delegation.

**Goal:** Validate network-composition weaknesses and test a lightweight bounded-cycle prototype against frozen native FAST and existing strong baselines.
**Architecture:** Reuse existing topology bridge/model without editing it; new numeric scorer and planner via a small compile-time adapter. Snapshot all compiled sources and verify legacy equivalence. Preserve pipeline2, SpreadOut and actual payload extraction.
**Tech Stack:** C++11, Python3 unittest, existing native C++ model, matplotlib for result plots.
**Spec:** simulation/bottleneck/DESIGN.md

## Global constraints

- Work in new bottleneck/ folder only; preserve dirty native files and all historical results. No commit/push/cleanup/worktree mutation.
- Paper rates400Gbps and450GB/s; new physical topology server aggregate400/gateway400/WAN100Gbps,700ns/hop.8GPU/server,3DC.
- No permutation enumeration, solver, extra barrier or packet-level claim. At most2 alternate-cycle attempts per residual for bounded policy.
-60fixed inputs×7policies; seed groups/units as DESIGN. Existing Max-Min is a baseline, not a contribution.

## Review focus

- Real-vs-virtual payload and per-GPU quotas: scorer must min(residual real demand,stage weight), never send padding.
- Same capacity/delay/pipeline model for all policies; do not reinterpret server aggregate as per-GPU bandwidth.
- Numeric cache reproduces map scorer/reference choices, including empty/self flows and ties.
- Bounded attempt count is not a global stage-count/no-regression guarantee; report losses and CPU cost.
- Source/raw/timing provenance and all native bytes remain independently auditable; no hidden warm-start parameter selection.

## Task1: numeric scorer and bounded planner

Files: cached.h/.cpp,tests/model_test.cc,tests/planner_test.cc,Makefile.
Interfaces: CachedScorer(topology).score(map,quota,remaining)->CostSummary{us,bytes}; CachedPlanner(top,rows,mode).decompose(Matrix&)->vector<PermutationSet>, stats compatible with old adapter. set_variant(reference|cached|bounded).

- [x] RED hand-cost100MB flows sharing100Gbps WAN:16000us serialization +52.8us path; compare numeric/map exact costs across sparse/virtual/self cases and route relabeling.
- [x] Implement numeric route table/scratch arrays. RED native-plan callback reference equivalence and quota conservation tests; implement cached/reference and bounded policies.
- [x] Verify all supported matching edges, min-positive coefficient, remaining real payload0, stage count≤N² and bounded attempts≤2×stages.

## Task2: adapter, frozen study and evidence

Files:driver.cc,study.py,tests/test_study.py,REPORT.md.
Interfaces: CLI preserves topology/main.cc's ::main with CachedPlanner substitution; explicit env assignments in recorded argv select planner/profile; --zipf retains native generator. legacy-profile selection exists for oracle checks. New study prepare/run/audit/render, source-addressed output and sealed raw registry. Initial renamed-main adapter failed and was corrected before formal preparation; ruling in PROGRESS.

- [x] RED real CLI hand/compatibility fixtures,30old oracle inputs, cached/reference identical semantic outputs, matrix/hash forgery rejection. Build snapshotted instrumented source and exact link factory adapter; new study never reads mutable source during run.
- [x] Prepare60input grid before outcome observation, save all parameters/matrices/source/binary; run420calls, independent Python path/pipeline/byte reconstruction for each, semantic replay excludes elapsed timing only after raw digest verification.
- [x] Report paired native/cached/bounded/Max-Min results, counterexamples, all3scales, planning overhead, model limits and linear plots. No tuning after validation.
- [x] Full existing topology/reproduction tests, fresh final reviewer (no Important fixes required). Keep as-is; no Git integration.

Outcome: run-33e58e040218,420results audited. Cached and reference exactly same schedules/times; extra planning median303.881→16.276ms at24servers. Bounded val speedup1.0686× vsnative but loses6/24 and is weaker thanMax-Min1.2726×. Balanced6servers256MiB5ms native229.800→171.315ms with unchanged5WANstages, establishing network-composition optimization space within this model. Not a universal new algorithm or EI-novelty conclusion. No further tuning on these validation seeds.
