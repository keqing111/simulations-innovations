# Final read-only review package

Repo /home/julius/fast/FAST. User authorized in-place implementation on existing dirty main, no commits/push/cleanup. Git history does not represent this delta; compare the sources listed below. Do NOT review/revert unrelated pre-existing native/comment modifications.

## Requirements

PLAN.md, DESIGN.md, ledger PROGRESS.md including all Ruling lines; main outcome REPORT.md and PAPER_OUTLINE.md. Freeze A1 schedules; reduce planning implementation cost; add honest same-model SPM control; preserve original timing and stage semantics.

## Delta

Base source: simulation/ordering/results/run-fd58e408d88f/source/scan/{planner.h,planner.cpp,main.cc}.
Final source: simulation/scan/{planner.h,planner.cpp,main.cc}; compile snapshot simulation/planning/results/final-96b18da3da36/source/scan/. Current source identity verified equal to build.json. Other13native tracked sources checked unchanged in protected_sources.json.

New files: simulation/planning/Makefile, tests/, study.py, validation/{isolated.py,bridge.py,report_figures.py,test_*.py}, reports/design/plan/progress/validation; one link in research/topology_aware/README.md.

Final keeps only pooled resource matcher and finite-score stable-tie sort; threshold fast DFS tried in V1 was reverted after measured regressions. No new scheduling heuristic beyond SPM baseline; A1 math unchanged. Default unsuffixed modes remain reference; explicit A1-opt is the optimized implementation.

## Evidence

- Final41Python + C++ tests green; VALIDATION.md commands and counts.
- V1 frozen run-45a8ce2df36d / run-600edb9c2a2c:480outcomes/11520timings; all240implementation pairs identical.
- Final-96b18da3da36/checks.json:480full semantic equalities plus116historical archive/binary/input equalities. Final bridge timings not used for performance.
- Final CPU isolated-final-96b18da3da36:8N48configs,96outcomes,2304timings; different process per policy, one warmup, reversed2blocks×12. No-op controls ~1; A1 GM1.1153; Zipf152.973→136.509ms medians. report_assets/metrics.json and provenance.json.
- Communication confirm balanced A1/MaxMin1.167,A1/SPM1.103; Zipf1.046/1.020; duplicates and6ZipfSPM losses disclosed. Worst A1 case has5extraWANstages; report frozen critical-point argument is NOT an algorithm crossover guarantee.

## Review Focus (verbatim from PLAN)

- Workspace reuse after failed augmentation, recursively displaced rows and zero/virtual demand must match reference exactly.
- Nonallocating sort must reproduce stable ties; fall back on nonfinite scoring, don't change floating arithmetic.
- Weighted matching must exclude forbidden edges, conserve payload, achieve exact assignment objective; no dummy reward.
- Same-process round-robin and cache/preprocess/trace costs must be charged symmetrically; paired semantics cannot compare only totals.
- Historical equality uses archived binary/input; no claiming fully self-contained past dependencies or external solver reproduction.

## Reviewer instructions

Use requesting-code-review/code-reviewer.md template: strengths; Critical/Important/Minor findings with exact file:line, effect and fix; recommendations; verdict; exhaustive Declined to judge list (empty if none). The spec is a vision, judge reasonable-user expectations too. One read-only reviewer only, no subagents, no checkout/index/HEAD/source/data mutation. Existing compiled tests may be run; any fresh test must use temporary files outside this checkout. Avoid processing huge archives unnecessarily; inspect manifests/metrics and selected raw cases as needed. Do not approve EI novelty/acceptance as a fact. Review final work, not speculative future directions.
