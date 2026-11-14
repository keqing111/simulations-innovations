"""Scenario preparation and provenance helpers for the all-to-all CLI."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import (
    read_matrix_artifact,
    write_matrix_artifact,
    write_schedule_artifact,
)
from .metrics import matrix_skew_metrics
from .model import HardwareProfile, LinkModel, TrafficMatrix
from .schedulers import SCHEDULER_NAMES, get_scheduler
from .traffic import generate_matrix
from .topology import write_topology


SCHEMA_VERSION = 1
SCHEDULERS = SCHEDULER_NAMES
HARDWARE_FIELDS = {
    "schema_version",
    "num_servers",
    "gpus_per_server",
    "intra_alpha_ns",
    "intra_beta_ns_per_byte",
    "inter_alpha_ns",
    "inter_beta_ns_per_byte",
    "topology_id",
    "link_bandwidth_gbps",
    "oversubscription",
}
MULTIDC_TOPOLOGY_FIELDS = {
    "kind",
    "data_centers",
    "servers_per_data_center",
    "intra_dc_bandwidth_gbps",
    "intra_dc_latency_ns",
    "inter_dc_bandwidth_gbps",
    "inter_dc_latency_ns",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_keys(
    value: dict[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(f"{label} has unexpected keys: {', '.join(unexpected)}")
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_hardware(path: Path) -> tuple[HardwareProfile, dict[str, Any], str]:
    data = _load_json(path)
    _validate_keys(
        data,
        allowed=HARDWARE_FIELDS,
        required=HARDWARE_FIELDS,
        label="hardware profile",
    )
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported hardware schema_version")
    profile = HardwareProfile(
        num_servers=int(data["num_servers"]),
        gpus_per_server=int(data["gpus_per_server"]),
        intra=LinkModel(
            float(data["intra_alpha_ns"]),
            float(data["intra_beta_ns_per_byte"]),
        ),
        inter=LinkModel(
            float(data["inter_alpha_ns"]),
            float(data["inter_beta_ns_per_byte"]),
        ),
        topology_id=str(data["topology_id"]),
        link_bandwidth_gbps=float(data["link_bandwidth_gbps"]),
        oversubscription=float(data["oversubscription"]),
    )
    return profile, data, canonical_sha256(data)


def resolve_output(path: Path, new_attempt: bool) -> Path:
    path = Path(path)
    if not path.exists() or not any(path.iterdir()):
        path.mkdir(parents=True, exist_ok=True)
        return path
    if not new_attempt:
        raise ValueError(f"output directory already exists and is nonempty: {path}")
    index = 1
    while True:
        attempt = path / f"attempt-{index:04d}"
        if not attempt.exists():
            attempt.mkdir(parents=True)
            return attempt
        index += 1


def repository_state(start: Path | None = None) -> dict[str, str]:
    current = (start or Path(__file__)).resolve()
    repo = next((parent for parent in (current, *current.parents) if (parent / ".git").exists()), None)
    if repo is None:
        return {"simai": "unavailable", "simccl": "unavailable", "ns3": "unavailable"}

    def revision(path: Path) -> str:
        if not path.exists():
            return "unavailable"
        try:
            sha = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            ).stdout
            return sha + ("-dirty" if dirty else "")
        except (OSError, subprocess.CalledProcessError):
            return "unavailable"

    return {
        "simai": revision(repo),
        "simccl": revision(repo / "SimCCL"),
        "ns3": revision(repo / "ns-3-alibabacloud"),
    }


def matrix_manifest_fields(matrix: TrafficMatrix, gpus_per_server: int) -> dict[str, Any]:
    return {
        **matrix_skew_metrics(matrix, gpus_per_server),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "code_revisions": repository_state(),
    }


def generate_artifact(
    *,
    generator: str,
    num_devices: int,
    gpus_per_server: int,
    output: Path,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    matrix = generate_matrix(generator, num_devices=num_devices, **parameters)
    return write_matrix_artifact(
        matrix,
        output,
        manifest_fields=matrix_manifest_fields(matrix, gpus_per_server),
    )


def schedule_artifact(
    *,
    matrix_dir: Path,
    hardware_path: Path,
    algorithm: str,
    seed: int,
    output: Path,
    fast_options: dict | None = None,
    execution_model: str = 'open-loop',
) -> dict[str, Any]:
    if algorithm.startswith('fast-joint-') and execution_model=='open-loop':
        raise ValueError('joint plans require an explicit completion-event execution model')
    matrix, matrix_manifest = read_matrix_artifact(matrix_dir)
    hardware, _, hardware_sha = load_hardware(hardware_path)
    hardware.validate_matrix(matrix)
    started = time.perf_counter_ns()
    schedule = get_scheduler(algorithm, fast_options=fast_options).schedule(matrix, hardware, seed=seed)
    elapsed = time.perf_counter_ns() - started
    validation = {}
    if algorithm.startswith('fast'):
        from .delivery import validate_fast_delivery
        validation['delivery_validation'] = validate_fast_delivery(
            matrix, schedule, hardware.gpus_per_server)
    if execution_model != 'open-loop':
        from .execution import (STAGE_BARRIER_MODEL, NATIVE_MODEL, MODELED_MODEL,
            ideal_scaleup_schedule, native_dependency_schedule, modeled_scaleup_schedule)
        if execution_model not in (STAGE_BARRIER_MODEL,NATIVE_MODEL,MODELED_MODEL):
            raise ValueError(f'unknown execution model: {execution_model}')
        schedule = (modeled_scaleup_schedule(schedule,hardware) if execution_model==MODELED_MODEL else
                    native_dependency_schedule(schedule,hardware) if execution_model==NATIVE_MODEL
                    else ideal_scaleup_schedule(schedule,hardware))
    return write_schedule_artifact(
        schedule,
        output,
        matrix_sha256=matrix_manifest["matrix_sha256"],
        hardware_sha256=hardware_sha,
        manifest_fields={
            "scheduler_wall_time_ns": elapsed,
            "logical_bytes": matrix.logical_bytes,
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "code_revisions": repository_state(),
            **validation,
        },
    )


def write_micro_workload(matrix: TrafficMatrix, path: Path) -> None:
    maximum_source_bytes = max(
        sum(int(value) for value in matrix.values[row, :])
        for row in range(matrix.num_devices)
    )
    path.write_text(
        f"MICRO model_parallel_NPU_group: 1 ep: {matrix.num_devices} "
        f"all_gpus: {matrix.num_devices}\n"
        "1\n"
        f"0 0 0 NONE 0 0 NONE 0 0 ALLTOALL {maximum_source_bytes} 0\n",
        encoding="utf-8",
    )


def prepare_scenario(config_path: Path, output: Path) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    config = _load_json(config_path)
    required = {"schema_version", "name", "hardware", "seed", "traffic", "schedulers"}
    allowed = {*required, "topology", "fast_options"}
    _validate_keys(config, allowed=allowed, required=required, label="scenario config")
    if config["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported scenario schema_version")
    topology_config = config.get("topology")
    if topology_config is not None:
        if not isinstance(topology_config, dict):
            raise ValueError("topology must be a JSON object")
        _validate_keys(
            topology_config,
            allowed=MULTIDC_TOPOLOGY_FIELDS,
            required=MULTIDC_TOPOLOGY_FIELDS,
            label="topology",
        )
        if topology_config["kind"] != "multi-dc-full-mesh":
            raise ValueError(f"unknown topology kind: {topology_config['kind']!r}")
    if not isinstance(config["traffic"], dict):
        raise ValueError("traffic must be a JSON object")
    traffic = dict(config["traffic"])
    generator = traffic.pop("generator", None)
    if generator == "balanced":
        allowed_traffic = {"num_devices", "pair_bytes", "total_bytes"}
        required_traffic = {"num_devices"}
        if ("pair_bytes" in traffic) == ("total_bytes" in traffic):
            raise ValueError("balanced traffic requires exactly one of pair_bytes or total_bytes")
    elif generator == "fast-uniform":
        allowed_traffic = {"num_devices", "bytes_per_unit", "total_bytes"}
        required_traffic = {"num_devices", "bytes_per_unit"}
    elif generator == "fast-zipf":
        allowed_traffic = {"num_devices", "alpha", "bytes_per_unit", "total_bytes"}
        required_traffic = {"num_devices", "alpha", "bytes_per_unit"}
    else:
        raise ValueError(f"unknown traffic generator: {generator!r}")
    _validate_keys(
        traffic,
        allowed=allowed_traffic,
        required=required_traffic,
        label="traffic",
    )

    hardware_path = (config_path.parent / str(config["hardware"])).resolve()
    hardware, hardware_data, hardware_sha = load_hardware(hardware_path)
    num_devices = int(traffic.pop("num_devices"))
    if num_devices != hardware.num_devices:
        raise ValueError("traffic num_devices does not match hardware profile")
    schedulers = config["schedulers"]
    if not isinstance(schedulers, list) or not schedulers:
        raise ValueError("schedulers must be a nonempty list")
    if len(set(schedulers)) != len(schedulers):
        raise ValueError("schedulers must not contain duplicates")
    unknown = sorted(set(schedulers) - set(SCHEDULERS))
    if unknown:
        raise ValueError(f"unknown schedulers: {', '.join(unknown)}")

    matrix = generate_matrix(
        generator,
        num_devices=num_devices,
        seed=int(config["seed"]) if generator != "balanced" else None,
        **traffic,
    ) if generator != "balanced" else generate_matrix(
        generator,
        num_devices=num_devices,
        **traffic,
    )
    matrix_dir = output / "matrix"
    matrix_manifest = write_matrix_artifact(
        matrix,
        matrix_dir,
        manifest_fields=matrix_manifest_fields(matrix, hardware.gpus_per_server),
    )
    (output / "hardware.json").write_text(
        json.dumps(hardware_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    schedule_hashes: dict[str, str] = {}
    for algorithm in schedulers:
        manifest = schedule_artifact(
            matrix_dir=matrix_dir,
            hardware_path=hardware_path,
            algorithm=algorithm,
            seed=int(config["seed"]),
            output=output / "schedules" / algorithm,
            fast_options=config.get('fast_options'),
        )
        schedule_hashes[algorithm] = manifest["schedule_sha256"]
    write_micro_workload(matrix, output / "workload.csv")
    topology_manifest = write_topology(
        hardware,
        output / "topology.txt",
        expected_num_devices=matrix.num_devices,
        topology_config=topology_config,
    )
    scenario_manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": str(config["name"]),
        "seed": int(config["seed"]),
        "matrix_sha256": matrix_manifest["matrix_sha256"],
        "hardware_sha256": hardware_sha,
        "schedule_sha256": schedule_hashes,
        "topology": topology_manifest,
        "schedulers": schedulers,
        "code_revisions": repository_state(),
        "fast_options": config.get('fast_options'),
    }
    (output / "scenario_manifest.json").write_text(
        json.dumps(scenario_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return scenario_manifest
