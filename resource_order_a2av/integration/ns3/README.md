# Endpoint execution interface

This directory contains source interfaces, not a complete ns-3 distribution.

- `HistoricalNativeFastRuntime.h`: unchanged runtime used by the original adapter.
- `NativeFastRuntime.h`: wrapper selecting the original implementation for C=1,
  and pooled FIFO endpoint slots for C>1.
- `NativeFastTransport.h`: existing transport hooks supplying credits, payload
  visibility, ACK callbacks, fixed-flow identifiers and diagnostic events.
- `test_runtime.cc`: deterministic event-contract test without a packet simulator.
- `replay.py`: child-process adapter and slot/FIFO/event/byte/route validator.
  `run(..., build_manifest=...)` requires an explicitly supplied JSON build
  record containing `status="built"`, `binary`, `binary_sha256`, and a
  `libraries` dictionary mapping each linked ns-3 library path to its SHA256.
  No machine-specific build record is shipped. This adapter explicitly sets
  the concurrency variable in the child and uses the multi-slot validator.

Set `SIMAI_RESOURCE_ORDER_CONCURRENCY` in the **ns-3 process** to a positive slot
count (the contract test covers 1, 2, 4, 8). An unset variable selects C=1.
Each GPU has C send slots and C receive slots, independently. Receive reservation
begins at credit issue and ends at payload visibility. Send occupancy begins at
data release and ends at ACK. Endpoint FIFO order is preserved; an unready head
cannot be bypassed. Completions may arrive out of order and release pooled slots.
No physical link bandwidth is increased and no global barrier is inserted.

Use a separate backend build: retain the original runtime as
`HistoricalNativeFastRuntime.h`, place the wrapper next to it as
`NativeFastRuntime.h`, and rebuild the scratch application linked to the existing
transport. The transport already includes `NativeFastRuntime.h`.
This assumes the custom SimAI integration supplies its existing RDMA callbacks;
copying these headers alone into vanilla ns-3 is not a complete integration.

Important: the frozen Python `simai.run` clears inherited `SIMAI_*` variables,
so setting the variable only in its parent shell is insufficient. A caller that
uses the multi-slot build must pass the value explicitly in the child process
environment and validate active slot occupancy and FIFO order. The single-slot
`validate_events` routine must not be used as a multi-slot validator.

The multi-slot runtime is an execution-model sensitivity interface. It is not
an NCCL channel implementation and does not make the single-slot planner a
concurrency-aware optimizer. The original FAST local balance/dispatch and round
protocol modes are rejected for C>1 rather than silently changing their semantics.

The revision campaign drivers, logs, measurement tables and figures are
deliberately excluded from this code-only branch.
