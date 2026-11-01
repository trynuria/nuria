"""Check retained experience-navigation evidence, optionally replay programs.

This does not make model requests. The selected cohort and every failed outcome
remain in the evidence; independent replay must agree before verification passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cognition.experience_navigation import METHODS
from cognition.outcome_worker import IsolatedPython, identity
from scripts.evaluate_experience_navigation import read_rows
from scripts.evaluate_outcome_loop import exact_paired, save


def verify(package, *, workspace=None, dependencies=()):
    package = Path(package)
    tasks = read_rows(package / "tasks.jsonl.gz")
    upstream = {x["task_id"]: x for x in read_rows(package / "upstream-tasks.jsonl.gz")}
    enrollment = json.loads((package / "test-enrollment.json").read_text())
    history = read_rows(package / "test-experience.jsonl.gz")
    if identity(history) != enrollment["experience_sha256"]:
        raise ValueError("Common history differs from the prospective enrollment")
    history_ids = {x["job_id"] for x in history}
    records = read_rows(package / "executions.jsonl.gz")
    results = json.loads((package / "held-out-results.json").read_text())
    tests = [x for x in tasks if x["task_id"] in set(enrollment["task_ids"])]
    if len({x["task_id"] for x in tests}) != len(enrollment["task_ids"]):
        raise ValueError("Task enrollment is incomplete or duplicated")
    if {x["task_id"] for x in tests} & {x["task_id"] for x in history}:
        raise ValueError("Held-out tasks appear in the experience corpus")
    for task in tasks:
        original = upstream[task["task_id"]]
        if (
            task["source_sha256"] != identity(original)
            or task["prompt"] != original["complete_prompt"]
            or not task["checker"].startswith(original["test"])
        ):
            raise ValueError("An upstream specification or checker was changed")
    executor = (
        IsolatedPython(workspace, dependency_roots=dependencies) if workspace else None
    )
    vectors, replays = {}, []
    for method in METHODS:
        method_records = [
            x
            for x in records
            if x["method"] == method and x["job_id"].startswith("test-")
        ]
        by_task = {x["task_id"]: x for x in method_records}
        if len(by_task) != len(method_records) or set(by_task) != {
            x["task_id"] for x in tests
        }:
            raise ValueError("A method omits or duplicates an enrolled task")
        vectors[method] = []
        for task in tests:
            record = by_task[task["task_id"]]
            if record["implementation_sha256"] != enrollment["implementation_sha256"]:
                raise ValueError("A proposal used a different implementation")
            if method == "navigation" and record[
                "correction_catalog_sha256"
            ] != enrollment.get("correction_catalog_sha256"):
                raise ValueError("A proposal used another checked-correction catalog")
            if record["experience_sha256"] != enrollment["experience_sha256"]:
                raise ValueError("A method consulted a different training history")
            if not set(record["experience_ids"]) <= history_ids:
                raise ValueError("A proposal consulted unenrolled experience")
            if method == "no_memory" and (
                record["experience_ids"] or record["navigation"]["view"]
            ):
                raise ValueError("The no-memory control accessed experience")
            if (
                record["source_sha256"] != task["source_sha256"]
                or record["prompt"] != task["prompt"]
            ):
                raise ValueError("Methods received different task information")
            if record["proposed_code_sha256"] != identity(record["code"]):
                raise ValueError("The committed program changed")
            if record["checker"]["passed"] is not record["accepted"]:
                raise ValueError("Acceptance differs from the host check")
            if (
                record["checker"]["code_sha256"]
                != hashlib.sha256(record["code"].encode()).hexdigest()
                or record["checker"]["checks_sha256"]
                != hashlib.sha256(task["checker"].encode()).hexdigest()
            ):
                raise ValueError("The host checked another program or contract")
            if len(record["attempts"]) != 3 or any(
                x["usage"]["completion_tokens"] > 2400 for x in record["attempts"]
            ):
                raise ValueError("A method exceeded its prospective inference ceiling")
            for attempt in record["attempts"]:
                if (
                    "content" in attempt
                    and identity(attempt["content"]) != attempt["response_sha256"]
                ):
                    raise ValueError("Retained model output changed")
            vectors[method].append(record["accepted"])
            if executor:
                actual = executor.run(
                    record["code"],
                    task["checker"],
                    label=method + "-" + task["task_id"].split("/")[1],
                )
                replays.append(
                    {
                        "task_id": task["task_id"],
                        "method": method,
                        "recorded": record["accepted"],
                        "actual": actual["passed"],
                        "checker": actual,
                    }
                )
    comparisons = {
        m: exact_paired(vectors["navigation"], vectors[m]) for m in METHODS[1:]
    }
    if (
        comparisons != results["comparisons"]
        or {m: sum(v) for m, v in vectors.items()} != results["accepted"]
    ):
        raise ValueError("Published scores differ from the complete retained outcomes")
    report = {
        "tasks_per_method": len(tests),
        "accepted": {m: sum(v) for m, v in vectors.items()},
        "comparisons": comparisons,
        "replayed_programs": len(replays),
        "mismatches": [
            {k: row[k] for k in ("task_id", "method", "recorded", "actual")}
            for row in replays
            if row["recorded"] != row["actual"]
        ],
    }
    if workspace:
        save(Path(workspace) / "replay.json", {"summary": report, "programs": replays})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(
        args.package, workspace=args.workspace, dependencies=args.dependency_root
    )
    if args.output:
        save(args.output, result)
    print(json.dumps(result, sort_keys=True))
    if result["mismatches"]:
        raise SystemExit(
            "Independent program replay differs; inspect retained evidence"
        )


if __name__ == "__main__":
    main()
