"""Qualify an optional policy from a finite enrolled course of checked jobs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cognition.outcome_learning import learn_policy
from cognition.outcome_worker import IsolatedPython, OutcomeStore
from scripts.ask_hosted_analyst import load_reasoner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course", type=Path)
    parser.add_argument("history", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--max-evaluations", type=int, default=72)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("A policy artifact must use a new output file")
    course = json.loads(args.course.read_text())
    history = OutcomeStore(args.history)
    history.verify()
    client = load_reasoner(args.config)
    artifact = learn_policy(
        client,
        IsolatedPython(
            args.output.parent / (args.output.stem + "-executions"),
            dependency_roots=args.dependency_root,
        ),
        OutcomeStore(args.output.parent / (args.output.stem + "-qualification.sqlite")),
        training=course["training"],
        validation=course["validation"],
        experience=history.experiences(),
        max_evaluations=args.max_evaluations,
    )
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "policy_sha256": artifact["policy_sha256"],
                "validation_scores": artifact["validation_scores"],
                "activated": False,
                "budget": client.budget.snapshot(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
