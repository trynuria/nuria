"""Run finite delivered work with factual structural input observations."""

import argparse
import json
import os
import uuid
from pathlib import Path

from cognition.experience_navigation import METHODS
from cognition.input_observations import inspect_enrollment
from cognition.outcome_worker import IsolatedPython, OutcomeStore
from scripts.ask_hosted_analyst import load_reasoner
from scripts.evaluate_outcome_loop import BoundedClient
from scripts.run_delivered_course import run_delivered
from scripts.run_outcome_course import run_course


def direct_work(
    enrollment, workspace, config, *, dependencies=(), resource_root=None, method=None
):
    """The established simpler proposal/execution-repair route, unchanged."""
    client = load_reasoner(config)
    if resource_root is not None:
        client = BoundedClient(client, resource_root)
    workspace = Path(workspace)
    result = run_course(
        client,
        IsolatedPython(workspace / "executions", dependency_roots=dependencies),
        OutcomeStore(workspace / "outcomes.sqlite"),
        enrollment["jobs"],
    )
    rows = result["results"]
    return {
        "version": result["version"],
        "strategy": "direct",
        "jobs": rows,
        "journal": result["journal"],
        "accepted": sum(row["accepted"] is True for row in rows),
        "replayed": sum(row["replayed"] for row in rows),
    }


def run_inspected(enrollment, workspace, config, *, strategy="challenged", **kwargs):
    if strategy not in ("challenged", "direct"):
        raise ValueError("Unknown inspected-work strategy")
    observed = inspect_enrollment(enrollment)
    if strategy == "direct":
        kwargs["runner"] = direct_work
    result = run_delivered(observed, workspace, config, **kwargs)
    result["input_inspection"] = observed["input_inspection"]
    result["strategy"] = strategy
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--method", choices=METHODS, default="retrieval")
    parser.add_argument(
        "--strategy", choices=("challenged", "direct"), default="direct"
    )
    args = parser.parse_args()
    result = run_inspected(
        json.loads(args.enrollment.read_text()),
        args.workspace,
        args.config,
        resource_root=args.resource_root,
        dependencies=args.dependency_root,
        method=args.method,
        strategy=args.strategy,
    )
    path = args.workspace / ("inspected-course-" + uuid.uuid4().hex + ".json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "receipt": str(path),
                "accepted": result["work"]["accepted"],
                "delivered": len(result["delivery"]["deliveries"]),
                "replayed": result["work"]["replayed"],
                "rejected_jobs": result["rejected_jobs"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
