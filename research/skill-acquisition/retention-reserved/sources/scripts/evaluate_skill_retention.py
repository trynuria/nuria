"""Compare one strong solver with and without buyer-verified program retention.

Each branch uses the same expression language, observations, factored synthesis
and actual output checks. Six future files per unseen format are fixed before
development. This is a conventional verified memoization study, not new AI.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import random
import time
from pathlib import Path
from statistics import mean, stdev

from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG
from cognition.skills import SkillLibrary
from scripts import evaluate_skill_expressions as tasks

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nuria.verified-skill-retention.v1"
BRANCHES = ("factored", "retained_factored", "frozen_before_unseen")
REPEATS = 6
MODES = {
    "factored": "factored",
    "retained_factored": "retained_factored",
    "frozen_before_unseen": "retained_factored",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_hashes():
    filenames = (
        "cognition/skills.py",
        "cognition/skill_expressions.py",
        "cognition/skill_factored.py",
        "scripts/evaluate_skill_expressions.py",
        "scripts/verify_skill_expression_results.py",
        "scripts/evaluate_skill_retention.py",
        "scripts/verify_skill_retention.py",
    )
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in filenames
    }


def config():
    expression = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
    expression["max_macros"] = 0
    return {
        "schema": SCHEMA,
        "status": "development-not-preregistered",
        "development_seeds": [9101, 9102, 9103],
        "reserved_seeds": list(range(9201, 9213)),
        "acquisition_jobs": 24,
        "unseen_jobs": 16,
        "serve_repeats_per_format": REPEATS,
        "serve_jobs": 16 * REPEATS,
        "examples": 4,
        "validation_rows": 8,
        "scored_rows": 12,
        "max_cegis_rounds": 2,
        "shared_search_budget": 100000,
        "expression_config": expression,
        "branches": list(BRANCHES),
        "modes": MODES,
        "acquisition_recipes": copy.deepcopy(tasks.TRAIN_RECIPES),
        "transfer_recipes": copy.deepcopy(tasks.TRANSFER_RECIPES),
        "source_archive_sha256": tasks.ARCHIVE_SHA,
        "source_csv_sha256": tasks.CSV_SHA,
        "source_hashes": source_hashes(),
        "split": "2011 acquisition/2012 unseen+serve. First1728 rows/year form3 disjoint development buckets; remaining rows/year form12 disjoint reserved buckets.",
        "correlation": "Within a seed, a format samples7*24 distinct2012 rows for first file and6servefiles. Rows may repeat across DIFFERENT formats. Seeds have disjoint source-row buckets. Paired intervals cluster entire seeds, never rows.",
        "information": "All branches receive same4 visibleIO examples perfile. Validation/scored rows and recipe/format/row metadata never reach solve. Serve phase is frozen input distribution with new rows, not an external customer workload.",
        "execution_order": "Latin-square rotation of branch order across seed triples; every branch appears once in each position per3seeds.",
        "baseline": "Both branches use the identical strong factored solver. Retention first tries previously buyer-verified wholeASTs. All receipts consumed; stateless branch retains no program. No fragment macros in any branch.",
        "frozen_control": "Learns acquisition24files, then consumes genuine outcomes without retaining any new AST during unseen/serve.",
        "gates": {
            "minimum_cpu_per_correct_reduction": 0.10,
            "minimum_work_per_correct_reduction": 0.10,
            "correctness": "Retained exact task success must be no worse than factored in every seed and in each phase.",
            "practical": "Across all136jobs including acquisition, paired seed-level95% Student-t upperCI of retained/factored CPU-per-correct ratio <0.90; chargedwork-per-correct meanratio<=0.90.",
            "scope": "Conventional program retention on recurring generated formats over observed data; no neural, consciousness or algorithmic-novelty claim.",
        },
    }


def year_bucket(rows, seed):
    year = [row for row in rows if row["yr"] == "1"]
    if seed in (9101, 9102, 9103):
        return [row for i, row in enumerate(year[:1728]) if i % 3 == seed - 9101]
    if 9201 <= seed <= 9212:
        return [row for i, row in enumerate(year[1728:]) if i % 12 == seed - 9201]
    raise ValueError("Unregistered row partition")


def make_jobs(rows, seed):
    jobs = []
    for entry in tasks.make_tasks(rows, seed, "acquisition", 24):
        job = {key: copy.deepcopy(value) for key, value in entry.items() if key != "id"}
        job.update(format_index=entry["step"], repeat=0)
        job["id"] = digest(job)
        jobs.append(job)
    pool = year_bucket(rows, seed)
    by_format = {}
    for index, recipe in enumerate(tasks.TRANSFER_RECIPES):
        chosen = random.Random(f"{SCHEMA}:rows:{seed}:{index}").sample(
            pool, (REPEATS + 1) * 24
        )
        for repeat in range(REPEATS + 1):
            selected = chosen[repeat * 24 : (repeat + 1) * 24]
            distinct = {}
            for row in selected:
                distinct.setdefault(tasks.target_for(row, recipe), row)
            if len(distinct) < 4:
                raise ValueError("Registered file lacks4 distinct example outputs")
            first = list(distinct.values())[:4]
            selected = first + [row for row in selected if row not in first]
            job = {
                "phase": "unseen" if repeat == 0 else "serve",
                "step": index if repeat == 0 else None,
                "format_index": index,
                "repeat": repeat,
                "recipe": copy.deepcopy(recipe),
                "row_ids": [int(row["instant"]) for row in selected],
                "inputs": [
                    json.dumps(
                        [
                            tasks.encode_field(tasks.numeric(row[field]), encoding)
                            for field, encoding in zip(
                                tasks.FIELDS, recipe["encodings"], strict=True
                            )
                        ],
                        separators=(",", ":"),
                    )
                    for row in selected
                ],
                "outputs": [tasks.target_for(row, recipe) for row in selected],
            }
            by_format[index, repeat] = job
    for index in range(16):
        job = by_format[index, 0]
        job["id"] = digest(job)
        jobs.append(job)
    order = random.Random(f"{SCHEMA}:order:{seed}")
    for repeat in range(1, REPEATS + 1):
        indices = list(range(16))
        order.shuffle(indices)
        for index in indices:
            job = by_format[index, repeat]
            job["step"] = len(jobs) - 40
            job["id"] = digest(job)
            jobs.append(job)
    return jobs


def run_job(library, job, branch, protocol):
    start, cpu = time.monotonic(), time.process_time()
    inputs = list(job["inputs"][:4])
    outputs = list(job["outputs"][:4])
    retain = branch != "factored" and (
        branch != "frozen_before_unseen" or job["phase"] == "acquisition"
    )
    remaining = protocol["shared_search_budget"]
    attempts = []
    program = None
    for index in range(protocol["max_cegis_rounds"]):
        if remaining < 1:
            break
        proposal = library.solve(
            inputs,
            outputs,
            mode=MODES[branch],
            budget=remaining,
            features=tasks.features(inputs),
        )
        remaining -= proposal["work"]["total"]
        program = proposal["program"]
        attempt = {
            "round": index,
            "inputs": list(inputs),
            "outputs": list(outputs),
            "proposal": proposal,
            "retain": retain,
            "validation": None,
            "receipt": None,
        }
        attempts.append(attempt)
        if program is None:
            break
        checked = tasks.check_program(
            program, job["inputs"][4:12], job["outputs"][4:12]
        )
        attempt["validation"] = checked
        receipt = {
            "source_id": library.source_id,
            "proposal_id": proposal["proposal_id"],
            "program_sha256": proposal["program_sha256"],
            "evaluation_id": digest({"job": job["id"], "round": index}),
            "accepted": checked["passed"],
            "correct_rows": checked["correct"],
            "total_rows": 8,
            "evidence_sha256": digest(
                {
                    "inputs": job["inputs"][4:12],
                    "outputs": job["outputs"][4:12],
                    "observed": checked["outputs"],
                }
            ),
            "work": checked["tool_units"],
        }
        library.admit_verified(proposal["proposal_id"], receipt, retain=retain)
        attempt["receipt"] = receipt
        if checked["passed"]:
            break
        # Only buyer validation counterexamples enter a new search, never scored rows.
        offset = checked["first_counterexample_index"]
        inputs.append(job["inputs"][4 + offset])
        outputs.append(job["outputs"][4 + offset])
    score = (
        tasks.check_program(program, job["inputs"][12:], job["outputs"][12:])
        if program
        else {
            "passed": False,
            "correct": 0,
            "rows": 12,
            "outputs": [None] * 12,
            "tool_units": 0,
            "first_counterexample_index": 0,
            "actual_sha256": digest([None] * 12),
        }
    )
    search = sum(a["proposal"]["work"]["total"] for a in attempts)
    buyer = sum((a["validation"] or {}).get("tool_units", 0) for a in attempts)
    state = library.dump()
    state_hash = digest(state)
    return {
        "job": job,
        "branch": branch,
        "attempts": attempts,
        "program": program,
        "score": score,
        "search_work": search,
        "buyer_work": buyer,
        "score_work": score["tool_units"],
        "total_work": search + buyer + score["tool_units"],
        "program_count": len(state["programs"]),
        "state_sha256": state_hash,
        "cpu_seconds": time.process_time() - cpu,
        "wall_seconds": time.monotonic() - start,
    }


def ci(values):
    average = mean(values)
    n = len(values)
    if n < 2:
        return {"mean": average, "lower95": None, "upper95": None, "seeds": n}
    critical = {3: 4.302652729911275, 12: 2.200985160091639}[n]
    margin = critical * stdev(values) / math.sqrt(n)
    return {
        "mean": average,
        "lower95": average - margin,
        "upper95": average + margin,
        "seeds": n,
    }


def summary(trials):
    branches = {}
    for branch in BRANCHES:
        rows = [trial for trial in trials if trial["branch"] == branch]
        branches[branch] = {
            "success": mean(r["success"] for r in rows),
            "phase_success": {
                phase: mean(r["phase_success"][phase] for r in rows)
                for phase in ("acquisition", "unseen", "serve")
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
                for i in range(1, REPEATS + 1)
            },
        }
    by = {(trial["seed"], trial["branch"]): trial for trial in trials}
    seeds = sorted({trial["seed"] for trial in trials})
    ratios = []
    work_ratios = []
    noninferior = True
    for seed in seeds:
        left, right = by[seed, "retained_factored"], by[seed, "factored"]
        noninferior &= all(
            left["phase_success"][p] >= right["phase_success"][p]
            for p in ("acquisition", "unseen", "serve")
        )
        if left["cpu_per_correct"] is None or right["cpu_per_correct"] is None:
            ratios.append(1e6)
            work_ratios.append(1e6)
        else:
            ratios.append(
                left["cpu_per_correct"] / max(1e-12, right["cpu_per_correct"])
            )
            work_ratios.append(
                left["work_per_correct"] / max(1, right["work_per_correct"])
            )
    cpu = ci(ratios)
    work = ci(work_ratios)
    return {
        "branches": branches,
        "retained_to_factored_cpu_per_correct": cpu,
        "retained_to_factored_work_per_correct": work,
        "gates": {
            "correctness_noninferiority": bool(noninferior),
            "practical_retention_benefit": bool(
                noninferior
                and cpu["upper95"] is not None
                and cpu["upper95"] < 0.9
                and work["mean"] <= 0.9
            ),
            "neural_specificity": "not_tested",
        },
        "interpretation": "A paired recurring-format program-cache comparison, including acquisition and all validation/search/checkpoint-preparation CPU. Not a novel AI algorithm or independent hardware benchmark.",
    }


def evaluate(protocol, seeds, destination):
    destination.mkdir(parents=True, exist_ok=False)
    rows = tasks.load_rows()
    trials = []
    start_hashes = source_hashes()
    (destination / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    (destination / "execution-start.json").write_text(
        json.dumps(
            {"source_hashes": start_hashes, "seeds": seeds, "valid_result": False},
            indent=2,
        )
        + "\n"
    )
    with gzip.open(
        destination / "traces.jsonl.gz", "wt", encoding="utf-8", newline="\n"
    ) as stream:
        for seed in seeds:
            jobs = make_jobs(rows, seed)
            shift = (seed - 9101 if seed < 9200 else seed - 9201) % len(BRANCHES)
            execution_order = BRANCHES[shift:] + BRANCHES[:shift]
            for branch in execution_order:
                initialization_cpu = time.process_time()
                library = SkillLibrary(
                    f"retention:{seed}:{branch}", config=protocol["expression_config"]
                )
                initialization_cpu = time.process_time() - initialization_cpu
                records = []
                for job in jobs:
                    record = run_job(library, job, branch, protocol)
                    record["seed"] = seed
                    stream.write(canonical(record) + "\n")
                    records.append(record)
                correct = sum(r["score"]["passed"] for r in records)
                work = sum(r["total_work"] for r in records)
                cpu = sum(r["cpu_seconds"] for r in records) + initialization_cpu
                trials.append(
                    {
                        "seed": seed,
                        "branch": branch,
                        "jobs": len(records),
                        "correct": correct,
                        "success": correct / len(records),
                        "phase_success": {
                            phase: mean(
                                r["score"]["passed"]
                                for r in records
                                if r["job"]["phase"] == phase
                            )
                            for phase in ("acquisition", "unseen", "serve")
                        },
                        "serve_repeat_success": {
                            str(i): mean(
                                r["score"]["passed"]
                                for r in records
                                if r["job"]["phase"] == "serve"
                                and r["job"]["repeat"] == i
                            )
                            for i in range(1, REPEATS + 1)
                        },
                        "total_work": work,
                        "initialization_cpu_seconds": initialization_cpu,
                        "cpu_seconds": cpu,
                        "cpu_per_correct": cpu / correct if correct else None,
                        "work_per_correct": work / correct if correct else None,
                        "final_library": library.dump(),
                    }
                )
    if source_hashes() != start_hashes or start_hashes != protocol["source_hashes"]:
        (destination / "execution-status.json").write_text(
            json.dumps(
                {
                    "valid_result": False,
                    "reason": "source_identity_changed",
                    "initial_source_hashes": start_hashes,
                    "final_source_hashes": source_hashes(),
                },
                indent=2,
            )
            + "\n"
        )
        raise ValueError(
            "Source changed during the experiment; partial evidence remains, no valid result issued"
        )
    for name in start_hashes:
        path = destination / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    result = {
        "schema": SCHEMA + ".results",
        "protocol_sha256": digest(protocol),
        "seeds": seeds,
        "trace_sha256": hashlib.sha256(
            (destination / "traces.jsonl.gz").read_bytes()
        ).hexdigest(),
        "source_hashes": start_hashes,
        "trials": trials,
        "summary": summary(trials),
    }
    (destination / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    (destination / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--phase",
        choices=("development", "freeze", "reserved", "manifest"),
        required=True,
    )
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--protocol", type=Path)
    p.add_argument("--expected-protocol-sha256")
    args = p.parse_args()
    if args.out.exists():
        raise ValueError("Preserve prior output; choose new path")
    protocol = config()
    if args.phase == "manifest":
        args.out.write_text(
            json.dumps(
                {
                    "schema": SCHEMA + ".tasks",
                    "source": {
                        "archive_sha256": tasks.ARCHIVE_SHA,
                        "csv_sha256": tasks.CSV_SHA,
                        "doi": "10.24432/C5W894",
                    },
                    "tasks": make_jobs(tasks.load_rows(), 9101),
                },
                indent=2,
            )
            + "\n"
        )
        return
    if args.phase == "freeze":
        protocol["status"] = "frozen-before-reserved-execution"
        args.out.write_text(json.dumps(protocol, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "protocol": str(args.out),
                    "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                }
            )
        )
        return
    if args.phase == "reserved":
        if args.protocol is None or args.expected_protocol_sha256 is None:
            raise ValueError("Exact preregistered protocol required")
        raw = args.protocol.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.expected_protocol_sha256:
            raise ValueError("Protocol identity changed")
        protocol = json.loads(raw)
        if (
            protocol["status"] != "frozen-before-reserved-execution"
            or protocol["source_hashes"] != source_hashes()
        ):
            raise ValueError("Frozen source contract changed")
        seeds = protocol["reserved_seeds"]
    else:
        seeds = protocol["development_seeds"]
    result = evaluate(protocol, seeds, args.out)
    print(
        json.dumps(
            {"result": str(args.out / "result.json"), "summary": result["summary"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
