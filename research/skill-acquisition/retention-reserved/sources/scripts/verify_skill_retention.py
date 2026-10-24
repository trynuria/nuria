"""Independent exact-output, coverage and paired accounting rescore for retention.

No learner or evaluator imports. The separate AST interpreter reconstructs source
values and actual program results. CPU durations are recorded measurements, not
independently witnessed or cryptographically attested resource consumption.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

from scripts.verify_skill_expression_results import (
    generated_input,
    outcome,
    source_target,
)

ROOT = Path(__file__).resolve().parents[1]


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def interval(values):
    n = len(values)
    average = mean(values)
    if n < 2:
        return {"mean": average, "lower95": None, "upper95": None, "seeds": n}
    t = {3: 4.302652729911275, 12: 2.200985160091639}[n]
    radius = t * stdev(values) / math.sqrt(n)
    return {
        "mean": average,
        "lower95": average - radius,
        "upper95": average + radius,
        "seeds": n,
    }


def summarize(trials, branches, repeats):
    groups = {}
    for branch in branches:
        rows = [trial for trial in trials if trial["branch"] == branch]
        groups[branch] = {
            "success": mean(r["success"] for r in rows),
            "phase_success": {
                p: mean(r["phase_success"][p] for r in rows)
                for p in ("acquisition", "unseen", "serve")
            },
            "mean_total_work": mean(r["total_work"] for r in rows),
            "mean_cpu_seconds": mean(r["cpu_seconds"] for r in rows),
            "mean_cpu_per_correct": mean(r["cpu_per_correct"] for r in rows)
            if all(r["cpu_per_correct"] is not None for r in rows)
            else None,
            "mean_work_per_correct": mean(r["work_per_correct"] for r in rows)
            if all(r["work_per_correct"] is not None for r in rows)
            else None,
            "serve_repeat_success": {
                str(i): mean(r["serve_repeat_success"][str(i)] for r in rows)
                for i in range(1, repeats + 1)
            },
        }
    rows = {(r["seed"], r["branch"]): r for r in trials}
    seeds = sorted({r["seed"] for r in trials})
    cpu = []
    work = []
    safe = True
    for seed in seeds:
        a, b = rows[seed, "retained_factored"], rows[seed, "factored"]
        safe &= all(
            a["phase_success"][p] >= b["phase_success"][p]
            for p in ("acquisition", "unseen", "serve")
        )
        if a["cpu_per_correct"] is None or b["cpu_per_correct"] is None:
            cpu.append(1e6)
            work.append(1e6)
        else:
            cpu.append(a["cpu_per_correct"] / max(1e-12, b["cpu_per_correct"]))
            work.append(a["work_per_correct"] / max(1, b["work_per_correct"]))
    ci_cpu, ci_work = interval(cpu), interval(work)
    return {
        "branches": groups,
        "retained_to_factored_cpu_per_correct": ci_cpu,
        "retained_to_factored_work_per_correct": ci_work,
        "gates": {
            "correctness_noninferiority": bool(safe),
            "practical_retention_benefit": bool(
                safe
                and ci_cpu["upper95"] is not None
                and ci_cpu["upper95"] < 0.9
                and ci_work["mean"] <= 0.9
            ),
            "neural_specificity": "not_tested",
        },
    }


def verify(directory, *, check_sources=True):
    protocol = json.loads((directory / "protocol.json").read_text())
    result = json.loads((directory / "result.json").read_text())
    require(
        digest(protocol) == result["protocol_sha256"], "Protocol commitment changed"
    )
    require(
        result["source_hashes"] == protocol["source_hashes"],
        "Implementation identity changed",
    )
    if check_sources:
        for name, expected in protocol["source_hashes"].items():
            snapshot = directory / "sources" / name
            source = snapshot if snapshot.exists() else ROOT / name
            require(
                hashlib.sha256(source.read_bytes()).hexdigest() == expected,
                "Source bytes changed: " + name,
            )
    raw = (
        ROOT / "research/intervention-memory/data/bike-sharing-original.zip"
    ).read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == protocol["source_archive_sha256"],
        "Dataset archive changed",
    )
    csvraw = zipfile.ZipFile(io.BytesIO(raw)).read("hour.csv")
    require(
        hashlib.sha256(csvraw).hexdigest() == protocol["source_csv_sha256"],
        "Dataset CSV changed",
    )
    source = list(csv.DictReader(io.StringIO(csvraw.decode())))
    rows = {int(row["instant"]): row for row in source}
    buckets = {}
    for seed in result["seeds"]:
        for year in ("0", "1"):
            yearrows = [row for row in source if row["yr"] == year]
            pool = (
                [row for i, row in enumerate(yearrows[:1728]) if i % 3 == seed - 9101]
                if seed < 9200
                else [
                    row
                    for i, row in enumerate(yearrows[1728:])
                    if i % 12 == seed - 9201
                ]
            )
            buckets[seed, year] = {int(row["instant"]) for row in pool}
    require(
        result["seeds"] in (protocol["development_seeds"], protocol["reserved_seeds"]),
        "Unregistered seeds",
    )
    raw = (directory / "traces.jsonl.gz").read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == result["trace_sha256"],
        "Trace identity changed",
    )
    records = [json.loads(line) for line in gzip.decompress(raw).decode().splitlines()]
    branches = protocol["branches"]
    seeds = result["seeds"]
    repeats = protocol["serve_repeats_per_format"]
    count = (
        protocol["acquisition_jobs"] + protocol["unseen_jobs"] + protocol["serve_jobs"]
    )
    require(
        len(records) == len(seeds) * len(branches) * count,
        "Missing or extra job records",
    )
    jobs = {}
    seen = set()
    trials = defaultdict(list)
    for record in records:
        seed, branch, job = record["seed"], record["branch"], record["job"]
        require(seed in seeds and branch in branches, "Unexpected trial")
        phase = job["phase"]
        require(phase in ("acquisition", "unseen", "serve"), "Unexpected phase")
        if phase in ("acquisition", "unseen"):
            require(
                job["format_index"] == job["step"], "First-file format sequence changed"
            )
        recipes = (
            protocol["acquisition_recipes"]
            if phase == "acquisition"
            else protocol["transfer_recipes"]
        )
        require(
            type(job["format_index"]) is int
            and 0 <= job["format_index"] < len(recipes),
            "Format index outside contract",
        )
        require(job["recipe"] == recipes[job["format_index"]], "Format recipe changed")
        require(
            job["repeat"] == 0 if phase != "serve" else 1 <= job["repeat"] <= repeats,
            "Repeat schedule changed",
        )
        key = seed, branch, phase, job["step"]
        require(key not in seen, "Duplicate job")
        seen.add(key)
        common = seed, phase, job["step"]
        require(
            common not in jobs or jobs[common] == job,
            "Branches received different files",
        )
        jobs[common] = job
        require(
            job["id"] == digest({k: v for k, v in job.items() if k != "id"}),
            "Job identity changed",
        )
        require(
            len(job["inputs"]) == len(job["outputs"]) == len(job["row_ids"]) == 24,
            "File size changed",
        )
        year = "0" if phase == "acquisition" else "1"
        for row_id, inp, expected in zip(
            job["row_ids"], job["inputs"], job["outputs"], strict=True
        ):
            require(
                row_id in buckets[seed, year], "Row escaped registered seed/year bucket"
            )
            row = rows[row_id]
            require(
                source_target(row, job["recipe"]) == expected
                and generated_input(row, job["recipe"]) == inp,
                "Source-derived input/output changed",
            )
        require(
            len(set(job["outputs"][:4])) == 4, "Visible target identifiability changed"
        )
        visible_in = list(job["inputs"][:4])
        visible_out = list(job["outputs"][:4])
        search = buyer = 0
        program = None
        for index, attempt in enumerate(record["attempts"]):
            require(
                index < protocol["max_cegis_rounds"] and attempt["round"] == index,
                "CEGIS schedule changed",
            )
            require(
                attempt["inputs"] == visible_in and attempt["outputs"] == visible_out,
                "Undeclared information reached search",
            )
            proposal = attempt["proposal"]
            program = proposal["program"]
            meter = proposal["work"]
            require(
                proposal["mode"] == protocol["modes"][branch], "Solver branch changed"
            )
            require(
                all(
                    type(meter[k]) is int and meter[k] >= 0
                    for k in (
                        "total",
                        "primitive_attempts",
                        "candidates",
                        "byte_blocks",
                        "errors",
                        "semantic_duplicates",
                    )
                ),
                "Malformed resource meter",
            )
            require(
                meter["total"]
                == meter["primitive_attempts"]
                + meter["candidates"]
                + meter["byte_blocks"],
                "Resource meter does not sum",
            )
            search += meter["total"]
            require(
                search <= protocol["shared_search_budget"],
                "Shared search budget exceeded",
            )
            retain = branch != "factored" and (
                branch != "frozen_before_unseen" or phase == "acquisition"
            )
            require(attempt["retain"] == retain, "Retention control leaked")
            require(
                proposal["solved_training"] == (program is not None),
                "Solved flag changed",
            )
            if program is None:
                require(
                    attempt["validation"] is None and attempt["receipt"] is None,
                    "Unsolved program received outcome",
                )
                continue
            require(
                proposal["program_sha256"] == digest(program),
                "Selected AST identity changed",
            )
            require(
                outcome(
                    program, visible_in, visible_out, protocol["expression_config"]
                )["passed"],
                "AST does not fit visible examples",
            )
            check = outcome(
                program,
                job["inputs"][4:12],
                job["outputs"][4:12],
                protocol["expression_config"],
            )
            require(check == attempt["validation"], "Actual buyer output changed")
            buyer += check["tool_units"]
            receipt = attempt["receipt"]
            require(
                receipt["source_id"] == proposal["source_id"]
                and receipt["proposal_id"] == proposal["proposal_id"]
                and receipt["program_sha256"] == proposal["program_sha256"],
                "Buyer receipt origin changed",
            )
            require(
                receipt["accepted"] == check["passed"]
                and receipt["correct_rows"] == check["correct"]
                and receipt["total_rows"] == 8
                and receipt["work"] == check["tool_units"],
                "Buyer receipt verdict/accounting changed",
            )
            require(
                receipt["evaluation_id"] == digest({"job": job["id"], "round": index}),
                "Buyer evaluation identity changed",
            )
            require(
                receipt["evidence_sha256"]
                == digest(
                    {
                        "inputs": job["inputs"][4:12],
                        "outputs": job["outputs"][4:12],
                        "observed": check["outputs"],
                    }
                ),
                "Buyer output evidence changed",
            )
            if not check["passed"]:
                offset = check["first_counterexample_index"]
                visible_in.append(job["inputs"][4 + offset])
                visible_out.append(job["outputs"][4 + offset])
            elif index + 1 < len(record["attempts"]):
                raise ValueError("Extra search after successful validation")
        require(record["program"] == program, "Final AST differs from selected program")
        score = outcome(
            program,
            job["inputs"][12:],
            job["outputs"][12:],
            protocol["expression_config"],
        )
        require(score == record["score"], "Scored executable outputs changed")
        require(
            record["search_work"] == search
            and record["buyer_work"] == buyer
            and record["score_work"] == score["tool_units"]
            and record["total_work"] == search + buyer + score["tool_units"],
            "Execution accounting changed",
        )
        require(
            all(
                type(record[k]) in (int, float)
                and math.isfinite(record[k])
                and record[k] >= 0
                for k in ("cpu_seconds", "wall_seconds")
            ),
            "Invalid measured duration",
        )
        trials[seed, branch].append(record)
    source_owner = {}
    per_format = defaultdict(set)
    acq_owner = defaultdict(set)
    for (seed, phase, step), job in jobs.items():
        for row in job["row_ids"]:
            require(
                row not in source_owner or source_owner[row] == seed,
                "Source row reused across seeds",
            )
            source_owner[row] = seed
            if phase == "acquisition":
                require(row not in acq_owner[seed], "Acquisition row reused")
                acq_owner[seed].add(row)
            else:
                group = seed, job["format_index"]
                require(
                    row not in per_format[group],
                    "Source row repeated within one format",
                )
                per_format[group].add(row)
    for seed in seeds:
        require(len(acq_owner[seed]) == 24 * 24, "Acquisition coverage incomplete")
        for index in range(16):
            require(
                len(per_format[seed, index]) == (repeats + 1) * 24,
                "Recurring-format rows incomplete",
            )
        for repeat in range(1, repeats + 1):
            formats = sorted(
                job["format_index"]
                for (owner, phase, step), job in jobs.items()
                if owner == seed and phase == "serve" and job["repeat"] == repeat
            )
            require(
                formats == list(range(16)),
                "Recurring-format repeat coverage incomplete",
            )
    require(
        len(result["trials"]) == len(seeds) * len(branches), "Trial coverage changed"
    )
    rebuilt = []
    for retained in result["trials"]:
        seed, branch = retained["seed"], retained["branch"]
        group = trials[seed, branch]
        require(len(group) == count, "Incomplete phase jobs")
        for phase, n in (("acquisition", 24), ("unseen", 16), ("serve", 16 * repeats)):
            subset = [r for r in group if r["job"]["phase"] == phase]
            require(
                sorted(r["job"]["step"] for r in subset) == list(range(n)),
                "Phase sequence incomplete",
            )
        correct = sum(r["score"]["passed"] for r in group)
        work = sum(r["total_work"] for r in group)
        initialization = retained["initialization_cpu_seconds"]
        require(
            type(initialization) in (int, float)
            and math.isfinite(initialization)
            and initialization >= 0,
            "Invalid initialization accounting",
        )
        cpu = sum(r["cpu_seconds"] for r in group) + initialization
        trial = {
            "seed": seed,
            "branch": branch,
            "jobs": len(group),
            "correct": correct,
            "success": correct / len(group),
            "phase_success": {
                p: mean(r["score"]["passed"] for r in group if r["job"]["phase"] == p)
                for p in ("acquisition", "unseen", "serve")
            },
            "serve_repeat_success": {
                str(i): mean(
                    r["score"]["passed"]
                    for r in group
                    if r["job"]["phase"] == "serve" and r["job"]["repeat"] == i
                )
                for i in range(1, repeats + 1)
            },
            "total_work": work,
            "initialization_cpu_seconds": initialization,
            "cpu_seconds": cpu,
            "cpu_per_correct": cpu / correct if correct else None,
            "work_per_correct": work / correct if correct else None,
        }
        require({k: retained[k] for k in trial} == trial, "Trial accounting changed")
        rebuilt.append(trial)
    check = summarize(rebuilt, branches, repeats)
    require(
        {k: result["summary"][k] for k in check} == check,
        "Summary or registered gate changed",
    )
    return {
        "schema": "nuria.skill-retention.verification.v1",
        "passed": True,
        "jobs": len(records),
        "unique_files": len(jobs),
        "unique_source_rows": len(source_owner),
        "seeds": len(seeds),
        "gates": check["gates"],
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Independent exact-output/receipt/split/accounting/gate rescore. No full synthesis replay or CPU attestation. Rows correlated within seed across different formats are disclosed and never treated as independent samples.",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    result = verify(args.directory)
    if args.out:
        if args.out.exists():
            raise ValueError("Preserve previous verification")
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
