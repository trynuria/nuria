"""Run enrolled learning work and deliver its independently checked artifacts.

Output expectations are compiled into the host checker before inference. A
program that returns a plausible answer but fails delivery remains a rejected
experience. Collection and identical-job replay make no extra model requests.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import uuid
from pathlib import Path

from cognition.experience_navigation import METHODS
from cognition.outcome_worker import identity
from cognition.program_delivery import _files
from scripts.collect_program_delivery import collect_course
from scripts.run_experience_course import run

VERSION = "nuria.delivered-course.v1"


def prepare(enrollment):
    jobs = enrollment["jobs"]
    if type(jobs) is not list or not 1 <= len(jobs) <= 32:
        raise ValueError("Enroll a finite delivered-work course")
    if len({job["job_id"] for job in jobs}) != len(jobs):
        raise ValueError("Delivered jobs cannot repeat")
    prepared = copy.deepcopy(enrollment)
    for job in prepared["jobs"]:
        task, delivery = job["task"], job["delivery"]
        files = _files(delivery)
        if delivery["job_id"] != job["job_id"] or delivery["task_sha256"] != identity(
            task
        ):
            raise ValueError("Delivery expectations are not bound to the original task")
        if any(item["name"] not in task["prompt"] for item in files):
            raise ValueError(
                "Artifact file names must be declared in the task specification"
            )
        # These checks stay outside every model request, like the original
        # checker. The original checks are retained and only strengthened.
        checks = (
            "\ndef _nuria_check_deliveries():\n"
            " import hashlib,json\n"
            " from pathlib import Path\n"
            " for item in " + repr(files) + ":\n"
            "  with Path(item['name']).open('rb') as stream: raw=stream.read(131073)\n"
            "  assert len(raw)<=131072,'Delivered artifact exceeds byte allowance'\n"
            "  value=json.loads(raw)\n"
            "  canonical=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()\n"
            "  assert hashlib.sha256(canonical).hexdigest()==item['semantic_sha256'],'Delivered artifact differs from host expectation'\n"
            "_nuria_check_deliveries()\n"
        )
        task["checker"] += checks
        if len(task["checker"].encode()) > 65536:
            raise ValueError("Combined host checks exceed the existing work allowance")
        delivery["task_sha256"] = identity(task)
    prepared["delivery_compilation"] = {
        "version": VERSION,
        "original_enrollment_sha256": identity(enrollment),
        "compiler_sha256": identity(Path(__file__).read_text()),
    }
    return prepared


def run_delivered(
    enrollment,
    workspace,
    config,
    *,
    dependencies=(),
    resource_root=None,
    method="navigation",
):
    prepared = prepare(enrollment)
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = (json.dumps(prepared, sort_keys=True, indent=2) + "\n").encode()
    path = workspace / ("enrollment-" + identity(prepared) + ".json")
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("Retained delivered-work enrollment changed")
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    work = run(
        prepared,
        workspace,
        config,
        dependencies=dependencies,
        resource_root=resource_root,
        method=method,
    )
    outcomes = {result["job_id"]: result for result in work["jobs"]}
    accepted = [
        job for job in prepared["jobs"] if outcomes[job["job_id"]]["accepted"] is True
    ]
    delivered = (
        collect_course({"jobs": accepted}, workspace, workspace / "deliveries")
        if accepted
        else {"deliveries": [], "replayed": 0}
    )
    return {
        "version": VERSION,
        "original_enrollment_sha256": identity(enrollment),
        "prepared_enrollment_sha256": identity(prepared),
        "work": work,
        "delivery": delivered,
        "rejected_jobs": [
            job for job, row in outcomes.items() if row["accepted"] is False
        ],
        "unknown_jobs": [
            job for job, row in outcomes.items() if row["accepted"] is None
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("enrollment", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--resource-root", required=True, type=Path)
    parser.add_argument("--dependency-root", action="append", default=[])
    parser.add_argument("--method", choices=METHODS, default="navigation")
    args = parser.parse_args()
    result = run_delivered(
        json.loads(args.enrollment.read_text()),
        args.workspace,
        args.config,
        resource_root=args.resource_root,
        dependencies=args.dependency_root,
        method=args.method,
    )
    path = args.workspace / ("delivered-course-" + uuid.uuid4().hex + ".json")
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
                "unknown_jobs": result["unknown_jobs"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
