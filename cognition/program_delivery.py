"""Collect source-bound JSON artifacts from accepted isolated work.

This adapter adds no model, execution, deployment or payment authority. A host
enrolls exact output names and independent semantic hashes before collection.
Completed deliveries replay from their immutable bundle without redoing work.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from cognition.outcome_worker import identity

VERSION = "nuria.program-delivery.v1"
SUFFIX = {
    "nuria.outcome-worker.v1": "-check",
    "nuria.experience-navigation.v3": "-host",
}
NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,100}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _read(directory, name, maximum):
    if not NAME.fullmatch(name):
        raise ValueError("Delivery files need simple enrolled names")
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ValueError("Delivery file exceeds its regular-file bound")
            data = stream.read(maximum + 1)
    finally:
        os.close(root)
    if len(data) > maximum:
        raise ValueError("Delivery file grew beyond its byte allowance")
    return data


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _files(contract):
    if type(contract) is not dict or set(contract) != {
        "job_id",
        "task_sha256",
        "files",
    }:
        raise ValueError("A host delivery enrollment is required")
    if not NAME.fullmatch(contract["job_id"]) or not SHA.fullmatch(
        contract["task_sha256"]
    ):
        raise ValueError("Delivery job and task identities are invalid")
    files = contract["files"]
    if type(files) is not list or not 1 <= len(files) <= 8:
        raise ValueError("Enroll one to eight JSON artifacts")
    names = set()
    for item in files:
        if (
            type(item) is not dict
            or set(item) != {"name", "semantic_sha256"}
            or not NAME.fullmatch(item["name"])
            or item["name"] in ("receipt.json", "program.py")
            or item["name"] in names
            or not SHA.fullmatch(item["semantic_sha256"])
        ):
            raise ValueError("Delivery file enrollment is invalid")
        names.add(item["name"])
    return files


def collect(store, task, enrollment, execution_root, delivery_root):
    files = _files(enrollment)
    if identity(task) != enrollment["task_sha256"]:
        raise ValueError(
            "Delivery enrollment belongs to another task or input snapshot"
        )
    journal = store.verify()
    records = {row["job_id"]: row for row in store.experiences()}
    record = records.get(enrollment["job_id"])
    if (
        record is None
        or record.get("accepted") is not True
        or record.get("source_sha256") != task["source_sha256"]
        or record.get("task_id") != task["task_id"]
        or record.get("prompt") != task["prompt"]
        or record.get("version") not in SUFFIX
    ):
        raise ValueError("A source-bound accepted work receipt is required")
    code = record["code"].encode()
    checked = record.get("checker") or {}
    if (
        checked.get("passed") is not True
        or checked.get("code_sha256") != hashlib.sha256(code).hexdigest()
        or checked.get("checks_sha256")
        != hashlib.sha256(task["checker"].encode()).hexdigest()
    ):
        raise ValueError("Host acceptance checked another program or task")
    binding = {
        "enrollment_sha256": identity(enrollment),
        "task_sha256": identity(task),
        "result_sha256": identity(record),
    }
    delivery_root = Path(delivery_root)
    delivery_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if delivery_root.is_symlink():
        raise ValueError("Delivery storage cannot be a symlink")
    target = delivery_root / identity(binding)
    if target.exists():
        receipt = json.loads(_read(target, "receipt.json", 32768))
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if receipt.get("binding") != binding or receipt.get(
            "receipt_sha256"
        ) != identity(body):
            raise ValueError("Retained delivery receipt changed")
        if {item["name"] for item in receipt["files"]} != {
            item["name"] for item in files
        }:
            raise ValueError("Retained artifact list changed")
        for item in receipt["files"]:
            data = _read(target, item["name"], 131072)
            expected = next(x for x in files if x["name"] == item["name"])
            if (
                hashlib.sha256(data).hexdigest() != item["sha256"]
                or identity(json.loads(data)) != expected["semantic_sha256"]
            ):
                raise ValueError("Retained delivery artifact changed")
        return {**receipt, "replayed": True}
    work = Path(execution_root) / (record["job_id"] + SUFFIX[record["version"]])
    if _read(work, "program.py", 32768) != code:
        raise ValueError("Execution directory contains another program")
    for name, text in task.get("inputs", {}).items():
        if _read(work / "inputs", name, 262144) != text.encode():
            raise ValueError("Execution input differs from the enrolled snapshot")
    payload, metadata = {}, []
    for item in files:
        data = _read(work, item["name"], 131072)
        semantic = identity(json.loads(data))
        if semantic != item["semantic_sha256"]:
            raise ValueError("Artifact differs from independent host acceptance")
        payload[item["name"]] = data
        metadata.append(
            {**item, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        )
    if sum(map(len, payload.values())) > 524288:
        raise ValueError("Delivery exceeds its total artifact byte allowance")
    receipt = {
        "schema": VERSION,
        "job_id": record["job_id"],
        "source_sha256": task["source_sha256"],
        "binding": binding,
        "program_sha256": checked["code_sha256"],
        "checker_sha256": checked["checks_sha256"],
        "journal_at_collection": journal,
        "files": metadata,
        "model_calls": 0,
        "program_executions": 0,
        "financial_activity": False,
    }
    receipt["receipt_sha256"] = identity(receipt)
    target.mkdir(
        mode=0o700
    )  # A partial prior delivery requires explicit reconciliation.
    for name, data in payload.items():
        _write(target / name, data)
    _write(
        target / "receipt.json",
        (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode(),
    )
    return {**receipt, "replayed": False}
