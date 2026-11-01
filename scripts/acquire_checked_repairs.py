"""Acquire replay-checked corrections from explicitly enrolled past outcomes."""

import argparse
import json
from pathlib import Path

from cognition.checked_repairs import acquire_corrections, validate_corrections
from cognition.outcome_worker import IsolatedPython
from scripts.evaluate_experience_navigation import read_rows
from scripts.evaluate_outcome_loop import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("tasks", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    records = json.loads(args.history.read_text())
    tasks = read_rows(args.tasks)
    executor = IsolatedPython(args.workspace, dependency_roots=args.dependency_root)
    catalog = acquire_corrections(records, tasks, executor)
    validate_corrections(catalog, records)
    save(args.output, catalog)
    print(
        json.dumps(
            {
                "selected_pairs": len(catalog["corrections"]),
                "verified": sum(row["verified"] for row in catalog["corrections"]),
                "artifact_sha256": catalog["artifact_sha256"],
                "model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
