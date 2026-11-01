"""Collect host-enrolled artifacts after an existing work course is accepted."""

import argparse
import json
from pathlib import Path

from cognition.outcome_worker import OutcomeStore
from cognition.program_delivery import collect


def collect_course(enrollment, state, output):
    jobs = enrollment["jobs"]
    if type(jobs) is not list or not 1 <= len(jobs) <= 32:
        raise ValueError("A finite enrolled delivery course is required")
    if len({job["job_id"] for job in jobs}) != len(jobs):
        raise ValueError("Delivery jobs cannot repeat")
    state = Path(state)
    if not (state / "outcomes.sqlite").is_file():
        raise ValueError("An existing accepted work journal is required")
    store = OutcomeStore(state / "outcomes.sqlite")
    results = []
    for job in jobs:
        if job["delivery"]["job_id"] != job["job_id"]:
            raise ValueError("Delivery enrollment names another job")
        results.append(
            collect(store, job["task"], job["delivery"], state / "executions", output)
        )
    return {"deliveries": results, "replayed": sum(row["replayed"] for row in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = collect_course(
        json.loads(args.enrollment.read_text()), args.state, args.output
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
