"""Derive descriptive phase costs and cache-use counts from frozen study traces.

This is postprocessing, not an evaluated policy or a new registered success gate.
The primary result and preregistered paired intervals remain unchanged.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean


def summarize(directory):
    result = json.loads((directory / "result.json").read_text())
    rows = [
        json.loads(line)
        for line in gzip.decompress((directory / "traces.jsonl.gz").read_bytes())
        .decode()
        .splitlines()
    ]
    per_group = defaultdict(list)
    for row in rows:
        job = row["job"]
        phase = job["phase"]
        label = phase if phase != "serve" else "serve-" + str(job["repeat"])
        per_group[label, row["branch"]].append(row)
    phases = []
    for label in ("acquisition", "unseen", *[f"serve-{i}" for i in range(1, 7)]):
        by_branch = {}
        for branch in ("factored", "retained_factored", "frozen_before_unseen"):
            group = per_group[label, branch]
            by_branch[branch] = {
                "jobs": len(group),
                "success": mean(r["score"]["passed"] for r in group),
                "mean_cpu_per_job": mean(r["cpu_seconds"] for r in group),
                "mean_total_work_per_job": mean(r["total_work"] for r in group),
            }
        phases.append({"phase": label, "branches": by_branch})
    known = defaultdict(set)
    hits = defaultdict(int)
    seen = defaultdict(int)
    for row in rows:
        key = row["seed"], row["branch"]
        phase = row["job"]["phase"]
        for attempt in row["attempts"]:
            proposal = attempt["proposal"]
            program = proposal["program_sha256"]
            seen[row["branch"], phase] += 1
            if program is not None and program in known[key]:
                hits[row["branch"], phase] += 1
            receipt = attempt["receipt"]
            if receipt and receipt["accepted"] and attempt["retain"]:
                known[key].add(program)
    break_even = []
    for seed in result["seeds"]:
        groups = {
            branch: [r for r in rows if r["seed"] == seed and r["branch"] == branch]
            for branch in ("factored", "retained_factored")
        }
        initialization = {
            trial["branch"]: trial["initialization_cpu_seconds"]
            for trial in result["trials"]
            if trial["seed"] == seed
        }
        sums = {branch: initialization[branch] for branch in groups}
        deltas = []
        for left, right in zip(
            groups["retained_factored"], groups["factored"], strict=True
        ):
            sums["retained_factored"] += left["cpu_seconds"]
            sums["factored"] += right["cpu_seconds"]
            deltas.append(sums["retained_factored"] - sums["factored"])
        stable = next(
            (i for i in range(len(deltas)) if all(delta <= 0 for delta in deltas[i:])),
            None,
        )
        job = groups["retained_factored"][stable]["job"] if stable is not None else None
        break_even.append(
            {
                "seed": seed,
                "first_job_after_which_cumulative_retained_cpu_stays_below_stateless": stable
                + 1
                if stable is not None
                else None,
                "phase": job["phase"] if job else None,
                "repeat": job["repeat"] if job else None,
            }
        )
    return {
        "schema": "nuria.skill-retention.descriptive-phases.v1",
        "result_sha256": hashlib.sha256(
            (directory / "result.json").read_bytes()
        ).hexdigest(),
        "trace_sha256": result["trace_sha256"],
        "phase_costs": phases,
        "known_verified_program_selections": [
            {
                "branch": branch,
                "phase": phase,
                "attempts": attempts,
                "previously_verified_program_selections": hits[branch, phase],
            }
            for (branch, phase), attempts in sorted(seen.items())
        ],
        "cumulative_cpu_break_even": break_even,
        "failed_scored_files": sum(not r["score"]["passed"] for r in rows),
        "repeated_cegis_files": sum(len(r["attempts"]) > 1 for r in rows),
        "scope": "Descriptive postprocessing only. Phase CPU excludes one-time initialization, which primary totals and cumulative break-even include. Rows within seed correlated across formats; no row-level inferential test is performed.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Preserve earlier descriptive evidence")
    report = summarize(args.directory)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.out),
                "failed_scored_files": report["failed_scored_files"],
                "repeated_cegis_files": report["repeated_cegis_files"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
