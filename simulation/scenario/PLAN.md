# 拓扑场景扫描 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans inline with TDD and PROGRESS.md. User opted out of approval pauses; no Git mutations.

**Goal:** Find stable topology/load advantage regions and evaluate a constructive resource-aware matching candidate under unchanged timing semantics.
**Architecture:** Add A1/A2 policy hooks to existing planner; opt-in CLI accepts DC labels/capacities. New scenario/ generator, route/pipeline audit and private snapshot engine register explore/confirm suites independently.
**Tech Stack:** C++11/Python3/matplotlib already installed.
**Spec:** DESIGN.md

## Global Constraints

- No new model/barrier/routing/CC, no real-world performance claims. Source snapshots and new outputs; preserve dirty workspace and all old results, no Git.
- 7 methods;106 exploratory cases×5 repeats,48 confirmation cases×21 repeats. No retuning between suites.
- Block/balanced exact row/column and WAN-byte controls; new-seed Zipf regeneration; avoidD2 duplicate scenarios.
- Exact positive supported matching, real bytes conserved, bounded1/2 constructions per stage; no permutation enumeration.

## Review Focus

- Dynamic augmentation must update resource loads without underflow/corrupting a perfect matching (Task1 random and literal tests).
- Equal workload marginals/WAN bytes and deterministic DC/layout generation (Task2 independent literal and regeneration tests).
- Variable capacities/DCs must not silently fall back to old3DC audit (Task2 real CLI tests and forged topology rejection).
- Archive registration keys must include block topology/layout; no accidental input-cache reuse acrossD (Task2 paired tests).
- Validation is held-out inputs/layouts, not independent physical network or homogeneous random balanced repeats (Task3 report scope).

### Task1: Constructive matching

Files: scan/planner.h/.cpp; scenario/tests/planner_test.cc; scenario/Makefile.
- [x] RED: A parsing/interface and hand6-server LAN matching, sparse random feasible threshold matching/real support conservation.
- [x] GREEN: load-aware augmenting DFS at q*,1/2 row orders, compare against existing exact seed, counts+per-stage trace.
- [x] Verify: C++ tests plus all prior scan/refine compatibility tests.

### Task2: Parameterized topology and controlled inputs

Files: scenario/config.h, study.py, tests/test_study.py; opt-in scan/main.cc; scan/study.py input_key hook.
- [x] RED:106/48 grids, block row/column/WAN equality, realCLI7-method shape/cost equivalence and tamper rejection.
- [x] GREEN: comma-separated DC vector/capacities/profile; independent route-capacity/pipeline audit; preserve legacy CLI defaults.
- [x] Verify: all smoke outcomes and default frozen-policy equality; no model evaluator/global timing changes.

### Task3: Execute and report

- [x] Freeze code, methods and both case suites before results; run742 explore then336 confirm outcomes.
- [x] Full replay/byte/capacity/pipeline/input regeneration audit, figures and report stratified by controls/load/DC/capacity; no selective best-sample claim.
- [x] Run all existing suites, isolated archive rebuild and one fresh read-only review; resolve Important findings via RED→GREEN.
- [x] Report advantages/negative cases/CPU, next2–3 suggestions, preserve Git/workspace.
