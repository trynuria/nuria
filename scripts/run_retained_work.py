"""Execute an accepted program on newly enrolled data, without model inference.

Profiles and independent acceptance contracts are host authority. Source journal
hashes establish local consistency, not remote authentication or general program
correctness. Rejected transfer remains a recorded outcome.
"""

import argparse
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from cognition.outcome_worker import VERSION as OUTCOME_VERSION
from cognition.outcome_worker import IsolatedPython, OutcomeStore, identity
from cognition.program_delivery import collect
from scripts.package_experience_navigation import read_store
from scripts.run_delivered_course import prepare

VERSION = "nuria.retained-program.v1"
NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")


def _profile(profile):
    if (
        type(profile) is not dict
        or set(profile) != {"profile_id", "function_contract"}
        or type(profile["profile_id"]) is not str
        or not NAME.fullmatch(profile["profile_id"])
        or type(profile["function_contract"]) is not str
        or not 1 <= len(profile["function_contract"].encode()) <= 4096
    ):
        raise ValueError("An explicit bounded host program profile is required")


def _origin(path, job_id):
    records, journal = read_store(path)
    found = [row for row in records if row["job_id"] == job_id]
    if len(found) != 1:
        raise ValueError("Accepted origin is missing from its verified journal")
    record = found[0]
    checker = record.get("checker") or {}
    if (
        record.get("accepted") is not True
        or checker.get("passed") is not True
        or checker.get("code_sha256")
        != hashlib.sha256(record["code"].encode()).hexdigest()
    ):
        raise ValueError("Only independently accepted origin programs can transfer")
    return record, journal[-1]["sha256"]


def _delivery(receipt, record, task_sha256):
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    if (
        receipt.get("schema") != "nuria.program-delivery.v1"
        or receipt.get("receipt_sha256") != identity(body)
        or receipt.get("binding", {}).get("result_sha256") != identity(record)
        or receipt["binding"].get("task_sha256") != task_sha256
        or receipt.get("program_sha256") != record["checker"]["code_sha256"]
        or receipt.get("checker_sha256") != record["checker"]["checks_sha256"]
        or not receipt.get("files")
    ):
        raise ValueError("An exact completed origin delivery receipt is required")


def acquire(origin_store, origin_task, *, job_id, profile, origin_delivery):
    """Enroll exact accepted code; never alter the originating work history."""
    _profile(profile)
    origin_delivery = {k: v for k, v in origin_delivery.items() if k != "replayed"}
    record, head = _origin(origin_store, job_id)
    if (
        record["task_id"] != origin_task["task_id"]
        or record["source_sha256"] != origin_task["source_sha256"]
        or record["prompt"] != origin_task["prompt"]
        or record["checker"]["checks_sha256"]
        != hashlib.sha256(origin_task["checker"].encode()).hexdigest()
    ):
        raise ValueError("The origin task differs from its independent acceptance")
    _delivery(origin_delivery, record, identity(origin_task))
    skill = {
        "schema": VERSION,
        "profile": profile,
        "origin": {
            "job_id": job_id,
            "result_sha256": identity(record),
            "task_sha256": identity(origin_task),
            "source_sha256": record["source_sha256"],
            "program_sha256": record["checker"]["code_sha256"],
            "checker_sha256": record["checker"]["checks_sha256"],
            "journal_head_at_acquisition": head,
        },
        "origin_delivery": origin_delivery,
        "code": record["code"],
    }
    return {**skill, "skill_sha256": identity(skill)}


def execute(skill, origin_store, task, *, job_id, profile, executor, store):
    """Re-execute exact learned code; changed inputs need their own checked job."""
    _profile(profile)
    body = {k: v for k, v in skill.items() if k != "skill_sha256"}
    if (
        skill.get("schema") != VERSION
        or skill.get("skill_sha256") != identity(body)
        or skill.get("profile") != profile
        or hashlib.sha256(skill["code"].encode()).hexdigest()
        != skill["origin"]["program_sha256"]
    ):
        raise ValueError("Retained code or its enrolled profile changed")
    if (
        type(job_id) is not str
        or not NAME.fullmatch(job_id)
        or not HASH.fullmatch(task["source_sha256"])
        or not 1 <= len(task["task_id"].encode()) <= 128
        or len(task["prompt"].encode()) > 65536
        or len(task["checker"].encode()) > 65536
    ):
        raise ValueError("A bounded source-bound transfer task is required")
    record, _ = _origin(origin_store, skill["origin"]["job_id"])
    if (
        identity(record) != skill["origin"]["result_sha256"]
        or record["code"] != skill["code"]
    ):
        raise ValueError("The accepted originating program changed")
    _delivery(skill["origin_delivery"], record, skill["origin"]["task_sha256"])
    contract = {
        "schema": VERSION,
        "task": task,
        "skill_sha256": skill["skill_sha256"],
        "profile_sha256": identity(profile),
    }
    replay = store.begin(job_id, contract)
    if replay is not None:
        return {**replay, "replayed": True}
    checked = executor.run(
        skill["code"],
        task["checker"],
        label=job_id + "-check",
        inputs=task.get("inputs"),
    )
    result = {
        "version": OUTCOME_VERSION,
        "execution_route": VERSION,
        "job_id": job_id,
        "task_id": task["task_id"],
        "source_sha256": task["source_sha256"],
        "prompt": task["prompt"],
        "family": task.get("family"),
        "policy_sha256": identity({"schema": VERSION, "profile": profile}),
        "experience_ids": [skill["origin"]["job_id"]],
        "attempts": [],
        "code": skill["code"],
        "lesson": "Exact retained program; acceptance is limited to this host contract.",
        "checker": checked,
        "accepted": checked["passed"],
        "proposed_code_sha256": identity(skill["code"]),
        "proposal_error": None,
        "replayed": False,
        "skill_sha256": skill["skill_sha256"],
        "origin_result_sha256": skill["origin"]["result_sha256"],
        "model_calls": 0,
        "program_executions": 1,
    }
    store.finish(job_id, result)
    return result


def run(skill, origin_store, enrollment, workspace, *, profile, dependencies=()):
    prepared = prepare(enrollment)
    workspace = Path(workspace)
    executor = IsolatedPython(workspace / "executions", dependency_roots=dependencies)
    store = OutcomeStore(workspace / "outcomes.sqlite")
    rows, deliveries = [], []
    for job in prepared["jobs"]:
        result = execute(
            skill,
            origin_store,
            job["task"],
            job_id=job["job_id"],
            profile=profile,
            executor=executor,
            store=store,
        )
        rows.append(result)
        if result["accepted"]:
            deliveries.append(
                collect(
                    store,
                    job["task"],
                    job["delivery"],
                    workspace / "executions",
                    workspace / "deliveries",
                )
            )
    return {
        "schema": VERSION,
        "skill_sha256": skill["skill_sha256"],
        "enrollment_sha256": identity(prepared),
        "results": rows,
        "deliveries": deliveries,
        "journal": store.verify(),
        "model_calls": 0,
    }


def _write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", type=Path)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--origin-store", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--dependency-root", action="append", default=[])
    args = parser.parse_args()
    result = run(
        json.loads(args.skill.read_text()),
        args.origin_store,
        json.loads(args.enrollment.read_text()),
        args.workspace,
        profile=json.loads(args.profile.read_text()),
        dependencies=args.dependency_root,
    )
    receipt = args.workspace / ("retained-course-" + uuid.uuid4().hex + ".json")
    _write(receipt, result)
    print(
        json.dumps(
            {
                "accepted": sum(r["accepted"] for r in result["results"]),
                "jobs": len(result["results"]),
                "delivered": len(result["deliveries"]),
                "replayed": sum(r["replayed"] for r in result["results"]),
                "model_calls": 0,
                "receipt": str(receipt),
            }
        )
    )


if __name__ == "__main__":
    main()
