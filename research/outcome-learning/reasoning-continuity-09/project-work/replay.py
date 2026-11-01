"""Verify actual program origins and replay four checked deliveries without APIs."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from cognition.outcome_worker import IsolatedPython, identity  # noqa: E402
from scripts.run_retained_work import _delivery  # noqa: E402


def verify(package, workspace):
    history = json.loads((package / "operational-history.json").read_text())
    by_job = {row["job_id"]: row for row in history["records"]}
    previous, begun, outcomes = "0" * 64, set(), set()
    for number, item in enumerate(history["journal"], 1):
        event = item["event"]
        if (
            item["seq"] != number
            or item["previous"] != previous
            or item["sha256"] != identity({"previous": previous, "event": event})
        ):
            raise ValueError("Operational outcome journal changed")
        previous = item["sha256"]
        if event["event"] == "begin":
            begun.add(event["job"])
        elif event["event"] == "outcome":
            if event["job"] not in begun or event["result_sha256"] != identity(
                by_job[event["job"]]
            ):
                raise ValueError("Operational result differs from its journal")
            outcomes.add(event["job"])
        else:
            raise ValueError("Unknown operational journal event")
    if outcomes != set(by_job) or len(by_job) != 4:
        raise ValueError("Retain all four operational work outcomes")
    skill = json.loads((package / "acquired-accounting-skill.json").read_text())
    if skill["skill_sha256"] != identity(
        {k: v for k, v in skill.items() if k != "skill_sha256"}
    ):
        raise ValueError("Acquired program changed")
    origin = by_job[skill["origin"]["job_id"]]
    original = json.loads((package / "compiled-origin-enrollment.json").read_text())
    task = next(x["task"] for x in original["jobs"] if x["job_id"] == origin["job_id"])
    _delivery(skill["origin_delivery"], origin, identity(task))
    if skill["code"] != origin["code"] or skill["origin"]["result_sha256"] != identity(
        origin
    ):
        raise ValueError("Retained tool differs from its accepted origin")
    if workspace.exists():
        raise ValueError("Replay needs a new retained workspace")
    executor = IsolatedPython(workspace)
    rows = []
    later = json.loads((package / "compiled-later-enrollment.json").read_text())
    for job in original["jobs"] + later["jobs"]:
        record, task = by_job[job["job_id"]], job["task"]
        if (
            record["accepted"] is not True
            or record["source_sha256"] != task["source_sha256"]
            or record["checker"]["code_sha256"]
            != hashlib.sha256(record["code"].encode()).hexdigest()
            or record["checker"]["checks_sha256"]
            != hashlib.sha256(task["checker"].encode()).hexdigest()
        ):
            raise ValueError("Delivery is not bound to its accepted source and code")
        checked = executor.run(
            record["code"],
            task["checker"],
            label=job["job_id"],
            inputs=task.get("inputs"),
        )
        rows.append({"job_id": job["job_id"], "checker": checked})
    report = {
        "programs": rows,
        "accepted": sum(x["checker"]["passed"] for x in rows),
        "expected": 4,
        "model_calls": 0,
        "original_histories_changed": False,
        "scope": "Two actual origin deliveries, one later actual ledger and one authored price-sensitivity scenario; no model or neural superiority claim.",
    }
    with (workspace / "report.json").open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    result = verify(Path(__file__).resolve().parent, args.workspace)
    print(
        json.dumps({k: v for k, v in result.items() if k != "programs"}, sort_keys=True)
    )
    if result["accepted"] != result["expected"]:
        raise SystemExit("Native project work differs from its accepted record")
