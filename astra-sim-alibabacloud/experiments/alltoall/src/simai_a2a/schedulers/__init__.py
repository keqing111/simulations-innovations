"""Scheduler registry and shared deterministic schedule construction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..model import HardwareProfile, Schedule, ScheduledFlow


RawFlow = tuple[int, int, int, int, str, int]


def _finalize_schedule(
    algorithm: str,
    raw_flows: Iterable[RawFlow],
    hardware: HardwareProfile,
    predicted_makespan_ns: int | None,
    metadata: dict[str, Any],
) -> Schedule:
    ordered = sorted(
        raw_flows,
        key=lambda flow: (flow[3], flow[4], flow[5], flow[0], flow[1], flow[2]),
    )
    flows = [
        ScheduledFlow(index, src, dst, size, start, phase, stage)
        for index, (src, dst, size, start, phase, stage) in enumerate(ordered)
    ]
    for flow in flows:
        flow.validate(hardware)
    return Schedule(algorithm, flows, predicted_makespan_ns, metadata)


SCHEDULER_NAMES = ("direct-concurrent", "spreadout", "roundrobin", "fast",
                   "fast-wan", "fast-weighted", "fast-wan-weighted", "fast-cost",
                   "fast-native-nvidia", "fast-native-cost", "fast-native-mixed",
                   "fast-paper", "fast-paper-wan", "fast-paper-quantum",
                   "fast-paper-progress", "fast-paper-amortized", "fast-paper-adaptive",
                   "fast-cpp-native", "fast-cpp-maxmin", "fast-cpp-partition", "fast-cpp-adaptive")
SCHEDULER_NAMES += tuple(f'fast-joint-{objective}-b{budget}' for objective in ('stage','capacity')
                         for budget in (1,4))


def get_scheduler(name: str, *, fast_options: dict | None = None):
    if name in SCHEDULER_NAMES and name.startswith('fast-joint-'):
        from .joint import JointScheduler
        return JointScheduler(name, fast_options)
    if name in ('fast-paper','fast-paper-wan','fast-paper-quantum',
                'fast-paper-progress','fast-paper-amortized','fast-paper-adaptive',
                'fast-cpp-native','fast-cpp-maxmin','fast-cpp-partition','fast-cpp-adaptive'):
        from .paper_fast import PaperFastScheduler
        return PaperFastScheduler(name,fast_options)
    if name in ('fast-native-nvidia','fast-native-cost','fast-native-mixed'):
        from .fast_native import NativeFastScheduler
        return NativeFastScheduler(name,fast_options)
    if name == "direct-concurrent":
        from .direct import DirectConcurrentScheduler

        return DirectConcurrentScheduler()
    if name == "spreadout":
        from .spreadout import SpreadOutScheduler

        return SpreadOutScheduler()
    if name == "roundrobin":
        from .roundrobin import RoundRobinScheduler

        return RoundRobinScheduler()
    if name in ("fast", "fast-wan", "fast-weighted", "fast-wan-weighted", "fast-cost"):
        try:
            from .fast import FastScheduler
        except ImportError as exc:
            raise RuntimeError("FAST scheduler is not installed") from exc
        return FastScheduler(algorithm=name, fast_options=fast_options)
    accepted = ", ".join(sorted(SCHEDULER_NAMES))
    raise ValueError(f"unknown scheduler {name!r}; expected one of: {accepted}")


__all__ = ["get_scheduler"]
