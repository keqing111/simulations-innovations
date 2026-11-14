"""FAST's native GPU-level cyclic-diagonal SpreadOut baseline."""

from ..model import HardwareProfile, Schedule, TrafficMatrix
from . import RawFlow, _finalize_schedule


class SpreadOutScheduler:
    def schedule(
        self,
        matrix: TrafficMatrix,
        hardware: HardwareProfile,
        *,
        seed: int,
    ) -> Schedule:
        hardware.validate_matrix(matrix)
        raw: list[RawFlow] = []
        current_start = 0
        stage_starts: list[int] = []
        for offset in range(1, matrix.num_devices):
            stage_id = offset - 1
            stage_starts.append(current_start)
            duration = 0
            for src in range(matrix.num_devices):
                dst = (src + offset) % matrix.num_devices
                size = int(matrix.values[src, dst])
                if size == 0:
                    continue
                link = (
                    hardware.intra
                    if src // hardware.gpus_per_server == dst // hardware.gpus_per_server
                    else hardware.inter
                )
                duration = max(duration, link.duration_ns(size))
                raw.append((src, dst, size, current_start, "direct", stage_id))
            current_start += duration
        return _finalize_schedule(
            "spreadout",
            raw,
            hardware,
            current_start,
            {
                "seed": seed,
                "logical_bytes": matrix.logical_bytes,
                "physical_bytes": sum(flow[2] for flow in raw),
                "stage_starts_ns": stage_starts,
                "n_stages": matrix.num_devices-1,
                "stage_records": [dict(rank_mp=[(rank+offset)%matrix.num_devices
                    for rank in range(matrix.num_devices)]) for offset in range(1,matrix.num_devices)],
                "native_round_protocol": True,
                "baseline_scope": "cyclic rank-level spreadout, not an NCCL protocol reproduction",
            },
        )
