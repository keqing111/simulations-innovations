# SDD ledger — plan: simulation/ordering/PLAN.md

2026-10-30. User approved diagnostic→single-construction ordering→same-budget ablation; inline execution without approval pauses.

Ruling: Existing authorized dirty checkout and source snapshots/new ordering outputs, no Git mutations — preserves established workflow; old data not overwritten.
Pre-flight Task1→Task2: diagnostics take exact q*, any supported perfect matching and true perGPU remaining; CLI must export initial true rows to independently validate virtual/real distinction.
Pre-flight Task2→Task3: exact whole-server renumbering is part of input identity; native original ordering can legitimately be numbering-sensitive, so compare paired robustness, don't demand identical schedules.
Ruling: SCC freedom analysis is offline and not used in policy decisions — prevents changing planning budget or charging diagnostics unevenly; cannot claim its runtime is online optimization cost or savings.

- Baseline scenario3 Python tests passed before changes, including historical CLI compatibility; user-owned native changes preserved.
- Task1 complete: watched missing support/order/row-pressure APIs fail compilation; then C++ literal unique/cycle/virtual/triangular support, row order and capacity-swap tests plus45 sparse decompositions passed.
- Task2 complete: watched missing harness fail; all4 Python tests passed including tiny independent matching enumeration(test-only),52/64 grid, exact matrix/DC renumbering, real7-policy CLI, order/pressure/diagnostic tamper rejection, frozen four-policy equality and28-result smoke audit.
- Task3: code and both suites frozen before outcomes: explore run-3529088c29dc(52×7),confirm run-f36c2931b947(64×7). Full regression running before starting host timing; no outcome inspected yet.

Task3: Ruling: First exploratory run stopped by an independent-audit tie mismatch atN12/D6/balanced,stage2: C++ pressure6367090.773333333 vs mathematically equivalent Python summation6367090.773333334. No algorithm error or policy retuning; independently validate pressure numerically, then validate row sorting against exported exact double values. Regression test observed RED(row visit order), then all5 Python tests GREEN after auditor-only fix; rerun all inputs under new source identities. Old incomplete52-case archive(prefix61records) and unstarted confirmation archive preserved and excluded from final results.

- Task3 complete: final explore run-5f741f90791652×7=364,confirm run-fd58e408d88f64×7=448; run and render each passed full semantic replay, input/source/hash, independent q* and route/pipeline/real-residual/pressure/order/SCC audits.11228 timing samples,8/10 distinct matrices. No rule retuning after initial outcome inspection.
- Extra data checks: same initial real rows/padded matrix across all6 FAST-like methods for116 cases;56balanced exact schedule checks(D=A1,DP=P);32 physical renumber pairs preserve canonical routed totals.
- Final tests: new5 Python plus native C++45 decompositions pass; prior29 Python tests plus C++ checks pass; total34. Archive-only rebuild source identity equal and21/21 semantic matches.17 native/model source fingerprints unchanged.
- One final independent read-only review completed; no Critical/Important finding. Final: minor (deferred): uninstrumented planning timing—current timers include initial_rows/order/pressure trace copies; retain frozen paths, disclose limit in REPORT/VALIDATION, don't treat tiny timing differences as deployment proof.
- Final: Ruling: reviewer declined full replay/statistical generalization/external fidelity/publication readiness—main executor has two full audits, but no claim of independent third replay, statistical significance, real-network fidelity or EI acceptance. Scope limits retained; costs if wrong are overgeneralization, not hidden missing implementation.
- Final observed result: confirm D/P/DP vsA1 communication0.998161/1.000826/1.002800,planning-inclusive0.999199/0.995002/0.995966. New ordering not promoted. Max-Min real-free stages97.14%balanced/99.02%Zipf; uniqueness hypothesis not supported. CurrentA1 vsMax-Min1.08862,vsnative1.59920,withplanning vsMax-Min1.06356.
- Final: Ruling: preserve in-place main checkout, ledger and old/new source/results, no Git integration/cleanup menu—follows established user workflow; costs if wrong are manual integration later, not data loss.
