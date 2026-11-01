"""Reproduce every published recovery program without provider access."""

import argparse
import hashlib
import json
from pathlib import Path

from cognition.input_observations import inspect_enrollment
from cognition.outcome_worker import IsolatedPython, OutcomeStore
from scripts.package_experience_navigation import read_store
from scripts.run_delivered_course import prepare
from scripts.run_retained_work import acquire


def verify(package, workspace, *, dependencies=()):
    package, workspace = Path(package), Path(workspace)
    if workspace.exists():
        raise ValueError("Replay requires a new retained workspace")
    enrollment = json.loads((package / "enrollment.json").read_text())
    freeze = json.loads((package / "execution-source-before-models.json").read_text())
    if (
        hashlib.sha256((package / "enrollment.json").read_bytes()).hexdigest()
        != freeze["enrollment_sha256"]
    ):
        raise ValueError("Recovery enrollment changed")
    repository = Path(__file__).resolve().parents[1]
    for name, expected in freeze["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Runtime identity must be repository-relative")
        if hashlib.sha256((repository / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Use the prospectively frozen recovery runtime")
    tasks = {
        job["job_id"]: job["task"]
        for job in prepare(inspect_enrollment(enrollment))["jobs"]
    }
    courses = json.loads((package / "courses.json").read_text())
    if set(courses) != {"sol-low", "sol-medium", "astra-medium"}:
        raise ValueError("All three prospective configurations are required")
    rows = []
    for option, qualification in courses.items():
        records = qualification["course"]["work"]["jobs"]
        if len(records) != len(tasks) or {r["job_id"] for r in records} != set(tasks):
            raise ValueError("Every enrolled recovery must be retained")
        executor = IsolatedPython(workspace / option, dependency_roots=dependencies)
        for record in records:
            task = tasks[record["job_id"]]
            checked = executor.run(
                record["code"],
                task["checker"],
                label=record["job_id"],
                inputs=task.get("inputs"),
            )
            rows.append(
                {
                    "condition": option,
                    "job_id": record["job_id"],
                    "published_accepted": record["accepted"],
                    "replayed_accepted": checked["passed"],
                    "code_sha256": checked["code_sha256"],
                    "checks_sha256": checked["checks_sha256"],
                }
            )
    origin = OutcomeStore(workspace / "reconstructed-origin.sqlite")
    journal = json.loads((package / "qualification-origin-journal.json").read_text())
    records = courses["sol-low"]["course"]["work"]["jobs"]
    contracts = {
        row["event"]["job"]: row["event"]["contract_sha256"]
        for row in journal
        if row["event"]["event"] == "begin"
    }
    with origin.connect() as database:
        for row in records:
            database.execute(
                "INSERT INTO jobs VALUES(?,?,'finished',?)",
                (
                    row["job_id"],
                    contracts[row["job_id"]],
                    json.dumps(row, sort_keys=True),
                ),
            )
        for row in journal:
            database.execute(
                "INSERT INTO events VALUES(?,?,?,?)",
                (
                    row["seq"],
                    row["previous"],
                    row["sha256"],
                    json.dumps(row["event"], sort_keys=True),
                ),
            )
    read_store(origin.path)
    before = origin.path.read_bytes()
    skills = json.loads((package / "qualified-skills.json").read_text())
    profiles = json.loads((package / "qualified-profiles.json").read_text())
    receipts = {
        row["job_id"]: row
        for row in courses["sol-low"]["course"]["delivery"]["deliveries"]
    }
    if set(skills) != set(tasks) or set(profiles) != set(tasks):
        raise ValueError("Both qualified report profiles must be retained")
    for job_id in tasks:
        rebuilt = acquire(
            origin.path,
            tasks[job_id],
            job_id=job_id,
            profile=profiles[job_id],
            origin_delivery=receipts[job_id],
        )
        if rebuilt != skills[job_id]:
            raise ValueError("Qualified program differs from its accepted origin")
    if before != origin.path.read_bytes():
        raise ValueError("Skill acquisition altered the originating journal")
    return {
        "programs": len(rows),
        "rows": rows,
        "mismatches": [
            r for r in rows if r["published_accepted"] != r["replayed_accepted"]
        ],
        "model_calls": 0,
        "reconstructed_qualified_skills": len(skills),
        "origin_unchanged": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    result = verify(args.package, args.workspace, dependencies=args.dependency_root)
    args.workspace.mkdir(parents=True, exist_ok=True)
    with (args.workspace / "verification.json").open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
    if result["mismatches"]:
        raise SystemExit("Published recovery outcomes did not reproduce")


if __name__ == "__main__":
    main()
