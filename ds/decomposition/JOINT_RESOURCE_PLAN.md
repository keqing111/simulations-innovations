# Joint coefficient/resource prototype implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execute inline; retain this file as the ledger. No commit or push is authorized this turn.

**Goal:** Test mathematically motivated joint coefficient/matching heuristics on existing development cases, using native FAST replay for complete timing.

**Architecture:** One Python research module generates bounded weighted assignments and evaluates real payloads. A small optional replay flag in the existing CLI installs an externally generated valid decomposition through existing hooks; Local/Global/FAST algorithms remain unchanged. This is an exploratory prototype, not an online performance claim.

**Tech Stack:** Python standard library; existing C++11 FAST executable and unittest suite.

**Spec:** `paper/MATHEMATICAL_MODEL_AND_HEURISTIC.md`, approved by the user's latest request to try these directions.

## Global constraints

- Same native input, padding, scaling, topology and pipeline; no partial permutations or arbitrary coefficient splitting.
- Every coefficient is the selected matching's actual minimum residual. Real traffic and auxiliary demand remain separate.
- No full-permutation enumeration in the heuristic, no repeated complete decompositions to pick the fastest plan, no external solver/package.
- At most four sampled thresholds per state after a maximum-feasible-threshold search. Weighted Hungarian, not reordered Kuhn, generates candidates.
- Compare stage time per byte, capacity-excess per byte and propagation-degree-excess per byte separately; one-step continuation is a separate ablation.
- Matching refinement follows strict objective improvement and stops at a fixed point, not a hard-coded number of price rounds. It remains heuristic.
- Existing dirty changes and comments are preserved. New prototype files isolate the work in the existing research branch; no worktree reset/copy/commit.

## Review focus

- Sparse infeasible assignment graphs: return no matching, never select forbidden edges.
- Threshold not attained: recompute actual q and reprice the entire action.
- Scaling, virtual capacity and zero real payload: preserve exact delivery, never count dummy bytes as service.
- Replay tampering: reject non-permutations, non-minimum coefficients, incomplete decomposition and trailing data.
- Future approximation: preserve negative results and distinguish network-only proxy from native full-pipeline timing.

### Task 1: Validated native replay

Files: modify `simulation/bvn_decompose_main.cpp`; add `simulation/tests/test_joint_resource.py`.

Interface: `--replay-plan PATH`, pipeline/topology only, strategy0/no refinements. Text format: stage count, then each stage's ORIGINAL-unit capacity and n destinations. Replay reconstructs the same padded original-unit matrix from native stages, validates exact minimum extraction and installs p_sets through existing prepare/communication hooks. Replay steps use original units for both alpha fields and are explicitly marked replay-only; replay CPU is not algorithm CPU.

- [x] Write tests for native parity and malformed/truncated/incomplete/over-split plans.
- [x] Observe expected failure from missing replay flag.
- [x] Implement small parsing/install helpers and replay branch; build and verify tests.

### Task 2: Joint matching prototype

File: add `ds/decomposition/joint_resource.py`; tests in the same new test module.

Interfaces: `assignment(costs)` -> permutation or None; `State(raw, unit)` reconstructs native auxiliary/pending; `propose(state, objective, budget=4)` -> legal actions; `plan(raw, unit, objective, lookahead=False)` -> trace and exact physical schedule.

Fixed-q edge work is cached per proposal. Averages of active resource/propagation subgradients guide full weighted assignments. Every returned assignment is reevaluated with its actual coefficient and canonical stage cost. The price proxy is not claimed to solve minimax assignment exactly. Finite strict descent/seen matchings bound revisits, not a polynomial number of iterations.

- [x] Test weighted optima against tiny independent brute-force checks, forbidden support, coefficient/delivery and potential consistency.
- [x] Observe expected missing-module failures.
- [x] Implement bounded threshold sampling, weighted matching, exact evaluation, objective-driven generation and optional one-step continuation.
- [x] Verify toy cases and archived scaled/padded demand, including pure virtual actions.

### Task 3: Paired development evidence and mechanism report

Files: research module runner, new output directory, `ds/decomposition/JOINT_RESOURCE_REPORT.md`.

- [x] Freeze rules and source/binary fingerprints before reading new performance results.
- [x] Run existing 42 development conditions first; native, maximum bottleneck and cycle remain reference methods. Treat all data as development, not holdout.
- [x] Replay every proposed plan; compare real payloads, canonical network cost and full FAST recurrence. No best-of-method oracle reported as an algorithm.
- [x] Report all wins/ties/losses, representative failures, q changes, CPU and assignment calls. CPU is Python prototype planning and cannot be marketed as C++ online cost.
- [x] Run full existing check/bvn-check suite, review changes, and record remaining limitations.

## Ledger

- Initial ruling: user authorizes exploratory implementation and asks to proceed without routine approval; no new approval gate or formal experiment preregistration is inferred.
- Initial ruling: use the current dirty research branch without disturbing prior work; prototype is opt-in and reuses existing evaluator/hooks.
- Initial ruling: ARS experiment-agent's command-only execution scope is insufficient for this requested algorithm development; use the coding workflow for implementation and clearly label development evidence, not a formal ARS study/paper readiness verdict.
- Task 1: complete — new replay tests first failed with `unknown option`; parsing/minimum/reconstruction and native parity tests now pass.
- Task 2: ruling — a full weighted direction can tie at opposite resource extremes. Add discrete line search over its alternating-cycle components (including arbitrary cycle lengths), not exhaustive two/three-row neighborhoods. The hand-checked four-endpoint test verifies the balanced interior matching is generated.
- Task 2: ruling — return one terminal matching per threshold (at most four), not every intermediate iterate; one-step continuation remains bounded by this pool. Pure-virtual, zero-network actions retain zero score and cannot be credited with real bytes.
- Task 2: complete — 14 scoped tests pass, including weighted optima, scaled/padded real delivery, long cycle directions and fixed-point objectives. Independent read-only review found no critical/important issue; its two minor findings (budget upper bound and huge finite cost overflow) were reproduced and repaired with red/green tests. Extreme-cost rescaling is isolated from current data magnitudes; no universal numerical stability claim.
- Task 3: ruling — add same-generator hard-max-q ablations (budget=1) for stage and capacity scores, so coefficient relaxation is not confounded with replacing the matching generator. Default remains budget=4. These are development analyses added after initial exploration, not preregistered confirmation.
- Task 3: ruling — extend unchanged rules to previously seen seed202/303 and existing 8/16server cases; all remain development/check data. One-step continuation is evaluated only at n<=6 due Python cost, explicitly disclosed in metadata.
- Task 3: complete — final metadata complete=true, 384 outputs / 66 conditions; 5021 stages independently audited; 252 previous physical plans reproduced. Final suite: 49 Python tests, integrated C++, 5443 independent BvN checks. Report: JOINT_RESOURCE_REPORT.md. Two simple scores retained; degree/lookahead not promoted. No online or publication readiness claim, no commit/push.
