"""Explicit enrollment of an accepted historical query as a checked report.

Only a closed, retained procedural-memory snapshot is accepted. Its original
bytes are never opened for writing or checkpointed. Enrollment copies the
snapshot, verifies the accepted origin and checks the known report profile.
This is host enrollment, not autonomous discovery of report semantics.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.analytical_report_worker import PROFILE, check_recent_work
from cognition.analytical_skills import AnalyticalSkills
from cognition.procedural_memory import VERSION as MEMORY_VERSION
from cognition.procedural_memory import ProceduralMemory, canonical, digest

VERSION = "nuria.analytical-enrollment.v1"
MAX_MEMORY_BYTES = 8 * 1024**2
MAX_JSON_BYTES = 1024**2
PATHS = {"skill_bank": "skills.sqlite", "worker_state": "worker"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, maximum=256):
    require(
        type(value) is str and 0 < len(value.encode()) <= maximum,
        "A bounded explicit host identity is required",
    )
    return value


def file_hash(path, maximum, *, private=False):
    path = Path(path)
    require(
        not path.is_symlink()
        and path.is_file()
        and path.stat().st_size <= maximum
        and (not private or not path.stat().st_mode & 0o077),
        "Expected a bounded regular input file with appropriate permissions",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_identity(path):
    path = Path(path)
    identity = file_hash(path, MAX_MEMORY_BYTES, private=True)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        require(
            not sidecar.is_symlink()
            and (
                not sidecar.exists()
                or sidecar.is_file()
                and sidecar.stat().st_size == 0
            ),
            "Enrollment requires a closed retained memory snapshot; journal state is present",
        )
    return identity


def write_record(path, record):
    raw = (canonical(record) + "\n").encode()
    require(len(raw) <= MAX_JSON_BYTES, "Enrollment record exceeds its bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_record(path):
    file_hash(path, MAX_JSON_BYTES, private=True)
    return json.loads(Path(path).read_bytes())


def memory_binding(path):
    """Read only metadata from an already checked closed snapshot."""
    connection = sqlite3.connect(
        "file:" + quote(str(Path(path).resolve()), safe="/") + "?mode=ro&immutable=1",
        uri=True,
    )
    started, steps = time.monotonic(), 0

    def progress():
        nonlocal steps
        steps += 100
        return int(steps >= 100000 or time.monotonic() - started >= 2)

    try:
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16384)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
        connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.set_progress_handler(progress, 100)
        rows = connection.execute("SELECT id,payload FROM memory_identity").fetchmany(2)
    finally:
        connection.close()
    require(len(rows) == 1 and rows[0][0] == 1, "Singular memory identity required")
    binding = json.loads(rows[0][1])
    require(
        type(binding) is dict
        and set(binding) == {"schema", "source_id", "model_id", "tool_version"}
        and binding["schema"] == MEMORY_VERSION
        and binding["tool_version"] == SCHEMA,
        "Unsupported memory enrollment identity",
    )
    for key in ("source_id", "model_id", "tool_version"):
        text(binding[key], 512 if key == "model_id" else 256)
    return binding


def implementation_hashes():
    root = Path(__file__).resolve().parents[1]
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "cognition/analytical_enrollment.py",
            "cognition/analytical_report_worker.py",
            "cognition/analytical_skills.py",
            "cognition/analyst_workspace.py",
            "cognition/procedural_memory.py",
        )
    }


def load_enrollment(path):
    """Validate a private host descriptor and its fixed relative state paths."""
    path = Path(path)
    root = path.parent
    require(
        not root.is_symlink() and root.is_dir() and not root.stat().st_mode & 0o077,
        "Private enrollment directory required",
    )
    descriptor = read_record(path)
    body = {
        key: value for key, value in descriptor.items() if key != "enrollment_sha256"
    }
    require(
        descriptor.get("schema") == VERSION
        and descriptor.get("profile") == PROFILE
        and descriptor.get("paths") == PATHS
        and descriptor.get("enrollment_sha256") == digest(body)
        and descriptor.get("implementations") == implementation_hashes(),
        "Enrollment identity, implementation or paths changed",
    )
    for name in ("source_id", "checker_id", "report_id", "db_id"):
        text(descriptor[name])
    require(
        type(descriptor["skill_id"]) is str
        and len(descriptor["skill_id"]) == 64
        and all(value in "0123456789abcdef" for value in descriptor["skill_id"]),
        "A full enrolled skill identity is required",
    )
    require(
        descriptor["neural_decision"] is None and descriptor["neural_credit"] is None,
        "Report enrollment cannot create neural authority",
    )
    root = root.resolve()
    bank, worker = root / PATHS["skill_bank"], root / PATHS["worker_state"]
    file_hash(bank, MAX_MEMORY_BYTES, private=True)
    require(not worker.is_symlink(), "Worker state cannot be a symlink")
    return {"descriptor": descriptor, "skill_bank": bank, "worker_state": worker}


def enroll_report(
    output,
    *,
    memory_snapshot,
    episode_id,
    execution_receipt,
    registry,
    db_id,
    report_id,
):
    """Promote a checked origin while preserving the original memory bytes.

    Repeating a completed, identical enrollment returns its descriptor without
    another promotion or query. An interrupted enrollment remains retained and
    refuses automatic retry. The host still chooses the report's meaning.
    """
    memory_snapshot, registry, output = map(Path, (memory_snapshot, registry, output))
    original_sha = snapshot_identity(memory_snapshot)
    require(
        type(execution_receipt) is dict
        and len(canonical(execution_receipt).encode()) <= MAX_JSON_BYTES,
        "Bounded retained execution receipt required",
    )
    text(episode_id, 64)
    require(
        len(episode_id) == 64
        and all(value in "0123456789abcdef" for value in episode_id),
        "Full originating episode identity required",
    )
    request = {
        "schema": VERSION,
        "memory_snapshot_sha256": original_sha,
        "episode_id": episode_id,
        "execution_receipt_sha256": digest(execution_receipt),
        "registry_file_sha256": file_hash(registry, 65536),
        "db_id": text(db_id, 64),
        "report_id": text(report_id),
        "profile": PROFILE,
        "implementations": implementation_hashes(),
    }
    require(not output.is_symlink(), "Enrollment output cannot be a symlink")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not output.stat().st_mode & 0o077, "Private enrollment output required")
    lock = output / "writer.lock"
    require(not lock.is_symlink(), "Enrollment lock cannot be a symlink")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    memory, bank = None, None
    try:
        require(not os.fstat(fd).st_mode & 0o077, "Private enrollment lock required")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_file = output / "request.json"
        if request_file.exists():
            require(
                read_record(request_file) == request, "Changed duplicate enrollment"
            )
            require(
                (output / "enrollment.json").exists(),
                "Interrupted enrollment is retained; automatic retry is disabled",
            )
            loaded = load_enrollment(output / "enrollment.json")
            require(
                snapshot_identity(memory_snapshot) == original_sha, "Origin changed"
            )
            return loaded["descriptor"]
        require(
            {value.name for value in output.iterdir()} == {"writer.lock"},
            "Enrollment output contains unrecognized existing work",
        )
        write_record(request_file, request)
        retained = output / "retained-memory.sqlite"
        working = output / "working-memory.sqlite"
        original_bytes = memory_snapshot.read_bytes()
        require(
            hashlib.sha256(original_bytes).hexdigest() == original_sha, "Origin changed"
        )
        for target in (retained, working):
            target_fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(target_fd, "wb") as stream:
                stream.write(original_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        require(
            snapshot_identity(memory_snapshot) == original_sha,
            "Origin changed during copy",
        )
        binding = memory_binding(retained)
        memory = ProceduralMemory(
            working,
            **{key: binding[key] for key in ("source_id", "model_id", "tool_version")},
        )
        origin = memory.episode_state(episode_id, max_actions=256)
        settlement = origin["settlement"]
        require(
            settlement is not None and settlement.get("accepted") is True,
            "An accepted host terminal is required",
        )
        checker_id = text(settlement["checker_id"])
        bank = AnalyticalSkills(
            output / PATHS["skill_bank"],
            source_id=binding["source_id"],
            checker_id=checker_id,
        )
        promoted = bank.promote(
            memory, episode_id, registry, db_id, report_id, execution_receipt
        )
        workspace = AnalystWorkspace(registry)
        check = check_recent_work(
            {
                "receipt": execution_receipt,
                "execution_sha256": digest(
                    {"episode_id": episode_id, "receipt": execution_receipt}
                ),
            },
            workspace,
            db_id,
        )
        write_record(output / "origin-check.json", check)
        require(
            check["accepted"] is True,
            "Origin does not satisfy the enrolled report profile",
        )
        require(
            snapshot_identity(memory_snapshot) == original_sha
            and file_hash(registry, 65536) == request["registry_file_sha256"],
            "Origin or source changed during enrollment",
        )
        descriptor = {
            "schema": VERSION,
            "profile": PROFILE,
            "source_id": binding["source_id"],
            "checker_id": checker_id,
            "report_id": report_id,
            "db_id": db_id,
            "skill_id": promoted["skill_id"],
            "bank_binding_sha256": bank.binding_sha256,
            "paths": dict(PATHS),
            "origin": {
                "memory_snapshot_sha256": original_sha,
                "memory_binding_sha256": memory.binding_sha256,
                "episode_id": episode_id,
                "proposal_sha256": origin["proposal"]["proposal_sha256"],
                "execution_receipt_sha256": execution_receipt["result_sha256"],
                "terminal_receipt_sha256": settlement["receipt_sha256"],
                "profile_check": check,
            },
            "implementations": request["implementations"],
            "neural_decision": None,
            "neural_credit": None,
        }
        descriptor["enrollment_sha256"] = digest(descriptor)
        write_record(output / "enrollment.json", descriptor)
        return descriptor
    finally:
        if bank is not None:
            bank.close()
        if memory is not None:
            memory.close()
        os.close(fd)
