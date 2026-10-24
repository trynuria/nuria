"""Reanalyze the already observed PROSE split without changing its examples.

This is explicitly seen-set reanalysis, not a second reserved qualification.
Two-example old/composed learners start from the exact previously published
seven-program checkpoint at both 10,000 and 50,000 search units. No benchmark
body is fetched, redistributed, moved to training or passed through a new split.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from cognition.active_skills import probe_cost
from cognition.text_composition import CompositionalTextLibrary
from cognition.text_skills import TextSkillLibrary
from scripts.evaluate_active_skills import external_jobs
from scripts.verify_active_skill_results import digest, outcome, serial

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nuria.composed-external-seen-reanalysis.v1"
METHODS = ("old_fixed2", "composed_fixed2")
BUDGETS = (10000, 50000)
INITIAL_ARTIFACT = "9bc790e1c8d0b5d2f33b7007006a79d12e0ce8e59c4eb3c6d0629b7a53abc1c3"
INITIAL_CANONICAL = "1a24d8371ef86121d37d2414c3f4cd81bab50372553697e6bdf299b3c156afe3"
SOURCES = (
    "scripts/reanalyze_composed_external.py",
    "tests/test_composed_external_reanalysis.py",
    "scripts/evaluate_active_skills.py",
    "scripts/verify_active_skill_results.py",
    "cognition/active_skills.py",
    "cognition/text_composition.py",
    "cognition/text_skills.py",
    "cognition/skills.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def units(value):
    return 1 + (len(serial(value).encode()) + 31) // 32


def source_hashes():
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES
    }


def run_job(initial, job, method, budget, config, source_id, namespace):
    started = time.process_time()
    require(method in METHODS and budget in BUDGETS, "Unregistered reanalysis branch")
    factory = TextSkillLibrary if method == "old_fixed2" else CompositionalTextLibrary
    library = factory(source_id, config, state=initial)
    before = library.dump()
    indices, seen = [], set()
    selected = {"program": None, "program_sha256": None, "proposal_id": None}
    synthesis_work = 0
    if job["unsupported"] is None:
        for index, value in enumerate(job["diagnostic_inputs"]):
            if value not in seen:
                indices.append(index)
                seen.add(value)
        require(1 <= len(indices) <= 2, "Original two-row diagnostic split changed")
        proposal = library.solve(
            [job["diagnostic_inputs"][index] for index in indices],
            [job["diagnostic_outputs"][index] for index in indices],
            mode="retained_factored",
            budget=budget,
        )
        selected = {key: proposal[key] for key in selected}
        synthesis_work = proposal["work"]["total"]
    committed = digest(
        {
            "job_id": job["id"],
            "method": method,
            "budget": budget,
            **selected,
            "probe_indices": indices,
        }
    )
    checked = outcome(
        selected["program"], job["score_inputs"], job["score_outputs"], config
    )
    admission = None
    if selected["proposal_id"] is not None:
        admission = library.admit_verified(
            selected["proposal_id"],
            {
                "source_id": source_id,
                "proposal_id": selected["proposal_id"],
                "program_sha256": selected["program_sha256"],
                "evaluation_id": digest(
                    [namespace, method, budget, job["id"], committed]
                ),
                "accepted": checked["passed"],
                "correct_rows": checked["correct"],
                "total_rows": checked["rows"],
                "evidence_sha256": digest(checked),
                "work": checked["total_work"],
            },
        )
    final = library.dump()
    require(not final["pending"], "Reanalysis leaves an unresolved program")
    probe_work = sum(
        probe_cost(job["diagnostic_inputs"][index], job["diagnostic_outputs"][index])
        for index in indices
    )
    administration = units(before) + units(final)
    record = {
        "method": method,
        "budget": budget,
        "job_id": job["id"],
        "family": job["family"],
        "unsupported": job["unsupported"],
        **selected,
        "probe_indices": indices,
        "committed_proposal_sha256": committed,
        "checked": checked,
        "admission": admission,
        "library_before": before,
        "library_after": final,
        "probe_work": probe_work,
        "synthesis_work": synthesis_work,
        "administration_work": administration,
        "total_work": probe_work
        + synthesis_work
        + checked["total_work"]
        + administration,
    }
    serial(record)
    record["cpu_seconds"] = time.process_time() - started
    return record, final


def summarize(records, jobs):
    by_job = {job["id"]: job for job in jobs}
    totals = {}
    for budget in BUDGETS:
        totals[str(budget)] = {}
        for method in METHODS:
            rows = [
                row
                for row in records
                if row["method"] == method and row["budget"] == budget
            ]
            totals[str(budget)][method] = {
                "jobs": len(rows),
                "exact_successes": sum(row["checked"]["passed"] for row in rows),
                "proposed": sum(row["program"] is not None for row in rows),
                "wrong_proposals": sum(
                    row["program"] is not None and not row["checked"]["passed"]
                    for row in rows
                ),
                "unsupported": sum(row["unsupported"] is not None for row in rows),
                "labels": sum(len(row["probe_indices"]) for row in rows),
                "score_rows": sum(row["checked"]["rows"] for row in rows),
                "correct_rows": sum(row["checked"]["correct"] for row in rows),
                "novel_score_rows": sum(
                    value not in set(by_job[row["job_id"]]["diagnostic_inputs"])
                    for row in rows
                    for value in by_job[row["job_id"]]["score_inputs"]
                ),
                "correct_novel_rows": sum(
                    actual == target
                    for row in rows
                    for value, actual, target in zip(
                        by_job[row["job_id"]]["score_inputs"],
                        row["checked"]["outputs"],
                        by_job[row["job_id"]]["score_outputs"],
                        strict=True,
                    )
                    if value not in set(by_job[row["job_id"]]["diagnostic_inputs"])
                ),
                "total_work": sum(row["total_work"] for row in rows),
                "cpu_seconds": sum(row["cpu_seconds"] for row in rows),
            }
    return totals


def verify_records(records, jobs, config, initial):
    """Use the separate data-only interpreter, never replay or retune search."""
    expected = {
        (budget, method, job["id"])
        for budget in BUDGETS
        for method in METHODS
        for job in jobs
    }
    by_job = {job["id"]: job for job in jobs}
    require(len(by_job) == len(jobs), "Duplicate original source task")
    states, seen = {}, set()
    for row in records:
        key = row["budget"], row["method"], row["job_id"]
        require(
            key in expected and key not in seen,
            "Duplicate or undeclared reanalysis result",
        )
        seen.add(key)
        budget, method, identity = key
        branch = budget, method
        job = by_job[identity]
        before, after = row["library_before"], row["library_after"]
        require(
            before == states.get(branch, initial),
            "Learning history differs between jobs",
        )
        require(
            row["unsupported"] == job["unsupported"],
            "Unsupported task was silently changed",
        )
        indices, observed = [], set()
        if job["unsupported"] is None:
            for index, value in enumerate(job["diagnostic_inputs"]):
                if value not in observed:
                    indices.append(index)
                    observed.add(value)
        require(
            indices == row["probe_indices"] and len(indices) <= 2,
            "Original diagnostic evidence changed",
        )
        require(
            row["program_sha256"]
            == (None if row["program"] is None else digest(row["program"])),
            "Program commitment changed",
        )
        require(
            row["committed_proposal_sha256"]
            == digest(
                {
                    "job_id": identity,
                    "method": method,
                    "budget": budget,
                    **{
                        key: row[key]
                        for key in ("program", "program_sha256", "proposal_id")
                    },
                    "probe_indices": indices,
                }
            ),
            "Committed proposal changed",
        )
        checked = outcome(
            row["program"], job["score_inputs"], job["score_outputs"], config
        )
        require(
            checked == row["checked"],
            "Independent external outputs or buyer work changed",
        )
        require(not after["pending"], "Final provisional program remains unresolved")
        expected_programs = set(before["programs"])
        receipts = copy.deepcopy(before["receipts"])
        admission = row["admission"]
        if row["proposal_id"] is not None:
            require(
                admission is not None
                and admission == after["receipts"].get(row["proposal_id"]),
                "Buyer receipt is missing",
            )
            require(
                admission["accepted"] == checked["passed"]
                and admission["correct_rows"] == checked["correct"]
                and admission["total_rows"] == checked["rows"]
                and admission["evidence_sha256"] == digest(checked)
                and admission["work"] == checked["total_work"]
                and admission["program_sha256"] == row["program_sha256"],
                "Buyer receipt misreports independent acceptance",
            )
            receipts[row["proposal_id"]] = admission
            if checked["passed"] and len(expected_programs) < config["max_programs"]:
                expected_programs.add(row["program_sha256"])
        else:
            require(admission is None, "Unproposed artifact was admitted")
        require(
            set(after["programs"]) == expected_programs
            and after["receipts"] == receipts,
            "Membership or receipts changed without checked acceptance",
        )
        require(
            row["probe_work"]
            == sum(
                probe_cost(
                    job["diagnostic_inputs"][index], job["diagnostic_outputs"][index]
                )
                for index in indices
            ),
            "Diagnostic cost changed",
        )
        require(
            type(row["synthesis_work"]) is int and 0 <= row["synthesis_work"] <= budget,
            "Search cost exceeds its branch allowance",
        )
        require(
            after["total_work"]
            == before["total_work"]
            + row["synthesis_work"]
            + (checked["total_work"] if admission else 0),
            "Library omits failed or admitted work",
        )
        require(
            row["administration_work"] == units(before) + units(after),
            "State cost changed",
        )
        require(
            row["total_work"]
            == row["probe_work"]
            + row["synthesis_work"]
            + checked["total_work"]
            + row["administration_work"],
            "Complete job work changed",
        )
        states[branch] = after
    require(seen == expected, "Missing failed, unsupported or successful original task")
    return {
        "verified": True,
        "records": len(records),
        "task_count": len(jobs),
        "limits": "Outputs, source membership, original diagnostic split, admission and cost arithmetic checked with the separate data-only interpreter. Search is not replayed; CPU and instrumentation are not independently physically attested.",
    }


def public_rows(records):
    return [
        {
            "task_id": row["job_id"],
            "family": row["family"],
            "budget": row["budget"],
            "method": row["method"],
            "unsupported": row["unsupported"],
            "program_sha256": row["program_sha256"],
            "proposal_sha256": row["committed_proposal_sha256"],
            "exact": row["checked"]["passed"],
            "correct_rows": row["checked"]["correct"],
            "score_rows": row["checked"]["rows"],
            "labels": len(row["probe_indices"]),
            "total_work": row["total_work"],
            "cpu_seconds": row["cpu_seconds"],
            "programs_before": len(row["library_before"]["programs"]),
            "programs_after": len(row["library_after"]["programs"]),
        }
        for row in records
    ]


def write_new(path, value):
    with path.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(
            json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=ROOT / "research/active-skills/source-inventory.json",
    )
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--initial-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(
        not args.output.resolve().is_relative_to(ROOT),
        "Raw evidence must stay outside public source",
    )
    initial_raw = args.initial_library.read_bytes()
    initial = json.loads(initial_raw)
    require(
        hashlib.sha256(initial_raw).hexdigest() == INITIAL_ARTIFACT
        and digest(initial) == INITIAL_CANONICAL,
        "Previously published seven-program checkpoint differs",
    )
    hashes = source_hashes()
    config, jobs, provenance = external_jobs(
        args.inventory, args.source_directory, "reserved"
    )
    require(len(jobs) == 167, "The already observed original denominator changed")
    source_id = initial["source_id"]
    TextSkillLibrary(source_id, config, state=initial)
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_new(
        args.output / "started.json",
        {
            "schema": SCHEMA,
            "status": "in_progress",
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "source_hashes": hashes,
            "initial_library_sha256": INITIAL_CANONICAL,
            "budgets": BUDGETS,
            "methods": METHODS,
            "task_count": 167,
            "scope": "Previously observed original split, not a fresh reserved qualification",
        },
    )
    records = []
    namespace = digest([SCHEMA, hashes, INITIAL_CANONICAL])
    for budget_index, budget in enumerate(BUDGETS):
        states = {method: copy.deepcopy(initial) for method in METHODS}
        for ordinal, job in enumerate(jobs):
            order = METHODS if (ordinal + budget_index) % 2 == 0 else METHODS[::-1]
            for method in order:
                record, states[method] = run_job(
                    states[method], job, method, budget, config, source_id, namespace
                )
                records.append(record)
    require(hashes == source_hashes(), "Reanalysis source changed during execution")
    verification = verify_records(records, jobs, config, initial)
    summary = {
        "schema": SCHEMA,
        "status": "completed_seen_set_reanalysis",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "source_hashes": hashes,
        "initial_library_sha256": INITIAL_CANONICAL,
        "initial_programs": 7,
        "original_source_commit": provenance["repository_commit"],
        "inventory_sha256": provenance["inventory_sha256"],
        "tasks": 167,
        "original_score_rows": sum(
            max(0, job["original_examples"] - 2) for job in jobs
        ),
        "results": summarize(records, jobs),
        "scope": "Previously observed Microsoft PROSE synthetic tasks, original first two diagnostics and all remaining original scoring rows, same seven-program warm start. Seen-set reanalysis, not fresh qualification or a neural advantage. The two fixed search budgets are separate sensitivity branches; neither branch was chosen after its outcomes.",
        "private_trace_sha256": digest(records),
    }
    write_new(args.output / "jobs.private.json", jobs)
    write_new(args.output / "traces.private.json", records)
    write_new(args.output / "summary.json", summary)
    write_new(args.output / "task-results.json", public_rows(records))
    write_new(args.output / "verification.json", verification)
    print(serial(summary["results"]))


if __name__ == "__main__":
    main()
