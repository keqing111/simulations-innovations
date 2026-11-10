#!/usr/bin/env python3
"""Build paired metrics and grouped summaries from raw BvN experiment data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIE_REL = 1e-9


def close_time(a, b):
    return abs(a - b) <= TIE_REL * max(1.0, abs(a), abs(b))


def geometric_mean(values):
    values = [x for x in values if x is not None and math.isfinite(x) and x > 0]
    if not values:
        return None
    return math.exp(sum(math.log(x) for x in values) / len(values))


def ratio(base, method):
    if method == 0:
        return 1.0 if base == 0 else None
    return base / method


def outcome(base, method):
    if close_time(base, method):
        return "tie"
    return "win" if method < base else "loss"


def median(values):
    values = [x for x in values if x is not None and math.isfinite(x)]
    return statistics.median(values) if values else None


def load_rows(raw_root):
    rows = []
    for case_dir in sorted(p for p in raw_root.iterdir() if p.is_dir()):
        meta_path = case_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        raw_by_method = {}
        for run in meta["runs"]:
            if run.get("status") not in ("ok", "skipped"):
                continue
            raw_path = case_dir / f"{run['method']}.json"
            if not raw_path.exists():
                continue
            raw = json.loads(raw_path.read_text())
            raw_by_method[run["method"]] = raw
        native = raw_by_method.get("fast-native")
        endpoint = raw_by_method.get("partial-endpoint")
        native_comm = native.get("communication_us") if native else None
        endpoint_comm = endpoint.get("communication_us") if endpoint else None
        for method, raw in raw_by_method.items():
            options = raw.get("options", {})
            samples = raw.get("planning_samples_us", [])
            comm = raw.get("communication_us")
            rows.append({
                "case": meta["case"],
                "topology": meta["topology"],
                "workload": meta["workload"],
                "size_mib": meta["size_mib"],
                "seed": meta["seed"],
                "n": meta["n"],
                "unit_bytes": meta["unit_bytes"],
                "demand_bytes": raw.get("demand_bytes"),
                "method": method,
                "space": options.get("space"),
                "global_rule": options.get("global"),
                "local_rule": options.get("local"),
                "candidate_evaluations": raw.get("candidate_evaluations"),
                "edit_attempts": raw.get("edit_attempts"),
                "stages": raw.get("execution_stages"),
                "decomposition_steps": raw.get("decomposition_steps"),
                "communication_us": comm,
                "serialization_sum_us": raw.get("serialization_sum_us"),
                "propagation_sum_us": raw.get("propagation_sum_us"),
                "speedup_native": ratio(native_comm, comm)
                if native_comm is not None else None,
                "speedup_endpoint": ratio(endpoint_comm, comm)
                if endpoint_comm is not None else None,
                "vs_native": outcome(native_comm, comm)
                if native_comm is not None else None,
                "vs_endpoint": outcome(endpoint_comm, comm)
                if endpoint_comm is not None else None,
                "planning_median_us": median(samples),
                "planning_min_us": min(samples) if samples else None,
                "planning_max_us": max(samples) if samples else None,
                "max_pair_error_bytes": raw.get("max_pair_error_bytes"),
            })
    return rows


def group_rows(rows, key_names):
    groups = {}
    for row in rows:
        key = tuple(row[k] for k in key_names)
        groups.setdefault(key, []).append(row)
    out = []
    for key, items in sorted(groups.items(), key=lambda kv: kv[0]):
        for method in sorted({r["method"] for r in items}):
            mine = [r for r in items if r["method"] == method]
            native_pairs = [(r["speedup_native"], r["vs_native"]) for r in mine
                            if r["speedup_native"] is not None]
            endpoint_pairs = [(r["speedup_endpoint"], r["vs_endpoint"])
                              for r in mine if r["speedup_endpoint"] is not None]
            entry = {k: v for k, v in zip(key_names, key)}
            entry.update({
                "method": method,
                "cases": len(mine),
                "gm_speedup_native": geometric_mean([x[0] for x in native_pairs]),
                "gm_speedup_endpoint": geometric_mean([x[0] for x in endpoint_pairs]),
                "wins_native": sum(1 for _, o in native_pairs if o == "win"),
                "ties_native": sum(1 for _, o in native_pairs if o == "tie"),
                "losses_native": sum(1 for _, o in native_pairs if o == "loss"),
                "wins_endpoint": sum(1 for _, o in endpoint_pairs if o == "win"),
                "ties_endpoint": sum(1 for _, o in endpoint_pairs if o == "tie"),
                "losses_endpoint": sum(1 for _, o in endpoint_pairs if o == "loss"),
                "median_stages": median([r["stages"] for r in mine]),
                "median_planning_us": median([r["planning_median_us"] for r in mine]),
                "total_candidate_evaluations": sum(
                    r["candidate_evaluations"] or 0 for r in mine),
                "total_edit_attempts": sum(r["edit_attempts"] or 0 for r in mine),
                "worst_speedup_native": min(
                    (x[0] for x in native_pairs), default=None),
                "best_speedup_native": max(
                    (x[0] for x in native_pairs), default=None),
            })
            out.append(entry)
    return out


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})


def fmt(value, digits=4):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=str(ROOT / "ds/experiments/raw/full"))
    parser.add_argument("--out", default=str(ROOT / "ds/experiments/summary"))
    args = parser.parse_args()
    raw_root = Path(args.raw)
    out_dir = Path(args.out)
    rows = load_rows(raw_root)
    if not rows:
        raise SystemExit(f"no raw rows under {raw_root}")

    fields = [
        "case", "topology", "workload", "size_mib", "seed", "n", "unit_bytes",
        "demand_bytes", "method", "space", "global_rule", "local_rule",
        "candidate_evaluations", "edit_attempts", "stages",
        "decomposition_steps", "communication_us", "serialization_sum_us",
        "propagation_sum_us", "speedup_native", "speedup_endpoint",
        "vs_native", "vs_endpoint", "planning_median_us", "planning_min_us",
        "planning_max_us", "max_pair_error_bytes",
    ]
    write_csv(out_dir / "paired.csv", rows, fields)
    groups_topo = group_rows(rows, ("topology",))
    groups_work = group_rows(rows, ("topology", "workload"))
    groups_size = group_rows(rows, ("topology", "workload", "size_mib"))
    group_fields = [
        "topology", "workload", "size_mib", "method", "cases",
        "gm_speedup_native", "gm_speedup_endpoint", "wins_native",
        "ties_native", "losses_native", "wins_endpoint", "ties_endpoint",
        "losses_endpoint", "median_stages", "median_planning_us",
        "total_candidate_evaluations", "total_edit_attempts",
        "worst_speedup_native", "best_speedup_native",
    ]
    write_csv(out_dir / "group_by_topology.csv", groups_topo,
              [x for x in group_fields if x != "workload" and x != "size_mib"])
    write_csv(out_dir / "group_by_workload.csv", groups_work,
              [x for x in group_fields if x != "size_mib"])
    write_csv(out_dir / "group_by_size.csv", groups_size, group_fields)
    (out_dir / "summary.json").write_text(json.dumps({
        "raw": str(raw_root),
        "rows": rows,
        "group_by_topology": groups_topo,
        "group_by_workload": groups_work,
        "group_by_size": groups_size,
        "tie_rel_tolerance": TIE_REL,
    }, indent=2, sort_keys=True) + "\n")

    print("overall by method (relative to fast-native / partial-endpoint):")
    for entry in group_rows(rows, ()):
        print(f"  {entry['method']:18s} n={entry['cases']:3d} "
              f"gm_native={fmt(entry['gm_speedup_native'])} "
              f"(W/T/L {entry['wins_native']}/{entry['ties_native']}/"
              f"{entry['losses_native']}) "
              f"gm_endpoint={fmt(entry['gm_speedup_endpoint'])} "
              f"(W/T/L {entry['wins_endpoint']}/{entry['ties_endpoint']}/"
              f"{entry['losses_endpoint']})")
    print(f"wrote {out_dir / 'paired.csv'}, group CSVs, and summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
