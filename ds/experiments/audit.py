#!/usr/bin/env python3
"""Independent replay and invariant audit for raw partial-BvN results.

This deliberately does not call bvn_simulator.  It parses the BVN_CASE_V1 input,
replays every reported stage from the reported flows and fixed path routes, and
checks conservation, stage structure, resource totals, and the alpha+serial
formula.  In Bytes mode conservation is exact integer bytes; in Time/Delay modes
it uses the tool's declared absolute tolerance of 1e-6 byte per pair.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REL_TOL = 1e-9
TIME_ABS_TOL = 1e-6
BYTES_ABS_TOL = 1e-6
RESOURCE_CROSS_TOL_TIME = 1e-3


def close(a, b, rel=REL_TOL, abs_tol=0.0):
    return abs(a - b) <= abs_tol + rel * max(1.0, abs(a), abs(b))


def parse_case(path):
    lines = [line.strip() for line in Path(path).read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    pos = 0
    if lines[pos] != "BVN_CASE_V1":
        raise ValueError("bad case header")
    pos += 1
    parts = lines[pos].split()
    if parts[0] != "N":
        raise ValueError("missing N")
    n = int(parts[1])
    pos += 1
    if lines[pos] != "DEMAND":
        raise ValueError("missing DEMAND")
    pos += 1
    demand = [int(lines[pos + i]) for i in range(n * n)]
    pos += n * n
    parts = lines[pos].split()
    if parts[0] != "RESOURCES":
        raise ValueError("missing RESOURCES")
    resource_count = int(parts[1])
    pos += 1
    resources = []
    for _ in range(resource_count):
        parts = lines[pos].split()
        if int(parts[0]) != len(resources):
            raise ValueError("resource ids must be contiguous")
        resources.append((parts[1], float(parts[2])))
        pos += 1
    parts = lines[pos].split()
    if parts[0] != "PATHS":
        raise ValueError("missing PATHS")
    path_count = int(parts[1])
    pos += 1
    paths = {}
    for _ in range(path_count):
        parts = lines[pos].split()
        src, dst = int(parts[0]), int(parts[1])
        prop, rtt, count = float(parts[2]), float(parts[3]), int(parts[4])
        ids = [int(x) for x in parts[5:]]
        if len(ids) != count:
            raise ValueError("path resource count mismatch")
        paths[(src, dst)] = (prop, rtt, ids)
        pos += 1
    if pos != len(lines):
        raise ValueError("trailing lines")
    if len(paths) != n * (n - 1):
        raise ValueError("path coverage")
    return {
        "n": n,
        "demand": demand,
        "resources": resources,
        "paths": paths,
        "path_count": path_count,
    }


def plan_fingerprint(raw):
    stages = []
    for stage in raw.get("stages", []):
        flows = tuple(sorted((int(f["src"]), int(f["dst"]), float(f["bytes"]))
                             for f in stage.get("flows", [])))
        stages.append(flows)
    return stages


def audit_run(raw, case, space, case_id, method):
    errors = []
    warnings = []

    def fail(msg):
        errors.append(msg)

    n = case["n"]
    demand = case["demand"]
    resources = case["resources"]
    paths = case["paths"]
    if raw.get("n") != n:
        fail(f"n mismatch raw={raw.get('n')} case={n}")
    total_demand = sum(demand)
    if abs(raw.get("demand_bytes", -1) - total_demand) > 1e-6:
        fail(f"demand_bytes mismatch raw={raw.get('demand_bytes')} expected={total_demand}")

    stages = raw.get("stages", [])
    if not isinstance(stages, list):
        fail("stages is not a list")
        return errors, warnings, {}
    if total_demand > 0 and not stages:
        fail("nonzero demand produced no stages")
    if total_demand == 0 and stages:
        fail("zero demand produced nonempty stages")

    pair_total = [0.0] * (n * n)
    resource_total = [0.0] * len(resources)
    replay_total = 0.0
    replay_serial = 0.0
    replay_alpha = 0.0
    byte_integer = space == "bytes"

    for si, stage in enumerate(stages):
        flows = stage.get("flows", [])
        if not isinstance(flows, list) or not flows:
            fail(f"stage {si}: empty or invalid flows")
            continue
        seen_src = set()
        seen_dst = set()
        stage_res = [0.0] * len(resources)
        stage_alpha = 0.0
        stage_bytes = 0.0
        for fi, flow in enumerate(flows):
            src, dst = int(flow["src"]), int(flow["dst"])
            value = float(flow["bytes"])
            if not math.isfinite(value) or value <= 0:
                fail(f"stage {si} flow {fi}: nonpositive/nonfinite bytes {value}")
                continue
            if src == dst:
                fail(f"stage {si} flow {fi}: self flow {src}->{dst}")
            if src == dst or (src, dst) not in paths:
                fail(f"stage {si} flow {fi}: missing path {src}->{dst}")
                continue
            if src in seen_src:
                fail(f"stage {si}: repeated source {src}")
            if dst in seen_dst:
                fail(f"stage {si}: repeated destination {dst}")
            seen_src.add(src)
            seen_dst.add(dst)
            if byte_integer and abs(value - round(value)) > BYTES_ABS_TOL:
                fail(f"stage {si} flow {fi}: noninteger bytes {value!r} in Bytes mode")
            prop, _rtt, ids = paths[(src, dst)]
            stage_alpha = max(stage_alpha, prop)
            stage_bytes += value
            pair_total[src * n + dst] += value
            for rid in ids:
                stage_res[rid] += value
                resource_total[rid] += value
        expected_serial = max(
            [0.008 * b / resources[r][1] for r, b in enumerate(stage_res)] or [0.0])
        expected_total = stage_alpha + expected_serial
        report_res = stage.get("resource_bytes", [])
        if len(report_res) != len(resources):
            fail(f"stage {si}: resource_bytes length {len(report_res)} != {len(resources)}")
        else:
            for rid, value in enumerate(stage_res):
                if not close(float(report_res[rid]), value, abs_tol=1e-6):
                    fail(f"stage {si} resource {rid}: report={report_res[rid]} "
                         f"replay={value}")
        for field, value in (("alpha_us", stage_alpha),
                             ("serial_us", expected_serial),
                             ("total_us", expected_total)):
            if not close(float(stage.get(field, float("nan"))), value, abs_tol=1e-9):
                fail(f"stage {si} {field}: report={stage.get(field)} replay={value}")
        replay_total += expected_total
        replay_serial += expected_serial
        replay_alpha += stage_alpha

    for field, value in (("communication_us", replay_total),
                         ("serialization_sum_us", replay_serial),
                         ("propagation_sum_us", replay_alpha)):
        if not close(float(raw.get(field, float("nan"))), value, abs_tol=1e-8):
            fail(f"{field}: report={raw.get(field)} replay={value}")
    if abs(float(raw.get("execution_stages", -1)) - len(stages)) > 0.5:
        fail(f"execution_stages mismatch raw={raw.get('execution_stages')} "
             f"stages={len(stages)}")

    if len(raw.get("resource_total_bytes", [])) != len(resources):
        fail("resource_total_bytes length mismatch")
    else:
        for rid, value in enumerate(resource_total):
            if not close(float(raw["resource_total_bytes"][rid]), value, abs_tol=1e-6):
                fail(f"resource_total_bytes[{rid}]: report={raw['resource_total_bytes'][rid]} "
                     f"replay={value}")

    max_err = 0.0
    abs_tol = BYTES_ABS_TOL if byte_integer else TIME_ABS_TOL
    for s in range(n):
        for d in range(n):
            if s == d:
                continue
            err = abs(pair_total[s * n + d] - demand[s * n + d])
            max_err = max(max_err, err)
            if err > abs_tol:
                fail(f"pair {s}->{d}: replay={pair_total[s * n + d]} "
                     f"demand={demand[s * n + d]} error={err}")
    if not close(float(raw.get("max_pair_error_bytes", -1)), max_err,
                 abs_tol=abs_tol):
        fail(f"max_pair_error_bytes mismatch raw={raw.get('max_pair_error_bytes')} "
             f"replay={max_err}")
    fingerprint = plan_fingerprint(raw)
    stats = {
        "stages": len(stages),
        "decomposition_steps": raw.get("decomposition_steps"),
        "candidate_evaluations": raw.get("candidate_evaluations"),
        "edit_attempts": raw.get("edit_attempts"),
        "communication_us": raw.get("communication_us"),
        "replay_total_us": replay_total,
        "replay_serial_us": replay_serial,
        "replay_alpha_us": replay_alpha,
        "max_pair_error_bytes": max_err,
        "resource_total_bytes": resource_total,
        "plan_fingerprint": fingerprint,
    }
    return errors, warnings, stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default=str(ROOT / "ds/experiments/raw/full"))
    parser.add_argument("--out", default=str(ROOT / "ds/experiments/audit/audit.json"))
    args = parser.parse_args()
    raw_root = Path(args.raw)
    all_errors = []
    cases = []
    for case_dir in sorted(p for p in raw_root.iterdir() if p.is_dir()):
        meta_path = case_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        case_path = ROOT / meta["case_path"]
        case = parse_case(case_path)
        case_entry = {"case": meta["case"], "methods": {},
                      "resource_total_by_method": {}}
        fingerprints = {}
        for method in meta["runs"]:
            if method.get("status") not in ("ok", "skipped"):
                continue
            raw_path = case_dir / f"{method['method']}.json"
            if not raw_path.exists():
                all_errors.append({"case": meta["case"],
                                   "method": method["method"],
                                   "error": f"missing raw file {raw_path}"})
                continue
            raw = json.loads(raw_path.read_text())
            space = raw.get("options", {}).get("space", "unknown")
            errors, warnings, stats = audit_run(
                raw, case, space, meta["case"], method["method"])
            case_entry["methods"][method["method"]] = {
                "errors": errors,
                "warnings": warnings,
                "stats": stats,
            }
            case_entry["resource_total_by_method"][method["method"]] = \
                stats.get("resource_total_bytes", [])
            fingerprints[method["method"]] = stats.get("plan_fingerprint")
            all_errors.extend({"case": meta["case"], "method": method["method"],
                               "error": e} for e in errors)
        # Cross-method resource totals must agree (same physical demand/routes).
        totals = {k: v for k, v in case_entry["resource_total_by_method"].items()
                  if v}
        if len(totals) > 1:
            names = sorted(totals)
            base_name = names[0]
            base = totals[base_name]
            space = None
            for m in names:
                rp = case_dir / f"{m}.json"
                if rp.exists():
                    raw = json.loads(rp.read_text())
                    space = raw.get("options", {}).get("space")
                    if space == "bytes":
                        break
            tol = 1e-6 if space == "bytes" else RESOURCE_CROSS_TOL_TIME
            for other in names[1:]:
                if len(base) != len(totals[other]):
                    all_errors.append({"case": meta["case"], "method": other,
                                       "error": "resource total length mismatch"})
                    continue
                for rid, (a, b) in enumerate(zip(base, totals[other])):
                    if abs(a - b) > tol + 1e-12 * max(1.0, abs(a), abs(b)):
                        all_errors.append(
                            {"case": meta["case"], "method": other,
                             "error": f"resource total {rid} differs from "
                                      f"{base_name}: {a} vs {b}"})
        # C0 should be an empty schedule for every method.
        if meta["case"] == "HAND-C0":
            for method, fp in fingerprints.items():
                if fp:
                    all_errors.append({"case": meta["case"], "method": method,
                                       "error": "C0 expected empty plan"})
        cases.append(case_entry)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "raw": str(raw_root),
        "cases": cases,
        "errors": all_errors,
        "case_count": len(cases),
    }
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"audited {len(cases)} cases; {len(all_errors)} errors")
    for err in all_errors[:20]:
        print(f"  {err['case']} {err['method']}: {err['error']}")
    if len(all_errors) > 20:
        print(f"  ... {len(all_errors) - 20} more")
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
