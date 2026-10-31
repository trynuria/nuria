"""Reconstruct recorded outcomes and optionally rerun every held-out program."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from cognition.outcome_worker import IsolatedPython, identity
from scripts.evaluate_outcome_loop import exact_paired, save


def rows(path):
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def verify(package, *, workspace=None, dependencies=()):
    package = Path(package)
    tasks = rows(package / "tasks.jsonl.gz")
    upstream = {
        task["task_id"]: task for task in rows(package / "upstream-tasks.jsonl.gz")
    }
    for task in tasks:
        raw = upstream[task["task_id"]]
        if (
            task["source_sha256"] != identity(raw)
            or task["prompt"] != raw["complete_prompt"]
            or not task["checker"].startswith(raw["test"])
        ):
            raise ValueError(
                "Task source or independent tests differ from the pinned dataset"
            )
    traces = rows(package / "executions.jsonl.gz")
    tests = [x for x in tasks if x["split"] == "test"]
    expected = json.loads((package / "held-out-results.json").read_text())
    methods = ["simple_retrieval", "simple_reflection", "nuria_gepa"]
    if "outcome_playbook" in expected["methods"]:
        methods.append("outcome_playbook")
    freeze = json.loads((package / "test-execution-freeze.json").read_text())
    common = json.loads((package / "common-experience.json").read_text())
    if identity(common) != freeze["experience_sha256"]:
        raise ValueError("Common training experience differs from the test seal")
    if freeze["test_tasks"] != [
        {k: task[k] for k in ("task_id", "source_sha256", "family")} for task in tests
    ]:
        raise ValueError("Test tasks differ from their prospective enrollment")
    task_ids = {x["task_id"] for x in tests}
    if task_ids & {x["task_id"] for x in common}:
        raise ValueError("Held-out work appears in training experience")
    vectors, replayed = {}, []
    runtime = (
        IsolatedPython(workspace, dependency_roots=dependencies) if workspace else None
    )
    for method in methods:
        records = {x["task_id"]: x for x in traces if x["method"] == method}
        if set(records) != task_ids:
            raise ValueError("The comparison omits or duplicates selected tasks")
        vector = []
        for task in tests:
            record = records[task["task_id"]]
            if (
                record["source_sha256"] != task["source_sha256"]
                or record["prompt"] != task["prompt"]
            ):
                raise ValueError("A proposal used different task information")
            if record["policy_sha256"] != freeze["policy_sha256"][method]:
                raise ValueError("A test proposal used an unfrozen policy")
            if any(
                x in {r["job_id"] for r in traces if r["method"] in methods}
                for x in record["experience_ids"]
            ):
                raise ValueError("A held-out proposal consulted another test outcome")
            if record["proposed_code_sha256"] != identity(record["code"]):
                raise ValueError("Committed program differs")
            if len(record["attempts"]) > 2 or any(
                x["usage"]["completion_tokens"] > 2400 for x in record["attempts"]
            ):
                raise ValueError("Job inference allowance exceeded")
            if record["checker"]["passed"] is not record["accepted"]:
                raise ValueError("Acceptance differs from its independent execution")
            if (
                record["checker"]["checks_sha256"]
                != hashlib.sha256(task["checker"].encode()).hexdigest()
            ):
                raise ValueError("Acceptance used a different checker")
            if (
                record["checker"]["code_sha256"]
                != hashlib.sha256(record["code"].encode()).hexdigest()
            ):
                raise ValueError("Acceptance executed a different program")
            vector.append(record["accepted"])
            alias = record.get("execution_alias_of")
            if alias and (
                alias != freeze.get("gepa_execution_alias_of")
                or record["policy_sha256"] != freeze["policy_sha256"][alias]
            ):
                raise ValueError(
                    "An execution alias does not describe the identical policy"
                )
            if runtime and not alias:
                report = runtime.run(
                    record["code"],
                    task["checker"],
                    label=method + "-" + task["task_id"].split("/")[1],
                )
                replayed.append(
                    {
                        "method": method,
                        "task_id": task["task_id"],
                        "recorded": record["accepted"],
                        "actual": report["passed"],
                        "checker": report,
                    }
                )
        vectors[method] = vector
        if (
            sum(vector) != expected["methods"][method]["accepted"]
            or len(vector) != expected["methods"][method]["tasks"]
        ):
            raise ValueError("Published totals differ from retained outcomes")
    paired = {
        method: exact_paired(vectors["nuria_gepa"], vectors[method])
        for method in methods[:2]
    }
    if paired != expected["paired"]:
        raise ValueError("Published paired comparisons differ")
    if "outcome_playbook" in vectors:
        for method in methods[:2]:
            comparison = exact_paired(vectors["outcome_playbook"], vectors[method])
            for key, value in comparison.items():
                if expected["playbook_comparisons"][method][key] != value:
                    raise ValueError("Playbook comparison differs from its outcomes")
    result = {
        "verified_tasks_per_method": len(tests),
        "accepted": {k: sum(v) for k, v in vectors.items()},
        "paired": paired,
        "replayed_programs": len(replayed),
        "replay_mismatches": [
            {k: x[k] for k in ("method", "task_id", "recorded", "actual")}
            for x in replayed
            if x["recorded"] != x["actual"]
        ],
    }
    if workspace:
        save(Path(workspace) / "replay.json", {"summary": result, "programs": replayed})
    return result


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
    if result["replay_mismatches"]:
        raise SystemExit("Independent replay differs; inspect retained evidence")


if __name__ == "__main__":
    main()
