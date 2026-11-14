"""Command-line interface for all-to-all artifacts and scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import add_wan_statistics, analyze_fct, write_summary
from .campaign import aggregate_campaign, run_campaign
from .reporting import write_campaign_report
from .runner import run_scenario
from .scenario import (
    SCHEDULERS,
    generate_artifact,
    prepare_scenario,
    resolve_output,
    schedule_artifact,
)


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--new-attempt", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m simai_a2a")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="generate one traffic matrix")
    generate.add_argument(
        "--generator",
        required=True,
        choices=("balanced", "fast-uniform", "fast-zipf"),
    )
    generate.add_argument("--num-devices", required=True, type=int)
    generate.add_argument("--gpus-per-server", required=True, type=int)
    generate.add_argument("--pair-bytes", type=int)
    generate.add_argument("--bytes-per-unit", type=int)
    generate.add_argument("--total-bytes", type=int)
    generate.add_argument("--alpha", type=float)
    generate.add_argument("--seed", type=int, default=0)
    _add_output(generate)

    schedule = commands.add_parser("schedule", help="schedule an existing matrix")
    schedule.add_argument("--matrix-dir", required=True, type=Path)
    schedule.add_argument("--hardware", required=True, type=Path)
    schedule.add_argument(
        "--algorithm",
        required=True,
        choices=SCHEDULERS,
    )
    schedule.add_argument("--seed", type=int, default=0)
    schedule.add_argument('--fast-options', type=Path, help='JSON pair-class profile for FAST extensions')
    schedule.add_argument('--execution-model',default='open-loop',choices=(
        'open-loop','stage-barrier-ideal-scaleup','fast-nvidia-deps-ideal-scaleup',
        'fast-nvidia-deps-modeled-scaleup'))
    _add_output(schedule)

    prepare = commands.add_parser("prepare", help="prepare a complete scenario")
    prepare.add_argument("--config", required=True, type=Path)
    _add_output(prepare)

    run = commands.add_parser("run", help="run one prepared schedule through ns-3")
    run.add_argument("--scenario", required=True, type=Path)
    run.add_argument(
        "--algorithm",
        required=True,
        choices=SCHEDULERS,
    )
    run.add_argument("--topology", required=True, type=Path)
    run.add_argument("--binary", required=True, type=Path)
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument("--output-root", type=Path)
    run.add_argument(
        "--ns3-threads",
        type=int,
        help="ns-3 MTP worker count; 0 selects the sequential event scheduler",
    )

    analyze = commands.add_parser("analyze", help="analyze an existing ns-3 attempt")
    analyze.add_argument("--scenario", required=True, type=Path)
    analyze.add_argument(
        "--algorithm",
        required=True,
        choices=SCHEDULERS,
    )
    analyze.add_argument("--attempt", required=True, type=Path)
    analyze.add_argument("--output", type=Path)

    campaign = commands.add_parser("campaign-run", help="run or resume an experiment campaign")
    campaign.add_argument("--config", required=True, type=Path)
    campaign.add_argument("--output", required=True, type=Path)
    campaign.add_argument("--binary", required=True, type=Path)
    campaign.add_argument("--timeout", type=float, default=300.0)
    campaign.add_argument("--max-attempts", type=int, default=2)
    campaign.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="maximum independent ns-3 runs to execute concurrently",
    )

    aggregate = commands.add_parser("campaign-aggregate", help="rebuild campaign tables")
    aggregate.add_argument("--output", required=True, type=Path)

    report = commands.add_parser("campaign-report", help="write campaign plots and summary")
    report.add_argument("--output", required=True, type=Path)
    return parser


def _generator_parameters(args: argparse.Namespace) -> dict:
    if args.generator == "balanced":
        if (args.pair_bytes is None) == (args.total_bytes is None):
            raise ValueError("balanced traffic requires exactly one of --pair-bytes or --total-bytes")
        return {"pair_bytes": args.pair_bytes} if args.pair_bytes is not None else {
            "total_bytes": args.total_bytes
        }
    if args.bytes_per_unit is None:
        raise ValueError("--bytes-per-unit is required for FAST traffic")
    parameters = {"bytes_per_unit": args.bytes_per_unit, "seed": args.seed}
    if args.total_bytes is not None:
        parameters["total_bytes"] = args.total_bytes
    if args.generator == "fast-zipf":
        if args.alpha is None:
            raise ValueError("--alpha is required for fast-zipf traffic")
        parameters["alpha"] = args.alpha
    return parameters


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "campaign-run":
            result = run_campaign(
                args.config,
                args.output,
                args.binary,
                timeout_s=args.timeout,
                max_attempts=args.max_attempts,
                jobs=args.jobs,
            )
            print(json.dumps({"result": result}, sort_keys=True))
            return 0
        if args.command == "campaign-aggregate":
            rows = aggregate_campaign(args.output)
            print(json.dumps({"row_count": len(rows)}, sort_keys=True))
            return 0
        if args.command == "campaign-report":
            report_path = write_campaign_report(args.output)
            print(json.dumps({"report": str(report_path)}, sort_keys=True))
            return 0
        if args.command == "run":
            result = run_scenario(
                args.scenario,
                args.algorithm,
                args.topology,
                args.binary,
                timeout_s=args.timeout,
                output_root=args.output_root,
                ns3_threads=args.ns3_threads,
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if result["success"] else 3
        if args.command == "analyze":
            attempt = args.attempt.resolve()
            scenario = args.scenario.resolve()
            scenario_manifest_path = scenario / "scenario_manifest.json"
            hardware_path = scenario / "hardware.json"
            scenario_manifest = (
                json.loads(scenario_manifest_path.read_text(encoding="utf-8"))
                if scenario_manifest_path.is_file()
                else {"topology": {}}
            )
            hardware = (
                json.loads(hardware_path.read_text(encoding="utf-8"))
                if hardware_path.is_file()
                else None
            )
            result = analyze_fct(
                scenario / "matrix",
                scenario / "schedules" / args.algorithm,
                attempt / "fct.txt",
                attempt / "pfc.txt",
                attempt / "qlen.txt",
                attempt / "bw.txt",
                attempt / "rate.txt",
                attempt / "cnp.txt",
                tor_id=scenario_manifest.get("topology", {}).get("tor_id"),
                tor_ids=scenario_manifest.get("topology", {}).get("tor_ids"),
                gateway_ids=scenario_manifest.get("topology", {}).get("gateway_ids"),
                gateway_wan_ports=scenario_manifest.get("topology", {}).get(
                    "gateway_wan_ports"
                ),
                tor_port_capacity_gbps=(
                    scenario_manifest.get("topology", {}).get(
                        "tor_port_capacity_gbps"
                    )
                    or (
                        float(hardware["link_bandwidth_gbps"])
                        * int(hardware["gpus_per_server"])
                        / float(hardware["oversubscription"])
                    )
                ) if hardware is not None else None,
                gateway_port_capacity_gbps=scenario_manifest.get("topology", {}).get(
                    "gateway_wan_port_capacity_gbps"
                ),
            )
            output = args.output or attempt / "summary.json"
            add_wan_statistics(result, attempt/'ns3.conf', scenario_manifest.get('topology', {}))
            write_summary(result, output)
            print(json.dumps({"output": str(output), "result": result}, sort_keys=True))
            return 0

        output = resolve_output(args.output, args.new_attempt)
        if args.command == "generate":
            result = generate_artifact(
                generator=args.generator,
                num_devices=args.num_devices,
                gpus_per_server=args.gpus_per_server,
                parameters=_generator_parameters(args),
                output=output,
            )
        elif args.command == "schedule":
            result = schedule_artifact(
                matrix_dir=args.matrix_dir,
                hardware_path=args.hardware,
                algorithm=args.algorithm,
                seed=args.seed,
                output=output,
                fast_options=(json.loads(args.fast_options.read_text(encoding='utf-8'))
                              if args.fast_options is not None else None),
                execution_model=args.execution_model,
            )
        elif args.command == "prepare":
            result = prepare_scenario(args.config, output)
        else:
            raise ValueError(f"unsupported command: {args.command}")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(output), "result": result}, sort_keys=True))
    return 0
