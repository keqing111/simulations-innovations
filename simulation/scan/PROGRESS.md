# SDD ledger — plan: simulation/scan/PLAN.md

Spec: research/topology_aware/OPTIMIZATION_SCAN.md, user approved 2026-10-29.

Execution: inline. Task evidence and deviations recorded below; preserve all existing user work.

Ruling: Work in existing user-approved dirty workspace rather than creating a Git worktree — all existing work and untracked modules are required inputs, user requested no Git workflow/interruption — isolation is source snapshots and new scan/ outputs, not a clean Git branch.
Ruling: Retain this on-disk plan/ledger instead of commit-based skill scripts — user requires no Git operations; preserving an auditable record takes precedence over temporary-script cleanup.
Pre-flight Task1→Task2: deferred prepare must expose app before decomposition; planner owns exactly one to_sdsm/decompose call.
Pre-flight Task2→Task3: new statistics must identify factor mask, thresholds, coefficient guard and actual single decomposition; harness independently checks payload/model, not scheduler scores.
Pre-flight Task3→Task4: source identity must freeze before results, all26 cases are development/diagnostic, repeat timings are not independent demands.

- Baseline: make -C simulation/bottleneck check GREEN (2 C++ binaries, 2 Python tests).
- Task1: complete. Missing-interface RED→entry_test GREEN, exact native/deferred stage/payload/pipeline identity and missing callback rejection.
- Task2: complete. RED absent module→make check GREEN; 24 random matrices across all8 masks+Max-Min; exact native/bounded/Max-Min identity; real-only pressure, explicit coefficient rejection, uint32 coarse threshold and exact support reconstruction.
- Task3: RED observed: scan/study.py absent in real CLI integration test; implementing snapshotted harness.
  Initial CLI integration GREEN (20 smoke results and frozen native/bounded/Max-Min/SpreadOut comparisons). Additional range test found repeat count4294967297 narrowing to1; reproduced RED, fixed validation before narrowing; rerunning suite.
  Task3 complete: make -C simulation/scan check GREEN (2 C++ binaries +3 Python tests,20 real smoke rows, tamper rejection); range fix RED→GREEN. Existing bottleneck and topology checks GREEN after optional hook change.
Ruling: SpreadOut comparison uses only actual SpreadOut stages/bytes/model total, not old bridge's unrelated FAST balance/dispatch metadata — new SpreadOut does not generate a discarded FAST schedule — communication semantics must remain identical.
- Task4: pending.
- Task4 first execution run-f5a6696f2ed1:260 rows replayed and104 same-input frozen baseline comparisons passed, but REGISTERED SEEDS INVALID. Found differing original stage counts across WAN settings; traced to test.h ctor srand(time). New driver seeded before constructor. Preserved run with INVALID.md; no parameter tuning; fix and rerun required.
  Generator fix: frozen256-weight comparison RED (clock-seeded output differed), then seed AFTER constructor. Regeneration audit test RED (modified matrix +matching digest wrongly accepted), then cache identical inputs across WAN and independently regenerate registered inputs during checked(). Both new tests precede fixes; suite rerun required.
  Corrected suite GREEN:2 C++ binaries +5 Python tests, including both new RED→GREEN regressions,20-row smoke and all historical-equivalence checks. Re-freezing new identity and rerunning all260 outcomes/21 repeats; no candidate/scoring/budget changes.
- Task5: pending.
- Task4 complete: corrected run-7be22345a21c260/260 outcomes +5460 measured planning samples, all semantic replays and registered13 inputs pass.104 frozen historical method checks pass,26 matrices exactly match previous intended inputs. Linear plots rendered and report filled; first invalid run excluded.
Ruling: Static Topology evaluator construction remains untimed input setup; ResourceCache compilation is measured inside each full plan — scope disclosed, no first-cold-start end-to-end claim.
Ruling: Historical-equivalence audit requires preserved previous run native_bottleneck binary; new archive supports current native replay but does not regenerate old binary — external artifact requirement disclosed.
Final review: fresh reviewer reviewed code, seed fix, new tests, all260 corrected raw records,21 timing counts, source archive/seed regeneration and26 historical matrix equalities. Initial Important seed/provenance finding fixed by two RED→GREEN regressions and full new run. No additional Critical/Important finding reported.
Final: minor (deferred): fully self-contained historical baseline equivalence still needs previous binary; dependency disclosed rather than duplicating old archives.
Ruling: Reviewer completed code/data review then hit provider usage limit before reading final result prose — main executor checks final prose against raw/CSV and explicitly discloses this boundary, no claim of full independent text review.
Task5 complete: final fresh scan check GREEN (2C++ +5Python), old bottleneck GREEN (2C++ +2Python), old topology GREEN (2C++ +8Python), paper reproduction GREEN (7Python). Archived-source-only full260 audit passes; clean rebuild from extracted archive reproduces20 method outcomes for positive/counterexample fixtures. Report headline numerics,5460 timing samples, all local links and PNG headers checked. No Git mutation or cleanup.
Final status: approved initial scan implementation and development evaluation complete. No independent new-seed validation, no next-round algorithm variants executed. Old invalid run retained and excluded.
