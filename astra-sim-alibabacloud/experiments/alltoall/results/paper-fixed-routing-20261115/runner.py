"""Process-based ns-3 experiment runner with strict completion checks."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .analysis import add_wan_statistics, analyze_fct, write_summary
from .artifacts import read_schedule_artifact
from .execution import (STAGE_BARRIER_MODEL, NATIVE_MODEL, MODELED_MODEL, NATIVE_MODELS,
                        write_stage_file, write_native_commands, write_local_times)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_TEMPLATE = PACKAGE_ROOT / "configs" / "ns3.conf"
RUNTIME_SEMANTICS_VERSION=19
NS3_ENV_KEYS=('NS_ATTRIBUTE_DEFAULT','NS_GLOBAL_VALUE')


def effective_ns3_environment(overrides=None):
    if overrides is not None and (not isinstance(overrides,dict) or any(
            key not in NS3_ENV_KEYS or value is not None and not isinstance(value,str)
            for key,value in overrides.items())):
        raise ValueError('ns3_env supports only string/None simulator attribute/global overrides')
    return {key:(overrides[key] if overrides is not None and key in overrides else os.environ.get(key))
            for key in NS3_ENV_KEYS}


def _file_sha(path):
    # Metadata-only caches miss same-size updates on coarse/frozen-clock filesystems.
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def backend_shared_libraries(binary_path):
    """ns3 builds dynamically link modules; the executable alone is not its behavior."""
    directory=Path(binary_path).resolve().parent.parent/'lib'
    return {p.name:p for p in sorted(directory.glob('libns3.*.so'))}


def execution_fingerprint(scenario_dir,algorithm,topology_path,binary_path,config_template,ns3_threads,*,ns3_env=None):
    scenario_dir=Path(scenario_dir);source=scenario_dir/'schedules'/algorithm
    schedule,_=read_schedule_artifact(source)
    files={'schedule':source/'schedule.csv','schedule_metadata':source/'schedule_metadata.csv',
           'matrix':scenario_dir/'matrix/matrix.npy','topology':Path(topology_path),
           'workload':scenario_dir/'workload.csv','backend':Path(binary_path),'config_template':Path(config_template)}
    for name in ('hardware.json','scenario_manifest.json','routes.csv','background.csv'):
        if (scenario_dir/name).is_file():files[name]=scenario_dir/name
    files.update({f'backend_module:{name}':path for name,path in backend_shared_libraries(binary_path).items()})
    value=dict(runtime_semantics_version=RUNTIME_SEMANTICS_VERSION,
               ns3_environment=effective_ns3_environment(ns3_env),
               files={name:_file_sha(path) for name,path in files.items()},
               metadata=schedule.metadata,threads=ns3_threads,
               send_latency=('0' if schedule.metadata.get('execution_model') in NATIVE_MODELS
                             else os.environ.get('AS_SEND_LAT')))
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _next_attempt(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        attempt = root / f"attempt-{index:04d}"
        try:
            attempt.mkdir()
            return attempt
        except FileExistsError:
            index += 1


def _render_config(template: Path, attempt: Path) -> Path:
    names = {
        "FLOW_FILE": attempt / "flow.txt",
        "TRACE_FILE": attempt / "trace.txt",
        "TRACE_OUTPUT_FILE": attempt / "trace.out",
        "FCT_OUTPUT_FILE": attempt / "fct.txt",
        "PFC_OUTPUT_FILE": attempt / "pfc.txt",
        "QLEN_MON_FILE": attempt / "qlen.txt",
        "BW_MON_FILE": attempt / "bw.txt",
        "RATE_MON_FILE": attempt / "rate.txt",
        "CNP_MON_FILE": attempt / "cnp.txt",
    }
    rendered = template.read_text(encoding="utf-8").format(
        **{key: str(value.resolve()) for key, value in names.items()}
    )
    config = attempt / "ns3.conf"
    config.write_text(rendered, encoding="utf-8")
    names["FLOW_FILE"].write_text("0\n", encoding="utf-8")
    names["TRACE_FILE"].write_text("0\n", encoding="utf-8")
    return config


def run_scenario(
    scenario_dir: Path,
    algorithm: str,
    topology_path: Path,
    binary_path: Path,
    *,
    timeout_s: float,
    output_root: Path | None = None,
    config_template: Path = DEFAULT_CONFIG_TEMPLATE,
    ns3_threads: int | None = None,
    ns3_env: dict[str,str|None] | None = None,
) -> dict[str, Any]:
    """Run one prepared schedule and return its persisted run manifest."""
    ns3_environment=effective_ns3_environment(ns3_env)
    configured_version = os.environ.get("SIMAI_NCCL_VERSION", "v2.30")
    if configured_version != "v2.30":
        raise ValueError("external scheduling supports only SIMAI_NCCL_VERSION=v2.30")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if (
        ns3_threads is not None
        and (
            isinstance(ns3_threads, bool)
            or not isinstance(ns3_threads, int)
            or ns3_threads < 0
        )
    ):
        raise ValueError("ns3_threads must be a nonnegative integer")
    scenario_dir = Path(scenario_dir).resolve()
    schedule_source = scenario_dir / "schedules" / algorithm
    schedule, _ = read_schedule_artifact(schedule_source)
    execution_model = schedule.metadata.get('execution_model', 'open-loop')
    if execution_model not in ('open-loop', STAGE_BARRIER_MODEL,*NATIVE_MODELS):
        raise ValueError(f'unsupported execution model: {execution_model}')
    barrier_mode = execution_model == STAGE_BARRIER_MODEL
    native_mode = execution_model in NATIVE_MODELS
    if barrier_mode or native_mode:
        if ns3_threads not in (None, 0):
            raise ValueError('stage barriers require ns3_threads=0')
        ns3_threads = 0
    if schedule.algorithm != algorithm:
        raise ValueError("schedule algorithm does not match the requested algorithm")
    binary_path = Path(binary_path).resolve()
    topology_path = Path(topology_path).resolve()
    if not binary_path.is_file():
        raise ValueError(f"ns-3 binary does not exist: {binary_path}")
    if not os.access(binary_path, os.X_OK):
        raise ValueError(f"ns-3 binary is not executable: {binary_path}")
    if not topology_path.is_file():
        raise ValueError(f"topology does not exist: {topology_path}")

    attempts_root = (
        Path(output_root).resolve()
        if output_root is not None
        else scenario_dir / "runs" / algorithm
    )
    attempt = _next_attempt(attempts_root)
    local_topology = attempt / "topology.txt"
    local_workload = attempt / "workload.txt"
    local_schedule = attempt / "schedule.csv"
    shutil.copy2(topology_path, local_topology)
    shutil.copy2(scenario_dir / "workload.csv", local_workload)
    shutil.copy2(schedule_source / "schedule.csv", local_schedule)
    for name in ('schedule_metadata.csv','schedule_manifest.json'):
        shutil.copy2(schedule_source/name,attempt/name)
    for name in ('hardware.json','scenario_manifest.json','routes.csv','background.csv'):
        if (scenario_dir/name).is_file():shutil.copy2(scenario_dir/name,attempt/name)
    for name in ('matrix.npy','matrix_manifest.json'):
        shutil.copy2(scenario_dir/'matrix'/name,attempt/name)
    environment = {**os.environ, 'SIMAI_NCCL_VERSION': 'v2.30'}
    for key,value in ns3_environment.items():
        if value is None:environment.pop(key,None)
        else:environment[key]=value
    environment.pop('SIMAI_EXTERNAL_STAGE_FILE', None)
    environment.pop('SIMAI_NATIVE_FAST_FILE', None)
    environment.pop('SIMAI_NATIVE_WAN_PORTS', None)
    environment.pop('SIMAI_NATIVE_LOCAL_TIMES', None)
    for key in ('SIMAI_STATIC_ROUTES','SIMAI_BACKGROUND_FILE','SIMAI_FOREGROUND_GPUS','SIMAI_FOREGROUND_START_NS'):
        environment.pop(key,None)
    stage_file = attempt / 'stages.csv'
    if barrier_mode:
        write_stage_file(schedule, stage_file)
        environment['SIMAI_EXTERNAL_STAGE_FILE'] = str(stage_file.resolve())
    if native_mode:
        write_native_commands(schedule,attempt/'native.csv')
        environment['SIMAI_NATIVE_FAST_FILE']=str((attempt/'native.csv').resolve())
        environment['AS_SEND_LAT']='0'
        if execution_model==MODELED_MODEL:
            write_local_times(schedule,attempt/'local_times.txt')
            environment['SIMAI_NATIVE_LOCAL_TIMES']=str((attempt/'local_times.txt').resolve())
    config = _render_config(Path(config_template), attempt)
    scenario_manifest_path = scenario_dir / "scenario_manifest.json"
    hardware_path = scenario_dir / "hardware.json"
    scenario_manifest = (
        json.loads(scenario_manifest_path.read_text(encoding="utf-8"))
        if scenario_manifest_path.is_file()
        else {"topology": {}}
    )
    hardware_data = (
        json.loads(hardware_path.read_text(encoding="utf-8"))
        if hardware_path.is_file()
        else None
    )
    for name,key in [('routes.csv','SIMAI_STATIC_ROUTES'),('background.csv','SIMAI_BACKGROUND_FILE')]:
        if (attempt/name).is_file():environment[key]=str((attempt/name).resolve())
    top_metadata=scenario_manifest['topology']
    if 'foreground_gpu_count' in top_metadata:
        environment['SIMAI_FOREGROUND_GPUS']=str(top_metadata['foreground_gpu_count'])
        environment['SIMAI_FOREGROUND_START_NS']=str(top_metadata.get('foreground_start_ns',0))
    if native_mode:
        environment['SIMAI_NATIVE_WAN_PORTS']=','.join(
            f'{node}:{port}' for node,port in scenario_manifest['topology'].get('gateway_wan_ports',[]))
    command = [str(binary_path)]
    if ns3_threads is not None:
        command.extend(["-t", str(ns3_threads)])
    command.extend([
        "-n", str(local_topology.resolve()),
        "-c", str(config.resolve()),
        "-w", str(local_workload.resolve()),
        "-s", str(local_schedule.resolve()),
    ])
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "runtime_semantics_version": RUNTIME_SEMANTICS_VERSION,
        "ns3_environment": ns3_environment,
        "backend_shared_library_sha256": {name:_file_sha(path) for name,path in
                                           backend_shared_libraries(binary_path).items()},
        "input_fingerprint": execution_fingerprint(scenario_dir,algorithm,topology_path,
                                                  binary_path,config_template,ns3_threads,ns3_env=ns3_environment),
        "execution_model": execution_model,
        "algorithm": algorithm,
        "attempt_dir": str(attempt.resolve()),
        "command": command,
        "timeout_s": timeout_s,
        "ns3_threads": ns3_threads,
        "success": False,
    }
    started = time.monotonic_ns()
    try:
        if (barrier_mode or native_mode) and not schedule.flows:
            if execution_model==MODELED_MODEL:
                from .execution import write_local_only_events
                write_local_only_events(schedule,attempt/'native.csv.events.csv')
            # There are no packets to simulate when every operation is ideal
            # scale-up. Do not fabricate a dummy flow or launch an empty CSV.
            for name in ('fct.txt', 'pfc.txt', 'qlen.txt', 'bw.txt', 'rate.txt', 'cnp.txt'):
                (attempt / name).write_text('', encoding='utf-8')
            completed = subprocess.CompletedProcess(command, 0, '', '')
            manifest['backend_execution'] = ('local-only-alpha-beta' if execution_model==MODELED_MODEL
                                             else 'skipped-empty-network')
        else:
            completed = subprocess.run(
                command,
                cwd=attempt,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_s,
            )
        (attempt / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (attempt / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        manifest["returncode"] = completed.returncode
        if completed.returncode != 0:
            manifest["status"] = "failed"
        else:
            try:
                summary = analyze_fct(
                    scenario_dir / "matrix",
                    schedule_source,
                    attempt / "fct.txt",
                    attempt / "pfc.txt",
                    attempt / "qlen.txt",
                    attempt / "bw.txt",
                    attempt / "rate.txt",
                    attempt / "cnp.txt",
                    tor_id=scenario_manifest["topology"].get("tor_id"),
                    tor_ids=scenario_manifest["topology"].get("tor_ids"),
                    gateway_ids=scenario_manifest["topology"].get("gateway_ids"),
                    gateway_wan_ports=scenario_manifest["topology"].get(
                        "gateway_wan_ports"
                    ),
                    tor_port_capacity_gbps=(
                        scenario_manifest["topology"].get("tor_port_capacity_gbps")
                        or (
                            float(hardware_data["link_bandwidth_gbps"])
                            * int(hardware_data["gpus_per_server"])
                            / float(hardware_data["oversubscription"])
                        )
                    ) if hardware_data is not None else None,
                    gateway_port_capacity_gbps=scenario_manifest["topology"].get(
                        "gateway_wan_port_capacity_gbps"
                    ),
                )
                add_wan_statistics(summary, config, scenario_manifest['topology'])
                summary['execution_model'] = execution_model
                if barrier_mode:
                    if schedule.flows and not Path(str(stage_file)+'.events.csv').is_file():
                        raise ValueError('backend did not acknowledge stage-barrier execution')
            except (OSError, ValueError) as exc:
                manifest["status"] = "incomplete"
                manifest["error"] = str(exc)
            else:
                write_summary(summary, attempt / "summary.json")
                manifest["status"] = "success"
                manifest["success"] = True
                manifest["summary"] = summary
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        (attempt / "stdout.log").write_text(stdout, encoding="utf-8")
        (attempt / "stderr.log").write_text(stderr, encoding="utf-8")
        manifest["status"] = "timeout"
        manifest["error"] = f"simulation exceeded {timeout_s} seconds"
    manifest["elapsed_wall_ns"] = time.monotonic_ns() - started
    _write_json_atomic(attempt / "run_manifest.json", manifest)
    return manifest
