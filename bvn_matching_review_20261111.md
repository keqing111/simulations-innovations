# Complete BvN matching review — 2026-11-11

Reviewed `bvn_matching_plan_20261111.md`, `bvn_matching_implementation_20261111.md`, and `/tmp/bvn_match_review_20261111.diff`, including the experiment driver and study script. This review uses the newly authorized **native padded/full-perfect-matching** contract, not the earlier partial-matching design. No static matrix transformation is required for this round. No production/test code, git state, or experiment outputs were changed; no subagents or full test suites were run.

## Verdict

- **Core algorithm/integration compliance: pass.** The four policies operate on the same native padded integer residual, preserve complete positive-support matchings and existing extraction, and route explicitly through the existing FAST pipeline.
- **Whole-delivery compliance/quality: changes required for the CLI boundary below.** The implementation is appropriately scoped and the recorded oracle/regression evidence is strong. There are also minor experiment-observability gaps relative to the plan.

## Important findings

### I1. Valid zero-demand pipeline input produces invalid JSON on success

**Location:** `simulation/bvn_decompose_main.cpp:106` and `simulation/bvn_decompose_main.cpp:130`.

The driver copies and serializes `result.ratio` without checking whether it is finite. The preserved legacy pipeline computes `(t-bound)/t`; all-zero demand therefore yields NaN even though total time is finite and zero. The new driver's total-time-only check at line 104 passes, and it emits `"unhidden_intra_ratio":-nan` with exit status 0. `-nan` is not a JSON value, so consumers cannot read an otherwise valid empty result.

Focused reproduction with the existing built executable:

```text
Arguments: --mode pipeline --n 2 --g 1 --repeat 1 --strategy 1
stdin: 0 0 0 0
Exit: 0
Output includes: "stage_count":0, "total_us":0, "unhidden_intra_ratio":-nan
Python json.loads: Expecting value
```

Handle the undefined zero-time ratio at the **new serialization/driver boundary** (for example, JSON null with documented semantics, or a defined empty-workload convention), leaving the legacy recurrence unchanged. Add a zero-demand CLI JSON regression; check the other serialized numerical outputs for finiteness before claiming success.

## Minor findings

### M1. Promised timing breakdown is not exposed by the experiment driver

**Location:** `simulation/bvn_decompose_main.cpp:87`, `simulation/bvn_decompose_main.cpp:102`, `simulation/bvn_decompose_main.cpp:128`.

The plan promises separate padding time and complete planning time in addition to decomposition CPU. The driver correctly labels process CPU and its broad `setup_and_execution_wall_samples_us`, but the broad timer starts before Local/Global/topology construction and ends after pipeline model evaluation. It cannot serve as either padding time or the planning-only interval. `hooks.planning_us` is available but discarded; padding is not separately measured/exposed. Add clearly named measurements/output, and preserve their CPU-versus-wall distinction, before representing the requested timing breakdown as delivered. Do not infer padding time by subtracting CPU from wall time.

### M2. Per-step solver-call counts are dropped from CLI output

**Location:** `simulation/bvn_decompose_main.cpp:37`.

The core records each step's `threshold_probes` and `weighted_calls`, and the plan requests per-stage call counts. `stats_json()` emits only aggregate counts, omitting these two step fields. In particular, a ResidualDegree sampled pool records the full descending pool even when search succeeds early, so its array length cannot reconstruct the actual number of restricted probes. Serialize the recorded per-step counts; no algorithm change is necessary.

No Critical findings.

## Checked reasoning and evidence

- S0's no-argument decomposition and callback decomposition bodies are unchanged. The typed no-stats Kuhn path delegates directly; instrumented Kuhn uses the same native unordered traversal and extraction rather than a normalized replacement.
- S1 binary-searches sorted distinct positive residual thresholds with complete deterministic Kuhn feasibility. Its retained matching corresponds to the greatest feasible threshold.
- S2 uses a genuine shortest-augmenting-path Hungarian assignment. Forbidden edges never relax slack; importantly, slack previously established by another row in the alternating tree remains eligible even when the current row has a forbidden edge. Final assignment edges are checked. Cost 0/1 maximizes threshold equalities; a positive equality count makes actual minimum equal the threshold, so the Z-then-threshold comparison implements Z-then-actual-q. Zero-equality candidates are logged and skipped, and the maximum feasible threshold guarantees at least one positive-Z candidate.
- The bounded quantile pool includes the largest feasible threshold, and includes the minimum for budget at least 2. S3 scans it descending, restricting every edge incident to any maximum-degree row or column to equality with the threshold. A successful perfect matching therefore removes one edge at each such vertex and drops maximum degree exactly one. An unsuccessful scan retains the S1 matching and marks the fallback.
- Every policy recomputes the selected matching's actual minimum and elimination/degree statistics from the pre-consumption residual, then uses the existing native `update_permutation_sets()` as its sole extraction path. Positive elimination and bounded cumulative frequency enforce progression. No online permutation enumeration or topology reward was introduced.
- Global configuration rejects legacy topology-matching overrides in either setter order and rejects prepare/communication/deferred overrides. Observation hooks and Native topology evaluation remain supported. Pipeline/pipeline3 explicitly reject the new configuration rather than ignoring it. The no-topology and topology branches decompose once each on their respective routes; statistics are copied to observation hooks after selection.
- CPU timing uses process `std::clock()`, reports its nominal tick, and remains distinct from the existing wall-clock fields and predicted communication times. Instrumentation overhead is included rather than described as free. The implementation report states dense collection/sorting, Kuhn and Hungarian costs rather than claiming sparse-only collection.
- The study pairs identical generated input across four strategies, records inputs/source/binary hashes and raw results, uses exact inter-server payload audits, and does not call synthetic receiver skew a real MoE trace. Same-server demand is explicitly excluded in the pipeline study to avoid the preserved legacy intrinsic/self-loop ambiguity. The independent parent audit covers the current 84 outputs; this review did not rerun that study.
- Recorded fresh evidence includes 720 n<=5 oracle configurations, zero/sparse/scaled boundaries, native parity, fallback/zero-objective fixtures, integration rejection tests, the existing 5,443-assertion partial suite, and historical/archive checks. These are used as reported evidence, not misrepresented as tests rerun by this reviewer.

## Scoped reporting-fix re-review

**I1: ADDRESSED. M1: ADDRESSED. M2: ADDRESSED. Final whole-delivery compliance and quality verdict: pass; no outstanding review findings.** This supersedes the initial changes-required verdict above.

Reviewed only the three reporting fixes, leaving the completed matching-policy review unchanged:

- The new driver defines the zero-total-time overlap ratio as zero, checks a nonzero-time result's ratio for finiteness, and leaves the legacy pipeline recurrence untouched. The convention is localized to the new experiment interface.
- Padding now has a separately named wall-time sample in direct mode and an observation-only `padding_wall_us` hook around the existing native fill in both pipeline routes. The hook is reset at entry. Pipeline planning samples use the existing planning-only hook, distinct from the retained broader setup/execution wall samples and from process decomposition CPU. Direct-mode planning covers construction, padding and decomposition, stopping before reconstruction validation.
- Each serialized step now includes its recorded threshold-probe and weighted-assignment call counts.

Independent focused verification against the rebuilt executable passed: all four strategies on a zero-demand N=2/G=1 pipeline produced strictly parsed JSON, total time and ratio zero, and nonnegative one-element padding/planning arrays. The S2 3x3 example's per-step probe/weighted-call sums matched its aggregate counters, with one-element timing arrays. No full suite or experiment sweep was rerun by this reviewer. No matching logic or recurrence changes were introduced by these fixes, and no new issue was found in their scoped review.
