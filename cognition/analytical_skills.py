"""Host-qualified executable reports over compatible immutable SQLite revisions.

The host assigns report identities and provides the independent checker. These
methods are not agent tools. Accepted originating SQL is re-executed, never an
old answer cache. Qualification establishes finite checked reuse, not general
query correctness, model learning or a neural advantage.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, select_only
from cognition.procedural_memory import ProceduralMemory, canonical, digest

VERSION = "nuria.analytical-skills.v1"
HASH = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, maximum):
    _require(
        type(value) is str and 0 < len(value.encode()) <= maximum,
        "Analytical skill text exceeds its bound",
    )
    return value


def _hash(value):
    _require(type(value) is str and HASH.fullmatch(value), "Full SHA256 required")
    return value


def _receipt_hash(receipt):
    body = {k: v for k, v in receipt.items() if k != "result_sha256"}
    return _workspace_digest(body)


def _workspace_digest(body):
    raw = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _workspace(registry, db_id):
    """Inspect exact DDL and static knowledge; no caller-supplied contract hash."""
    _text(db_id, 64)
    registry = Path(registry).resolve()
    workspace = AnalystWorkspace(registry)
    _require(db_id in workspace._manifest["databases"], "Unknown report database")
    source = workspace._manifest["databases"][db_id]
    schema = workspace.call(
        db_id,
        "query",
        {
            "sql": "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        },
    )
    _require(
        schema["status"] == "ok" and schema["complete"] is True,
        "Complete bounded schema inspection is required",
    )
    contract = {
        "db_id": db_id,
        "tool_schema": SCHEMA,
        "workspace_implementation_sha256": schema["runtime"]["implementation_sha256"],
        "definitions": schema["rows"],
        "documents": {
            name: record["sha256"] for name, record in source["documents"].items()
        },
    }
    identity = {
        "dataset_revision": workspace._manifest["revision"],
        "db_id": db_id,
        "db_sha256": source["database"]["sha256"],
        "registry_sha256": workspace.registry_sha256,
        "registry_file_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
    }
    return workspace, identity, contract, schema


class AnalyticalSkills:
    """One private writer; immutable skill origins and source-bound executions.

    The host's Python checker and report_id are trusted authority. This ledger
    verifies their identities and records their verdicts; it does not authenticate
    a remote checker, infer report semantics or prove an oracle is correct.
    """

    def __init__(self, path, *, source_id, checker_id, max_skills=32, max_events=1024):
        _require(type(max_skills) is int and 1 <= max_skills <= 128, "Invalid cap")
        _require(
            type(max_events) is int and 8 <= max_events <= 4096, "Invalid event cap"
        )
        self.binding = {
            "schema": VERSION,
            "source_id": _text(source_id, 256),
            "checker_id": _text(checker_id, 256),
            "max_skills": max_skills,
            "max_events": max_events,
        }
        self.binding_sha256 = digest(self.binding)
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require(not path.is_symlink(), "Skill database cannot be a symlink")
        if not path.exists():
            try:
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            except FileExistsError:
                pass
        lock = path.with_name(path.name + ".writer.lock")
        _require(not lock.is_symlink(), "Writer lock cannot be a symlink")
        self._fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        self.db = None
        try:
            _require(
                not path.stat().st_mode & 0o077
                and not os.fstat(self._fd).st_mode & 0o077,
                "Analytical skills require a private database file",
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
            self.db.execute("PRAGMA synchronous=FULL")
            names = {
                row[0]
                for row in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            _require(
                not names or names == {"skill_identity", "skill_events"},
                "Path contains another database",
            )
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS skill_identity(
                id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skill_events(
                seq INTEGER PRIMARY KEY,previous_hash TEXT NOT NULL,
                hash TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,item_id TEXT NOT NULL,
                payload TEXT NOT NULL,UNIQUE(kind,item_id));
            """)
            with self._transaction():
                identity = self.db.execute(
                    "SELECT payload FROM skill_identity WHERE id=1"
                ).fetchone()
                if identity is None:
                    _require(
                        self.db.execute("SELECT count(*) FROM skill_events").fetchone()[
                            0
                        ]
                        == 0,
                        "History has no binding",
                    )
                    self.db.execute(
                        "INSERT INTO skill_identity VALUES(1,?)",
                        (canonical(self.binding),),
                    )
                else:
                    _require(
                        identity[0] == canonical(self.binding), "Skill binding changed"
                    )
            self.verify()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _one(self, kind, item_id):
        row = self.db.execute(
            "SELECT payload FROM skill_events WHERE kind=? AND item_id=?",
            (kind, item_id),
        ).fetchone()
        return json.loads(row[0])["record"] if row else None

    def _append(self, kind, item_id, record):
        previous = self._one(kind, item_id)
        if previous is not None:
            _require(previous == record, "Changed duplicate skill evidence")
            return previous
        count = self.db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
        _require(count < self.binding["max_events"], "Skill event cap reached")
        row = self.db.execute(
            "SELECT seq,hash FROM skill_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, parent = row if row else (0, "0" * 64)
        raw = canonical(
            {
                "schema": VERSION,
                "binding_sha256": self.binding_sha256,
                "kind": kind,
                "item_id": item_id,
                "record": record,
            }
        )
        head = hashlib.sha256((parent + raw).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO skill_events VALUES(?,?,?,?,?,?)",
            (seq + 1, parent, head, kind, item_id, raw),
        )
        return copy.deepcopy(record)

    def verify(self):
        parent = "0" * 64
        count = 0
        for seq, previous, head, kind, item_id, raw in self.db.execute(
            "SELECT seq,previous_hash,hash,kind,item_id,payload "
            "FROM skill_events ORDER BY seq"
        ):
            count += 1
            body = json.loads(raw)
            _require(
                seq == count
                and previous == parent
                and canonical(body) == raw
                and body["schema"] == VERSION
                and body["binding_sha256"] == self.binding_sha256
                and body["kind"] == kind
                and body["item_id"] == item_id
                and hashlib.sha256((parent + raw).encode()).hexdigest() == head,
                "Skill history identity changed",
            )
            parent = head
        _require(count <= self.binding["max_events"], "Skill history exceeds cap")
        return {"events": count, "head_sha256": parent}

    def get(self, skill_id):
        skill = self._one("skill", _hash(skill_id))
        _require(skill is not None, "Unknown analytical skill")
        revoked = any(
            record["skill_id"] == skill_id and record["accepted"] is False
            for (raw,) in self.db.execute(
                "SELECT payload FROM skill_events WHERE kind='result'"
            )
            for record in [json.loads(raw)["record"]]
        )
        return {**skill, "active": not revoked}

    def state(self):
        return {
            **self.verify(),
            "binding": copy.deepcopy(self.binding),
            "skills": self.db.execute(
                "SELECT count(*) FROM skill_events WHERE kind='skill'"
            ).fetchone()[0],
            "pending": self.db.execute(
                "SELECT count(*) FROM skill_events a WHERE a.kind='reserve' "
                "AND NOT EXISTS(SELECT 1 FROM skill_events b "
                "WHERE b.kind='result' AND b.item_id=a.item_id)"
            ).fetchone()[0],
        }

    def _pending(self):
        return self.db.execute(
            "SELECT count(*) FROM skill_events a WHERE a.kind='reserve' "
            "AND NOT EXISTS(SELECT 1 FROM skill_events b "
            "WHERE b.kind='result' AND b.item_id=a.item_id)"
        ).fetchone()[0]

    def promote(
        self, memory, episode_id, registry, db_id, report_id, execution_receipt
    ):
        """Derive executable SQL from the accepted episode and its own receipt.

        Replaying SQL on the origin here would generate a different receipt and
        cannot replace the actual retained action evidence supplied by the host.
        """
        _require(
            isinstance(memory, ProceduralMemory), "Actual procedural memory required"
        )
        memory.verify()
        origin = memory.episode_state(_hash(episode_id), max_actions=256)
        proposal, settlement = origin["proposal"], origin["settlement"]
        _require(
            memory.binding["source_id"] == self.binding["source_id"]
            and memory.binding["tool_version"] == SCHEMA,
            "Origin source or tool binding differs",
        )
        _require(
            proposal is not None
            and settlement is not None
            and settlement.get("accepted") is True
            and settlement.get("checker_id") == self.binding["checker_id"]
            and settlement["proposal_sha256"] == proposal["proposal_sha256"],
            "A committed independently accepted origin is required",
        )
        sql = _text(proposal["final_sql"], 16384)
        _require(select_only(sql), "Report requires a read-only query")
        _require(type(execution_receipt) is dict, "Retained own execution required")
        receipt = copy.deepcopy(execution_receipt)
        _require(
            receipt.get("tool") == "query"
            and receipt.get("status") == "ok"
            and receipt.get("complete") is True
            and _receipt_hash(receipt) == receipt.get("result_sha256")
            and any(
                action["tool_name"] == "query"
                and action["ok"] is True
                and action["sql"] == sql
                and action["result_sha256"] == receipt["result_sha256"]
                for action in origin["actions"]
            ),
            "Origin query lacks its own authentic complete receipt",
        )
        workspace, source, contract, inspection = _workspace(registry, db_id)
        _require(
            receipt["source"]
            == {k: v for k, v in source.items() if k != "registry_file_sha256"}
            and receipt["request_sha256"]
            == _workspace_digest(
                {
                    "db_id": db_id,
                    "tool": "query",
                    "arguments": {"sql": sql},
                    "limits": workspace.limits,
                }
            )
            and receipt.get("runtime", {}).get("implementation_sha256")
            == contract["workspace_implementation_sha256"],
            "Origin receipt does not identify this exact source and query",
        )
        record = {
            "schema": VERSION,
            "report_id": _text(report_id, 256),
            "final_sql": sql,
            "source": source,
            "contract": contract,
            "contract_sha256": digest(contract),
            "schema_inspection_receipt": inspection,
            "origin": {
                "episode_id": episode_id,
                "memory_binding_sha256": memory.binding_sha256,
                "proposal_sha256": proposal["proposal_sha256"],
                "accepted_settlement": settlement,
                "execution_receipt": receipt,
            },
        }
        skill_id = digest(
            {k: v for k, v in record.items() if k != "schema_inspection_receipt"}
        )
        record["skill_id"] = skill_id
        with self._transaction():
            previous = self._one("skill", skill_id)
            if previous is not None:
                return previous
            count = self.db.execute(
                "SELECT count(*) FROM skill_events WHERE kind='skill'"
            ).fetchone()[0]
            _require(count < self.binding["max_skills"], "Analytical skill cap reached")
            events = self.db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
            _require(
                events + self._pending() < self.binding["max_events"],
                "Pending verdict reservations consume the event allowance",
            )
            return self._append("skill", skill_id, record)

    def execute(self, skill_id, *, job_id, registry, db_id, report_id):
        """Reserve, then execute fresh data; a complete query is still unchecked."""
        skill = self.get(skill_id)
        _require(skill["active"], "Analytical skill was revoked by a failed check")
        _require(report_id == skill["report_id"], "Host report identity changed")
        job_id = _text(job_id, 256)
        workspace, source, contract, inspection = _workspace(registry, db_id)
        _require(
            digest(contract) == skill["contract_sha256"],
            "Report schema, implementation or business knowledge changed",
        )
        reserve = {
            "job_id": job_id,
            "skill_id": skill_id,
            "report_id": report_id,
            "source": source,
            "query_sha256": digest(skill["final_sql"]),
            "contract_sha256": digest(contract),
        }
        with self._transaction():
            old = self._one("reserve", job_id)
            if old is not None:
                _require(old == reserve, "Changed duplicate analytical execution")
                result = self._one("execution", job_id)
                _require(
                    result is not None, "Interrupted reserved execution is unresolved"
                )
                return result
            remaining = (
                self.binding["max_events"]
                - self.db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
            )
            _require(
                remaining >= 3 + self._pending(),
                "No space for execution and all reserved verdicts",
            )
            self._append("reserve", job_id, reserve)
        receipt = workspace.call(db_id, "query", {"sql": skill["final_sql"]})
        record = {
            **reserve,
            "schema_inspection_receipt": inspection,
            "receipt": receipt,
            "status": "pending_check",
        }
        record["execution_sha256"] = digest(record)
        with self._transaction():
            return self._append("execution", job_id, record)

    def settle(self, job_id, *, verify_result):
        """Host-only independent verdict, durably bound to exact fresh execution.

        Exceptions and malformed checker replies leave the execution pending.
        Rejected answers revoke reuse; already committed verdicts are idempotent.
        The checker must be read-only and must not reenter this ledger.
        """
        _text(job_id, 256)
        _require(callable(verify_result), "Host result checker callable required")
        with self._transaction():
            previous = self._one("result", job_id)
            if previous is not None:
                return previous
            execution = self._one("execution", job_id)
            _require(execution is not None, "No retained execution for this job")
            reply = verify_result(copy.deepcopy(execution))
            _require(
                type(reply) is dict
                and set(reply) == {"accepted", "evidence_sha256", "checker_id"}
                and type(reply["accepted"]) is bool
                and reply["checker_id"] == self.binding["checker_id"],
                "Malformed or changed host checker identity",
            )
            _hash(reply["evidence_sha256"])
            _require(
                not reply["accepted"]
                or execution["receipt"]["status"] == "ok"
                and execution["receipt"]["complete"] is True,
                "An incomplete query cannot be accepted",
            )
            result = {
                "job_id": job_id,
                "skill_id": execution["skill_id"],
                "source": execution["source"],
                "execution_sha256": execution["execution_sha256"],
                **reply,
                "status": "accepted" if reply["accepted"] else "rejected",
            }
            result["result_sha256"] = digest(result)
            return self._append("result", job_id, result)
