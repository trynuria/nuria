"""Run one explicitly enrolled, independently checked programming job.

The manifest supplies public or otherwise authorized input files as bounded
text, an immutable source identity, the specification and a trusted checker.
The model cannot choose its acceptance test. A repeated identical job ID
returns its existing outcome without model calls or duplicate execution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.outcome_learning import qualified_policy
from cognition.outcome_worker import BASE_POLICY, IsolatedPython, OutcomeStore, run_work
from scripts.ask_hosted_analyst import load_reasoner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if set(manifest) != {"job_id", "task"}:
        parser.error("Manifest requires exactly job_id and task")
    task = manifest["task"]
    if not {"task_id", "source_sha256", "prompt", "checker"} <= set(task):
        parser.error("Task needs an explicit source, specification and host checker")
    policy = (
        BASE_POLICY
        if args.policy is None
        else qualified_policy(json.loads(args.policy.read_text()))
    )
    # No background polling, provider discovery or credential fallback.
    client = load_reasoner(args.config)
    store = OutcomeStore(args.state / "outcomes.sqlite")
    runtime = IsolatedPython(
        args.state / "executions", dependency_roots=args.dependency_root
    )
    result = run_work(
        client, runtime, store, task, job_id=manifest["job_id"], policy=policy
    )
    print(
        json.dumps(
            {
                "job_id": result["job_id"],
                "accepted": result["accepted"],
                "replayed": result["replayed"],
                "program_sha256": result["proposed_code_sha256"],
                "journal": store.verify(),
                "budget": client.budget.snapshot(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
