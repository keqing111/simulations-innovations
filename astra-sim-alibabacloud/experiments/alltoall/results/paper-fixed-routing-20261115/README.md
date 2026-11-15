# Fixed-routing paper experiment evidence

24conditions,96valid main records:32older pilot records+4first natural mechanism records reused after verification,60new main backend runs. `paired.csv` is the full24condition table. `paper_summary.json` usesVolume-MaxQ as primary:0.9993345x,10wins4ties10losses. The1.8135x controlled result is separate, exploratory, not included in that mean.

Paper artifacts are exactly `figure1_end_to_end.{png,pdf}`, `figure2_mechanism.{png,pdf}` and `paper_table.csv`. Zipf bars show mean/min/max across three input seeds, not a confidence interval. Spreadout appears only in the summary table; generic audit plots retained locally are not paper figures.

`first_differences.json` records20first same-R/q* matching differences; `plan_comparison.csv` distinguishes localq* equality from equal full coefficient sequences. All4equal plans are Leaf balanced controls;6full coefficient sequences are equal in total.

`paired-evidence.tar.gz` includes all synthetic inputs, plans/FCT/diagnostics and two controlled variants' detailed timing evidence. Its per-member hashes are checked. The initial independent-endpoint manual-overlap run timed out after a nativefinished event and remains invalid; it is not retried, not silently promoted to a successful performance datum. The six-active-server shared-endpoint variant has4successful runs, andactual Resourcep/q exactly equal hand-overlap. Main96records are all successful. Overall unique records including the8extra control records are103successful+1invalid.

Launch-time source files and `source_hashes.json` remain unchanged as snapshots. After review, the experiment controller's cache guard was strengthened to regenerate every flow, full metadata/local timers and predicted time, not onlyp/q. All36cached runs passed this read-only check with no new simulator execution; see `post_review_reuse_audit.json` and `post_review_controller_snapshot.py`. `figure_analysis_snapshot.py` records figure generation. No scheduler, balance, dispatch or congestion semantics changed.

Local absolute paths in historical records describe provenance, not fresh-clone availability. Extracted CSV/figures are readable without the backend. The evidence bundle contains synthetic data only and no binary/credentials. A fresh-clone backend build has not been verified. Tests106passed6skipped in scope; fullsuite258passed11skipped with13known inherited issues, listed in `verification.json`.

Chinese narrative and draft: `research/resource_a2av/PAPER_EXPERIMENT_RESULTS.md`, `PAPER_EXPERIMENT_SECTION_ZH.md`.
