# Invalid registered-seed run — do not use for study conclusions

2026-10-29: preserved, not deleted or rewritten. Zipf constructor in simulation/test.h calls srand(time(NULL)). This run's new --zipf entry seeded before constructing it, so the declared seed was overridden. Matrices were saved and comparisons/replays within each saved input are valid, but these are NOT the intended registered Zipf inputs; WAN settings also did not consistently reuse a matrix.

Detected by comparing native stage counts against the previous study and inspecting the generator constructor. No algorithm or scoring parameters were selected from these outcomes. Root cause requires seed-after-constructor, frozen-generator regression, paired-input reuse and regeneration audit, followed by a new run/source identity. This directory is excluded from the final scan report.
