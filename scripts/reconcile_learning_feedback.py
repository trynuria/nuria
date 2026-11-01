"""Exercise consistent experience on complete, already recorded native outcomes.

This is an integration check using revealed evidence, not a fresh reasoning
comparison. It makes no model calls, reruns no program and changes no original
journal. The imported receipts are retained in a new explicit workspace.
"""

import argparse
import hashlib
import json
from pathlib import Path

from cognition.outcome_consistency import ConsistentOutcomeStore
from cognition.outcome_worker import identity
from scripts.evaluate_experience_navigation import read_rows
from scripts.verify_reasoner_transfer import verify


def reconcile(package, workspace):
    package, workspace = Path(package), Path(workspace)
    if workspace.exists():
        raise ValueError("Feedback reconciliation needs a new retained workspace")
    verified = verify(package)
    records = read_rows(package / "executions.jsonl.gz")
    by_key = {
        (item["condition"], item["record"]["task_id"]): item["record"]
        for item in records
    }
    replay = json.loads((package / "independent-replay.json").read_text())
    if len(replay["programs"]) != len(records) or {
        (row["condition"], row["task_id"]) for row in replay["programs"]
    } != set(by_key):
        raise ValueError("Retain every native replay observation")
    sources = [
        "executions.jsonl.gz",
        "independent-replay.json",
        "timing-823/characterization.json",
        "negative-qualification.json",
    ]
    source_hashes = {
        name: hashlib.sha256((package / name).read_bytes()).hexdigest()
        for name in sources
    }
    store = ConsistentOutcomeStore(workspace / "outcomes.sqlite")
    for item in records:
        row = item["record"]
        store.begin(
            row["job_id"],
            {
                "imported_condition": item["condition"],
                "original_record_sha256": identity(row),
                "source_package": source_hashes,
            },
        )
        store.finish(row["job_id"], row)
    before = identity(store.raw_experiences())
    observations = []
    for row in replay["programs"]:
        original = by_key[(row["condition"], row["task_id"])]
        observations.append(
            (
                original,
                "native-" + row["condition"] + "-" + row["task_id"].split("/")[1],
                row["checker"],
            )
        )
    for name, characterization in [
        (
            "first",
            json.loads((package / "timing-823/characterization.json").read_text()),
        ),
        (
            "second",
            json.loads((package / "negative-qualification.json").read_text())[
                "timing_characterization"
            ],
        ),
    ]:
        for row in characterization["rows"]:
            if row["kind"] == "unchanged_wall_clock" and row["program"] in (
                "sol-low",
                "astra-medium",
            ):
                original = by_key[(row["program"], "BigCodeBench/823")]
                observations.append(
                    (
                        original,
                        name + "-" + row["program"] + "-" + str(row["round"]),
                        row["checker"],
                    )
                )
    for row, name, checker in observations:
        store.observe(row["job_id"], name, checker, result_sha256=identity(row))
    journal = store.verify()
    for row, name, checker in observations:
        if not store.observe(row["job_id"], name, checker, result_sha256=identity(row))[
            "replayed"
        ]:
            raise ValueError("An observation replay added work")
    after = ConsistentOutcomeStore(store.path).learning_report()
    if store.verify() != journal or identity(store.raw_experiences()) != before:
        raise ValueError("Reconciliation changed history or duplicated receipts")
    withheld = [
        {"condition": condition, "task_id": task_id, "job_id": row["job_id"]}
        for (condition, task_id), row in sorted(by_key.items())
        if row["job_id"] in after["withheld_job_ids"]
    ]
    if {(row["condition"], row["task_id"]) for row in withheld} != {
        ("sol-low", "BigCodeBench/823"),
        ("astra-medium", "BigCodeBench/823"),
    }:
        raise ValueError("Unexpected feedback consistency result")
    report = {
        "version": "nuria.feedback-reconciliation.v1",
        "implementation_sha256": {
            name: hashlib.sha256(
                (Path(__file__).resolve().parents[1] / name).read_bytes()
            ).hexdigest()
            for name in (
                "cognition/outcome_consistency.py",
                "scripts/reconcile_learning_feedback.py",
                "scripts/run_consistent_course.py",
            )
        },
        "sources_sha256": source_hashes,
        "original_comparison": verified["result"],
        "raw_jobs": after["raw_jobs"],
        "additional_observations": after["additional_observations"],
        "eligible_future_experiences": len(after["records"]),
        "withheld": withheld,
        "raw_history_sha256_before": before,
        "raw_history_sha256_after": identity(store.raw_experiences()),
        "duplicate_observation_journal_unchanged": True,
        "journal": journal,
        "model_calls": 0,
        "new_program_executions": 0,
        "original_grades_changed": False,
        "scope": "Revealed native evidence; finite per-job learning eligibility, no accuracy, neural or AGI claim",
    }
    with (workspace / "report.json").open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    report = reconcile(args.package, args.workspace)
    if args.expected is not None and report != json.loads(args.expected.read_text()):
        raise ValueError(
            "Reconciled feedback differs from the complete published report"
        )
    print(json.dumps(report, indent=2))
