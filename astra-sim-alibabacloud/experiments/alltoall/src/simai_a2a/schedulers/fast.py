"""Adapter from the C++-faithful FAST port to the common schedule model."""

from __future__ import annotations

import numpy as np

from ..model import HardwareProfile, Schedule, TrafficMatrix
from . import RawFlow, _finalize_schedule
from . import fast_port


class FastScheduler:
    def __init__(self, scaling: bool = False, *, algorithm: str = "fast",
                 fast_options: dict | None = None) -> None:
        self.scaling = scaling
        self.algorithm = algorithm
        self.fast_options = fast_options
        self.profile = None
        if algorithm != 'fast':
            from .fast_wan import PairProfile
            if scaling:
                raise ValueError('FAST WAN extensions require scaling=False')
            self.profile = PairProfile.from_dict(fast_options)

    def schedule(
        self,
        matrix: TrafficMatrix,
        hardware: HardwareProfile,
        *,
        seed: int,
    ) -> Schedule:
        hardware.validate_matrix(matrix)
        if self.profile is not None and len(self.profile.server_dc_ids) != hardware.num_servers:
            raise ValueError('server_dc_ids length must equal num_servers')
        ids = np.arange(matrix.num_devices) // hardware.gpus_per_server
        if hardware.num_servers == 1 or not np.any(matrix.values[ids[:, None] != ids[None, :]]):
            raw: list[RawFlow] = []
            stage_starts: list[int] = []
            slot_ends: list[int] = []
            current_start = 0
            for offset in range(1, hardware.gpus_per_server):
                stage_starts.append(current_start)
                stage_duration = 0
                for src in range(hardware.num_devices):
                    base = src // hardware.gpus_per_server * hardware.gpus_per_server
                    dst = base + (src + offset) % hardware.gpus_per_server
                    size = int(matrix.values[src, dst])
                    if size > 0:
                        raw.append(
                            (src, dst, size, current_start, "intrinsic", offset - 1)
                        )
                        stage_duration = max(
                            stage_duration,
                            hardware.intra.duration_ns(size),
                        )
                current_start += stage_duration
                slot_ends.append(current_start)
            metadata = {
                "seed": seed,
                "scaling": self.scaling,
                "logical_bytes": matrix.logical_bytes,
                "physical_bytes": sum(flow[2] for flow in raw),
                "bound_ns": current_start,
                "baseline_spreadout_ns": current_start,
                "speedup_vs_spreadout": 1.0,
                "balance_end_ns": 0,
                "stage_starts_ns": stage_starts,
                "slot_ends_ns": slot_ends,
                "final_dispatch_start_ns": current_start,
                "n_stages": 0,
                "degenerate_single_server": hardware.num_servers == 1,
                "degenerate_local_only": True,
            }
            return _finalize_schedule(
                self.algorithm,
                raw,
                hardware,
                current_start,
                metadata,
            )
        inter = {
            "alpha": hardware.inter.alpha_ns / 1000.0,
            "beta": hardware.inter.beta_ns_per_byte * 1000.0,
        }
        intra = {
            "alpha": hardware.intra.alpha_ns / 1000.0,
            "beta": hardware.intra.beta_ns_per_byte * 1000.0,
        }
        if self.algorithm != "fast":
            from .fast_wan import run_wan_full
            result = run_wan_full(
                matrix.values, hardware.num_servers, hardware.gpus_per_server,
                inter, intra, self.profile,
                wan_priority=(self.algorithm in ("fast-wan", "fast-wan-weighted") or
                              self.algorithm == 'fast-cost' and
                              self.profile.inter_dc_bandwidth_gbps < self.profile.intra_dc_bandwidth_gbps),
                weighted="weighted" in self.algorithm,
                cost_priority=self.algorithm == "fast-cost",
            )
        else:
            result = fast_port.run_fast_full(
                matrix.values,
                hardware.num_servers,
                hardware.gpus_per_server,
                "ETHER400",
                "H100",
                scaling=self.scaling,
                MBpu=1e-6,
                pipeline="p2",
                inter_override=inter,
                intra_override=intra,
            )
        legacy_flows, timeline = fast_port.emit_schedule(result, MBpu=1e-6)
        raw: list[RawFlow] = []
        for flow, extra in zip(legacy_flows, timeline["flow_metadata"]):
            src, dst, size, start_us = flow
            raw.append(
                (
                    int(src),
                    int(dst),
                    int(size),
                    int(round(float(start_us) * 1000.0)),
                    str(extra["phase"]),
                    int(extra["stage_id"]),
                )
            )

        physical_bytes = sum(flow[2] for flow in raw)
        metadata = {
            "seed": seed,
            "scaling": self.scaling,
            "logical_bytes": matrix.logical_bytes,
            "physical_bytes": physical_bytes,
            "bound_ns": int(round(float(result["bound"]) * 1000.0)),
            "baseline_spreadout_ns": int(round(float(result["baseline"]) * 1000.0)),
            "speedup_vs_spreadout": float(result["speedup"]),
            "balance_end_ns": int(round(float(timeline["t_bal_us"]) * 1000.0)),
            "stage_starts_ns": [
                int(round(float(value) * 1000.0))
                for value in timeline["stage_starts_us"]
            ],
            "slot_ends_ns": [
                int(round(float(value) * 1000.0))
                for value in timeline["slot_ends_us"]
            ],
            "final_dispatch_start_ns": int(
                round(float(timeline["final_dispatch_start_us"]) * 1000.0)
            ),
            "n_stages": int(timeline["n_stages"]),
            "dispatch_semantics_version": 2,
        }
        if "stage_records" in result:
            metadata.update(stage_records=result['stage_records'],
                            synthesis=result['synthesis'],
                            real_server_demand=result['real_server_demand'],
                            fast_options=self.fast_options)
            if self.algorithm == 'fast-cost':
                metadata['cost_model'] = {
                    'weight': 'max_pair_bandwidth / pair_bandwidth',
                    'priority': 'real_per_gpu_residual_bytes * (weight - 1)',
                    'ties': 'native adjacency order',
                    'rtt_usage': 'physical ns-3 propagation only; no scheduler release delay',
                    'byte_extraction': 'native minimum positive completed residual',
                }
        else:
            metadata['stage_records'] = [
                dict(stage_id=k, mp=p['mp'], quota_per_gpu_bytes=[p['freq']]*hardware.num_servers)
                for k, p in enumerate(result['p_sets'])
            ]
        return _finalize_schedule(
            self.algorithm,
            raw,
            hardware,
            int(round(float(result["t"]) * 1000.0)),
            metadata,
        )
