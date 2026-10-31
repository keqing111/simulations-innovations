# WAN边界扫描 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, TDD; one fresh read-only final review. User opts out of approval pauses, Git writes and cleanup.

**Goal:** Quantify frozen A1 topology advantage boundaries, without changing any scheduler/model.
**Architecture:** Copy/validate the final planning engine, adapt its private Python scenario capacity boundary, independently reconstruct native outputs, and compare reoptimized/frozen-anchor schedules.
**Tech Stack:** Existing Python3/C++ binary/matplotlib, no new dependency.
**Spec:** DESIGN.md

## Global Constraints

-90cases/540outcomes,3rank matrices,6opt strategies, fixed BW25/100/400 and WAN50/1000/2500/3000/5000; no result-based retuning.
-Preserve dirty user changes/all old archives; no C++ edit/rebuild, no Git mutation. CPU times excluded from derived communication evidence.
-Frozen-anchor roots are not rerun-algorithm thresholds; separate known760203 from new810203, balanced not duplicated by seed.

## Review Focus

-Capacity override must affect both actual native CLI and independently reconstructed directed resources, not silently remain100Gbps.
-Frozen-cost evaluator must include max-based local hiding and SpreadOut local rounds; zero/absent WAN bytes cannot earn WAN propagation.
-Resume/audit must bind commands, matrix generator, source/binary identity, raw record set and derived tables; wrong/stale tables must not produce labelled figures.
-Nonmonotone/tied curves and coarse grid cannot be reported as unique or global thresholds.
-No new planning/scheduler code or misleading CPU/SOTA/deployment/generalization claims.

### Task1: Frozen runner, reconstruction and registration

Files: study.py, tests/test_study.py, Makefile.
Interfaces: specs(smoke=False); load_engine(run_dir); prepare/run/audit/checked; capacities(case).
- [x] RED grid/CLI-bandwidth behavior and real smoke48result+tamper tests.
- [x] GREEN archive frozen engine, generator cache,6single-policy calls, private capacity adaptation and reconstruction; strict resumed raw/derived audit.
- [x] All new tests green; source/native inputs unchanged.

### Task2: Counterfactual analysis and figures

Files: analysis.py, tests/test_analysis.py; REPORT.md/report assets.
Interfaces: frozen_cost(raw,case,bandwidths), schedule_signature(raw), roots_on_grid(fn,xs), analyze/render over audited registered records.
- [x] RED hand pipeline24/31.88µs, SpreadOut/zero-WAN, masked-resource/changed BW checks; multiple/tied/nonbracket root cases.
- [x] GREEN frozen repricing,540row joins, roots/rerun crossings and capacity bottleneck labels; source-bound plot generation.
- [x] Smoke/frozen-invariance checks green;90grid frozen before first formal result,540native outputs and audit; anchors48oldchecks; full prior41tests.
- [x] Report all strata/gaps, no retuning; final independent review completed with0Critical/0Important/0Minor; preserve workspace. No fix pass required.
