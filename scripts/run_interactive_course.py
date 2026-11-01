"""Run finite interactive work with existing checked artifact delivery."""

import argparse
import json
import uuid
from pathlib import Path

from cognition.input_observations import inspect_enrollment
from cognition.interactive_work import run_interactive_work
from cognition.outcome_consistency import ConsistentOutcomeStore
from cognition.outcome_worker import IsolatedPython
from cognition.reasoning_continuity import ContinuitySession, ContinuityTransport
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient
from scripts.run_delivered_course import run_delivered


def runner(
    enrollment, workspace, config, *, dependencies=(), resource_root=None, method=None
):
    if resource_root is None:
        raise ValueError("Interactive inference requires its shared resource authority")
    workspace = Path(workspace)
    provider = load_reasoner(config)
    if method not in (None, "navigation", "continuity"):
        raise ValueError("Use the existing visible route or explicit continuity")
    if method == "continuity":
        provider = ContinuityTransport(provider)
    client = BoundedClient(provider, resource_root)
    if method == "continuity":
        client = ContinuitySession(client, keep_reasoning=True)
    store = ConsistentOutcomeStore(workspace / "outcomes.sqlite")
    executor = IsolatedPython(workspace / "executions", dependency_roots=dependencies)
    results = [
        run_interactive_work(client, executor, store, job["task"], job_id=job["job_id"])
        for job in enrollment["jobs"]
    ]
    return {
        "version": "nuria.interactive-course.v1",
        "jobs": results,
        "journal": store.verify(),
        "accepted": sum(row["accepted"] is True for row in results),
        "replayed": sum(row["replayed"] for row in results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--resource-root", required=True, type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument(
        "--reasoning-continuity",
        action="store_true",
        help="Preserve opaque provider state within each bounded work job",
    )
    args = parser.parse_args()
    observed = inspect_enrollment(json.loads(args.enrollment.read_text()))
    result = run_delivered(
        observed,
        args.workspace,
        args.config,
        dependencies=args.dependency_root,
        resource_root=args.resource_root,
        runner=runner,
        method="continuity" if args.reasoning_continuity else None,
    )
    result["input_observations"] = {
        job["job_id"]: job["input_observation"] for job in observed["jobs"]
    }
    path = args.workspace / ("interactive-course-" + uuid.uuid4().hex + ".json")
    with path.open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "receipt": str(path),
                "accepted": result["work"]["accepted"],
                "delivered": len(result["delivery"]["deliveries"]),
                "replayed": result["work"]["replayed"],
            }
        )
    )


if __name__ == "__main__":
    main()
