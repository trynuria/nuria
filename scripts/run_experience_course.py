"""Execute explicitly enrolled jobs with durable checked-experience navigation."""

import argparse
import json
import uuid
from pathlib import Path

from cognition.experience_navigation import METHODS, run_navigation_work
from cognition.outcome_worker import IsolatedPython, OutcomeStore
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient, save


def run(
    enrollment,
    workspace,
    config,
    *,
    method="navigation",
    dependencies=(),
    resource_root=None,
):
    jobs = enrollment["jobs"]
    if type(jobs) is not list or not 1 <= len(jobs) <= 32:
        raise ValueError("A finite course must contain one to32 enrolled jobs")
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = load_reasoner(config)
    if resource_root is not None:
        client = BoundedClient(client, resource_root)
    store = OutcomeStore(workspace / "outcomes.sqlite")
    executor = IsolatedPython(workspace / "executions", dependency_roots=dependencies)
    results = []
    for job in jobs:
        results.append(
            run_navigation_work(
                client,
                executor,
                store,
                job["task"],
                job_id=job["job_id"],
                method=method,
            )
        )
    receipt = {
        "version": "nuria.experience-course.v1",
        "method": method,
        "jobs": results,
        "journal": store.verify(),
        "accepted": sum(x["accepted"] for x in results),
        "replayed": sum(x["replayed"] for x in results),
    }
    path = workspace / ("course-" + uuid.uuid4().hex + ".json")
    save(path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--method", choices=METHODS, default="navigation")
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--resource-root", type=Path)
    args = parser.parse_args()
    receipt = run(
        json.loads(args.enrollment.read_text()),
        args.workspace,
        args.config,
        method=args.method,
        dependencies=args.dependency_root,
        resource_root=args.resource_root,
    )
    print(
        json.dumps(
            {
                "jobs": len(receipt["jobs"]),
                "accepted": receipt["accepted"],
                "replayed": receipt["replayed"],
                "journal": receipt["journal"],
            }
        )
    )


if __name__ == "__main__":
    main()
