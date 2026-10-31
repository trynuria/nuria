"""Execute a bounded enrolled work sequence against one persistent history."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from cognition.outcome_learning import qualified_policy
from cognition.outcome_worker import (
    BASE_POLICY,
    IsolatedPython,
    OutcomeStore,
    identity,
    run_work,
)
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient


def run_course(client, executor, store, jobs, *, policy=BASE_POLICY, maximum_jobs=32):
    if type(jobs) is not list or not 1 <= len(jobs) <= maximum_jobs <= 128:
        raise ValueError("A course requires a finite enrolled job list")
    if len({x["job_id"] for x in jobs}) != len(jobs):
        raise ValueError("A course cannot repeat an enrolled job ID")
    store.verify()
    results = []
    for job in jobs:
        result = run_work(
            client, executor, store, job["task"], job_id=job["job_id"], policy=policy
        )
        results.append(result)
    return {
        "version": "nuria.outcome-course.v1",
        "enrollment_sha256": identity(jobs),
        "policy_sha256": identity(policy),
        "results": results,
        "journal": store.verify(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--resource-root", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--maximum-jobs", type=int, default=32)
    args = parser.parse_args()
    enrollment = json.loads(args.enrollment.read_text())
    policy = (
        BASE_POLICY
        if args.policy is None
        else qualified_policy(json.loads(args.policy.read_text()))
    )
    provider = load_reasoner(args.config)
    client = (
        BoundedClient(provider, args.resource_root) if args.resource_root else provider
    )
    before = provider.budget.snapshot()
    result = run_course(
        client,
        IsolatedPython(
            args.state / "executions", dependency_roots=args.dependency_root
        ),
        OutcomeStore(args.state / "outcomes.sqlite"),
        enrollment["jobs"],
        policy=policy,
        maximum_jobs=args.maximum_jobs,
    )
    after = provider.budget.snapshot()
    result["provider_requests_added"] = after["requests"] - before["requests"]
    result["configured_cost_and_liability_added_micro_usd"] = (
        after["accounted_cost_and_liability_micro_usd"]
        - before["accounted_cost_and_liability_micro_usd"]
    )
    result["budget"] = after
    output = args.state / "course-receipts"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = output / (uuid.uuid4().hex + ".json")
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "receipt": str(path),
                "accepted": sum(x["accepted"] is True for x in result["results"]),
                "jobs": len(result["results"]),
                "replayed": sum(x["replayed"] for x in result["results"]),
                "provider_requests_added": result["provider_requests_added"],
                "configured_cost_and_liability_added_micro_usd": result[
                    "configured_cost_and_liability_added_micro_usd"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
