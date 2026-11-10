# Independent FAST Objectives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Steps use checkbox syntax for tracking.

**Goal:** Implement and independently validate bandwidth-sharing and completion-time-guided matching without modifying annotated native code.

**Architecture:** New policy/driver and Python harness in this directory link hash-verified frozen native sources. Seven method labels isolate original matching, Max-Min, two separate objectives and SpreadOut.

**Tech Stack:** C++11/O3, Python standard library, g++, existing native FAST hooks.

**Spec:** `simulation/independent/DESIGN.md`.

## Global Constraints

- Create only this module and its owned outputs; no edits to existing sources or archived results.
- Preserve original byte demand, fixed paths and pipeline2; no combined strategy, new barrier or packet-network claims.
- Frozen source identity is 96b18da3da36; verify consumed sources before compile.
- No commit/push or cleanup of another agent's files.

## Review Focus

- Padding/self pairs cannot improve an objective by pretending to send WAN bytes.
- A time bottleneck matching can change coefficient or service volume; enforce exact-seed coefficient and nondecreasing real-byte guards.
- Current-stage improvements may increase later stages; disclose negative CCT and full counters.
- Reused mutable native locals must be reconstructed for every policy/repetition.
- All shared-resource timing and byte conservation must be reconstructed independently; a forged raw file must be rejected.

### Task 1: Independent native policy engine

**Files:** create `policy.h`, `policy.cpp`, `main.cc`, `build.py`, `tests/test_native.py`.

**Interfaces:** CLI `N G METHOD WAN_US SERVER_BW GATEWAY_BW WAN_BW DC_CSV REPEATS`, stdin unsigned rank matrix; JSON result or batch method map. `--zipf SEED COUNT` exposes the frozen generator.

- [x] Write tests for two literal mechanism fixtures, native/Max-Min/SpreadOut frozen compatibility, conservation, boundary rejection and degenerate demand.
- [x] Observe a failing CLI/build test before implementation.
- [x] Implement one bandwidth construction and minimum-bottleneck time matching with exact-coefficient/real-byte guards.
- [x] Verify tests, then add exhaustive small matching checks where necessary.

### Task 2: Frozen experiment and independent audit

**Files:** create `study.py`, `tests/test_study.py`, `Makefile`.

**Interfaces:** `--phase run|audit --output PATH [--smoke]`; prepare source/binary/inputs before results. Derive source ID and all input hashes; run and audit do not overwrite mismatched data.

- [x] Write smoke/audit/tamper tests and watch the expected failure.
- [x] Implement deterministic balanced/Zipf inputs, rotated repetitions, resource/pipeline reconstruction, paired comparisons and metadata.
- [x] Run the complete module suite and the frozen engine's relevant suite.

### Task 3: Bounded study and report

**Files:** create `REPORT.md`, update `PROGRESS.md`, generate owned results.

- [x] Run two mechanism fixtures and the fixed small grid; audit all raw records and method-equivalent baselines.
- [x] Report separate communication/CPU, propagation/serialization, stage counts and all negative outcomes.
- [x] Request one fresh read-only whole-module review; fix important findings with RED/GREEN tests.
- [x] Verify no pre-existing source was written and hand off without push or combination.
