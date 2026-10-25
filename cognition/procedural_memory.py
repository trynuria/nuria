"""Private, source-bound experience and independently qualified SQL procedures.

Only retrieve/propose belong in an agent tool surface. The host records actual
tool results and supplies the terminal checker callable. This module executes no
supplied SQL and authenticates neither a remote checker nor its expected answers.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

VERSION = "nuria.procedural-memory.v1"
HASH = re.compile(r"[0-9a-f]{64}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _text(value, limit, *, empty=False):
    if type(value) is not str or len(value.encode()) > limit or not (value or empty):
        raise ValueError("Memory text exceeds its declared bound")
    return value


def _hash(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise ValueError("Memory evidence needs a full SHA256 identity")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Memory integer exceeds its declared bound")
    return value


def _preconditions(value):
    if type(value) is str:
        value = [value] if value else []
    if type(value) is not list or len(value) > 16:
        raise ValueError("Procedure preconditions require a bounded list")
    return [_text(item, 1024) for item in value]


class ProceduralMemory:
    def __init__(self, path, *, source_id, model_id, tool_version):
        self._binding = canonical(
            {
                "schema": VERSION,
                "source_id": _text(source_id, 256),
                "model_id": _text(model_id, 512),
                "tool_version": _text(tool_version, 256),
            }
        )
        self.binding_sha256 = digest(self.binding)
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                pass
        if path.stat().st_mode & 0o077:
            raise ValueError("Procedural memory requires a private database file")
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        existing = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if existing and existing != {"memory_identity", "memory_events"}:
            raise ValueError("Path contains another database, not procedural memory")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memory_identity(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_events(
          seq INTEGER PRIMARY KEY,previous_hash TEXT NOT NULL,hash TEXT UNIQUE NOT NULL,
          kind TEXT NOT NULL,episode_id TEXT NOT NULL,item_id TEXT NOT NULL,payload TEXT NOT NULL,
          UNIQUE(kind,episode_id,item_id));
        CREATE INDEX IF NOT EXISTS memory_episode ON memory_events(episode_id,seq);
        """)
        with self._transaction():
            row = self.db.execute(
                "SELECT payload FROM memory_identity WHERE id=1"
            ).fetchone()
            if row is None:
                if self.db.execute("SELECT count(*) FROM memory_events").fetchone()[0]:
                    raise ValueError("Memory history is missing its source identity")
                self.db.execute(
                    "INSERT INTO memory_identity VALUES(1,?)",
                    (canonical(self.binding),),
                )
            elif row[0] != canonical(self.binding):
                raise ValueError("Memory source, exact model or tool version changed")
        self.verify()

    @property
    def binding(self):
        return json.loads(self._binding)

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _one(self, kind, episode_id, item_id=None):
        rows = self.db.execute(
            "SELECT payload FROM memory_events WHERE kind=? AND episode_id=? AND (? IS NULL OR item_id=?) ORDER BY seq",
            (kind, episode_id, item_id, item_id),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("A singular memory record is ambiguous")
        return json.loads(rows[0][0])["record"] if rows else None

    def _append(self, kind, episode_id, item_id, record):
        old = self._one(kind, episode_id, item_id)
        if old is not None:
            if old != record:
                raise ValueError("Changed duplicate memory evidence")
            return old
        row = self.db.execute(
            "SELECT seq,hash FROM memory_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        seq, parent = row if row else (0, "0" * 64)
        raw = canonical(
            {
                "schema": VERSION,
                "binding_sha256": self.binding_sha256,
                "kind": kind,
                "episode_id": episode_id,
                "item_id": item_id,
                "record": record,
            }
        )
        head = hashlib.sha256((parent + raw).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO memory_events VALUES(?,?,?,?,?,?,?)",
            (seq + 1, parent, head, kind, episode_id, item_id, raw),
        )
        return record

    def _origin(self, episode_id):
        origin = self._one("begin", _hash(episode_id))
        if origin is None:
            raise ValueError("Unknown memory episode")
        return origin

    def begin(self, job_id, question, context_sha256):
        """Idempotently open an origin for host-managed inspection or recovery."""
        return self._begin(job_id, question, context_sha256, require_new=False)

    def begin_once(self, job_id, question, context_sha256):
        """Reserve a fresh attempt before any possibly unaccounted inference.

        A begun origin is sufficient to refuse a duplicate. An interruption can
        happen before the first tool observation or terminal proposal, so an
        empty action list cannot establish that no model request was consumed.
        New attempts require distinct job identities and separate accounting.
        """
        return self._begin(job_id, question, context_sha256, require_new=True)

    def _begin(self, job_id, question, context_sha256, *, require_new):
        origin = {
            **self.binding,
            "job_id": _text(job_id, 256),
            "question": _text(question, 16384),
            "context_sha256": _hash(context_sha256),
        }
        episode_id = digest(origin)
        with self._transaction():
            previous = self.db.execute(
                "SELECT episode_id FROM memory_events WHERE kind='begin' AND item_id=?",
                (job_id,),
            ).fetchone()
            if previous and require_new:
                raise ValueError(
                    "An analytical attempt already reserved this job identity"
                )
            if previous and previous[0] != episode_id:
                raise ValueError("A job identity cannot change its question or context")
            self._append("begin", episode_id, job_id, origin)
        return episode_id

    def record_action(
        self,
        episode_id,
        action_id,
        tool_name,
        sql,
        ok,
        cost_units,
        result_sha256,
        error=None,
    ):
        """Host/executor-only observation; valid SQL is not terminal success."""
        record = {
            "action_id": _text(action_id, 128),
            "tool_name": _text(tool_name, 128),
            "sql": None if sql is None else _text(sql, 32768),
            "ok": ok,
            "cost_units": None
            if cost_units is None
            else _integer(cost_units, 0, 10**12),
            "result_sha256": _hash(result_sha256),
            "error": error,
        }
        if (
            type(ok) is not bool
            or (ok and error is not None)
            or (not ok and (type(error) is not str or not error))
        ):
            raise ValueError("Tool outcome and error disagree")
        if error is not None:
            _text(error, 256)
        with self._transaction():
            self._origin(episode_id)
            previous = self._one("action", episode_id, action_id)
            if previous is not None:
                if previous != record:
                    raise ValueError("Changed duplicate tool result")
                return previous
            if self._one("proposal", episode_id) or self._one("settlement", episode_id):
                raise ValueError("The committed episode cannot gain new tool results")
            count = self.db.execute(
                "SELECT count(*) FROM memory_events WHERE kind='action' AND episode_id=?",
                (episode_id,),
            ).fetchone()[0]
            if count >= 256:
                raise ValueError("Episode action bound reached")
            return self._append("action", episode_id, action_id, record)

    def propose(
        self, episode_id, final_sql, procedure_text="", rationale="", preconditions=""
    ):
        """Agent-facing proposal: only its own successfully executed SQL qualifies."""
        record = {
            "final_sql": _text(final_sql, 32768),
            "procedure_text": _text(procedure_text, 8192, empty=True),
            "rationale": _text(rationale, 4096, empty=True),
            "preconditions": _preconditions(preconditions),
        }
        with self._transaction():
            self._origin(episode_id)
            actions = self._actions(episode_id)
            if not any(
                action["ok"] and action["sql"] == final_sql for action in actions
            ):
                raise ValueError("Final SQL lacks a successful own execution")
            record["trace_sha256"] = digest(actions)
            record["proposal_sha256"] = digest(record)
            self._append("proposal", episode_id, episode_id, record)
        return record["proposal_sha256"]

    def _actions(self, episode_id):
        return [
            json.loads(row[0])["record"]
            for row in self.db.execute(
                "SELECT payload FROM memory_events WHERE kind='action' AND episode_id=? ORDER BY seq",
                (episode_id,),
            )
        ]

    def abstain(self, episode_id, reason):
        """Commit an explicit failure or limit without inventing valid SQL."""
        with self._transaction():
            self._origin(episode_id)
            record = {
                "final_sql": None,
                "procedure_text": "",
                "rationale": "",
                "preconditions": [],
                "abstention_reason": _text(reason, 1024),
                "trace_sha256": digest(self._actions(episode_id)),
            }
            record["proposal_sha256"] = digest(record)
            self._append("proposal", episode_id, episode_id, record)
        return record["proposal_sha256"]

    def episode_state(self, episode_id, *, max_actions=32):
        """Detached read-only recovery view; omitted action rows stay explicit."""
        _integer(max_actions, 0, 256)
        origin = self._origin(episode_id)
        rows = self.db.execute(
            "SELECT payload FROM memory_events WHERE kind='action' AND episode_id=? ORDER BY seq LIMIT ?",
            (episode_id, max_actions),
        ).fetchall()
        count = self.db.execute(
            "SELECT count(*) FROM memory_events WHERE kind='action' AND episode_id=?",
            (episode_id,),
        ).fetchone()[0]
        return {
            "origin": origin,
            "actions": [json.loads(row[0])["record"] for row in rows],
            "action_count": count,
            "actions_truncated": len(rows) != count,
            "proposal": self._one("proposal", episode_id),
            "settlement": self._one("settlement", episode_id),
        }

    def settle(self, episode_id, *, verify_terminal):
        """Host-only callable checks a committed proposal against external truth.

        The callable receives no future answer from this module and must return
        accepted, evidence_sha256 and checker_id. It must not reenter this ledger.
        Agent JSON cannot supply an accepted flag or replace this Python callable.
        """
        if not callable(verify_terminal):
            raise ValueError("Terminal qualification needs a host checker callable")
        with self._transaction():
            origin = self._origin(episode_id)
            previous = self._one("settlement", episode_id)
            if previous is not None:
                return previous
            proposal = self._one("proposal", episode_id)
            if proposal is None:
                raise ValueError("Terminal outcome has no committed proposal")
            request = {
                **origin,
                "episode_id": episode_id,
                "origin_sha256": episode_id,
                "proposal_sha256": proposal["proposal_sha256"],
                "final_sql": proposal["final_sql"],
                "abstained": proposal["final_sql"] is None,
                "abstention_reason": proposal.get("abstention_reason"),
            }
            try:
                outcome = verify_terminal(json.loads(canonical(request)))
                if (
                    type(outcome) is not dict
                    or set(outcome) != {"accepted", "evidence_sha256", "checker_id"}
                    or type(outcome["accepted"]) is not bool
                ):
                    raise ValueError("Malformed terminal checker receipt")
                _hash(outcome["evidence_sha256"])
                _text(outcome["checker_id"], 256)
                if proposal["final_sql"] is None and outcome["accepted"]:
                    raise ValueError("An abstention cannot be accepted")
            except Exception as exc:
                attempts = self.db.execute(
                    "SELECT count(*) FROM memory_events WHERE kind='checker_error' AND episode_id=?",
                    (episode_id,),
                ).fetchone()[0]
                error = {
                    "status": "unresolved",
                    "error_type": type(exc).__name__,
                    "proposal_sha256": proposal["proposal_sha256"],
                }
                self._append("checker_error", episode_id, str(attempts + 1), error)
                return error
            receipt = {
                "episode_id": episode_id,
                "origin_sha256": episode_id,
                "proposal_sha256": proposal["proposal_sha256"],
                **outcome,
                "terminal_reward": int(outcome["accepted"]),
            }
            receipt["receipt_sha256"] = digest(receipt)
            return self._append("settlement", episode_id, episode_id, receipt)

    def feedback(self, episode_id):
        """Host-only terminal credit; unrelated tool validity earns no reward."""
        self._origin(episode_id)
        return self._one("settlement", episode_id)

    def retrieve(
        self,
        question,
        *,
        mode="procedure",
        max_bytes=6000,
        max_tokens=1500,
        max_items=4,
        count_tokens=None,
    ):
        """Return a finite text block; provide the actual tokenizer for exact units."""
        _text(question, 16384)
        _integer(max_bytes, 0, 65536)
        _integer(max_tokens, 0, 16384)
        _integer(max_items, 0, 16)
        if (
            mode not in ("none", "episode", "procedure")
            or count_tokens is not None
            and not callable(count_tokens)
        ):
            raise ValueError("Unknown retrieval mode or tokenizer")
        measure = (
            count_tokens
            if count_tokens is not None
            else lambda value: len(value.encode())
        )
        selected, text_block, token_count = [], "", 0
        query = set(re.findall(r"\w+", question.casefold()))
        candidates = []
        if mode != "none" and max_bytes and max_tokens and max_items:
            rows = self.db.execute(
                "SELECT episode_id,payload FROM memory_events WHERE kind='settlement' ORDER BY seq DESC LIMIT 128"
            ).fetchall()
            for episode_id, raw in rows:
                settlement = json.loads(raw)["record"]
                if mode == "procedure" and not settlement["accepted"]:
                    continue
                origin, proposal = (
                    self._origin(episode_id),
                    self._one("proposal", episode_id),
                )
                if mode == "procedure" and not proposal["procedure_text"]:
                    continue
                item = {
                    "episode_id": episode_id,
                    "question": origin["question"],
                    "context_sha256": origin["context_sha256"],
                    "accepted": settlement["accepted"],
                    "evidence_sha256": settlement["evidence_sha256"],
                }
                if mode == "episode":
                    item.update(
                        {
                            "actions": self._actions(episode_id),
                            "final_sql": proposal["final_sql"],
                            "abstention_reason": proposal.get("abstention_reason"),
                        }
                    )
                else:
                    item.update(
                        {
                            key: proposal[key]
                            for key in (
                                "procedure_text",
                                "rationale",
                                "preconditions",
                                "final_sql",
                            )
                        }
                    )
                if len(canonical(item).encode()) > max_bytes:
                    continue
                ranking_text = canonical(
                    {
                        key: proposal[key]
                        for key in (
                            "procedure_text",
                            "rationale",
                            "preconditions",
                            "final_sql",
                        )
                    }
                )
                words = set(
                    re.findall(r"\w+", (origin["question"] + ranking_text).casefold())
                )
                overlap = len(query & words)
                if overlap:
                    candidates.append((overlap / max(1, len(query | words)), item))
            candidates.sort(key=lambda value: -value[0])
        for _, item in candidates:
            candidate = canonical([*selected, item])
            tokens = measure(candidate)
            _integer(tokens, 0, 10**9)
            if len(selected) >= max_items:
                break
            if len(candidate.encode()) <= max_bytes and tokens <= max_tokens:
                selected.append(item)
                text_block = candidate
                token_count = tokens
        return {
            "mode": mode,
            "text": text_block,
            "items": selected,
            "item_count": len(selected),
            "bytes": len(text_block.encode()),
            "tokens": token_count,
            "token_accounting": "caller-tokenizer"
            if count_tokens
            else "utf8-byte-upper-bound",
            "binding_sha256": self.binding_sha256,
            "memory_sha256": digest(
                {
                    "binding_sha256": self.binding_sha256,
                    "mode": mode,
                    "question_sha256": digest(question),
                    "items": selected,
                }
            ),
        }

    def outcome_stats(self, tool_name):
        """Beta(1,1) observation model, not a claim about completing the goal."""
        _text(tool_name, 128)
        count, success, cost, known = self.db.execute(
            "SELECT count(*),coalesce(sum(json_extract(payload,'$.record.ok')),0),coalesce(sum(json_extract(payload,'$.record.cost_units')),0),count(json_extract(payload,'$.record.cost_units')) FROM memory_events WHERE kind='action' AND json_extract(payload,'$.record.tool_name')=?",
            (tool_name,),
        ).fetchone()
        a, b = 1 + success, 1 + count - success
        return {
            "tool_name": tool_name,
            "observations": count,
            "tool_successes": success,
            "tool_errors": count - success,
            "total_cost_units": cost if known == count else None,
            "known_cost_units": cost,
            "cost_known_count": known,
            "cost_unknown_count": count - known,
            "mean_cost_units": cost / known if known else None,
            "success_probability_mean": a / (a + b),
            "posterior_standard_deviation": math.sqrt(
                a * b / ((a + b) ** 2 * (a + b + 1))
            ),
            "scope": "Observed tool validity and cost; not terminal task progress",
        }

    def verify(self):
        identity = self.db.execute(
            "SELECT payload FROM memory_identity WHERE id=1"
        ).fetchone()
        if identity != (canonical(self.binding),):
            raise ValueError("Procedural memory identity changed")
        parent, count = "0" * 64, 0
        for seq, previous, head, kind, episode_id, item_id, raw in self.db.execute(
            "SELECT * FROM memory_events ORDER BY seq"
        ):
            payload = json.loads(raw)
            count += 1
            if (
                seq != count
                or previous != parent
                or hashlib.sha256((parent + raw).encode()).hexdigest() != head
                or payload.get("binding_sha256") != self.binding_sha256
                or payload.get("schema") != VERSION
                or [payload.get(key) for key in ("kind", "episode_id", "item_id")]
                != [kind, episode_id, item_id]
            ):
                raise ValueError("Procedural memory integrity check failed")
            parent = head
        return {
            "events": count,
            "head": parent,
            "binding_sha256": self.binding_sha256,
            "scope": "Local hash consistency, not external attestation",
        }

    def export(self, *, max_events=10000):
        _integer(max_events, 0, 100000)
        identity = self.verify()
        rows = [
            {"seq": seq, "previous_hash": parent, "hash": head, **json.loads(raw)}
            for seq, parent, head, raw in self.db.execute(
                "SELECT seq,previous_hash,hash,payload FROM memory_events ORDER BY seq LIMIT ?",
                (max_events,),
            )
        ]
        return {
            "schema": VERSION,
            "binding": dict(self.binding),
            **identity,
            "records": rows,
            "truncated": len(rows) != identity["events"],
        }

    def close(self):
        self.db.close()
