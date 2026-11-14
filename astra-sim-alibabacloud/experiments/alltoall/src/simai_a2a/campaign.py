"""Resumable all-to-all experiment campaigns and flat result tables."""

from __future__ import annotations

import csv
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .runner import run_scenario
from .scenario import SCHEDULERS, load_hardware, prepare_scenario


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def expand_campaign(config_path: Path) -> list[dict[str, Any]]:
    config_path = Path(config_path).resolve()
    config = _load(config_path)
    required = {"schema_version", "name", "profiles", "workloads", "schedulers"}
    allowed = {*required, "topology", "ns3_config", "ns3_threads", "fast_options"}
    if not required <= set(config) or set(config) - allowed or config["schema_version"] != 1:
        raise ValueError("campaign fields or schema_version are invalid")
    schedulers = list(config["schedulers"])
    if not schedulers or len(set(schedulers)) != len(schedulers):
        raise ValueError("campaign schedulers must be unique and nonempty")
    if set(schedulers) - set(SCHEDULERS):
        raise ValueError("campaign contains an unknown scheduler")
    cases: list[dict[str, Any]] = []
    ids: set[str] = set()
    topology = config.get("topology")
    ns3_threads = config.get("ns3_threads")
    if (
        ns3_threads is not None
        and (isinstance(ns3_threads, bool) or not isinstance(ns3_threads, int) or ns3_threads < 0)
    ):
        raise ValueError("campaign ns3_threads must be a nonnegative integer")
    ns3_config = (
        str((config_path.parent / str(config["ns3_config"])).resolve())
        if "ns3_config" in config
        else None
    )
    for profile in config["profiles"]:
        profile_id = str(profile["id"])
        hardware_path = (config_path.parent / str(profile["hardware"])).resolve()
        hardware, _, _ = load_hardware(hardware_path)
        for workload in config["workloads"]:
            parameters = dict(workload.get("parameters", {}))
            if "total_bytes_per_device" in workload:
                parameters["total_bytes"] = (
                    int(workload["total_bytes_per_device"]) * hardware.num_devices
                )
            for seed in workload.get("seeds", [11]):
                case_id = f"{profile_id}__{workload['id']}__s{int(seed)}"
                if case_id in ids:
                    raise ValueError(f"duplicate campaign case {case_id}")
                ids.add(case_id)
                cases.append(
                    {
                        "id": case_id,
                        "profile_id": profile_id,
                        "workload_id": str(workload["id"]),
                        "hardware": str(hardware_path),
                        "seed": int(seed),
                        "traffic": {
                            "generator": str(workload["generator"]),
                            "num_devices": hardware.num_devices,
                            **parameters,
                        },
                        "schedulers": schedulers,
                        "topology": topology,
                        "ns3_config": ns3_config,
                        "ns3_threads": ns3_threads,
                        "fast_options": config.get('fast_options'),
                    }
                )
    return cases


def _successful_attempt(scenario: Path, algorithm: str) -> Path | None:
    root = scenario / "runs" / algorithm
    if not root.is_dir():
        return None
    for attempt in sorted(root.glob("attempt-*"), reverse=True):
        if all(
            (attempt / name).is_file()
            for name in ("summary.json", "bw.txt", "qlen.txt")
        ):
            manifest = _load(attempt / "run_manifest.json")
            if manifest.get("success") and manifest.get("runtime_semantics_version") == 13:
                return attempt
    return None


def run_campaign(
    config_path: Path,
    output: Path,
    binary: Path,
    *,
    timeout_s: float = 300.0,
    max_attempts: int = 2,
    jobs: int = 1,
) -> dict[str, Any]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if jobs < 1:
        raise ValueError("jobs must be positive")
    config_path = Path(config_path).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = expand_campaign(config_path)
    state: dict[str, Any] = {
        "schema_version": 1,
        "config": str(config_path),
        "case_count": len(cases),
        "run_count": sum(len(case["schedulers"]) for case in cases),
        "cases": {},
    }
    state_path = output / "campaign_manifest.json"

    prepared: list[tuple[int, dict[str, Any], Path]] = []
    for case_index, case in enumerate(cases, 1):
        scenario = output / "scenarios" / case["id"]
        scenario_config = output / "generated_configs" / f"{case['id']}.json"
        if not (scenario / "scenario_manifest.json").is_file():
            generated_config = {
                "schema_version": 1,
                "name": case["id"],
                "hardware": case["hardware"],
                "seed": case["seed"],
                "traffic": case["traffic"],
                "schedulers": case["schedulers"],
            }
            if case.get("topology") is not None:
                generated_config["topology"] = case["topology"]
            if case.get('fast_options') is not None:
                generated_config['fast_options'] = case['fast_options']
            _write(scenario_config, generated_config)
            prepare_scenario(scenario_config, scenario)
        case_state: dict[str, Any] = {
            "scenario": str(scenario),
            "algorithms": {
                algorithm: {"success": False, "attempt": None}
                for algorithm in case["schedulers"]
            },
        }
        state["cases"][case["id"]] = case_state
        prepared.append((case_index, case, scenario))

    def run_algorithm(
        case_index: int,
        case: dict[str, Any],
        scenario: Path,
        algorithm: str,
    ) -> dict[str, Any]:
        attempt = _successful_attempt(scenario, algorithm)
        tries = 0
        while attempt is None and tries < max_attempts:
            tries += 1
            print(
                f"[{case_index}/{len(cases)}] {case['id']} {algorithm} attempt {tries}",
                flush=True,
            )
            result = run_scenario(
                scenario,
                algorithm,
                scenario / "topology.txt",
                binary,
                timeout_s=timeout_s,
                ns3_threads=case.get("ns3_threads"),
                **(
                    {"config_template": Path(case["ns3_config"])}
                    if case.get("ns3_config") is not None
                    else {}
                ),
            )
            if result["success"]:
                attempt = Path(result["attempt_dir"])
        return {
            "success": attempt is not None,
            "attempt": str(attempt) if attempt is not None else None,
        }

    tasks = [
        (case_index, case, scenario, algorithm)
        for case_index, case, scenario in prepared
        for algorithm in case["schedulers"]
    ]

    def record(task: tuple[int, dict[str, Any], Path, str], result: dict[str, Any]) -> None:
        _, case, _, algorithm = task
        state["cases"][case["id"]]["algorithms"][algorithm] = result
        _write(state_path, state)

    if jobs == 1:
        for task in tasks:
            record(task, run_algorithm(*task))
    else:
        with ThreadPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            futures = {
                executor.submit(run_algorithm, *task): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                record(task, future.result())
    aggregate_campaign(output)
    return state


def _get(value: dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def aggregate_campaign(output: Path) -> list[dict[str, Any]]:
    output = Path(output).resolve()
    state = _load(output / "campaign_manifest.json")
    rows: list[dict[str, Any]] = []
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for case_id, case_state in state["cases"].items():
        scenario = Path(case_state["scenario"])
        scenario_manifest = _load(scenario / "scenario_manifest.json")
        matrix = _load(scenario / "matrix" / "matrix_manifest.json")
        hardware = _load(scenario / "hardware.json")
        for algorithm, run in case_state["algorithms"].items():
            if not run.get("success"):
                continue
            attempt = Path(run["attempt"])
            summary = _load(attempt / "summary.json")
            schedule = _load(scenario / "schedules" / algorithm / "schedule_manifest.json")
            row = {
                "case_id": case_id,
                "profile_id": case_id.split("__", 1)[0],
                "workload_id": case_id.split("__")[1],
                "seed": scenario_manifest["seed"],
                "algorithm": algorithm,
                "num_devices": matrix["num_devices"],
                "num_servers": hardware["num_servers"],
                "gpus_per_server": hardware["gpus_per_server"],
                "oversubscription": hardware["oversubscription"],
                "generator": matrix["metadata"].get("generator"),
                "normalization": matrix["metadata"].get("normalization", "native"),
                "alpha": matrix["metadata"].get("alpha"),
                "logical_bytes": summary["logical_bytes"],
                "scheduled_bytes": summary.get("scheduled_bytes", summary["physical_bytes"]),
                "schedule_byte_amplification": summary.get("schedule_byte_amplification"),
                "actual_makespan_ns": summary["actual_makespan_ns"],
                "predicted_makespan_ns": summary.get("predicted_makespan_ns"),
                "algorithm_bandwidth_gbps": summary.get("algorithm_bandwidth_gbps"),
                "per_device_algorithm_bandwidth_gbps": summary.get("per_device_algorithm_bandwidth_gbps"),
                "fct_p50_ns": _get(summary, "fct_ns", "p50"),
                "fct_p95_ns": _get(summary, "fct_ns", "p95"),
                "fct_p99_ns": _get(summary, "fct_ns", "p99"),
                "fct_max_ns": _get(summary, "fct_ns", "max"),
                "pfc_count": summary.get("pfc_count"),
                "pfc_pause_count": _get(summary, "pfc", "pause_count"),
                "pfc_resume_count": _get(summary, "pfc", "resume_count"),
                "pfc_affected_port_count": _get(
                    summary, "pfc", "affected_port_count"
                ),
                "cnp_total_count": _get(
                    summary, "congestion_control", "cnp", "total_count"
                ),
                "cnp_affected_flow_count": _get(
                    summary,
                    "congestion_control",
                    "cnp",
                    "affected_flow_count",
                ),
                "qp_min_rate_bps": _get(
                    summary, "congestion_control", "qp_rate", "min_bps"
                ),
                "qp_max_rate_bps": _get(
                    summary, "congestion_control", "qp_rate", "max_bps"
                ),
                "scheduler_wall_time_ns": schedule.get("scheduler_wall_time_ns"),
                "stage_count": _get(schedule, 'metadata', 'n_stages'),
                "wan_repair_count": _get(schedule, 'metadata', 'synthesis', 'repair_count'),
                "wan_repair_attempt_count": _get(schedule, 'metadata', 'synthesis', 'repair_attempt_count'),
                "wan_mean_utilization": _get(summary, 'wan', 'mean_utilization'),
                "wan_sampled_idle_ns": _get(summary, 'wan', 'sampled_idle_ns'),
                "wan_max_sampled_idle_gap_ns": _get(summary, 'wan', 'max_sampled_idle_gap_ns'),
                "cell_gini": matrix.get("cell_gini"),
                "source_gini": matrix.get("source_gini"),
                "destination_gini": matrix.get("destination_gini"),
                "server_pair_gini": matrix.get("server_pair_gini"),
                "source_bottleneck_ratio": matrix.get("source_bottleneck_ratio"),
                "destination_bottleneck_ratio": matrix.get("destination_bottleneck_ratio"),
                "tor_peak_queue_bytes": _get(summary, "network", "queue", "tor_peak_queue_bytes"),
                "tor_peak_port_bytes": _get(summary, "network", "queue", "tor_peak_port_bytes"),
                "tor_peak_utilization": _get(summary, "network", "bandwidth", "tor_peak_utilization"),
                "gateway_peak_queue_bytes": _get(
                    summary, "network", "queue", "gateway_peak_queue_bytes"
                ),
                "gateway_peak_port_bytes": _get(
                    summary, "network", "queue", "gateway_peak_port_bytes"
                ),
                "gateway_peak_utilization": _get(
                    summary, "network", "bandwidth", "gateway_peak_utilization"
                ),
                "stage_violation_fraction": _get(
                    summary, "stage_violations", "violation_fraction"
                ),
                "stage_max_violation_ns": _get(
                    summary, "stage_violations", "max_violation_ns"
                ),
                "fast_violation_fraction": _get(summary, "fast_stage_violations", "violation_fraction"),
                "fast_max_violation_ns": _get(summary, "fast_stage_violations", "max_violation_ns"),
                "attempt": str(attempt),
            }
            rows.append(row)
            by_scenario.setdefault(case_id, []).append(row)
    for scenario_rows in by_scenario.values():
        direct = next(
            (row["actual_makespan_ns"] for row in scenario_rows if row["algorithm"] == "direct-concurrent"),
            None,
        )
        spreadout = next(
            (row["actual_makespan_ns"] for row in scenario_rows if row["algorithm"] == "spreadout"),
            None,
        )
        roundrobin = next(
            (row["actual_makespan_ns"] for row in scenario_rows if row["algorithm"] == "roundrobin"),
            None,
        )
        original_fast = next(
            (row['actual_makespan_ns'] for row in scenario_rows if row['algorithm'] == 'fast'),
            None,
        )
        for row in scenario_rows:
            makespan = row["actual_makespan_ns"]
            row["speedup_vs_direct"] = direct / makespan if direct else None
            row["speedup_vs_spreadout"] = spreadout / makespan if spreadout else None
            row["speedup_vs_roundrobin"] = roundrobin / makespan if roundrobin else None
            row['speedup_vs_fast'] = original_fast / makespan if original_fast else None
    rows.sort(key=lambda row: (row["case_id"], row["algorithm"]))
    _write(output / "aggregate.json", rows)
    if rows:
        with (output / "aggregate.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return rows
