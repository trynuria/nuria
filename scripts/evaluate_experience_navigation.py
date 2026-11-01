"""Run a prospectively enrolled, outcome-grounded experience comparison.

Hosted inference is optional, explicit and bounded by the existing shared
ledger. Hidden terminal feedback is withheld until all test proposals commit.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from cognition.experience_navigation import METHODS, run_navigation_work
from cognition.outcome_worker import IsolatedPython, OutcomeStore, identity
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient, exact_paired, save


def read_rows(path):
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def load_tasks(root, split):
    return [x for x in read_rows(Path(root) / "tasks.jsonl.gz") if x["split"] == split]


def valid_tasks(root, split):
    root = Path(root)
    active = root / "preflight-active.json"
    name = "preflight.json"
    if active.exists():
        seal = json.loads(active.read_text())
        name = seal["file"]
        if Path(name).name != name:
            raise ValueError("Preflight identity must be a local file")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != seal["sha256"]:
            raise ValueError("Preflight differs from the enrolled reference check")
    rows = json.loads((root / name).read_text())
    valid = {x["task_id"] for x in rows if x["checker"]["passed"]}
    return [x for x in load_tasks(root, split) if x["task_id"] in valid]


def runtime(root, dependencies):
    return IsolatedPython(Path(root) / "executions", dependency_roots=dependencies)


def preflight(root, dependencies):
    root = Path(root)
    executor = runtime(root / "preflight", dependencies)
    results = []
    for task in read_rows(root / "tasks.jsonl.gz"):
        checked = executor.run(
            task["canonical_solution"],
            task["checker"],
            label=task["task_id"].replace("/", "-"),
        )
        results.append(
            {
                "task_id": task["task_id"],
                "split": task["split"],
                "source_sha256": task["source_sha256"],
                "checker": checked,
            }
        )
    save(root / "preflight.json", results)
    return {
        "selected": len(results),
        "valid": sum(x["checker"]["passed"] for x in results),
    }


def run(root, config, dependencies, split, maximum=None):
    root = Path(root)
    client = BoundedClient(load_reasoner(config), root)
    history_path = root / (
        "initial-experience.json" if split == "development" else "test-experience.json"
    )
    experience = json.loads(history_path.read_text())
    executor = runtime(root / split, dependencies)
    stores = {m: OutcomeStore(root / (split + "-" + m + ".sqlite")) for m in METHODS}
    tasks = valid_tasks(root, split)
    enrollment = root / "test-enrollment.json"
    if split == "test":
        enrolled = json.loads(enrollment.read_text())
        if identity(experience) != enrolled["experience_sha256"]:
            raise ValueError("Test history differs from its prospective enrollment")
        ids = set(enrolled["task_ids"])
        tasks = [x for x in tasks if x["task_id"] in ids]
        for name, expected in enrolled["source_files"].items():
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
                raise ValueError("Execution source changed after test enrollment")
    finished = 0
    for task in tasks:
        rotation = int(
            hashlib.sha256(task["task_id"].encode()).hexdigest()[:8], 16
        ) % len(METHODS)
        methods = METHODS[rotation:] + METHODS[:rotation]
        for method in methods:
            result = run_navigation_work(
                client,
                executor,
                stores[method],
                task,
                job_id=split + "-" + method + "-" + task["task_id"].split("/")[1],
                method=method,
                experience=experience,
                grade=split == "development",
            )
            print(
                json.dumps(
                    {
                        "split": split,
                        "task_id": task["task_id"],
                        "method": method,
                        "committed": True,
                        "accepted": result["accepted"],
                        "replayed": result["replayed"],
                        "budget": client.client.budget.snapshot(),
                    }
                ),
                flush=True,
            )
        finished += 1
        if maximum is not None and finished >= maximum:
            break
    for store in stores.values():
        store.verify()


def freeze_test(root):
    root = Path(root)
    if (root / "test-enrollment.json").exists():
        raise ValueError("A test enrollment is immutable")
    history = json.loads((root / "initial-experience.json").read_text())
    development = []
    for method in METHODS:
        development.extend(
            OutcomeStore(root / ("development-" + method + ".sqlite")).experiences()
        )
    if len(development) != len(valid_tasks(root, "development")) * len(METHODS):
        raise ValueError("Every enrolled development outcome must finish")
    history += development
    save(root / "test-experience.json", history)
    files = [
        "cognition/experience_navigation.py",
        "cognition/outcome_worker.py",
        "scripts/evaluate_experience_navigation.py",
    ]
    value = {
        "version": "nuria.navigation-test-seal.v1",
        "before_any_test_inference": True,
        "experience_sha256": identity(history),
        "history_records": len(history),
        "task_ids": [x["task_id"] for x in valid_tasks(root, "test")],
        "source_files": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files
        },
    }
    save(root / "test-enrollment.json", value)
    return value


def grade(root, dependencies):
    root = Path(root)
    enrollment = json.loads((root / "test-enrollment.json").read_text())
    ids = set(enrollment["task_ids"])
    tasks = [x for x in valid_tasks(root, "test") if x["task_id"] in ids]
    stores = {m: OutcomeStore(root / ("test-" + m + ".sqlite")) for m in METHODS}
    expected = {x["task_id"] for x in tasks}
    for store in stores.values():
        store.verify()
        if {x["task_id"] for x in store.experiences()} != expected:
            raise ValueError(
                "Commit every fresh proposal before revealing any terminal outcome"
            )
    executor = runtime(root / "terminal", dependencies)
    records = {}
    for method, store in stores.items():
        by_task = {x["task_id"]: x for x in store.experiences()}
        records[method] = []
        for task in tasks:
            proposal = by_task[task["task_id"]]
            if proposal["checker"] is None:
                checked = executor.run(
                    proposal["code"],
                    task["checker"],
                    label=method + "-" + task["task_id"].split("/")[1],
                )
                proposal = store.accept(proposal["job_id"], checked)
            records[method].append(proposal)
        store.verify()
    vectors = {m: [x["accepted"] for x in records[m]] for m in METHODS}
    comparisons = {
        m: exact_paired(vectors["navigation"], vectors[m]) for m in METHODS[1:]
    }
    families = {}
    for family in sorted({x["family"] for x in tasks}):
        families[family] = {
            m: sum(x["accepted"] for x in records[m] if x["family"] == family)
            for m in METHODS
        }
    supported = (
        len(tasks) >= 98
        and all(
            x["accuracy_delta"] >= 0.04 and x["one_sided_exact_p"] <= 0.0125
            for x in comparisons.values()
        )
        and all(
            counts["navigation"] >= counts[m] - 2
            for counts in families.values()
            for m in METHODS[1:]
        )
    )
    result = {
        "tasks": len(tasks),
        "accepted": {m: sum(v) for m, v in vectors.items()},
        "comparisons": comparisons,
        "families": families,
        "superiority_supported": supported,
        "runtime_promoted": False,
        "reason": "Production activation is separate; no promotion is performed by this evaluation.",
    }
    save(root / "held-out-results.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=("preflight", "development", "freeze", "test", "grade")
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--max-tasks", type=int)
    args = parser.parse_args()
    if args.stage == "preflight":
        result = preflight(args.root, args.dependency_root)
    elif args.stage == "freeze":
        result = freeze_test(args.root)
    elif args.stage == "grade":
        result = grade(args.root, args.dependency_root)
    else:
        if args.config is None:
            parser.error("Inference requires an explicit private client configuration")
        run(
            args.root,
            args.config,
            args.dependency_root,
            "development" if args.stage == "development" else "test",
            args.max_tasks,
        )
        return
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
