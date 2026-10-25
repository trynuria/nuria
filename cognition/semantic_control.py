"""A finite, source-bound LinUCB comparator for semantic first-tool selection.

The host supplies an already earned representation and verified terminal labels.
This module loads no encoder, runs no inference and authenticates no remote
checker. Its only learned parameters are derived from the retained JSON journal.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from pathlib import Path

import numpy as np

VERSION = "nuria.semantic-control.v1"
TOOLS = ("list_tables", "read_knowledge")
DIMENSIONS = 389
REGULARIZER = 1.0
EXPLORATION = 0.5
MAX_JOBS = 128
MAX_PENDING = 64
MAX_EVENTS = MAX_JOBS * 2
MAX_PAYLOAD = 32768
HASH = re.compile(r"[0-9a-f]{64}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    _require(type(value) is str and HASH.fullmatch(value), "A full SHA256 is required")
    return value


def _text(value, limit):
    _require(
        type(value) is str and 0 < len(value.encode()) <= limit,
        "Controller text exceeds its bound",
    )
    return value


def _implementation():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _statistics(features):
    """Retain the neural bridge's same observed-count and cost interpretation.

    Only reported host observations enter. A successful tool execution is not a
    successful terminal answer. Missing costs remain an explicit feature.
    """
    _require(
        type(features) is dict and not set(features) - set(TOOLS),
        "Only the two registered tools may supply observations",
    )
    result = {}
    allowed = {
        "tool_name",
        "observations",
        "tool_successes",
        "tool_errors",
        "total_cost_units",
        "known_cost_units",
        "cost_known_count",
        "cost_unknown_count",
        "mean_cost_units",
        "success_probability_mean",
        "posterior_standard_deviation",
        "scope",
    }
    for tool in TOOLS:
        record = features.get(tool)
        if record is None:
            record = {
                "observations": 0,
                "tool_successes": 0,
                "tool_errors": 0,
                "cost_known_count": 0,
                "cost_unknown_count": 0,
                "known_cost_units": 0,
                "mean_cost_units": None,
            }
        _require(
            type(record) is dict and not set(record) - allowed,
            "Unexpected observed tool fields",
        )
        n, successes, errors = (
            record.get(key) for key in ("observations", "tool_successes", "tool_errors")
        )
        _require(
            all(type(x) is int and 0 <= x <= 10**9 for x in (n, successes, errors))
            and n == successes + errors
            and record.get("tool_name", tool) == tool,
            "Observed tool counts disagree",
        )
        known = record.get(
            "cost_known_count", n if record.get("total_cost_units") is not None else 0
        )
        unknown = record.get("cost_unknown_count", n - known)
        cost = record.get("known_cost_units", record.get("total_cost_units"))
        _require(
            all(type(x) is int and 0 <= x <= n for x in (known, unknown))
            and known + unknown == n
            and type(cost) is int
            and 0 <= cost <= 10**15
            and (known != 0 or cost == 0),
            "Observed cost counts disagree",
        )
        mean_cost = cost / known if known else None
        _require(
            "mean_cost_units" not in record or record["mean_cost_units"] == mean_cost,
            "Observed mean cost disagrees",
        )
        a, b = 1 + successes, 1 + errors
        mean = a / (a + b)
        uncertainty = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
        for name, expected in (
            ("success_probability_mean", mean),
            ("posterior_standard_deviation", uncertainty),
        ):
            _require(
                name not in record
                or (
                    type(record[name]) in (int, float)
                    and math.isfinite(record[name])
                    and math.isclose(record[name], expected, abs_tol=1e-12)
                ),
                "Observed posterior disagrees with counts",
            )
        result[tool] = {
            "observations": n,
            "successes": successes,
            "errors": errors,
            "known_cost_count": known,
            "unknown_cost_count": unknown,
            "known_cost_units": cost,
            "mean_cost_units": mean_cost,
            "success_probability_mean": mean,
            "uncertainty": uncertainty,
            "cost_unknown": mean_cost is None or unknown > 0,
        }
    return result


def _observation(vector, receipt, binding_sha256):
    _require(
        type(vector) is list
        and len(vector) == 384
        and all(type(x) in (int, float) for x in vector),
        "Semantic values must be explicit JSON numbers",
    )
    values = np.asarray(vector, dtype="<f4")
    _require(
        values.shape == (384,)
        and np.isfinite(values).all()
        and np.isclose(np.linalg.norm(values), 1, rtol=0, atol=1e-4),
        "A finite normalized 384-vector is required",
    )
    _require(type(receipt) is dict, "An observation receipt is required")
    detached = copy.deepcopy(receipt)
    _require(
        len(canonical(detached).encode()) <= MAX_PAYLOAD
        and detached.get("schema") == "nuria.semantic-observation.v1"
        and detached.get("binding_sha256") == binding_sha256
        and type(detached.get("dimensions")) is int
        and detached["dimensions"] == 384
        and detached.get("vector_float32_sha256")
        == hashlib.sha256(values.tobytes()).hexdigest()
        and detached.get("receipt_sha256")
        == digest({k: v for k, v in detached.items() if k != "receipt_sha256"}),
        "Semantic receipt does not bind this vector and encoder",
    )
    _hash(detached.get("text_sha256"))
    return values.tolist(), detached


def _features(vector, statistics):
    """The complete semantic vector, bias and four declared observed features."""
    cost = statistics["mean_cost_units"]
    values = np.asarray(
        [
            *vector,
            1.0,
            statistics["success_probability_mean"],
            statistics["uncertainty"],
            min(1.0, cost / 100000) if cost is not None else 0.0,
            float(statistics["cost_unknown"]),
        ],
        dtype=np.float64,
    )
    values /= np.linalg.norm(values)
    _require(
        values.shape == (DIMENSIONS,) and np.isfinite(values).all(),
        "Controller context is not finite",
    )
    return values


def _model():
    return {
        tool: [np.eye(DIMENSIONS) / REGULARIZER, np.zeros(DIMENSIONS)] for tool in TOOLS
    }


def _scores(model, vector, statistics):
    result = {}
    for tool in TOOLS:
        context = _features(vector, statistics[tool])
        inverse, target = model[tool]
        variance = float(context @ inverse @ context)
        _require(
            math.isfinite(variance) and variance >= -1e-10, "Invalid ridge uncertainty"
        )
        mean = float(context @ inverse @ target)
        bonus = EXPLORATION * math.sqrt(max(0.0, variance))
        _require(math.isfinite(mean + bonus), "Invalid ridge score")
        result[tool] = {
            "mean_reward": mean,
            "exploration_bonus": bonus,
            "score": mean + bonus,
        }
    return result


def _learn(model, origin, accepted):
    tool = origin["decision"]["first_tool"]
    context = _features(origin["vector"], origin["statistics"][tool])
    inverse, target = model[tool]
    projected = inverse @ context
    denominator = 1.0 + float(context @ projected)
    _require(
        math.isfinite(denominator) and denominator >= 1 - 1e-10, "Invalid ridge update"
    )
    inverse -= np.outer(projected, projected) / denominator
    target += (1.0 if accepted else -1.0) * context
    _require(
        np.isfinite(inverse).all() and np.isfinite(target).all(),
        "Nonfinite ridge update",
    )


class SemanticLinearControl:
    """A finite conventional comparator; terminal authority remains with the host.

    An observation is supplied, never acquired here. Only ``choose`` and host
    ``settle`` append records. The entire history is checked and coefficients
    reconstructed before use; mutable cached arrays cannot alter a decision.
    """

    def __init__(
        self,
        directory,
        *,
        source_id,
        model_id,
        tool_version,
        encoder_binding,
        expected_head=None,
    ):
        _require(
            type(encoder_binding) is dict
            and type(encoder_binding.get("dimensions")) is int
            and encoder_binding["dimensions"] == 384
            and len(canonical(encoder_binding).encode()) <= 16384,
            "A bounded immutable encoder binding is required",
        )
        binding = {
            "schema": VERSION,
            "source_id": _text(source_id, 256),
            "model_id": _text(model_id, 512),
            "tool_version": _text(tool_version, 256),
            "encoder_binding": copy.deepcopy(encoder_binding),
            "encoder_binding_sha256": digest(encoder_binding),
            "implementation_sha256": _implementation(),
            "numpy_version": np.__version__,
            "regularizer": REGULARIZER,
            "exploration": EXPLORATION,
            "dimensions": DIMENSIONS,
            "tools": list(TOOLS),
            "features": "L2(full384,bias,tool_validity_posterior,posterior_sd,cost_over_100000_clipped,cost_unknown)",
            "reward": "host_verified_terminal_acceptance:+1,rejection:-1",
            "maximum_jobs": MAX_JOBS,
            "maximum_pending": MAX_PENDING,
            "maximum_events": MAX_EVENTS,
            "maximum_payload_bytes": MAX_PAYLOAD,
        }
        self._binding = canonical(binding)
        self.binding_sha256 = digest(binding)
        self.directory = Path(directory)
        _require(
            not self.directory.is_symlink(), "Controller state cannot be a symlink"
        )
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require(
            self.directory.is_dir() and not self.directory.stat().st_mode & 0o077,
            "Controller state requires a private mode-0700 directory",
        )
        self.lock = self.db = None
        self._locked = False
        self.failed = False
        try:
            lock_path = self.directory / "writer.lock"
            _require(not lock_path.is_symlink(), "Writer lock cannot be indirect")
            descriptor = os.open(
                lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
            )
            try:
                _require(
                    stat.S_ISREG(os.fstat(descriptor).st_mode)
                    and not os.fstat(descriptor).st_mode & 0o077,
                    "Writer lock must be a private regular file",
                )
                self.lock = os.fdopen(descriptor, "a+b")
            except BaseException:
                os.close(descriptor)
                raise
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
            self.path = self.directory / "control.sqlite"
            _require(
                not self.path.is_symlink(), "Controller database cannot be indirect"
            )
            if not self.path.exists():
                os.close(
                    os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                )
            mode = self.path.stat().st_mode
            _require(
                stat.S_ISREG(mode) and not mode & 0o077,
                "Controller database must be private and regular",
            )
            self._inode = self.path.stat().st_ino
            # Reject an unrelated existing database before changing its journal
            # mode, schema or identity. A read-only connection can see a retained
            # WAL without becoming its final checkpointing writer.
            probe = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                tables = {
                    row[0]
                    for row in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                _require(
                    not tables or tables == {"control_identity", "control_events"},
                    "Not a controller journal",
                )
                if tables:
                    previous = probe.execute(
                        "SELECT payload FROM control_identity WHERE id=1"
                    ).fetchone()
                    _require(
                        previous is None or previous[0] == self._binding,
                        "Source, encoder, implementation or controller terms changed",
                    )
                    _require(
                        previous is not None
                        or probe.execute(
                            "SELECT count(*) FROM control_events"
                        ).fetchone()[0]
                        == 0,
                        "Journal lost its identity",
                    )
            finally:
                probe.close()
            self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA wal_autocheckpoint=32")
            self.db.execute("PRAGMA journal_size_limit=131072")
            tables = {
                row[0]
                for row in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            _require(
                not tables or tables == {"control_identity", "control_events"},
                "Not a controller journal",
            )
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS control_identity(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS control_events(seq INTEGER PRIMARY KEY,previous_hash TEXT NOT NULL,hash TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);
            """)
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute(
                "SELECT payload FROM control_identity WHERE id=1"
            ).fetchone()
            if old is None:
                _require(
                    self.db.execute("SELECT count(*) FROM control_events").fetchone()[0]
                    == 0,
                    "Journal lost its identity",
                )
                self.db.execute(
                    "INSERT INTO control_identity VALUES(1,?)", (self._binding,)
                )
            else:
                _require(
                    old[0] == self._binding,
                    "Source, encoder, implementation or controller terms changed",
                )
            self.db.execute("COMMIT")
            head = self.verify()["head_sha256"]
            if expected_head is not None:
                _require(
                    head == _hash(expected_head),
                    "Journal does not match its retained head",
                )
        except BaseException:
            if self.db is not None and self.db.in_transaction:
                self.db.execute("ROLLBACK")
            self.close()
            raise

    @property
    def binding(self):
        return json.loads(self._binding)

    def _check(self):
        _require(
            self.db is not None and self.lock is not None and not self.failed,
            "Reopen the interrupted controller before use",
        )
        _require(
            digest(self.binding) == self.binding_sha256
            and self.binding["implementation_sha256"] == _implementation()
            and self.binding["regularizer"] == REGULARIZER
            and self.binding["exploration"] == EXPLORATION
            and self.binding["dimensions"] == DIMENSIONS
            and self.binding["maximum_jobs"] == MAX_JOBS
            and self.binding["maximum_pending"] == MAX_PENDING
            and self.binding["maximum_events"] == MAX_EVENTS
            and self.binding["maximum_payload_bytes"] == MAX_PAYLOAD
            and self.binding["numpy_version"] == np.__version__
            and self.binding["tools"] == list(TOOLS)
            and not self.path.is_symlink()
            and self.path.stat().st_ino == self._inode
            and not self.path.stat().st_mode & 0o077,
            "Controller implementation, terms or state changed",
        )
        row = self.db.execute(
            "SELECT payload FROM control_identity WHERE id=1"
        ).fetchone()
        _require(
            row is not None and row[0] == self._binding, "Journal identity changed"
        )

    def _replay(self):
        self._check()
        count = self.db.execute("SELECT count(*) FROM control_events").fetchone()[0]
        _require(
            0 <= count <= MAX_EVENTS, "Controller history exceeds its finite bound"
        )
        model, jobs, parent = _model(), {}, "0" * 64
        for sequence, previous, head, payload in self.db.execute(
            "SELECT seq,previous_hash,hash,payload FROM control_events ORDER BY seq"
        ):
            _require(
                sequence
                == len(jobs)
                + sum(job.get("settlement") is not None for job in jobs.values())
                + 1
                and previous == parent
                and len(payload.encode()) <= MAX_PAYLOAD
                and hashlib.sha256((parent + payload).encode()).hexdigest() == head,
                "Controller journal chain differs",
            )
            event = json.loads(payload)
            _require(
                set(event) == {"binding_sha256", "kind", "job_id", "record"}
                and canonical(event) == payload
                and event["binding_sha256"] == self.binding_sha256,
                "Controller event identity differs",
            )
            job_id = _text(event["job_id"], 256)
            if event["kind"] == "choose":
                _require(
                    job_id not in jobs
                    and len(jobs) < MAX_JOBS
                    and sum(row["settlement"] is None for row in jobs.values())
                    < MAX_PENDING,
                    "Duplicate or excessive originating decision",
                )
                origin = event["record"]
                _require(
                    set(origin)
                    == {
                        "context_sha256",
                        "vector",
                        "receipt",
                        "reported_statistics",
                        "statistics",
                        "decision",
                    },
                    "Unknown originating fields",
                )
                values, receipt = _observation(
                    origin["vector"],
                    origin["receipt"],
                    self.binding["encoder_binding_sha256"],
                )
                _hash(origin["context_sha256"])
                _require(
                    origin["vector"] == values and origin["receipt"] == receipt,
                    "Semantic origin differs",
                )
                # Normalize again from original reported counts, not persisted derived values.
                _require(
                    origin["statistics"] == _statistics(origin["reported_statistics"]),
                    "Origin statistics disagree",
                )
                scores = _scores(model, values, origin["statistics"])
                expected = self._decision(job_id, origin, scores)
                _require(
                    origin["decision"] == expected,
                    "Retained selection does not reproduce",
                )
                jobs[job_id] = {**origin, "settlement": None}
            elif event["kind"] == "settle":
                _require(
                    job_id in jobs and jobs[job_id]["settlement"] is None,
                    "Settlement has no unique originating decision",
                )
                terminal = event["record"]
                _require(
                    set(terminal)
                    == {
                        "accepted",
                        "origin_decision_sha256",
                        "evidence_sha256",
                        "terminal_sha256",
                    }
                    and type(terminal["accepted"]) is bool
                    and terminal["origin_decision_sha256"]
                    == jobs[job_id]["decision"]["decision_sha256"]
                    and terminal["evidence_sha256"]
                    == _hash(terminal["evidence_sha256"])
                    and terminal["terminal_sha256"]
                    == digest(
                        {k: v for k, v in terminal.items() if k != "terminal_sha256"}
                    ),
                    "Terminal outcome does not bind its origin",
                )
                _learn(model, jobs[job_id], terminal["accepted"])
                jobs[job_id]["settlement"] = terminal
            else:
                raise ValueError("Unexpected controller event")
            parent = head
        return model, jobs, parent, count

    def _decision(self, job_id, origin, scores):
        chosen = max(TOOLS, key=lambda tool: scores[tool]["score"])
        decision = {
            "schema": VERSION,
            "job_id": job_id,
            "first_tool": chosen,
            "scores": scores,
            "context_sha256": origin["context_sha256"],
            "vector_float32_sha256": origin["receipt"]["vector_float32_sha256"],
            "observation_receipt_sha256": origin["receipt"]["receipt_sha256"],
            "binding_sha256": self.binding_sha256,
            "feature_sha256": {
                tool: hashlib.sha256(
                    _features(origin["vector"], origin["statistics"][tool])
                    .astype("<f8")
                    .tobytes()
                ).hexdigest()
                for tool in TOOLS
            },
            "scope": "Conventional same-representation first-tool comparator; no measured task advantage established",
        }
        decision["decision_sha256"] = digest(decision)
        return decision

    def _append(self, kind, job_id, record, parent, count):
        payload = canonical(
            {
                "binding_sha256": self.binding_sha256,
                "kind": kind,
                "job_id": job_id,
                "record": record,
            }
        )
        _require(
            len(payload.encode()) <= MAX_PAYLOAD and count < MAX_EVENTS,
            "Controller record exceeds its finite bound",
        )
        head = hashlib.sha256((parent + payload).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO control_events VALUES(?,?,?,?)",
            (count + 1, parent, head, payload),
        )
        return head

    def _commit(self):
        self.db.execute("COMMIT")

    def _failure(self):
        self.failed = True
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")

    def choose(self, job_id, context_sha256, vector, receipt, toolstats):
        """Commit one selection from an already earned observation; no model call."""
        self._check()
        job_id, context_sha256 = _text(job_id, 256), _hash(context_sha256)
        values, observed = _observation(
            vector, receipt, self.binding["encoder_binding_sha256"]
        )
        statistics = _statistics(toolstats)
        reported = copy.deepcopy(toolstats)
        origin = {
            "context_sha256": context_sha256,
            "vector": values,
            "receipt": observed,
            "reported_statistics": reported,
            "statistics": statistics,
        }
        try:
            self.db.execute("BEGIN IMMEDIATE")
            model, jobs, parent, count = self._replay()
            existing = jobs.get(job_id)
            if existing is not None:
                _require(
                    all(existing[k] == v for k, v in origin.items()),
                    "Changed duplicate controller input",
                )
                self.db.execute("ROLLBACK")
                return copy.deepcopy(existing["decision"])
            _require(
                len(jobs) < MAX_JOBS
                and sum(row["settlement"] is None for row in jobs.values())
                < MAX_PENDING,
                "Controller job or pending bound reached",
            )
            decision = self._decision(
                job_id, origin, _scores(model, values, statistics)
            )
            self._append(
                "choose", job_id, {**origin, "decision": decision}, parent, count
            )
            self._commit()
            return copy.deepcopy(decision)
        except BaseException:
            self._failure()
            raise

    def settle(self, job_id, accepted, evidence_sha256):
        """Host-only verified outcome; credit the stored originating vector once."""
        self._check()
        job_id, evidence_sha256 = _text(job_id, 256), _hash(evidence_sha256)
        _require(
            type(accepted) is bool, "A verified Boolean terminal outcome is required"
        )
        try:
            self.db.execute("BEGIN IMMEDIATE")
            _, jobs, parent, count = self._replay()
            _require(job_id in jobs, "Terminal outcome has no originating decision")
            terminal = {
                "accepted": accepted,
                "evidence_sha256": evidence_sha256,
                "origin_decision_sha256": jobs[job_id]["decision"]["decision_sha256"],
            }
            terminal["terminal_sha256"] = digest(terminal)
            existing = jobs[job_id]["settlement"]
            if existing is not None:
                _require(
                    existing == terminal, "Contradictory repeated terminal outcome"
                )
                self.db.execute("ROLLBACK")
                return copy.deepcopy(existing)
            self._append("settle", job_id, terminal, parent, count)
            self._commit()
            return copy.deepcopy(terminal)
        except BaseException:
            self._failure()
            raise

    def verify(self):
        _, jobs, head, count = self._replay()
        return {
            "schema": VERSION,
            "binding_sha256": self.binding_sha256,
            "head_sha256": head,
            "events": count,
            "jobs": len(jobs),
            "settled": sum(row["settlement"] is not None for row in jobs.values()),
            "scope": "Local journal consistency; a retained external head is needed to detect a valid-prefix rollback",
        }

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            if self._locked:
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
                self._locked = False
            self.lock.close()
            self.lock = None
