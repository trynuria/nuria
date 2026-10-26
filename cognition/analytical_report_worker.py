"""Model-free host enrollment for a checked recurring report.

This adapter does not infer report meaning or grant model, neural or financial
authority. A host binds an existing skill to the reviewed report profile. Each
new result is checked against individual source rows, not the candidate SQL.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from cognition.analyst_workspace import AnalystWorkspace
from cognition.analytical_skills import AnalyticalSkills
from cognition.procedural_memory import canonical, digest

VERSION = "nuria.analytical-report-worker.v1"
PROFILE = "recent-work-provider-outcomes-v1"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, maximum=256):
    _require(
        type(value) is str and 0 < len(value.encode()) <= maximum,
        "An explicit bounded host identity is required",
    )
    return value


def _private_directory(path):
    path = Path(path)
    _require(not path.is_symlink(), "Worker state cannot be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require(
        path.is_dir() and not path.stat().st_mode & 0o077,
        "Private worker state required",
    )
    return path.resolve()


def _write(path, value):
    raw = (canonical(value) + "\n").encode()
    _require(len(raw) <= 2 * 1024**2, "Worker artifact exceeds its finite bound")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _read(path):
    _require(
        not path.is_symlink()
        and path.is_file()
        and not path.stat().st_mode & 0o077
        and path.stat().st_size <= 2 * 1024**2,
        "Retained worker record is not a private bounded regular file",
    )
    return json.loads(path.read_bytes())


def check_recent_work(execution, workspace, db_id):
    """Independently group raw rows under the enrolled report's exact meaning.

    The immutable database is the source, not external payment attestation.
    Delivery and positive utility never imply semantic acceptance.
    """
    entry = workspace._manifest["databases"][db_id]
    database = workspace._registered_file(entry["database"], 5 * 1024**2)
    source = {
        "dataset_revision": workspace._manifest["revision"],
        "db_id": db_id,
        "db_sha256": entry["database"]["sha256"],
        "registry_sha256": workspace.registry_sha256,
    }
    receipt = execution["receipt"]
    _require(receipt["source"] == source, "Report execution changed its exact source")
    groups, count, size, steps = {}, 0, 0, 0
    started = time.monotonic()

    def progress():
        nonlocal steps
        steps += 100
        return int(steps >= 1000000 or time.monotonic() - started >= 2)

    connection = sqlite3.connect(
        "file:" + quote(str(database), safe="/") + "?mode=ro&immutable=1", uri=True
    )
    try:
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.set_progress_handler(progress, 100)
        cursor = connection.execute(
            "SELECT provider,delivery_passed,outcome_utility,settlement_basis,"
            "settled_micro_usd FROM task_outcomes"
        )
        for provider, delivered, utility, basis, paid in cursor:
            count += 1
            size += len(canonical([provider, delivered, utility, basis, paid]).encode())
            _require(
                count <= 10000
                and size <= 2 * 1024**2
                and time.monotonic() - started < 2,
                "Independent report check exhausted its row, byte or time bound",
            )
            _require(
                provider is None or type(provider) is str,
                "Provider must be text or NULL",
            )
            _require(
                utility is None
                or type(utility) in (int, float)
                and math.isfinite(utility),
                "Utility must be finite numeric data or NULL",
            )
            _require(
                paid is None or type(paid) is int,
                "Payment units must be integers or NULL",
            )
            counters = groups.setdefault(provider, [0] * 6)
            _require(len(groups) <= 200, "Independent report group bound reached")
            counters[0] += 1
            counters[1] += delivered == 1
            counters[2] += utility is not None and utility > 0
            counters[3] += utility is None
            counters[4] += basis == "included_capacity"
            counters[5] += paid is not None and paid > 0
    finally:
        connection.close()
    workspace._registered_file(entry["database"], 5 * 1024**2)
    expected = [
        [key, *groups[key]]
        for key in sorted(groups, key=lambda value: (value is not None, value or ""))
    ]
    accepted = (
        receipt.get("status") == "ok"
        and receipt.get("complete") is True
        and len(receipt.get("columns", [])) == 7
        and receipt.get("rows") == expected
    )
    return {
        "accepted": accepted,
        "evidence_sha256": digest(
            {
                "profile": PROFILE,
                "execution_sha256": execution["execution_sha256"],
                "source": source,
                "source_rows": count,
                "expected_rows": expected,
            }
        ),
    }


class AnalyticalReportWorker:
    """One finite host-enrolled report, with durable no-retry job identities.

    Replays return retained artifacts without another query or checker call.
    An interrupted attempt or checker error remains pending. No fallback is
    enabled: the host must reconcile it or explicitly start a different workflow.
    """

    def __init__(
        self,
        state,
        bank,
        *,
        source_id,
        checker_id,
        report_id,
        db_id,
        skill_id=None,
        max_jobs=128,
    ):
        _require(
            isinstance(bank, AnalyticalSkills),
            "An enrolled analytical skill bank is required",
        )
        _require(
            type(max_jobs) is int and 1 <= max_jobs <= 128, "Invalid worker job cap"
        )
        _require(
            bank.binding["source_id"] == source_id
            and bank.binding["checker_id"] == checker_id,
            "Worker source or checker binding differs from the bank",
        )
        if skill_id is not None:
            _require(
                type(skill_id) is str
                and len(skill_id) == 64
                and all(value in "0123456789abcdef" for value in skill_id),
                "A full skill SHA256 is required",
            )
        self.bank = bank
        self.binding = {
            "schema": VERSION,
            "profile": PROFILE,
            "bank_binding_sha256": bank.binding_sha256,
            "source_id": _text(source_id),
            "checker_id": _text(checker_id),
            "report_id": _text(report_id),
            "db_id": _text(db_id, 64),
            "skill_id": skill_id,
            "max_jobs": max_jobs,
            "worker_implementation_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        }
        self.root = _private_directory(state)
        lock = self.root / "writer.lock"
        _require(not lock.is_symlink(), "Worker lock cannot be a symlink")
        self._fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _require(
                not os.fstat(self._fd).st_mode & 0o077, "Private worker lock required"
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = self.root / "binding.json"
            if identity.exists():
                _require(_read(identity) == self.binding, "Worker enrollment changed")
            else:
                _write(identity, self.binding)
            self.jobs = _private_directory(self.root / "jobs")
        except BaseException:
            self.close()
            raise

    def close(self):
        if getattr(self, "_fd", None) is not None:
            os.close(self._fd)
            self._fd = None

    @staticmethod
    def _envelope(record, *, replayed=False, query_calls=0, checker_calls=0):
        return {
            "status": record["status"],
            "artifact": record,
            "artifact_sha256": digest(record),
            "replayed": replayed,
            "invocation_work": {
                "workspace_query_calls": query_calls,
                "independent_check_calls": checker_calls,
                "model_calls": 0,
            },
        }

    def _validated_record(self, record, request):
        _require(
            record["request"] == request
            and record.get("neural_decision") is None
            and record.get("neural_credit") is None,
            "Retained result changed its job or authority",
        )
        if "execution" in record:
            _require(
                self.bank._one("execution", request["bank_job_id"])
                == record["execution"],
                "Worker execution differs from the skill journal",
            )
        if record["status"] in {"accepted", "rejected"}:
            _require(
                "execution" in record
                and self.bank._one("result", request["bank_job_id"])
                == record.get("verdict")
                and record["verdict"]["status"] == record["status"],
                "Worker verdict differs from the skill journal",
            )
        return record

    def run(self, job_id, registry):
        _require(self._fd is not None, "Worker is closed")
        _text(job_id)
        workspace = AnalystWorkspace(registry)
        db_id = self.binding["db_id"]
        _require(
            db_id in workspace._manifest["databases"],
            "Report database is not registered",
        )
        request = {
            "job_id": job_id,
            "bank_job_id": "report-worker:"
            + digest({"job_id": job_id, "binding": self.binding}),
            "binding_sha256": digest(self.binding),
            "registry_sha256": workspace.registry_sha256,
            "registry_file_sha256": hashlib.sha256(
                Path(registry).read_bytes()
            ).hexdigest(),
            "db_sha256": workspace._manifest["databases"][db_id]["database"]["sha256"],
        }
        directory = self.jobs / hashlib.sha256(job_id.encode()).hexdigest()
        if directory.exists():
            _require(
                not directory.is_symlink()
                and directory.resolve().is_relative_to(self.jobs),
                "A worker job directory cannot be a symlink or leave its state",
            )
            _require(
                _read(directory / "attempt.json") == request,
                "Changed duplicate worker job",
            )
            if (directory / "result.json").exists():
                saved = _read(directory / "result.json")
                return self._envelope(
                    self._validated_record(saved, request), replayed=True
                )
            return self._envelope(
                {
                    "schema": VERSION,
                    "request": request,
                    "status": "pending_check"
                    if (directory / "execution.json").exists()
                    else "pending_execution",
                    "neural_decision": None,
                    "neural_credit": None,
                },
                replayed=True,
            )
        _require(
            sum(value.is_dir() for value in self.jobs.iterdir())
            < self.binding["max_jobs"],
            "Worker job cap reached",
        )
        directory = _private_directory(directory)
        _write(directory / "attempt.json", request)
        record = {
            "schema": VERSION,
            "request": request,
            "profile": PROFILE,
            "neural_decision": None,
            "neural_credit": None,
        }
        skill_id = self.binding["skill_id"]
        if skill_id is None:
            record.update(
                status="unavailable",
                reason="No skill is enrolled; model fallback is disabled",
            )
            _write(directory / "result.json", record)
            return self._envelope(record)
        try:
            skill = self.bank.get(skill_id)
        except ValueError:
            record.update(status="unavailable", reason="The enrolled skill is absent")
            _write(directory / "result.json", record)
            return self._envelope(record)
        if not skill["active"]:
            record.update(status="unavailable", reason="The enrolled skill was revoked")
            _write(directory / "result.json", record)
            return self._envelope(record)
        documents = {
            key: value["sha256"]
            for key, value in workspace._manifest["databases"][db_id][
                "documents"
            ].items()
        }
        if (
            documents != skill["contract"]["documents"]
            or skill["report_id"] != self.binding["report_id"]
        ):
            record.update(
                status="incompatible",
                reason="Static knowledge or host report identity changed",
            )
            _write(directory / "result.json", record)
            return self._envelope(record)
        pending_before = self.bank.state()["pending"]
        old_execution = self.bank._one("execution", request["bank_job_id"])
        try:
            execution = self.bank.execute(
                skill_id,
                job_id=request["bank_job_id"],
                registry=registry,
                db_id=db_id,
                report_id=self.binding["report_id"],
            )
        except Exception as error:
            pending = self.bank.state()["pending"] > pending_before
            incompatible = (
                isinstance(error, ValueError)
                and str(error)
                == "Report schema, implementation or business knowledge changed"
            )
            record.update(
                status="pending_execution"
                if pending
                else "incompatible"
                if incompatible
                else "refused",
                error_type=type(error).__name__,
            )
            _write(directory / "result.json", record)
            return self._envelope(record, query_calls=None)
        _write(directory / "execution.json", execution)
        checker_calls = 0

        def checker(value):
            nonlocal checker_calls
            checker_calls += 1
            return {
                **check_recent_work(value, workspace, db_id),
                "checker_id": self.binding["checker_id"],
            }

        try:
            verdict = self.bank.settle(request["bank_job_id"], verify_result=checker)
        except Exception as error:
            record.update(
                status="pending_check",
                execution=execution,
                error_type=type(error).__name__,
            )
        else:
            record.update(
                status=verdict["status"], execution=execution, verdict=verdict
            )
        _write(directory / "result.json", record)
        return self._envelope(
            record,
            query_calls=1 if old_execution is not None else 2,
            checker_calls=checker_calls,
        )
