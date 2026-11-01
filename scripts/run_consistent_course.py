"""Finite work using checked experience with contradictory feedback withheld."""

import argparse
import json
from pathlib import Path

from cognition.experience_navigation import METHODS, run_navigation_work
from cognition.outcome_consistency import ConsistentOutcomeStore
from cognition.outcome_worker import IsolatedPython, identity, run_work
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient


def run_course(client, executor, store, jobs, *, method="direct"):
    if (
        method not in ("direct", *METHODS)
        or type(jobs) is not list
        or not 1 <= len(jobs) <= 32
        or len({job["job_id"] for job in jobs}) != len(jobs)
    ):
        raise ValueError("A finite, unique work enrollment is required")
    store = ConsistentOutcomeStore(store.path)
    before = store.learning_report()
    results = []
    for job in jobs:
        options = {"job_id": job["job_id"]}
        if method == "direct":
            result = run_work(client, executor, store, job["task"], **options)
        else:
            result = run_navigation_work(
                client, executor, store, job["task"], method=method, **options
            )
        results.append(result)
    return {
        "version": "nuria.consistent-course.v1",
        "method": method,
        "enrollment_sha256": identity(jobs),
        "results": results,
        "withheld_experience_ids": before["withheld_job_ids"],
        "journal": store.verify(),
        "scope": "Same work protocols with uncertain prior jobs withheld; no performance advantage established",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--resource-root", required=True, type=Path)
    parser.add_argument("--method", choices=("direct", *METHODS), default="direct")
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    result = run_course(
        BoundedClient(load_reasoner(args.config), args.resource_root),
        IsolatedPython(
            args.workspace / "executions", dependency_roots=args.dependency_root
        ),
        ConsistentOutcomeStore(args.workspace / "outcomes.sqlite"),
        json.loads(args.enrollment.read_text())["jobs"],
        method=args.method,
    )
    path = args.workspace / ("course-" + identity(result) + ".json")
    if path.exists():
        if json.loads(path.read_text()) != result:
            raise ValueError("Course receipt identity collision")
    else:
        with path.open("x") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "jobs": len(result["results"]),
                "withheld_experience_ids": result["withheld_experience_ids"],
                "receipt": str(path),
            }
        )
    )


if __name__ == "__main__":
    main()
