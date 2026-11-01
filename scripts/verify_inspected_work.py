"""Independently rerun the published input-grounded work qualification."""

import argparse
import hashlib
import json
from pathlib import Path

from cognition.input_observations import inspect_enrollment
from cognition.outcome_worker import IsolatedPython
from scripts.run_delivered_course import prepare


def verify(package, workspace, *, dependencies=()):
    package, workspace = Path(package), Path(workspace)
    if workspace.exists():
        raise ValueError("Independent replay requires a new retained workspace")
    repository = Path(__file__).resolve().parents[1]
    freeze = json.loads((package / "execution-source-before-models.json").read_text())
    for relative, expected in freeze["files"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Runtime identity must be repository-relative")
        if hashlib.sha256((repository / path).read_bytes()).hexdigest() != expected:
            raise ValueError("Replay requires the published runtime source")
    for name, field in [
        ("enrollment.json", "enrollment_sha256"),
        ("execution-source-before-models.zip", "archive_sha256"),
    ]:
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != freeze[field]:
            raise ValueError("Published qualification inputs changed")
    enrollment = json.loads((package / "enrollment.json").read_text())
    prepared = prepare(inspect_enrollment(enrollment))
    tasks = {job["job_id"]: job["task"] for job in prepared["jobs"]}
    courses = json.loads((package / "courses.json").read_text())
    if set(courses) != {"direct", "challenged"}:
        raise ValueError("Both prospective routes are required")
    rows = []
    for route, course in courses.items():
        records = course["work"]["jobs"]
        if len(records) != len(tasks) or {r["job_id"] for r in records} != set(tasks):
            raise ValueError("Every enrolled job must be retained")
        executor = IsolatedPython(workspace / route, dependency_roots=dependencies)
        for record in records:
            task = tasks[record["job_id"]]
            result = executor.run(
                record["code"],
                task["checker"],
                label=record["job_id"],
                inputs=task.get("inputs"),
            )
            rows.append(
                {
                    "route": route,
                    "job_id": record["job_id"],
                    "published_accepted": record["accepted"],
                    "replayed_accepted": result["passed"],
                    "program_sha256": result["code_sha256"],
                    "checks_sha256": result["checks_sha256"],
                }
            )
    mismatches = [r for r in rows if r["published_accepted"] != r["replayed_accepted"]]
    return {
        "replayed_programs": len(rows),
        "accepted": {
            route: sum(r["replayed_accepted"] for r in rows if r["route"] == route)
            for route in courses
        },
        "mismatches": mismatches,
        "rows": rows,
        "model_calls": 0,
        "scope": "Complete default, withheld-source and required-artifact checks",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    result = verify(args.package, args.workspace, dependencies=args.dependency_root)
    with args.output.open("x") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
    if result["mismatches"]:
        raise SystemExit("Published qualification outcomes did not reproduce")


if __name__ == "__main__":
    main()
