# WAN Resource Budget Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. User approved isolated implementation and minimum functional experiments; do not request another approval round.

**Goal:** Implement the approved resource-directed constructor and verify it with minimal SimAI/ns3 cases.

**Architecture:** One new scheduler module consumes existing prepared integer R/U and static topology snapshots. Existing materialize/replay stays unchanged. New tests and one smoke tool isolate results from the active campaign.

**Tech Stack:** Python3,NumPy,SciPy1.15.3 assignment as legality oracle, existing read-only SimAI/ns3 binary.

**Spec:** /home/julius/fast/wan-budget-v1/WAN_BUDGET_SPEC.md

## Global Constraints

- No modification of original worktree, existing frozen planner, running campaigns or shared backend. No push/merge.
- No multipath, partial matching, new barrier, arbitrary swap/threshold budget, real-payload fabrication, or claimed universal improvement.
- R/U positive byte integers; q positive integer<=selected R; every accepted stage eliminates R or U positive entry.
- Time proxy us,bandwidthGbps,x*.008/B; same proxy for H/admission/score. Real byte labels preserved by existing materialize.

## Review Focus

- Hidden real traffic in a completion cannot evade H: test full completed permutation price, not only anchor/selected early edges.
- Support graph perfect matching may exist but capacity completion fail: test fallback and log reason.
- Small q can fail to eliminate R: independently test true U progress and theoretical bound.
- Empty/local/virtual demand: no divide by zero, WAN service fabricated, or network dummy flow.
- Shared resource IDs and asymmetric paths: reject >1WAN perpair and price direction-specific resources.

### Task 1: Static resources, valuation, support legality

**Files:** new src/simai_a2a/schedulers/resource_budget.py; prototype_tests/test_resource_budget.py.
**Interfaces:** prepare_resources(topology)->dict; pressure(U,res)->list[float]; evaluate_round(R,U,p,q,res)->dict containing T_us,V_bytes,Phi_before/Phi_after,Z_R,Z_U,R_next,U_next; anchored_cap(R,anchor)->(q,p); support completion/SCC helpers private.
- [x] Write tests with literal small matrices, shared/independent topology prices, invalid multipath and legal arbitraryq; run RED.
- [x] Implement minimum functions and scipy support oracle; run GREEN.

### Task 2: Anchor/q proposals and budget construction

**Interfaces:** construct_round(R,U,res,anchor,q,H,required_edge=None)->dict|None; plan(prepared,topology,allow_split=True)->dict(permutations,coefficients,trace).
- [x] Write anchor lock, H checks, genuine handoff witness, fallback, local/empty and property tests; run RED.
- [x] Resource pressure anchors, anchor-conditioned coefficient cap, event-based smallq, SCC witness updates, budget greedy construction, capacity density selection, monotone residual update; run GREEN.
- [x] Verify no changes to original source hash and no native FAST simulator calls.

### Task 3: Minimal ns3 functional replay and report

**Files:** tools/run_wan_budget_smoke.py; prototype_tests/test_smoke.py; results/wan-budget-smoke/REPORT.md and raw artifacts.
**Interfaces:** fixture generator returns GPU matrix/hardware/topology; existing prepare/materialize/modeled_scaleup_schedule/run_scenario execute generated plans. read-only binary path explicit and hashed.
- [x] Test generated plans rebuild original GPU demand; script prepared output/metadata match methods and events.
- [x] Run three fixed tiny cases xthree methods, reject incorrect/incomplete results; collect detailed q/H/resource/progress trace and real FCT.
- [x] Run prototype test discovery plus copied v2 input/adapter regression. One fresh final read-only review required by executing-plans; fix important findings RED->GREEN; record all limitations and actual results. Keep isolated branch, no merge/push.
