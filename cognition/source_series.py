"""Finite host enrollment for evolving, individually immutable source snapshots.

The host owns revision admission, runtime factories and independent checking.
Nothing here is a model tool, a mutable database exemption or an inference service.
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

from cognition import analyst_workspace, grounded_agent, procedural_memory
from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory, canonical, digest
from scripts.ask_grounded_analyst import _complete_own_query

VERSION = "nuria.source-series.v1"
HASH = re.compile(r"[0-9a-f]{64}\Z")
MAX_EVENTS = 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_hash(value):
    require(type(value) is str and HASH.fullmatch(value), "A full SHA256 is required")
    return value


def bounded_text(value, maximum):
    require(
        type(value) is str and 0 < len(value.encode()) <= maximum,
        "Text exceeds its declared bound",
    )
    return value


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "Integer exceeds its bound")
    return value


def inspect_revision(registry, *, revision_varying_documents=None):
    """Observe the exact snapshot, keeping file and canonical hashes distinct."""
    registry = Path(registry).resolve()
    workspace = AnalystWorkspace(registry)
    varying = copy.deepcopy(
        {} if revision_varying_documents is None else revision_varying_documents
    )
    require(type(varying) is dict, "Provenance enrollment must name its database")
    for db_id, sections in varying.items():
        require(
            db_id in workspace._manifest["databases"], "Unknown provenance database"
        )
        require(
            type(sections) is list and sections and sections == sorted(set(sections)),
            "Provenance sections must be distinct and ordered",
        )
        for section in sections:
            bounded_text(section, 64)
            require(
                section not in {"schema", "columns", "business"},
                "Core knowledge cannot vary as provenance",
            )
            require(
                section in workspace._manifest["databases"][db_id]["documents"],
                "Provenance section is not supplied",
            )
    structures = {}
    for db_id, record in workspace._manifest["databases"].items():
        receipt = workspace.call(
            db_id,
            "query",
            {
                "sql": "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
            },
        )
        require(
            receipt["status"] == "ok" and receipt["complete"] is True,
            "Complete bounded schema inspection is required",
        )
        structures[db_id] = {
            "definitions": receipt["rows"],
            "documents": {
                name: {"role": "revision-varying-provenance"}
                if name in varying.get(db_id, [])
                else {"role": "static-knowledge", "sha256": item["sha256"]}
                for name, item in record["documents"].items()
            },
        }
    return {
        "registry_path": str(registry),
        "registry_file_sha256": file_sha(registry),
        "canonical_registry_sha256": workspace.registry_sha256,
        "dataset_revision": workspace._manifest["revision"],
        "databases": copy.deepcopy(workspace._manifest["databases"]),
        "schema_contract_sha256": digest(structures),
        "revision_varying_documents": varying,
    }


def implementation_identity():
    root = Path(analyst_workspace.__file__).parent
    return {
        "source_series.py": file_sha(__file__),
        **{
            Path(module.__file__).name: file_sha(module.__file__)
            for module in (analyst_workspace, grounded_agent, procedural_memory)
        },
        **{
            name: file_sha(root / name)
            for name in (
                "semantic_neural.py",
                "semantic_encoder.py",
                "hybrid_neural.py",
                "brain.py",
                "task_brain.py",
            )
        },
        "ask_grounded_analyst.py": file_sha(
            root.parent / "scripts/ask_grounded_analyst.py"
        ),
    }


def enrollment(
    registry,
    *,
    source_name,
    model_id,
    encoder_binding_sha256,
    checker_id,
    checker_implementation_sha256,
    maximum_jobs=128,
    maximum_pending=64,
    maximum_completion_tokens=768000,
    revision_varying_documents=None,
):
    """Prepare a stable contract; no model or encoding request is made."""
    observed = inspect_revision(
        registry, revision_varying_documents=revision_varying_documents
    )
    return {
        "schema": VERSION,
        "source_name": bounded_text(source_name, 256),
        "schema_contract_sha256": observed["schema_contract_sha256"],
        "revision_varying_documents": observed["revision_varying_documents"],
        "model_id": bounded_text(model_id, 512),
        "tool_version": SCHEMA,
        "encoder_binding_sha256": checked_hash(encoder_binding_sha256),
        "checker_id": bounded_text(checker_id, 256),
        "checker_implementation_sha256": checked_hash(checker_implementation_sha256),
        "implementation": implementation_identity(),
        "maximum_jobs": integer(maximum_jobs, 1, 128),
        "maximum_pending": integer(maximum_pending, 1, 64),
        "maximum_completion_tokens": integer(maximum_completion_tokens, 1, 768000),
        "maximum_cpu_encodes": integer(maximum_jobs, 1, 128),
        "mutation_policy": "immutable snapshots; identical schema and knowledge; ordered host admission",
    }


class SourceSeriesHost:
    """One private writer; exact snapshot receipts under one stable contract.

    Failed/ambiguous attempts remain reserved. A journaled verdict may be replayed
    through existing idempotent memory and neural settlement after interruption.
    The host supplies the checker; its identity is an enrollment, not attestation.
    """

    def __init__(self, directory, contract):
        self._contract = copy.deepcopy(contract)
        require(
            self._contract.get("schema") == VERSION
            and self._contract.get("tool_version") == SCHEMA
            and self._contract.get("implementation") == implementation_identity(),
            "Source-series implementation or tool contract changed",
        )
        integer(self._contract["maximum_jobs"], 1, 128)
        integer(self._contract["maximum_pending"], 1, 64)
        integer(self._contract["maximum_completion_tokens"], 1, 768000)
        require(
            self._contract["maximum_cpu_encodes"] == self._contract["maximum_jobs"],
            "Encoding authority differs from finite job enrollment",
        )
        self._contract_sha256 = digest(self._contract)
        self.directory = Path(directory)
        require(
            not self.directory.is_symlink(), "Series needs a real private directory"
        )
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        require(
            self.directory.stat().st_mode & 0o077 == 0,
            "Series directory must be private",
        )
        path = self.directory / "series.sqlite"
        require(not path.is_symlink(), "Series database must be regular")
        fresh = not path.exists()
        if fresh:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        require(
            path.is_file() and path.stat().st_mode & 0o077 == 0,
            "Series database must be private",
        )
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.lock = None
        try:
            tables = {
                r[0]
                for r in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if fresh:
                require(not tables, "Fresh series has unrelated data")
            else:
                require(
                    tables == {"identity", "events"},
                    "Existing series is incomplete or unrelated",
                )
                row = self.db.execute(
                    "SELECT payload FROM identity WHERE id=1"
                ).fetchone()
                require(
                    row is not None and row[0] == canonical(self._contract),
                    "Source-series enrollment changed",
                )
            lock_path = self.directory / "writer.lock"
            require(not lock_path.is_symlink(), "Writer lock must be regular")
            self.lock = lock_path.open("a+b")
            os.chmod(lock_path, 0o600)
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if fresh:
                self.db.executescript(
                    "CREATE TABLE identity(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);"
                    "CREATE TABLE events(seq INTEGER PRIMARY KEY,previous TEXT NOT NULL,hash TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);"
                )
                self.db.execute(
                    "INSERT INTO identity VALUES(1,?)", (canonical(self._contract),)
                )
            self._replay()
        except BaseException:
            self.close()
            raise

    @property
    def contract(self):
        return copy.deepcopy(self._contract)

    @property
    def source_id(self):
        return "sha256:" + self._contract_sha256

    def _check_identity(self):
        require(
            digest(self._contract) == self._contract_sha256
            and self._contract["implementation"] == implementation_identity(),
            "Series enrollment changed after opening",
        )

    def close(self):
        if getattr(self, "db", None) is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None

    @contextmanager
    def transaction(self):
        require(self.db is not None and self.lock is not None, "Series is closed")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _replay(self):
        self._check_identity()
        rows = self.db.execute(
            "SELECT seq,previous,hash,payload FROM events ORDER BY seq"
        ).fetchall()
        require(len(rows) <= MAX_EVENTS, "Source-series event bound reached")
        state = {"revisions": {}, "jobs": {}, "head_revision": None, "head": "0" * 64}
        for expected, (seq, previous, hashed, raw) in enumerate(rows, 1):
            require(
                seq == expected
                and previous == state["head"]
                and hashlib.sha256((previous + raw).encode()).hexdigest() == hashed,
                "Source-series journal changed",
            )
            event = json.loads(raw)
            require(
                type(event) is dict
                and set(event) == {"contract_sha256", "kind", "record"}
                and canonical(event) == raw
                and len(raw.encode()) <= 128 * 1024
                and event["contract_sha256"] == self._contract_sha256,
                "Event belongs to another contract",
            )
            kind, value = event["kind"], event["record"]
            require(type(value) is dict, "Event record is malformed")
            if kind == "revision_admitted":
                require(
                    set(value)
                    == {
                        "parent_revision_id",
                        "watermark",
                        "evidence_sha256",
                        "snapshot",
                        "revision_id",
                    },
                    "Revision record changed",
                )
                checked_hash(value["revision_id"])
                checked_hash(value["evidence_sha256"])
                integer(value["watermark"], 0, 2**63 - 1)
                require(
                    digest({k: v for k, v in value.items() if k != "revision_id"})
                    == value["revision_id"],
                    "Revision digest changed",
                )
                snapshot = value["snapshot"]
                require(
                    type(snapshot) is dict
                    and set(snapshot)
                    == {
                        "registry_path",
                        "registry_file_sha256",
                        "canonical_registry_sha256",
                        "dataset_revision",
                        "databases",
                        "schema_contract_sha256",
                        "revision_varying_documents",
                    },
                    "Snapshot descriptor changed",
                )
                for key in (
                    "registry_file_sha256",
                    "canonical_registry_sha256",
                    "schema_contract_sha256",
                ):
                    checked_hash(snapshot[key])
                require(
                    snapshot["schema_contract_sha256"]
                    == self._contract["schema_contract_sha256"]
                    and snapshot["revision_varying_documents"]
                    == self._contract["revision_varying_documents"],
                    "Snapshot compatibility contract changed",
                )
                require(
                    Path(bounded_text(snapshot["registry_path"], 4096)).is_absolute()
                    and type(snapshot["databases"]) is dict
                    and 1 <= len(snapshot["databases"]) <= 32,
                    "Snapshot location or database set changed",
                )
                require(
                    type(snapshot["dataset_revision"]) is str
                    and re.fullmatch(
                        r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})",
                        snapshot["dataset_revision"],
                    ),
                    "Dataset revision changed",
                )
                if state["head_revision"] is not None:
                    integer(
                        value["watermark"],
                        state["revisions"][state["head_revision"]]["watermark"] + 1,
                        2**63 - 1,
                    )
                require(
                    value["parent_revision_id"] == state["head_revision"]
                    and value["revision_id"] not in state["revisions"]
                    and len(state["revisions"]) < 128,
                    "Revision lineage changed",
                )
                state["revisions"][value["revision_id"]] = value
                state["head_revision"] = value["revision_id"]
            elif kind == "job_reserved":
                require(
                    set(value)
                    == {
                        "job_id",
                        "revision_id",
                        "canonical_registry_sha256",
                        "registry_file_sha256",
                        "question",
                        "db_id",
                        "completion_limit",
                        "cpu_encodes",
                        "seed",
                        "context_sha256",
                    },
                    "Reservation shape changed",
                )
                for key, maximum in (
                    ("job_id", 256),
                    ("question", 5000),
                    ("db_id", 64),
                ):
                    bounded_text(value[key], maximum)
                integer(value["completion_limit"], 1, 6000)
                integer(value["cpu_encodes"], 1, 1)
                integer(value["seed"], 0, 2**32 - 25)
                require(
                    digest({k: v for k, v in value.items() if k != "context_sha256"})
                    == value["context_sha256"],
                    "Job context changed",
                )
                require(
                    value["job_id"] not in state["jobs"]
                    and value["revision_id"] == state["head_revision"]
                    and value["revision_id"] is not None,
                    "Job origin changed",
                )
                snapshot = state["revisions"][value["revision_id"]]["snapshot"]
                require(
                    value["canonical_registry_sha256"]
                    == snapshot["canonical_registry_sha256"]
                    and value["registry_file_sha256"]
                    == snapshot["registry_file_sha256"]
                    and value["db_id"] in snapshot["databases"],
                    "Reserved snapshot identity changed",
                )
                require(
                    not any(
                        j["phase"] in {"running", "uncertain"}
                        for j in state["jobs"].values()
                    ),
                    "New authority followed uncertain work",
                )
                state["jobs"][value["job_id"]] = {"origin": value, "phase": "reserved"}
            else:
                require(
                    value["job_id"] in state["jobs"], "Event lacks its reserved job"
                )
                job = state["jobs"][value["job_id"]]
                allowed = {
                    "run_started": {"reserved"},
                    "run_uncertain": {"running"},
                    "run_closed": {"running"},
                    "terminal_checked": {"closed"},
                    "terminal_settled": {"checked"},
                }
                require(
                    kind in allowed and job["phase"] in allowed[kind],
                    "Invalid series lifecycle transition",
                )
                shapes = {
                    "run_started": {"job_id", "context_sha256"},
                    "run_uncertain": {"job_id", "error_type"},
                    "run_closed": {
                        "job_id",
                        "artifact_sha256",
                        "completion_tokens",
                        "cpu_encodes",
                    },
                    "terminal_checked": {
                        "job_id",
                        "revision_id",
                        "proposal_sha256",
                        "artifact_sha256",
                        "accepted",
                        "checker_id",
                        "evidence_sha256",
                    },
                    "terminal_settled": {
                        "job_id",
                        "revision_id",
                        "terminal",
                        "neural_credit",
                    },
                }
                require(set(value) == shapes[kind], "Lifecycle record shape changed")
                if kind == "run_started":
                    require(
                        value["context_sha256"] == job["origin"]["context_sha256"]
                        and not any(
                            j["phase"] in {"running", "uncertain"}
                            for j in state["jobs"].values()
                        ),
                        "Started work changed context or exceeded serialized authority",
                    )
                elif kind == "run_uncertain":
                    bounded_text(value["error_type"], 256)
                elif kind == "run_closed":
                    checked_hash(value["artifact_sha256"])
                    integer(
                        value["completion_tokens"], 0, job["origin"]["completion_limit"]
                    )
                    integer(value["cpu_encodes"], 1, 1)
                elif kind == "terminal_checked":
                    checked_hash(value["proposal_sha256"])
                    checked_hash(value["evidence_sha256"])
                    require(
                        value["artifact_sha256"] == job["run_closed"]["artifact_sha256"]
                        and value["revision_id"] == job["origin"]["revision_id"]
                        and type(value["accepted"]) is bool
                        and value["checker_id"] == self._contract["checker_id"],
                        "Checked verdict changed origin",
                    )
                elif kind == "terminal_settled":
                    terminal, credit, verdict = (
                        value["terminal"],
                        value["neural_credit"],
                        job["terminal_checked"],
                    )
                    require(
                        type(terminal) is dict
                        and type(credit) is dict
                        and value["revision_id"] == job["origin"]["revision_id"],
                        "Settlement changed origin",
                    )
                    checked_hash(terminal["receipt_sha256"])
                    checked_hash(terminal["episode_id"])
                    require(
                        terminal["origin_sha256"] == terminal["episode_id"]
                        and type(terminal["accepted"]) is bool
                        and type(credit["accepted"]) is bool,
                        "Settlement receipt types or memory origin changed",
                    )
                    require(
                        digest(
                            {k: v for k, v in terminal.items() if k != "receipt_sha256"}
                        )
                        == terminal["receipt_sha256"]
                        and all(
                            terminal[k] == verdict[k]
                            for k in (
                                "accepted",
                                "checker_id",
                                "proposal_sha256",
                                "evidence_sha256",
                            )
                        )
                        and credit["job_id"] == value["job_id"]
                        and credit["accepted"] == verdict["accepted"]
                        and credit["evidence_sha256"] == terminal["receipt_sha256"],
                        "Settlement differs from original qualified evidence",
                    )
                job["phase"] = {
                    "run_started": "running",
                    "run_uncertain": "uncertain",
                    "run_closed": "closed",
                    "terminal_checked": "checked",
                    "terminal_settled": "settled",
                }[kind]
                job[kind] = value
            require(
                len(state["jobs"]) <= self._contract["maximum_jobs"]
                and sum(j["phase"] != "settled" for j in state["jobs"].values())
                <= self._contract["maximum_pending"],
                "Replayed job authority exceeds enrollment",
            )
            tokens = sum(
                j["run_closed"]["completion_tokens"]
                if "run_closed" in j
                else j["origin"]["completion_limit"]
                for j in state["jobs"].values()
            )
            require(
                tokens <= self._contract["maximum_completion_tokens"]
                and len(state["jobs"]) <= self._contract["maximum_cpu_encodes"],
                "Replayed inference authority exceeds enrollment",
            )
            state["head"] = hashed
        return state

    def _append(self, kind, value):
        state = self._replay()
        count = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
        require(count < MAX_EVENTS, "Source-series event bound reached")
        raw = canonical(
            {"contract_sha256": self._contract_sha256, "kind": kind, "record": value}
        )
        require(
            len(raw.encode()) <= 128 * 1024, "Source-series event exceeds its bound"
        )
        hashed = hashlib.sha256((state["head"] + raw).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?)",
            (count + 1, state["head"], hashed, raw),
        )

    def state(self):
        return copy.deepcopy(self._replay())

    def admit_revision(
        self, registry, *, parent_revision_id, watermark, evidence_sha256
    ):
        observed = inspect_revision(
            registry,
            revision_varying_documents=self._contract["revision_varying_documents"],
        )
        require(
            observed["schema_contract_sha256"]
            == self._contract["schema_contract_sha256"],
            "Schema or supplied knowledge changed",
        )
        record = {
            "parent_revision_id": parent_revision_id,
            "watermark": integer(watermark, 0, 2**63 - 1),
            "evidence_sha256": checked_hash(evidence_sha256),
            "snapshot": observed,
        }
        record["revision_id"] = digest(record)
        with self.transaction():
            state = self._replay()
            if record["revision_id"] in state["revisions"]:
                require(
                    state["revisions"][record["revision_id"]] == record,
                    "Changed duplicate revision",
                )
                return copy.deepcopy(record)
            require(
                parent_revision_id == state["head_revision"],
                "Revision admission would fork or roll back the series",
            )
            if parent_revision_id is not None:
                parent = state["revisions"][parent_revision_id]
                require(
                    watermark > parent["watermark"], "Source watermark did not advance"
                )
            require(len(state["revisions"]) < 128, "Source revision bound reached")
            self._append("revision_admitted", record)
        return copy.deepcopy(record)

    def _workspace(self, revision):
        original = revision["snapshot"]
        require(
            inspect_revision(
                original["registry_path"],
                revision_varying_documents=self._contract["revision_varying_documents"],
            )
            == original,
            "Admitted snapshot bytes or identity changed",
        )
        workspace = AnalystWorkspace(original["registry_path"])
        require(
            workspace.registry_sha256 == original["canonical_registry_sha256"]
            and workspace._manifest["databases"] == original["databases"]
            and file_sha(original["registry_path"]) == original["registry_file_sha256"],
            "Snapshot registry changed during opening",
        )
        return workspace

    def allowance(self):
        state = self._replay()
        known = reserved = encodes = held_encodes = 0
        for job in state["jobs"].values():
            if "run_closed" in job:
                known += job["run_closed"]["completion_tokens"]
                encodes += job["run_closed"]["cpu_encodes"]
            else:
                reserved += job["origin"]["completion_limit"]
                held_encodes += 1
        return {
            "known_completion_tokens": known,
            "reserved_completion_tokens": reserved,
            "completed_cpu_encodes": encodes,
            "reserved_cpu_encodes": held_encodes,
            "unresolved_jobs": sorted(
                k
                for k, v in state["jobs"].items()
                if v["phase"] in {"running", "uncertain"}
            ),
        }

    def reserve(
        self, job_id, revision_id, question, db_id, *, completion_limit=6000, seed=0
    ):
        bounded_text(job_id, 256)
        bounded_text(question, 5000)
        bounded_text(db_id, 64)
        integer(completion_limit, 1, 6000)
        integer(seed, 0, 2**32 - 25)
        with self.transaction():
            state = self._replay()
            require(
                job_id not in state["jobs"],
                "Job identity is already reserved; retry refused",
            )
            require(
                revision_id == state["head_revision"] and revision_id is not None,
                "New work must use the current admitted revision",
            )
            snapshot = state["revisions"][revision_id]["snapshot"]
            require(db_id in snapshot["databases"], "Database is not admitted")
            self._workspace(state["revisions"][revision_id])
            require(
                len(state["jobs"]) < self._contract["maximum_jobs"],
                "Finite job bound reached",
            )
            require(
                sum(j["phase"] != "settled" for j in state["jobs"].values())
                < self._contract["maximum_pending"],
                "Pending outcome bound reached",
            )
            allowance = self.allowance()
            require(
                not allowance["unresolved_jobs"],
                "Unresolved inference prevents new authority",
            )
            require(
                allowance["known_completion_tokens"]
                + allowance["reserved_completion_tokens"]
                + completion_limit
                <= self._contract["maximum_completion_tokens"],
                "Completion authority exhausted",
            )
            require(
                allowance["completed_cpu_encodes"]
                + allowance["reserved_cpu_encodes"]
                + 1
                <= self._contract["maximum_cpu_encodes"],
                "Encoding authority exhausted",
            )
            origin = {
                "job_id": job_id,
                "revision_id": revision_id,
                "canonical_registry_sha256": snapshot["canonical_registry_sha256"],
                "registry_file_sha256": snapshot["registry_file_sha256"],
                "question": question,
                "db_id": db_id,
                "completion_limit": completion_limit,
                "cpu_encodes": 1,
                "seed": seed,
            }
            origin["context_sha256"] = digest(origin)
            self._append("job_reserved", origin)
        return copy.deepcopy(origin)

    def _memory(self):
        return ProceduralMemory(
            self.directory / "memory.sqlite",
            source_id=self.source_id,
            model_id=self._contract["model_id"],
            tool_version=self._contract["tool_version"],
        )

    def _bridge(self, encoder, bridge_factory):
        require(
            encoder.binding_sha256 == self._contract["encoder_binding_sha256"],
            "Encoder contract changed",
        )
        if bridge_factory is None:
            from cognition.semantic_neural import SemanticNeuralBridge

            bridge_factory = SemanticNeuralBridge
        bridge = bridge_factory(
            self.directory / "semantic-lab",
            source_id=self.source_id,
            model_id=self._contract["model_id"],
            tool_version=self._contract["tool_version"],
            encoder=encoder,
        )
        try:
            require(
                bridge.semantic_terms["source_id"] == self.source_id
                and bridge.semantic_terms["model_id"] == self._contract["model_id"]
                and bridge.semantic_terms["tool_version"]
                == self._contract["tool_version"]
                and bridge.encoder_binding_sha256
                == self._contract["encoder_binding_sha256"],
                "Neural runtime does not match enrollment",
            )
        except BaseException:
            bridge.close()
            raise
        return bridge

    def _artifact_path(self, job_id):
        return self.directory / (
            hashlib.sha256(job_id.encode()).hexdigest() + ".artifact.json"
        )

    def run(
        self,
        job_id,
        *,
        reasoner,
        encoder,
        model_id,
        tool_version=SCHEMA,
        bridge_factory=None,
    ):
        self._check_identity()
        require(
            model_id == self._contract["model_id"]
            and tool_version == self._contract["tool_version"],
            "Host runtime model or tool identity differs",
        )
        state = self._replay()
        require(
            job_id in state["jobs"] and state["jobs"][job_id]["phase"] == "reserved",
            "Only an unstarted reservation may execute",
        )
        require(
            not self.allowance()["unresolved_jobs"],
            "Unresolved inference prevents execution",
        )
        origin = state["jobs"][job_id]["origin"]
        workspace = self._workspace(state["revisions"][origin["revision_id"]])
        require(
            encoder.binding_sha256 == self._contract["encoder_binding_sha256"],
            "Encoder contract changed",
        )
        with self.transaction():
            self._append(
                "run_started",
                {"job_id": job_id, "context_sha256": origin["context_sha256"]},
            )
        memory = bridge = None
        try:
            memory = self._memory()
            bridge = self._bridge(encoder, bridge_factory)
            from cognition.hybrid_neural import STRATEGIES

            decision = bridge.choose(
                job_id,
                origin["context_sha256"],
                origin["question"],
                {tool: memory.outcome_stats(tool) for tool in STRATEGIES},
            )
            analyst = GroundedAnalyst(
                reasoner,
                workspace,
                memory,
                memory_mode="procedure",
                max_output_tokens=origin["completion_limit"],
                initial_tool=decision["first_tool"],
                neural_context={
                    key: decision[key]
                    for key in ("first_tool", "utilities", "action_spikes", "window_ms")
                },
            )
            report = analyst.run(
                job_id=job_id,
                question=origin["question"],
                db_id=origin["db_id"],
                seed=origin["seed"],
            )
            answer_complete = _complete_own_query(
                report, memory, workspace, origin["db_id"], job_id
            )
            self._workspace(state["revisions"][origin["revision_id"]])
            require(
                report["configuration"]["registry_sha256"]
                == origin["canonical_registry_sha256"],
                "Analytical result changed snapshot",
            )
            usage = report["model_usage"]
            require(
                usage["unknown_additional_usage"] is False,
                "Inference usage remains ambiguous",
            )
            integer(usage["completion_tokens"], 0, origin["completion_limit"])
            packet = {
                "origin": origin,
                "decision": decision,
                "report": report,
                "answer_complete": answer_complete,
            }
            raw = canonical(packet).encode()
            require(len(raw) <= 2 * 1024**2, "Artifact exceeds its private bound")
            fd = os.open(
                self._artifact_path(job_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            with self.transaction():
                self._append(
                    "run_closed",
                    {
                        "job_id": job_id,
                        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                        "completion_tokens": usage["completion_tokens"],
                        "cpu_encodes": 1,
                    },
                )
            return copy.deepcopy(packet)
        except BaseException as error:
            with self.transaction():
                if self._replay()["jobs"][job_id]["phase"] == "running":
                    self._append(
                        "run_uncertain",
                        {"job_id": job_id, "error_type": type(error).__name__},
                    )
            raise
        finally:
            if bridge is not None:
                bridge.close()
            if memory is not None:
                memory.close()

    def _packet(self, job_id, job):
        path = self._artifact_path(job_id)
        require(
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size <= 2 * 1024**2
            and file_sha(path) == job["run_closed"]["artifact_sha256"],
            "Retained analytical artifact changed",
        )
        packet = json.loads(path.read_bytes())
        require(
            packet["origin"] == job["origin"] and packet["report"]["job_id"] == job_id,
            "Retained job origin changed",
        )
        return packet

    def settle(
        self,
        job_id,
        *,
        revision_id,
        encoder,
        checker=None,
        checker_id=None,
        checker_implementation_sha256=None,
        bridge_factory=None,
    ):
        state = self._replay()
        require(job_id in state["jobs"], "Unknown job")
        job = state["jobs"][job_id]
        require(
            job["origin"]["revision_id"] == revision_id,
            "Outcome belongs to another source revision",
        )
        require(
            job["phase"] in {"closed", "checked", "settled"},
            "Outcome has no closed execution",
        )
        require(
            encoder.binding_sha256 == self._contract["encoder_binding_sha256"],
            "Encoder contract changed",
        )
        workspace = self._workspace(state["revisions"][revision_id])
        packet = self._packet(job_id, job)
        memory = bridge = None
        try:
            bridge = self._bridge(encoder, bridge_factory)
            neural_job = bridge.state["jobs"].get(job_id)
            require(
                neural_job is not None
                and neural_job["decision"] == packet["decision"]
                and neural_job["origin"]["context_sha256"]
                == job["origin"]["context_sha256"],
                "Neural outcome lacks the exact original decision",
            )
            memory = self._memory()
            answer_complete = _complete_own_query(
                packet["report"], memory, workspace, job["origin"]["db_id"], job_id
            )
            require(
                answer_complete == packet["answer_complete"],
                "Own answer completeness changed",
            )
            if job["phase"] == "closed":
                require(
                    callable(checker)
                    and checker_id == self._contract["checker_id"]
                    and checker_implementation_sha256
                    == self._contract["checker_implementation_sha256"],
                    "Independent checker enrollment differs",
                )
                request = {
                    "job_id": job_id,
                    "revision_id": revision_id,
                    "origin": copy.deepcopy(job["origin"]),
                    "report": copy.deepcopy(packet["report"]),
                }
                outcome = checker(request, workspace)
                require(
                    type(outcome) is dict
                    and set(outcome) == {"accepted", "evidence_sha256"}
                    and type(outcome["accepted"]) is bool,
                    "Malformed independent outcome",
                )
                checked_hash(outcome["evidence_sha256"])
                require(
                    answer_complete or not outcome["accepted"],
                    "An incomplete answer cannot be accepted",
                )
                self._workspace(state["revisions"][revision_id])
                verdict = {
                    "job_id": job_id,
                    "revision_id": revision_id,
                    "proposal_sha256": packet["report"]["proposal_sha256"],
                    "artifact_sha256": job["run_closed"]["artifact_sha256"],
                    "accepted": outcome["accepted"],
                    "checker_id": checker_id,
                    "evidence_sha256": digest(
                        {
                            "checker_evidence_sha256": outcome["evidence_sha256"],
                            "request_sha256": digest(request),
                            "registry_sha256": workspace.registry_sha256,
                        }
                    ),
                }
                with self.transaction():
                    self._append("terminal_checked", verdict)
                job = self._replay()["jobs"][job_id]
            verdict = job["terminal_checked"]
            terminal = memory.settle(
                packet["report"]["episode_id"],
                verify_terminal=lambda request: {
                    key: verdict[key]
                    for key in ("accepted", "evidence_sha256", "checker_id")
                },
            )
            require(
                terminal.get("accepted") == verdict["accepted"]
                and terminal.get("evidence_sha256") == verdict["evidence_sha256"]
                and terminal.get("checker_id") == verdict["checker_id"]
                and terminal.get("episode_id") == packet["report"]["episode_id"]
                and terminal.get("proposal_sha256") == verdict["proposal_sha256"],
                "Memory qualification differs from journaled outcome",
            )
            credit = bridge.settle(
                job_id, verdict["accepted"], terminal["receipt_sha256"]
            )
            receipt = {
                "job_id": job_id,
                "revision_id": revision_id,
                "terminal": terminal,
                "neural_credit": credit,
            }
            if job["phase"] == "settled":
                require(
                    receipt == job["terminal_settled"],
                    "Changed duplicate source-series outcome",
                )
                return copy.deepcopy(receipt)
            with self.transaction():
                self._append("terminal_settled", receipt)
            return copy.deepcopy(receipt)
        finally:
            if bridge is not None:
                bridge.close()
            if memory is not None:
                memory.close()
