"""Reconstruct accepted origins and rerun every later-source transfer/control."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY))

from cognition.input_observations import inspect_enrollment  # noqa: E402
from cognition.outcome_worker import IsolatedPython, OutcomeStore  # noqa: E402
from scripts.package_experience_navigation import read_store  # noqa: E402
from scripts.run_delivered_course import prepare  # noqa: E402
from scripts.run_retained_work import acquire, run  # noqa: E402


def replay(package, workspace):
    if workspace.exists():
        raise ValueError("Independent transfer replay needs a new retained workspace")
    fixed = json.loads((package / "freeze-before-transfer.json").read_text())
    for name, expected in fixed["files"].items():
        path = package / name if name == "contracts.py" else REPOSITORY / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("The published transfer runtime changed")
    for name, key in [
        ("profiles.json", "profiles_sha256"),
        ("skills-before-transfer.json", "skills_sha256"),
        ("runtime-before-transfer.zip", "archive_sha256"),
    ]:
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != fixed[key]:
            raise ValueError("The acquired programs or profiles changed")
    execution = json.loads((package / "execution-freeze.json").read_text())
    raw = (package / "cases.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != execution["cases_sha256"]:
        raise ValueError("Fresh source contracts or control programs changed")
    cases = json.loads(raw)
    published = json.loads((package / "summary.json").read_text())
    profiles = json.loads((package / "profiles.json").read_text())
    skills = json.loads((package / "skills-before-transfer.json").read_text())
    exported = json.loads((package / "origin-journal.json").read_text())
    origin = OutcomeStore(workspace / "reconstructed-origin.sqlite")
    contracts = {
        row["event"]["job"]: row["event"]["contract_sha256"]
        for row in exported["journal"]
        if row["event"]["event"] == "begin"
    }
    with origin.connect() as database:
        for row in exported["records"]:
            database.execute(
                "INSERT INTO jobs VALUES(?,?,'finished',?)",
                (
                    row["job_id"],
                    contracts[row["job_id"]],
                    json.dumps(row, sort_keys=True),
                ),
            )
        for row in exported["journal"]:
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
    originating = json.loads(
        (package.parent / "project-work-03/enrollment.json").read_text()
    )
    tasks = {
        row["job_id"]: row["task"]
        for row in prepare(inspect_enrollment(originating))["jobs"]
    }
    before = origin.path.read_bytes()
    rows = []
    for case in cases:
        kind, skill = case["kind"], skills[case["kind"]]
        acquired = acquire(
            origin.path,
            tasks[skill["origin"]["job_id"]],
            job_id=skill["origin"]["job_id"],
            profile=profiles[kind],
            origin_delivery=skill["origin_delivery"],
        )
        if acquired != skill:
            raise ValueError("An acquired tool differs from its accepted origin")
        target = workspace / "retained" / kind
        first = run(
            skill, origin.path, case["enrollment"], target, profile=profiles[kind]
        )
        original = (target / "outcomes.sqlite").read_bytes()
        executions = len(list((target / "executions").iterdir()))
        again = run(
            skill, origin.path, case["enrollment"], target, profile=profiles[kind]
        )
        if (
            (target / "outcomes.sqlite").read_bytes() != original
            or len(list((target / "executions").iterdir())) != executions
            or not all(row["replayed"] for row in again["results"])
        ):
            raise ValueError("Identical retained work executed or changed its journal")
        rows.append(
            {
                "kind": kind,
                "method": "retained_accepted_program",
                "accepted": first["results"][0]["accepted"],
            }
        )
        task = prepare(case["enrollment"])["jobs"][0]["task"]
        for method in [
            "independent_conventional_exact_program",
            "stale_answer_diagnostic",
        ]:
            checked = IsolatedPython(workspace / method / kind).run(
                case["programs"][method],
                task["checker"],
                label=case["job_id"],
                inputs=task["inputs"],
            )
            rows.append({"kind": kind, "method": method, "accepted": checked["passed"]})
    if origin.path.read_bytes() != before:
        raise ValueError("The reconstructed origin history changed")
    expected = {(r["kind"], r["method"]): r["accepted"] for r in published["rows"]}
    if len(rows) != len(expected) or any(
        expected[(r["kind"], r["method"])] != r["accepted"] for r in rows
    ):
        raise ValueError("A published transfer or control did not reproduce")
    return {
        "replayed_programs": len(rows),
        "counts": {
            method: sum(row["accepted"] for row in rows if row["method"] == method)
            for method in published["counts"]
        },
        "origin_unchanged": True,
        "duplicate_executions_added": 0,
        "model_calls": 0,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(Path(__file__).resolve().parent, args.workspace)
    with args.output.open("x") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}))


if __name__ == "__main__":
    main()
