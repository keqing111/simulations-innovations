# Fresh review package

Read PLAN.md, PROGRESS.md, REPORT.md and new files model.h/.cpp,matching.h/.cpp,main.cc,study.py,Makefile,tests/* under this directory. Original repo /home/julius/fast/FAST; native modifications only algorithm/all2all.h/.cpp,scheduler/global.h/.cpp,local.h/.cpp. Existing cstring additions are not this task. User requests no SimAI/ns3 and main original FAST + SpreadOut baselines, lightweight topology-aware Birkhoff, preserve original pipeline.

Implementation is ready, fixed 40-input/240-run grid registered in source BEFORE observing full results. Small real6-method smoke passes; original compatibility30inputs×2modes passes; hand/model/matching tests pass. Formal experiment/figures/report fill still pending intentionally until algorithm review. Reviewer focus correctness and scientific validity, do not ask to rerun old SimAI results or conflate CPU native model with GPU runtime. No code edits by reviewer; no children.

Review Focus (from plan):
1. Native self-match/virtual accounting differs from real payload; validate no user WAN bytes fabricated or omitted.
2. Native units and per-GPU vs server volume; hand-calculated shared-WAN and homogeneous tests.
3. Full original pipeline alignment vs custom adapter; pre-change oracle binary and original stage formula tests.
4. Every method shares evaluator and preserves preprocessing; no hidden execution changes or chosen-result filtering.
5. Malformed route/matrix/32-bit range, empty matrices, stage coverage and incomplete artifacts reject or handle explicitly.

Rulings and costs in PROGRESS.md. Full archival source, baseline old oracle+archive included byprepare. Need Critical/Important/Minor with exact locations and concrete reproducer where possible; include areas declined to judge. One final reviewer only; implementer makes one RED→GREEN fix pass, no rereview.
