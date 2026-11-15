# Heterogeneous A2Av Release and Pilot Plan

> **For agentic workers:** Use superpowers:executing-plans inline. No algorithm portfolio or heavy solver.

**Goal:** Publish current research to a new GitHub branch, then verify heterogeneous network behavior across separate workload dimensions.
**Architecture:** Keep existing scheduler as the only implementation; add a small research entry, report/config and a generic topology experiment driver. Export existing ns-3 local diff without changing the backend.
**Tech Stack:** Existing Python/numpy/scipy/pytest, SimAI/ns-3 and Git.
**Spec:** `research/resource_a2av/REPORT.md`, `experiment_matrix.json`.

## Global Constraints

- Push only a new branch to keqing111/simulations-innovations, never overwrite main or existing branches.
- Never publish credentials/raw model datasets; save compact trustworthy summaries and runtime source patch.
- No old failed retries, no original workspace/runtime edits, no congestion parameter changes.
- Keep legacy WAN behavior unchanged; heterogeneous grouping must be explicit physical-resource metadata.
- Paired four methods, same matrix/routes/transport; no global barrier.

## Review Focus

- Generic topology with no gateways: no division by zero or fabricated DC paths.
- A path traverses several shared resources: charge all; no mutually exclusive group assumption.
- Synthetic pressure and demand volume are distinct; background rho normalized by paths/capacity.
- New hardware rates and fixed routes must match physical replay, not just planner snapshots.
- Failed or unstarted methods retained; no cherry-picked condition omission.

### Task 1: Publish baseline, report and experiment matrix

- [x] Add concentrated report/index/config and existing ns-3 source patch; check patch against actual dirty submodule without applying.
- [x] Run scoped tests, scan tracked current/history for credentials and oversized Git blobs.
- [x] Commit and push new branch, verify remote HEAD. No merge/PR/main rewrite.

### Task 2: Generic shared resources and heterogeneous pilot

**Interfaces:** `prepare_resources(topology)` accepts optional `shared_resource_ids`, legacy default remains WAN IDs. `iterations.run(...,source_topology=None)` accepts a prepared fixed fabric without inferring DC layout. `research/resource_a2av/experiment.py` produces fixed cases and consumes existing four-method replay.

- [x] Tests RED: legacy compatibility, explicit shared coverage, invalid resource IDs, no-gateway scenario preparation, physical capacities/delays/routes, pilot count/no duplicate balanced seeds, background normalization.
- [x] Minimal implementation GREEN; no class hierarchy or scheduler duplication.
- [x] Pre-register18pilot cases,72methods; run with3workers and per-method180s timeout, retain failures without retry.
- [x] Validate byte delivery/fingerprints and per-case common inputs, summarize all paired speedups, CPU and negative cases. Report actual scope, not unexecuted phases.
- [ ] Fresh scoped/full tests, preserve inherited13failure set, read-only review, compact data commit and update only new remote branch.
