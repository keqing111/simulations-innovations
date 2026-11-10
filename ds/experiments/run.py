#!/usr/bin/env python3
"""Run bvn_simulator for every case/method in a manifest and store raw JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "simulation" / "build" / "bvn_simulator"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state():
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        head = None
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL).splitlines()
    except Exception:
        status = []
    return {"git_head": head, "git_status": status}


def load_manifest(path):
    data = json.loads(Path(path).read_text())
    if data.get("schema") != 1:
        raise SystemExit(f"unsupported manifest schema: {data.get('schema')}")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(ROOT / "ds/experiments/manifest.json"))
    parser.add_argument("--raw", default=str(ROOT / "ds/experiments/raw/full"))
    parser.add_argument("--methods", default=",".join(
        ["fast-native", "partial-endpoint", "partial-topology",
         "partial-time", "partial-delay"]))
    parser.add_argument("--filter", default=None,
                        help="regular expression matched against case id")
    parser.add_argument("--repeat", type=int, default=None,
                        help="override manifest repeat")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    if not BIN.exists():
        raise SystemExit(f"missing simulator, run make -C simulation bvn-build: {BIN}")
    manifest = load_manifest(args.manifest)
    methods = [x.strip() for x in args.methods.split(",") if x.strip()]
    unknown = set(methods) - set(manifest["methods"])
    if unknown:
        raise SystemExit(f"unknown methods: {sorted(unknown)}")
    repeat = args.repeat or int(manifest.get("repeat", 1))
    pattern = re.compile(args.filter) if args.filter else None

    raw_root = Path(args.raw).resolve()
    raw_root.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(Path(args.manifest).resolve()),
        "manifest_sha256": sha256_file(args.manifest),
        "binary": str(BIN),
        "binary_sha256": sha256_file(BIN),
        "repeat": repeat,
        "methods": methods,
        **git_state(),
        "cases": [],
    }
    errors = []
    selected = []
    for case in manifest["cases"]:
        if pattern and not pattern.search(case["id"]):
            continue
        selected.append(case)
    print(f"selected {len(selected)} cases x {len(methods)} methods, repeat={repeat}")

    for ci, case in enumerate(selected):
        case_id = case["id"]
        case_path = ROOT / case["path"]
        if not case_path.exists():
            errors.append({"case": case_id, "error": f"missing case file {case_path}"})
            if args.fail_fast:
                break
            continue
        out_dir = raw_root / case_id
        out_dir.mkdir(parents=True, exist_ok=True)
        case_meta = {
            "case": case_id,
            "case_path": str(case_path.relative_to(ROOT)),
            "case_sha256": sha256_file(case_path),
            "topology": case["topology"],
            "workload": case["workload"],
            "size_mib": case["size_mib"],
            "seed": case["seed"],
            "n": case["n"],
            "unit_bytes": case["unit_bytes"],
            "demand_bytes": case["demand_bytes"],
            "manifest_repeat": repeat,
            "runs": [],
        }
        for method in methods:
            out_path = out_dir / f"{method}.json"
            if args.skip_existing and out_path.exists():
                case_meta["runs"].append(
                    {"method": method, "status": "skipped",
                     "output": str(out_path.relative_to(ROOT))})
                continue
            cmd = [str(BIN), "--method", method, "--input", str(case_path),
                   "--unit-bytes",
                   str(case["unit_bytes"] if method == "fast-native" else 1),
                   "--repeat", str(repeat)]
            started = time.time()
            try:
                proc = subprocess.run(cmd, cwd=ROOT, text=True, timeout=args.timeout,
                                      capture_output=True)
            except subprocess.TimeoutExpired as exc:
                elapsed = time.time() - started
                log = out_dir / f"{method}.log"
                log.write_text(f"TIMEOUT after {elapsed:.3f}s\n"
                               f"cmd: {shlex.join(cmd)}\n"
                               f"stdout:\n{exc.stdout or ''}\n"
                               f"stderr:\n{exc.stderr or ''}\n")
                errors.append({"case": case_id, "method": method,
                               "error": "timeout", "elapsed_s": elapsed})
                case_meta["runs"].append({"method": method, "status": "timeout",
                                          "elapsed_s": elapsed})
                if args.fail_fast:
                    break
                continue
            elapsed = time.time() - started
            log = out_dir / f"{method}.log"
            log.write_text(f"cmd: {shlex.join(cmd)}\n"
                           f"elapsed_s: {elapsed:.6f}\n"
                           f"returncode: {proc.returncode}\n"
                           f"--- stdout ---\n{proc.stdout}\n"
                           f"--- stderr ---\n{proc.stderr}\n")
            parsed = None
            parse_error = None
            try:
                parsed = json.loads(proc.stdout)
            except Exception as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
            if proc.returncode != 0 or parsed is None:
                errors.append({"case": case_id, "method": method,
                               "error": "nonzero-exit-or-invalid-json",
                               "returncode": proc.returncode,
                               "parse_error": parse_error,
                               "stderr": proc.stderr[-4000:],
                               "elapsed_s": elapsed})
                case_meta["runs"].append({"method": method, "status": "failed",
                                          "returncode": proc.returncode,
                                          "parse_error": parse_error,
                                          "elapsed_s": elapsed})
                if args.fail_fast:
                    break
                continue
            parsed["_run"] = {
                "case_id": case_id,
                "method": method,
                "cmd": cmd,
                "elapsed_s": elapsed,
                "returncode": proc.returncode,
                "repeat": repeat,
                "unit_bytes": case["unit_bytes"] if method == "fast-native" else 1,
                "case_sha256": case_meta["case_sha256"],
                "binary_sha256": run_meta["binary_sha256"],
                "git_head": run_meta["git_head"],
            }
            out_path.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n")
            case_meta["runs"].append({"method": method, "status": "ok",
                                      "elapsed_s": elapsed,
                                      "output": str(out_path.relative_to(ROOT))})
        (out_dir / "meta.json").write_text(
            json.dumps(case_meta, indent=2, sort_keys=True) + "\n")
        run_meta["cases"].append(case_meta)
        done = sum(1 for r in case_meta["runs"] if r["status"] in ("ok", "skipped"))
        print(f"[{ci + 1}/{len(selected)}] {case_id}: {done}/{len(methods)} ok")
        if args.fail_fast and any(r["status"] in ("failed", "timeout")
                                  for r in case_meta["runs"]):
            break

    run_meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    run_meta["errors"] = errors
    (raw_root / "run_meta.json").write_text(
        json.dumps(run_meta, indent=2, sort_keys=True) + "\n")
    if errors:
        print(f"{len(errors)} errors; see {raw_root / 'run_meta.json'}",
              file=sys.stderr)
        return 1
    print(f"done: {len(run_meta['cases'])} cases, raw data in {raw_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
