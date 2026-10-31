# One fresh final implementation review

Scope: new simulation/bottleneck/ only; do not edit code, Git, historical topology/ or reproduction/. Base/head both0712d29 because user prohibits commits. Working tree has preexisting native instrumentation, reused explicitly, do not treat it as this task's diff. All compiled sources snapshotted and hashed by study.py; output is not paper-native exact same network, but a declared shared-network extension with unchanged native pipeline2/intra SpreadOut.

Read DESIGN.md, PLAN.md, PROGRESS.md, cached.h/.cpp,driver.cc,study.py and tests. Two C++ tests plus2Python tests pass; real smoke14calls,30prechangeoracle and paper2GPU100MBhandtest included. Missing implementation RED occurred before code. New formal60input420call experiment not run yet; no speedup claim exists.

Review focus verbatim:
- Real-vs-virtual payload and per-GPU quotas: scorer must min(residual real demand,stage weight), never send padding.
- Same capacity/delay/pipeline model for all policies; do not reinterpret server aggregate as per-GPU bandwidth.
- Numeric cache reproduces map scorer/reference choices, including empty/self flows and ties.
- Bounded attempt count is not a global stage-count/no-regression guarantee; report losses and CPU cost.
- Source/raw/timing provenance and all native bytes remain independently auditable; no hidden warm-start parameter selection.

Important rulings in PROGRESS: keep::main unrenamed to preserve implicit0return; recorded env assignments select profile/variant, factory configuration is lazy after initialization. No native core edits or per-candidate map allocations. Cached mode aims exact reference matching, bounded tries≤2cycles/stage and rejects coefficient smaller than original at that residual. This is prototype investigation, not EI novelty claim.

Verify independently; read-only diagnostics/tests allowed, no repo mutations beyond normal build artifacts, no subagents. Provide Critical/Important/Minor with concrete reproductions, plus Declined-to-judge list. One review only; root will fix Important findings RED→GREEN, no rereview. Formal outcome/CPU performance unknown and will be assessed afterward with frozen parameters.
