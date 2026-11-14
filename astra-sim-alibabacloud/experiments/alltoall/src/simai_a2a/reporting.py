"""Campaign-level Markdown summaries and plots."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ALGORITHM_ORDER = ("direct-concurrent", "spreadout", "roundrobin", "fast")


def write_campaign_report(output: Path) -> Path:
    output = Path(output).resolve()
    rows: list[dict[str, Any]] = json.loads(
        (output / "aggregate.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (output / "campaign_manifest.json").read_text(encoding="utf-8")
    )
    expected = int(manifest["run_count"])
    if not any(row.get('speedup_vs_spreadout') is not None for row in rows):
        # FAST ablations need not include the unrelated SpreadOut baseline.
        lines = [f'# {output.name} results', '', f'Completed runs: {len(rows)}/{expected}', '',
                 '| Algorithm | Runs | Mean makespan (ms) | Mean speedup vs FAST |',
                 '|---|---:|---:|---:|']
        for algorithm in sorted({row['algorithm'] for row in rows}):
            group = [row for row in rows if row['algorithm']==algorithm]
            speed = [row['speedup_vs_fast'] for row in group if row.get('speedup_vs_fast') is not None]
            speed_text = f'{statistics.mean(speed):.3f}' if speed else 'n/a'
            lines.append(f"| {algorithm} | {len(group)} | "
                         f"{statistics.mean(row['actual_makespan_ns'] for row in group)/1e6:.3f} | {speed_text} |")
        lines.extend(['', 'Per-workload figures: run `scripts/render_fast_wan_results.py`.', ''])
        report = output/'RESULTS.md'
        report.write_text('\n'.join(lines),encoding='utf-8')
        return report
    fast = [row for row in rows if row["algorithm"] == "fast"]
    lines = [
        f"# {output.name} results",
        "",
        f"- Completed runs: {len(rows)}/{expected}",
        f"- Scenario matrices: {len({row['case_id'] for row in rows})}",
        f"- Failed or missing runs: {expected - len(rows)}",
        "",
        "## FAST summary by profile",
        "",
        "| Profile | Samples | Speedup vs SpreadOut (mean/median/min/max) | Speedup vs Direct (mean) | Byte amplification (mean) |",
        "|---|---:|---:|---:|---:|",
    ]
    for profile in sorted({row["profile_id"] for row in fast}):
        group = [row for row in fast if row["profile_id"] == profile]
        spread = [float(row["speedup_vs_spreadout"]) for row in group]
        direct = [
            float(row["speedup_vs_direct"])
            for row in group
            if row.get("speedup_vs_direct") is not None
        ]
        amp = [float(row["schedule_byte_amplification"]) for row in group]
        lines.append(
            f"| {profile} | {len(group)} | {statistics.mean(spread):.3f} / "
            f"{statistics.median(spread):.3f} / {min(spread):.3f} / {max(spread):.3f} | "
            f"{statistics.mean(direct):.3f} | {statistics.mean(amp):.3f} |"
            if direct else
            f"| {profile} | {len(group)} | {statistics.mean(spread):.3f} / "
            f"{statistics.median(spread):.3f} / {min(spread):.3f} / {max(spread):.3f} | "
            f"n/a | {statistics.mean(amp):.3f} |"
        )
    lines.extend(["", "## Artifacts", "", "- `aggregate.csv`: flat table", "- `aggregate.json`: typed rows"])
    report = output / "RESULTS.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_plots(rows, output / "plots")
    return report


def _write_plots(rows: list[dict[str, Any]], directory: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory.mkdir(parents=True, exist_ok=True)
    fast = [row for row in rows if row["algorithm"] == "fast"]
    profiles = sorted({row["profile_id"] for row in fast})
    workloads = sorted({row["workload_id"] for row in fast})
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in fast:
        grouped[(row["profile_id"], row["workload_id"])].append(
            float(row["speedup_vs_spreadout"])
        )
    fig, ax = plt.subplots(figsize=(max(8, len(workloads) * 1.2), 4.8))
    width = 0.8 / max(1, len(profiles))
    for index, profile in enumerate(profiles):
        values = [statistics.mean(grouped[(profile, workload)]) for workload in workloads]
        xs = [position + (index - (len(profiles) - 1) / 2) * width for position in range(len(workloads))]
        ax.bar(xs, values, width=width, label=profile)
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_ylabel("FAST speedup vs SpreadOut")
    ax.set_xticks(range(len(workloads)), workloads, rotation=35, ha="right")
    ax.legend(ncol=max(1, len(profiles)))
    fig.tight_layout()
    fig.savefig(directory / "fast-speedup-vs-spreadout.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 4.8))
    for profile in profiles:
        group = [row for row in fast if row["profile_id"] == profile]
        ax.scatter(
            [float(row["schedule_byte_amplification"]) for row in group],
            [float(row["speedup_vs_spreadout"]) for row in group],
            label=profile,
            alpha=0.75,
        )
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_xlabel("Scheduled-byte amplification")
    ax.set_ylabel("FAST speedup vs SpreadOut")
    ax.legend()
    fig.tight_layout()
    fig.savefig(directory / "fast-amplification-vs-speedup.png", dpi=180)
    plt.close(fig)
