"""Provider-free verification of committed work and one unchanged native replay.

The original observations are never relabeled. Host records are inspectable
receipts, not independent provider billing or remote execution attestation.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY))

from cognition.interactive_work import POLICY, VERSION  # noqa: E402
from cognition.outcome_worker import IsolatedPython, identity  # noqa: E402
from scripts.evaluate_experience_navigation import read_rows  # noqa: E402
from scripts.evaluate_outcome_loop import exact_paired  # noqa: E402
from scripts.evaluate_reasoning_continuity import usage  # noqa: E402


def summarize(protocol, tasks, records):
    names = ("continuity", "visible")
    if set(protocol["conditions"]) != set(names):
        raise ValueError("Use exactly the enrolled conditions")
    ordered = {
        name: sorted(
            (r["record"] for r in records if r["condition"] == name),
            key=lambda row: row["task_id"],
        )
        for name in names
    }
    vectors = {name: [r["accepted"] for r in ordered[name]] for name in names}
    comparison = exact_paired(vectors["continuity"], vectors["visible"])
    families = {
        family: {
            name: sum(r["accepted"] for r in ordered[name] if r["family"] == family)
            for name in names
        }
        for family in sorted({task["family"] for task in tasks})
    }
    gate = protocol["primary_gate"]
    return {
        "tasks": len(tasks),
        "accepted": {name: sum(v) for name, v in vectors.items()},
        "comparison": comparison,
        "families": families,
        "usage": {
            name: usage(ordered[name], protocol["conditions"][name]) for name in names
        },
        "superiority_supported": len(tasks) >= gate["minimum_valid_tasks"]
        and comparison["accuracy_delta"] >= gate["absolute_accuracy_gain_min"]
        and comparison["one_sided_exact_p"] <= gate["one_sided_exact_p_max"]
        and all(
            x["continuity"] >= x["visible"] - gate["maximum_family_regression"]
            for x in families.values()
        ),
        "neural_advantage_established": False,
        "runtime_promoted": False,
    }


def material(package):
    protocol = json.loads((package / "protocol.json").read_text())
    for name, expected in protocol["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Keep source and evidence paths relative")
        path = (
            REPOSITORY / name
            if name.startswith(("cognition/", "scripts/", "tests/"))
            else package / name
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Prospective source or evidence changed")
    tasks = read_rows(package / "tasks.jsonl.gz")
    upstream = {
        row["task_id"]: row
        for row in read_rows(package / "raw-selected-tasks.jsonl.gz")
    }
    if len(tasks) != len(upstream) or len(tasks) != protocol["selected"]:
        raise ValueError("Retain every selected source task")
    for task in tasks:
        original = upstream[task["task_id"]]
        if (
            task["source_sha256"] != identity(original)
            or task["prompt"] != original["complete_prompt"]
            or not task["checker"].startswith(original["test"])
        ):
            raise ValueError("Upstream prompt or checker changed")
    preflight = json.loads((package / "preflight.json").read_text())
    valid = {x["task_id"] for x in preflight if x["checker"]["passed"]}
    tasks = [x for x in tasks if x["task_id"] in valid]
    if [x["task_id"] for x in tasks] != protocol["valid_task_ids"]:
        raise ValueError("Reference-valid enrollment changed")
    history = json.loads((package / "experience.json").read_text())
    if valid & {x["task_id"] for x in history}:
        raise ValueError("Fresh tasks overlap prior experience")
    return protocol, tasks, history


def commitments(package, protocol, tasks, history, seal, records):
    by_task = {task["task_id"]: task for task in tasks}
    events = read_rows(package / "journal-events.jsonl.gz")
    redactions = json.loads((package / "path-redactions.json").read_text())
    changed = {(x["condition"], x["job_id"]): x for x in redactions}
    if seal["protocol_sha256"] != identity(protocol):
        raise ValueError("Proposals belong to a different protocol")
    if seal["terminal_checks_revealed"] is not False or seal["tasks"] != len(tasks):
        raise ValueError("Require the complete pre-grading proposal seal")
    evidence = {}
    for name, config in protocol["conditions"].items():
        original = {x["job_id"]: x for x in seal["records"][name]}
        final = {
            x["record"]["job_id"]: x["record"]
            for x in records
            if x["condition"] == name
        }
        if (
            len(original) != len(tasks)
            or set(original) != set(final)
            or {x["task_id"] for x in final.values()} != set(by_task)
        ):
            raise ValueError("A condition omits or duplicates a program")
        for job, row in final.items():
            if identity({**row, "accepted": None, "checker": None}) != identity(
                original[job]
            ):
                raise ValueError("Pre-grading program or evidence changed")
            if (
                row["experience_sha256"] != identity(history)
                or row["implementation_sha256"]
                != identity((REPOSITORY / "cognition/interactive_work.py").read_text())
                or row["policy_sha256"] != identity(POLICY)
                or row["proposed_code_sha256"] != identity(row["code"])
                or row["execution_route"] != VERSION
                or not 1 <= len(row["attempts"]) <= 5
                or row["completion_tokens_used"] > 7200
            ):
                raise ValueError("Work differs from the enrolled protocol")
            if row["completion_tokens_used"] != sum(
                attempt["usage"]["completion_tokens"] for attempt in row["attempts"]
            ):
                raise ValueError("Per-job completion accounting changed")
            for attempt in row["attempts"]:
                if attempt["usage"]["completion_tokens"] > 2400 or (
                    "content" in attempt
                    and identity(attempt["content"]) != attempt["response_sha256"]
                ):
                    raise ValueError("Retained provider output changed")
            prior = evidence.setdefault(row["task_id"], row["experience_ids"])
            if prior != row["experience_ids"]:
                raise ValueError("Conditions received different retrieved outcomes")
        previous, begun, proposed, accepted = "0" * 64, set(), set(), set()
        sequence = sorted(
            (x for x in events if x["condition"] == name), key=lambda x: x["seq"]
        )
        for count, item in enumerate(sequence, 1):
            event = item["event"]
            if (
                item["seq"] != count
                or item["previous"] != previous
                or item["sha256"] != identity({"previous": previous, "event": event})
            ):
                raise ValueError("Original outcome journal changed")
            previous = item["sha256"]
            job = event["job"]
            row = original[job]
            if event["event"] == "begin":
                contract = {
                    "version": VERSION,
                    "implementation_sha256": row["implementation_sha256"],
                    "task": by_task[row["task_id"]],
                    "model": config,
                    "experience": identity(history),
                    "maximum_calls": 5,
                    "maximum_completion_tokens": 7200,
                    "maximum_call_tokens": 2400,
                    "grade": False,
                }
                if job in begun or event["contract_sha256"] != identity(contract):
                    raise ValueError("A job used a different execution contract")
                begun.add(job)
            elif event["event"] == "outcome":
                if (
                    job not in begun
                    or job in proposed
                    or event["result_sha256"] != identity(row)
                ):
                    raise ValueError("Original program commitment changed")
                proposed.add(job)
            elif event["event"] == "acceptance":
                change = changed.get((name, job))
                expected = (
                    change["original_result_sha256"] if change else identity(final[job])
                )
                if (
                    proposed != set(original)
                    or job in accepted
                    or event["result_sha256"] != expected
                ):
                    raise ValueError("Acceptance precedes or differs from commitment")
                accepted.add(job)
            else:
                raise ValueError("Unknown journal event")
        if (
            begun != set(original)
            or proposed != set(original)
            or accepted != set(original)
        ):
            raise ValueError("Retain the complete proposal and acceptance journal")


def verify(package, workspace=None, dependencies=()):
    package = Path(package)
    protocol, tasks, history = material(package)
    seal = json.loads((package / "proposals-before-grade.json").read_text())
    records = read_rows(package / "executions.jsonl.gz")
    commitments(package, protocol, tasks, history, seal, records)
    result = summarize(protocol, tasks, records)
    if result != json.loads((package / "results.json").read_text()):
        raise ValueError("Published result differs from complete paired outcomes")
    by_task = {task["task_id"]: task for task in tasks}
    if workspace is not None and Path(workspace).exists():
        raise ValueError("Use a new retained replay workspace")
    executor = (
        IsolatedPython(workspace, dependency_roots=dependencies) if workspace else None
    )
    actual = []
    for item in records:
        row, name = item["record"], item["condition"]
        checker, task = row["checker"], by_task[row["task_id"]]
        if (
            type(row["accepted"]) is not bool
            or checker["passed"] is not row["accepted"]
            or row["source_sha256"] != task["source_sha256"]
            or checker["code_sha256"]
            != hashlib.sha256(row["code"].encode()).hexdigest()
            or checker["checks_sha256"]
            != hashlib.sha256(task["checker"].encode()).hexdigest()
        ):
            raise ValueError(
                "Acceptance is not bound to its program and original checker"
            )
        if executor:
            observed = executor.run(
                row["code"],
                task["checker"],
                label=name + "-" + row["task_id"].split("/")[1],
                inputs=task.get("inputs"),
            )
            actual.append(
                {
                    "condition": name,
                    "task_id": row["task_id"],
                    "recorded": row["accepted"],
                    "actual": observed["passed"],
                    "checker": observed,
                }
            )
    report = {
        "original_result": result,
        "replayed_programs": len(actual),
        "mismatches": [
            {k: r[k] for k in ("condition", "task_id", "recorded", "actual")}
            for r in actual
            if r["recorded"] != r["actual"]
        ],
        "model_calls": 0,
        "original_grades_changed": False,
    }
    if workspace:
        with (Path(workspace) / "replay.json").open("x") as stream:
            json.dump(
                {"summary": report, "programs": actual},
                stream,
                sort_keys=True,
                indent=2,
            )
            stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, nargs="?")
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    print(
        json.dumps(
            verify(
                Path(__file__).resolve().parent, args.workspace, args.dependency_root
            ),
            sort_keys=True,
        )
    )
