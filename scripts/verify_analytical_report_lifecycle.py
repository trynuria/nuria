"""Replay one retained model-origin report through explicit host enrollment.

The historical origin is already known. Two declared synthetic revisions test
fresh execution and empty data; they are not an unseen reasoning benchmark.
No model, encoder, neural simulation or external service is called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path

ARCHIVE_SHA256 = "b7885e31b85a24b48fe86a742db30ecb517b2192a91c65eea994cb8466450f0a"
MANIFEST_SHA256 = "1e3cbb8a66f0c5ddd64e1d0532b9ceda4852e603c8ee2f04f7345f2947c5c57f"
DOMAIN_SHA256 = "aa0c6c0206fefdcb5469be37bff51c1e24545ed1b44ed510de07995f1cf3dda0"
REGISTRY_SHA256 = "dc46fa6bb4c5e3391c9cf45a07fa7420b013a714de62264348c86ac99fa2d2ca"
MEMBERS = (
    "semantic/artifact.json",
    "semantic/memory-export.json",
    "semantic/memory.sqlite",
    "semantic/terminal-check.json",
)
EPISODE = "e81a9ee09f8d989b33369bbda17366676bb43c2a5799887c66017d81b0fcb78a"
REPORT_ID = "provider-outcomes-report-v1"
ROWS = [
    ["synthetic-a", "new-provider", 1, 2, "included_capacity", 0],
    ["synthetic-b", "new-provider", 2, None, "other", 10],
    ["synthetic-c", "Ω-report", None, -3, None, -1],
    ["synthetic-d", "Ω-report", 0, 0, "included_capacity", 0],
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def truth(rows):
    """Independent grouping from individual rows, without candidate SQL."""
    groups = {}
    for _, provider, delivered, utility, basis, paid in rows:
        counters = groups.setdefault(provider, [0] * 6)
        counters[0] += 1
        counters[1] += delivered == 1
        counters[2] += utility is not None and utility > 0
        counters[3] += utility is None
        counters[4] += basis == "included_capacity"
        counters[5] += paid is not None and paid > 0
    return [[provider, *groups[provider]] for provider in sorted(groups)]


def extract_origin(repository, output):
    root = repository / "research/hybrid-agent/work-trials"
    manifest_path = root / "manifest.json"
    archive_path = root / "retained-work-trials.zip"
    if sha(manifest_path) != MANIFEST_SHA256 or sha(archive_path) != ARCHIVE_SHA256:
        raise ValueError("Retained historical evidence identity changed")
    manifest = json.loads(manifest_path.read_bytes())
    output.mkdir(mode=0o700)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 64:
            raise ValueError("Duplicate or excessive historical evidence members")
        for name in MEMBERS:
            metadata = archive.getinfo(name)
            declared = manifest["files"][name]
            if (
                metadata.file_size != declared["bytes"]
                or metadata.file_size > 128 * 1024
                or stat.S_IFMT(metadata.external_attr >> 16) == stat.S_IFLNK
                or metadata.is_dir()
            ):
                raise ValueError("Historical member exceeds its declared format")
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != declared["sha256"]:
                raise ValueError("Historical member identity changed")
            destination = output / Path(name).name
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
    return output


def make_revision(repository, directory, rows, name):
    """New synthetic rows under exactly the historical schema/static knowledge."""
    original = repository / "research/hybrid-agent/work-snapshot/registered"
    source_manifest = json.loads((original / "registry.json").read_bytes())
    directory.mkdir(mode=0o700)
    database = directory / "work.sqlite"
    shutil.copyfile(original / "work.sqlite", database)
    os.chmod(database, 0o600)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DELETE FROM task_outcomes")
        connection.executemany(
            "INSERT INTO task_outcomes(task_id,provider,state,kind,delivery_passed,"
            "outcome_utility,settlement_basis,settled_micro_usd) "
            "VALUES(?,?,'synthetic','report-fixture',?,?,?,?)",
            rows,
        )
    entry = source_manifest["databases"]["work"]
    for document in entry["documents"].values():
        shutil.copyfile(original / document["path"], directory / document["path"])
    entry["database"]["sha256"] = sha(database)
    source_manifest["revision"] = (
        "sha256:"
        + hashlib.sha256(
            canonical({"name": name, "database": sha(database), "rows": rows}).encode()
        ).hexdigest()
    )
    registry = directory / "registry.json"
    write_new(registry, source_manifest)
    return registry


def invoke(repository, enrollment, registry, job_id, output):
    args = [
        sys.executable,
        "-m",
        "scripts.run_analytical_report",
        "--enrollment",
        str(enrollment),
        "--registry",
        str(registry),
        "--job-id",
        job_id,
    ]
    process = subprocess.run(
        args, cwd=repository, capture_output=True, text=True, timeout=15
    )
    if process.returncode:
        write_new(
            output,
            {"returncode": process.returncode, "stderr": process.stderr[-1500:]},
        )
        raise ValueError("Enrolled report CLI did not complete")
    result = json.loads(process.stdout)
    write_new(output, result)
    if (
        result["status"] != "accepted"
        or result["artifact"].get("neural_decision") is not None
        or result["artifact"].get("neural_credit") is not None
        or result["invocation_work"]["model_calls"] != 0
    ):
        raise ValueError("Lifecycle did not produce a checked model-free report")
    return result


def verify(repository, workspace):
    repository, workspace = Path(repository).resolve(), Path(workspace).resolve()
    if workspace.exists():
        raise ValueError("Lifecycle replay requires a new workspace")
    workspace.mkdir(mode=0o700, parents=True)
    snapshot_root = repository / "research/hybrid-agent/work-snapshot"
    domain_path = snapshot_root / "question-and-check.json"
    registry = snapshot_root / "registered/registry.json"
    if sha(domain_path) != DOMAIN_SHA256 or sha(registry) != REGISTRY_SHA256:
        raise ValueError("Original work domain or registry identity changed")
    registered_before = {
        file.name: sha(file)
        for file in (snapshot_root / "registered").iterdir()
        if file.is_file()
    }
    domain = json.loads(domain_path.read_bytes())
    origin = extract_origin(repository, workspace / "origin")
    artifact = json.loads((origin / "artifact.json").read_bytes())
    if artifact["episode_id"] != EPISODE:
        raise ValueError("Unexpected retained origin")
    before = {path.name: sha(path) for path in origin.iterdir()}
    plan = {
        "schema": "nuria.analytical-report-lifecycle.plan.v1",
        "historical_origin": "Previously accepted semantic work query; already known",
        "report_id": REPORT_ID,
        "synthetic_cases": [
            {"name": "changed-rows", "rows": ROWS, "expected": truth(ROWS)},
            {"name": "empty", "rows": [], "expected": []},
        ],
        "scope": "Declared deterministic source-revision fixtures; no unseen model benchmark",
        "model_calls": 0,
    }
    write_new(workspace / "prospective-plan.json", plan)
    sys.path.insert(0, str(repository))
    from cognition.analytical_enrollment import enroll_report

    enrollment_root = workspace / "enrolled"
    enroll_report(
        enrollment_root,
        memory_snapshot=origin / "memory.sqlite",
        episode_id=EPISODE,
        execution_receipt=artifact["selected_receipt"],
        registry=registry,
        db_id="work",
        report_id=REPORT_ID,
    )
    enrollment = enrollment_root / "enrollment.json"
    first = invoke(
        repository,
        enrollment,
        registry,
        "historical-report",
        workspace / "historical.json",
    )
    repeated = invoke(
        repository,
        enrollment,
        registry,
        "historical-report",
        workspace / "repeated.json",
    )
    if (
        first["artifact"]["execution"]["receipt"]["rows"] != domain["expected_rows"]
        or first["artifact_sha256"] != repeated["artifact_sha256"]
        or not repeated["replayed"]
        or repeated["invocation_work"]
        != {"workspace_query_calls": 0, "independent_check_calls": 0, "model_calls": 0}
    ):
        raise ValueError("Historical output or no-repeat behavior differs")
    synthetic = []
    for case in plan["synthetic_cases"]:
        fresh = make_revision(
            repository, workspace / case["name"], case["rows"], case["name"]
        )
        checked = invoke(
            repository,
            enrollment,
            fresh,
            "synthetic-" + case["name"],
            workspace / (case["name"] + "-result.json"),
        )
        execution = checked["artifact"]["execution"]
        if (
            execution["receipt"]["rows"] != case["expected"]
            or execution["source"]["db_sha256"] == artifact["source"]["db_sha256"]
        ):
            raise ValueError("Synthetic revision did not execute its fresh source")
        synthetic.append(
            {
                "name": case["name"],
                "accepted": True,
                "rows": case["expected"],
                "fresh_source": True,
            }
        )
    if before != {path.name: sha(path) for path in origin.iterdir()}:
        raise ValueError("Lifecycle changed retained historical evidence")
    if registered_before != {
        file.name: sha(file)
        for file in (snapshot_root / "registered").iterdir()
        if file.is_file()
    }:
        raise ValueError("Lifecycle changed the registered historical source")
    result = {
        "schema": "nuria.analytical-report-lifecycle.verification.v1",
        "passed": True,
        "archive_sha256": ARCHIVE_SHA256,
        "origin_episode": EPISODE,
        "origin_memory_sha256": before["memory.sqlite"],
        "historical_report_rows": domain["expected_rows"],
        "historical_accepted": True,
        "retained_artifact_replay_identical": True,
        "replay_workspace_queries": 0,
        "replay_independent_checks": 0,
        "synthetic_plan_sha256": sha(workspace / "prospective-plan.json"),
        "synthetic_cases": synthetic,
        "original_evidence_unchanged": True,
        "model_calls": 0,
        "neural_credit": None,
        "scope": "Host-enrolled exact report reuse from a known accepted model query; synthetic fresh rows and recovery fixtures, not a model-intelligence or neural-advantage test.",
    }
    write_new(workspace / "verification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.repository, args.workspace)
    if args.output:
        write_new(args.output, result)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
