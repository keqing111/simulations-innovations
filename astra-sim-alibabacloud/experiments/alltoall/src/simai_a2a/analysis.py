"""Strict FCT joins and metrics for external all-to-all schedules."""

from __future__ import annotations

import json
import math
import csv
from pathlib import Path
from typing import Any

from .artifacts import read_matrix_artifact, read_schedule_artifact
from .model import Schedule


def _node_from_hex_ip(value: str) -> int:
    try:
        encoded = int(value, 16)
    except ValueError as exc:
        raise ValueError(f"invalid hexadecimal IP field: {value!r}") from exc
    return (encoded >> 8) & 0xFFFF


def _percentile(values: list[int], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of no observations")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def read_external_fct(path: Path) -> dict[int, dict[str, int]]:
    """Read the external nine-column ns-3 FCT format keyed by schedule row."""
    observations: dict[int, dict[str, int]] = {}
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 9:
            raise ValueError(f"FCT line {line_number} must have nine columns")
        try:
            flow_id = int(fields[8], 10)
            record = {
                "src": _node_from_hex_ip(fields[0]),
                "dst": _node_from_hex_ip(fields[1]),
                "size_bytes": int(fields[4], 10),
                "actual_start_ns": int(fields[5], 10),
                "fct_ns": int(fields[6], 10),
                "standalone_fct_ns": int(fields[7], 10),
            }
        except ValueError as exc:
            raise ValueError(f"FCT line {line_number} contains an invalid integer") from exc
        if flow_id < 0 or any(value < 0 for value in record.values()):
            raise ValueError(f"FCT line {line_number} contains a negative value")
        if flow_id in observations:
            raise ValueError(f"duplicate FCT flow_id {flow_id}")
        observations[flow_id] = record
    return observations


def fast_stage_violations(
    schedule: Schedule,
    observations: dict[int, dict[str, int]],
    *,
    collective_epoch_ns: int,
) -> dict[str, Any]:
    """Measure spill from each scheduled start slot into its next slot."""
    starts = sorted({flow.start_ns for flow in schedule.flows})
    boundaries: list[dict[str, int]] = []
    for previous_start, next_start in zip(starts, starts[1:]):
        previous_ids = [
            flow.flow_id for flow in schedule.flows if flow.start_ns == previous_start
        ]
        previous_end = max(
            observations[flow_id]["actual_start_ns"]
            + observations[flow_id]["fct_ns"]
            - collective_epoch_ns
            for flow_id in previous_ids
        )
        violation = max(0, previous_end - next_start)
        boundaries.append(
            {
                "previous_start_ns": previous_start,
                "next_start_ns": next_start,
                "actual_previous_slot_end_ns": previous_end,
                "violation_ns": violation,
            }
        )
    violations = [item["violation_ns"] for item in boundaries]
    count = sum(value > 0 for value in violations)
    return {
        "boundary_count": len(boundaries),
        "violation_count": count,
        "violation_fraction": count / len(boundaries) if boundaries else 0.0,
        "max_violation_ns": max(violations, default=0),
        "sum_violation_ns": sum(violations),
        "boundaries": boundaries,
    }


def analyze_pfc_events(path: Path) -> dict[str, Any]:
    """Summarize ns-3 QBB PFC pause/resume trace records."""
    events: list[tuple[int, int, int, int, int]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"PFC line {line_number} must have five columns")
        try:
            event = tuple(int(field, 10) for field in fields)
        except ValueError as exc:
            raise ValueError(f"PFC line {line_number} contains an invalid integer") from exc
        if event[0] < 0 or event[1] < 0 or event[2] < 0 or event[3] < 0:
            raise ValueError(f"PFC line {line_number} contains a negative value")
        if event[4] not in (0, 1):
            raise ValueError(f"PFC line {line_number} has invalid event type")
        events.append(event)
    event_times = [event[0] for event in events]
    return {
        "event_count": len(events),
        "pause_count": sum(event[4] == 1 for event in events),
        "resume_count": sum(event[4] == 0 for event in events),
        "affected_port_count": len({(event[1], event[3]) for event in events}),
        "first_event_ns": min(event_times, default=None),
        "last_event_ns": max(event_times, default=None),
    }


def analyze_fct(
    matrix_dir: Path,
    schedule_dir: Path,
    fct_path: Path,
    pfc_path: Path,
    qlen_path: Path | None = None,
    bw_path: Path | None = None,
    rate_path: Path | None = None,
    cnp_path: Path | None = None,
    tor_id: int | None = None,
    tor_ids: tuple[int, ...] | list[int] | None = None,
    gateway_ids: tuple[int, ...] | list[int] | None = None,
    gateway_wan_ports: tuple[tuple[int, int], ...] | list[list[int]] | None = None,
    tor_port_capacity_gbps: float | None = None,
    gateway_port_capacity_gbps: float | None = None,
) -> dict[str, Any]:
    matrix, matrix_manifest = read_matrix_artifact(matrix_dir)
    schedule, schedule_manifest = read_schedule_artifact(schedule_dir)
    if schedule_manifest["matrix_sha256"] != matrix_manifest["matrix_sha256"]:
        raise ValueError("schedule and matrix hashes do not match")
    observations = read_external_fct(fct_path)
    expected_ids = set(range(len(schedule.flows)))
    actual_ids = set(observations)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unknown = sorted(actual_ids - expected_ids)
        raise ValueError(f"FCT flow IDs differ; missing={missing}, unknown={unknown}")
    for flow in schedule.flows:
        observed = observations[flow.flow_id]
        expected = (flow.src, flow.dst, flow.size_bytes)
        actual = (observed["src"], observed["dst"], observed["size_bytes"])
        if actual != expected:
            raise ValueError(
                f"FCT flow_id {flow.flow_id} tuple mismatch: expected {expected}, got {actual}"
            )

    release_candidates = [
        observations[flow.flow_id]["actual_start_ns"] - flow.start_ns
        for flow in schedule.flows
    ]
    if not schedule.flows and schedule.metadata.get('execution_model') not in (
            'stage-barrier-ideal-scaleup','fast-nvidia-deps-ideal-scaleup','fast-nvidia-deps-modeled-scaleup'):
        raise ValueError('empty network schedule requires explicit ideal scale-up')
    collective_epoch_ns = min(release_candidates, default=0)
    actual_end_ns = max(
        observation["actual_start_ns"] + observation["fct_ns"]
        for observation in observations.values()
    ) if observations else 0
    native_runtime=None
    if schedule.metadata.get('execution_model') in ('fast-nvidia-deps-ideal-scaleup','fast-nvidia-deps-modeled-scaleup'):
        from .native_analysis import read_native_runtime
        native_runtime=read_native_runtime(schedule,Path(fct_path).parent/'native.csv.events.csv')
        collective_epoch_ns=native_runtime['epoch_ns']
        modeled=schedule.metadata['execution_model']=='fast-nvidia-deps-modeled-scaleup'
        if actual_end_ns>native_runtime['end_ns'] or (not modeled and actual_end_ns!=native_runtime['end_ns']):
            raise ValueError('native runtime finish does not match observed payload ACK completion')
        actual_end_ns=native_runtime['end_ns']
    actual_makespan_ns = actual_end_ns - collective_epoch_ns
    fcts = [observation["fct_ns"] for observation in observations.values()]
    predicted = schedule.predicted_makespan_ns
    pfc = analyze_pfc_events(pfc_path)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "algorithm": schedule.algorithm,
        "execution_model": schedule.metadata.get('execution_model', 'open-loop'),
        "flow_count": len(schedule.flows),
        "collective_epoch_ns": collective_epoch_ns,
        "actual_makespan_ns": actual_makespan_ns,
        "predicted_makespan_ns": predicted,
        "fidelity_ratio": (
            actual_makespan_ns / predicted if predicted is not None and predicted > 0 else None
        ),
        "logical_bytes": matrix.logical_bytes,
        "physical_bytes": schedule.physical_bytes,
        "scheduled_bytes": schedule.physical_bytes,
        "schedule_byte_amplification": (
            schedule.physical_bytes / matrix.logical_bytes
            if matrix.logical_bytes > 0
            else None
        ),
        "algorithm_bandwidth_gbps": (
            matrix.logical_bytes * 8 / actual_makespan_ns
            if actual_makespan_ns > 0
            else None
        ),
        "per_device_algorithm_bandwidth_gbps": (
            matrix.logical_bytes * 8 / actual_makespan_ns / matrix.num_devices
            if actual_makespan_ns > 0
            else None
        ),
        "fct_ns": {
            "mean": sum(fcts) / len(fcts) if fcts else None,
            "p50": _percentile(fcts, 0.50) if fcts else None,
            "p95": _percentile(fcts, 0.95) if fcts else None,
            "p99": _percentile(fcts, 0.99) if fcts else None,
            "max": max(fcts, default=None),
        },
        "pfc_count": pfc["event_count"],
        "pfc": pfc,
    }
    if len({flow.start_ns for flow in schedule.flows}) > 1:
        violations = fast_stage_violations(
            schedule,
            observations,
            collective_epoch_ns=collective_epoch_ns,
        )
        summary["stage_violations"] = violations
        if schedule.algorithm == "fast":
            summary["fast_stage_violations"] = violations
    if summary['execution_model'] == 'stage-barrier-ideal-scaleup':
        from .execution import stage_barrier_statistics
        summary['stage_barrier'] = stage_barrier_statistics(schedule, observations)
    if native_runtime is not None:
        summary['native_runtime']=native_runtime
        from .native_analysis import analyze_native_links
        summary['native_wan']=analyze_native_links(schedule,Path(fct_path).parent,
                                                  {'gateway_ids':list(gateway_ids or [])}) if schedule.flows else {'ports':[]}
    if qlen_path is not None or bw_path is not None:
        summary["network"] = analyze_network_monitors(
            qlen_path,
            bw_path,
            tor_id=tor_id,
            tor_ids=tor_ids,
            gateway_ids=gateway_ids,
            gateway_wan_ports=gateway_wan_ports,
            tor_port_capacity_gbps=tor_port_capacity_gbps,
            gateway_port_capacity_gbps=gateway_port_capacity_gbps,
        )
    if rate_path is not None or cnp_path is not None:
        summary["congestion_control"] = analyze_congestion_monitors(
            rate_path,
            cnp_path,
        )
    return summary


def _monitor_rows(path: Path | None) -> list[list[str]]:
    if path is None or not Path(path).is_file():
        return []
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, skipinitialspace=True)
        next(reader, None)
        return [[field.strip() for field in row] for row in reader if row]


def analyze_congestion_monitors(
    rate_path: Path | None,
    cnp_path: Path | None,
) -> dict[str, Any]:
    rate_rows = _monitor_rows(rate_path)
    cnp_rows = _monitor_rows(cnp_path)
    rates: list[int] = []
    for row in rate_rows:
        if len(row) != 7:
            raise ValueError("QP rate monitor row must have seven columns")
        rates.append(int(row[6]))
    cnp_by_flow: dict[tuple[int, int, int, int], int] = {}
    cnp_times: list[int] = []
    for row in cnp_rows:
        if len(row) != 7:
            raise ValueError("CNP monitor row must have seven columns")
        time_ns = int(row[0])
        key = tuple(int(field) for field in row[1:5])
        count = int(row[6])
        cnp_times.append(time_ns)
        cnp_by_flow[key] = max(cnp_by_flow.get(key, 0), count)
    return {
        "qp_rate": {
            "change_record_count": len(rates),
            "min_bps": min(rates, default=None),
            "max_bps": max(rates, default=None),
        },
        "cnp": {
            "record_count": len(cnp_rows),
            "total_count": sum(cnp_by_flow.values()),
            "affected_flow_count": len(cnp_by_flow),
            "first_event_ns": min(cnp_times, default=None),
            "last_event_ns": max(cnp_times, default=None),
        },
    }


def analyze_network_monitors(
    qlen_path: Path | None,
    bw_path: Path | None,
    *,
    tor_id: int | None = None,
    tor_ids: tuple[int, ...] | list[int] | None = None,
    gateway_ids: tuple[int, ...] | list[int] | None = None,
    gateway_wan_ports: tuple[tuple[int, int], ...] | list[list[int]] | None = None,
    tor_port_capacity_gbps: float | None = None,
    gateway_port_capacity_gbps: float | None = None,
) -> dict[str, Any]:
    qrows = _monitor_rows(qlen_path)
    brows = _monitor_rows(bw_path)
    qvalues: list[tuple[int, int, int, int]] = []
    for row in qrows:
        if len(row) != 6:
            raise ValueError("queue monitor row must have six columns")
        qvalues.append((int(row[1]), int(row[2]), int(row[4]), int(row[5])))
    bvalues: list[tuple[int, int, float]] = []
    for row in brows:
        if len(row) != 4:
            raise ValueError("bandwidth monitor row must have four columns")
        bvalues.append((int(row[1]), int(row[2]), float(row[3])))
    selected_tors = set(tor_ids or (() if tor_id is None else (tor_id,)))
    selected_gateways = set(gateway_ids or ())
    selected_gateway_ports = (
        {(int(node), int(port)) for node, port in gateway_wan_ports}
        if gateway_wan_ports is not None
        else None
    )
    tor_q = [value for node, _, value, _ in qvalues if node in selected_tors]
    tor_port_q = [value for node, _, _, value in qvalues if node in selected_tors]
    tor_bw = [value for node, _, value in bvalues if node in selected_tors]
    def is_gateway_port(node: int, port: int) -> bool:
        if node not in selected_gateways:
            return False
        return selected_gateway_ports is None or (node, port) in selected_gateway_ports

    gateway_q = [
        value for node, port, value, _ in qvalues if is_gateway_port(node, port)
    ]
    gateway_port_q = [
        value for node, port, _, value in qvalues if is_gateway_port(node, port)
    ]
    gateway_bw = [
        value for node, port, value in bvalues if is_gateway_port(node, port)
    ]
    nonzero_bw = [value for _, _, value in bvalues if value > 0]
    network: dict[str, Any] = {
        "queue": {
            "row_count": len(qvalues),
            "peak_queue_bytes": max((value for _, _, value, _ in qvalues), default=0),
            "peak_port_bytes": max((value for _, _, _, value in qvalues), default=0),
            "tor_peak_queue_bytes": max(tor_q, default=0),
            "tor_peak_port_bytes": max(tor_port_q, default=0),
            "gateway_peak_queue_bytes": max(gateway_q, default=0),
            "gateway_peak_port_bytes": max(gateway_port_q, default=0),
        },
        "bandwidth": {
            "row_count": len(bvalues),
            "peak_gbps": max((value for _, _, value in bvalues), default=0.0),
            "mean_active_gbps": (
                sum(nonzero_bw) / len(nonzero_bw) if nonzero_bw else 0.0
            ),
            "tor_peak_gbps": max(tor_bw, default=0.0),
            "tor_mean_active_gbps": (
                sum(value for value in tor_bw if value > 0)
                / sum(value > 0 for value in tor_bw)
                if any(value > 0 for value in tor_bw)
                else 0.0
            ),
            "gateway_peak_gbps": max(gateway_bw, default=0.0),
            "gateway_mean_active_gbps": (
                sum(value for value in gateway_bw if value > 0)
                / sum(value > 0 for value in gateway_bw)
                if any(value > 0 for value in gateway_bw)
                else 0.0
            ),
        },
    }
    if tor_port_capacity_gbps:
        network["bandwidth"]["tor_peak_utilization"] = (
            max(tor_bw, default=0.0) / tor_port_capacity_gbps
        )
        active_tor = [value for value in tor_bw if value > 0]
        network["bandwidth"]["tor_mean_active_utilization"] = (
            (sum(active_tor) / len(active_tor) if active_tor else 0.0)
            / tor_port_capacity_gbps
        )
    if gateway_port_capacity_gbps:
        network["bandwidth"]["gateway_peak_utilization"] = (
            max(gateway_bw, default=0.0) / gateway_port_capacity_gbps
        )
        active_gateway = [value for value in gateway_bw if value > 0]
        network["bandwidth"]["gateway_mean_active_utilization"] = (
            (sum(active_gateway) / len(active_gateway) if active_gateway else 0.0)
            / gateway_port_capacity_gbps
        )
    return network


def write_summary(summary: dict[str, Any], path: Path) -> None:
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def analyze_wan_bandwidth(path: Path, ports, *, epoch_ns: int, makespan_ns: int,
                          interval_ns: int, capacity_gbps: float) -> dict:
    """Integrate sparse samples over the collective, explicitly filling zeros.

    Monitor timestamps are right edges of fixed sampling windows. Busy/idle
    are sample-level statistics, not packet-exact serialization intervals.
    """
    if interval_ns <= 0 or capacity_gbps <= 0 or makespan_ns <= 0:
        raise ValueError("WAN monitoring requires positive interval/capacity/window")
    if not Path(path).is_file():
        raise ValueError(f'WAN bandwidth trace is missing: {path}')
    first = epoch_ns // interval_ns
    end = epoch_ns + makespan_ns
    last = (end + interval_ns-1) // interval_ns
    selected = {tuple(p): {} for p in ports}
    for row in _monitor_rows(path):
        if len(row) != 4:
            raise ValueError("bandwidth monitor row must have four columns")
        key = (int(row[1]), int(row[2]))
        if key in selected:
            index = (int(row[0])-1) // interval_ns
            if first <= index < last:
                selected[key][index] = float(row[3])
    widths = [max(0, min(end, (k+1)*interval_ns)-max(epoch_ns, k*interval_ns))
              for k in range(first, last)]
    results = []
    for (node, port), samples in selected.items():
        values = [samples.get(k, 0.0) for k in range(first, last)]
        active = [i for i, value in enumerate(values) if value > 0]
        lo, hi = (min(active), max(active)) if active else (0, -1)
        idle = current = longest = active_idle = 0
        for i, (value, width) in enumerate(zip(values, widths)):
            if value <= 0:
                idle += width
                current += width
                longest = max(longest, current)
                if lo <= i <= hi:
                    active_idle += width
            else:
                current = 0
        bits = sum(value*width for value, width in zip(values, widths))
        results.append(dict(node_id=node, port_id=port,
                            mean_utilization=bits/(capacity_gbps*makespan_ns),
                            sampled_idle_ns=idle, max_sampled_idle_gap_ns=longest,
                            active_window_idle_ns=active_idle,
                            observed_wire_bytes=bits/8,
                            first_active_sample_ns=(first+lo)*interval_ns if active else None,
                            last_active_sample_ns=(first+hi+1)*interval_ns if active else None))
    return dict(interval_ns=interval_ns, epoch_ns=epoch_ns, end_ns=end,
                capacity_gbps=capacity_gbps, ports=results,
                mean_utilization=sum(p['mean_utilization'] for p in results)/len(results)
                if results else 0.0,
                sampled_idle_ns=sum(p['sampled_idle_ns'] for p in results),
                max_sampled_idle_gap_ns=max((p['max_sampled_idle_gap_ns'] for p in results), default=0))


def add_wan_statistics(summary: dict, config_path: Path, topology: dict) -> None:
    """Shared run/reanalysis path; reject incomplete or shifted monitoring."""
    ports = topology.get('gateway_wan_ports')
    if not ports:
        return
    settings = dict(line.split(maxsplit=1) for line in Path(config_path).read_text().splitlines()
                    if len(line.split(maxsplit=1)) == 2)
    if int(settings.get('MON_START', '-1')) != 0:
        raise ValueError('WAN analysis requires MON_START 0 (zero-aligned, complete observation)')
    if int(settings.get('MON_END', '0'))*1000 < summary['collective_epoch_ns'] + summary['actual_makespan_ns']:
        raise ValueError('WAN monitor ends before collective completion')
    if 'BW_MON_INTERVAL' not in settings:
        raise ValueError('WAN analysis requires BW_MON_INTERVAL')
    if summary['actual_makespan_ns'] == 0:
        # A local-only ideal plan has no network observation interval. Its
        # utilization is undefined, not evidence of a measured idle WAN.
        summary['wan'] = dict(interval_ns=int(settings['BW_MON_INTERVAL'])*1000,
                              epoch_ns=summary['collective_epoch_ns'],
                              end_ns=summary['collective_epoch_ns'],
                              capacity_gbps=topology['gateway_wan_port_capacity_gbps'],
                              ports=[], mean_utilization=None, sampled_idle_ns=0,
                              max_sampled_idle_gap_ns=0)
        return
    summary['wan'] = analyze_wan_bandwidth(
        Path(config_path).parent/'bw.txt', ports,
        epoch_ns=summary['collective_epoch_ns'], makespan_ns=summary['actual_makespan_ns'],
        interval_ns=int(settings['BW_MON_INTERVAL'])*1000,
        capacity_gbps=topology['gateway_wan_port_capacity_gbps'])
