# SDD ledger — plan: simulation/independent/PLAN.md

Pre-flight: Task 1 CLI/schema is consumed by Task 2 audit and Task 3 report; use explicit method and topology fields plus complete stages/payloads.

Ruling: Follow the user's standing instruction to execute without intermediate approval prompts. Work only in the new experiment directory with a hash-verified frozen engine, rather than branch/checkout/stash the concurrently annotated main tree. This provides source/build isolation without moving their edits; it costs an extra frozen-data dependency, documented in DESIGN.

Ruling: Do not commit or push during this task. The user requested algorithm implementation, and other agents own unrelated dirty files.

Ruling: First validate original-seed strategies; also include Max-Min-seed counterparts so a gain cannot silently be attributed to the existing q* algorithm. Exact-current-coefficient and real-service guards deliberately restrict candidates; this may reject useful changes but makes the first isolated experiment interpretable.

Task 1: core CLI tests observed RED (missing build/policies), then GREEN (5 Python tests); original/Max-Min/SpreadOut frames match frozen native output. A compile-only name collision with std::array was corrected locally. Added guard fixtures, overflow rejection and exhaustive bottleneck checks before final suite.

Task 2: initial study test observed RED (missing harness), then GREEN (native smoke/audit and forged CCT/resource rejection). Added dependency snapshot tamper test and finite-result regression: both failed before their fixes, then the full 9-test suite passed (including 160 exhaustive C++ bottleneck checks).

Task 1: complete — frozen baselines, conservation, two literal mechanisms, exact-coefficient/volume guard fixtures and 160 exhaustive small assignments pass.
Task 2: complete — full native smoke/replay, generator/byte/pipeline audit and self-consistent forged CCT/resource rejection pass. Extracted engine source tampering and finite-input overflow now fail closed.
Task 3: in progress — run the fixed grid before reading aggregate outcomes; no combination or post-result tuning.

Task 3: first full grid run-f2fd4dcecd77 completed 182 native replays and 78 frozen-baseline controls. Bundled source independently rebuilt; 14 mechanism outputs exactly match. First 9-test suite passed.

Final review: fresh read-only reviewer confirmed objectives/guards, reconstructed all 182 rows and independently diagnosed 88 further valid matrices/topologies (616 policy outputs); no algorithm issue. One Important finding: no-replay audit accepted impossible local costs. Four new tamper subtests (negative balance, negative intrinsic, negative/NaN hidden dispatch) were observed RED before finite/nonnegative validation was added. Minor report wording was narrowed to sent-volume shadow checks and independent server-pair/resource/pipeline-composition checks, not a reimplementation of native local scheduling.

Final: Ruling: grade the no-replay local-domain gap Important rather than merely documenting a partial audit — users reasonably expect impossible costs rejected; fix it and rerun the unchanged algorithm/grid under a new harness source identity. Costs: extra verification run; no algorithm tuning or performance-data substitution.

Final: fixed audit-domain gap — four negative/nonfinite tamper subtests RED→GREEN; complete suite 10/10 Python tests, including 160 exhaustive C++ checks, passed. Minor report evidence wording clarified; no deferred review findings, no declined-to-judge items.

Task 3: complete — final source identity 2c90c8145c7d, final run run-2c90c8145c7d-serial. After tests finished, reran full grid sequentially; 182 full native reconstructions/replays, 78 frozen baseline controls, 182 old/new complete semantic bridge checks, 182 bundled-source rebuild checks pass. Algorithms/objectives and input grid were not adjusted after viewing results. Only this new directory was written; no Git index/branch/commit/push operations.

Ruling: initial post-fix run overlapped this agent's test/compile work, so do not use its CPU samples for the report. Keep it as a non-timing check; use the identically configured serial rerun for diagnostic CPU numbers. No host-wide isolation is claimed; three samples are not a production latency benchmark.
