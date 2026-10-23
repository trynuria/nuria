"""Isolated real-compute intervention loop with joint neural recovery.

The sensory mapping and reward rule are explicit engineering choices. This
runner establishes wiring, checked SQL results and persistence; comparative
performance and a useful neural contribution require separate evaluations.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import secrets
from pathlib import Path

import brian2
import numpy as np

from cognition import (
    brain,
    intervention_encoding,
    intervention_memory,
    sqlite_lab,
    task_brain,
)
from cognition.brain import MODEL_VERSION, sha
from cognition.intervention_encoding import InterventionBrain
from cognition.intervention_memory import InterventionMemory
from cognition.journal import Journal, canonical
from cognition.sqlite_lab import FAMILIES, SQLiteLab, _queries

VERSION = "sqlite-neural-intervention-runner-v1"
REFERENCE_COST = 2_000_000
ACTION_BY_ARM = ("rest", "explore", "predict", "experiment")
MAX_SCHEDULE_BYTES = 4 * 1024 * 1024


def digest(value):
    return sha(canonical(value))


def validate_input(value, source_id):
    if (
        type(value) is not dict
        or set(value) != {"source_id", "sequence", "queries"}
        or value["source_id"] != source_id
        or type(value["sequence"]) is not int
        or not 0 <= value["sequence"] <= 2**53 - 1
    ):
        raise ValueError("A bounded source-bound workload sequence is required")
    return {**value, "queries": _queries(value["queries"])}


class InterventionRunner:
    """Run one registered SQLite intervention per observed workload.

    New state directories must be empty. Resume requires the exact source,
    dataset, implementation, circuit/runtime and controller configuration.
    Input identity is immutable and sequence order cannot be changed. Delayed
    feedback, controller state and trusted Brian checkpoint commit together.

    Attempt disclosures survive a crash outside the cognitive transaction. Any
    unfinished attempt makes cumulative physical-compute accounting uncertain;
    retry may repeat local computation but never repeats a financial action.
    """

    def __init__(
        self,
        directory: Path,
        csv_bytes: bytes,
        source_id: str,
        *,
        discount=0.98,
        alpha=0.75,
        reference_cost=REFERENCE_COST,
        feedback_delay=2,
    ):
        # Validate numerical terms before creating files or network state.
        self.policy = InterventionMemory(source_id, 23, discount=discount, alpha=alpha)
        InterventionMemory.utility(0, reference_cost)
        if type(feedback_delay) is not int or not 0 <= feedback_delay <= 64:
            raise ValueError("Local feedback delay must be between zero and 64 steps")
        self.directory = Path(directory)
        self.source_id = source_id
        self.reference_cost = reference_cost
        self.feedback_delay = feedback_delay
        self.lab = SQLiteLab(csv_bytes)
        self.lock = None
        self.journal = None
        self.closed = False
        self.failed = False
        self.accounting_uncertain = False
        self.last_sequence = None
        self.previous_histogram = [0.0] * 3
        self.pending_feedback = []
        self.next_feedback_sequence = 1
        self.finished = False
        self.terms = {
            "schema": VERSION,
            "scope": "isolated local SQL/neural lab; no live market input, RPC, account or payment authority",
            "source_id": source_id,
            "dataset_sha256": self.lab.dataset_sha256,
            "rows": self.lab.rows,
            "circuit": MODEL_VERSION,
            "controller": intervention_memory.VERSION,
            "discount": float(discount),
            "alpha": float(alpha),
            "reference_cost": reference_cost,
            "feedback_delay_steps": feedback_delay,
            "window_ms": 20,
            "encoding": "sqlite-query-histogram-v1: current family histogram * 0.9 plus query count / 32 * 0.25 at sensory neurons 0:32,32:64,64:96; query count / 32 * 0.65 at 96:128; no TVB simulator",
            "context": "current three-family histogram, previous three-family histogram, query count / 32, sixteen pooled actual spike features",
            "action_by_arm": list(ACTION_BY_ARM),
            "brian2_version": brian2.__version__,
            "numpy_version": np.__version__,
            "sqlite_version": self.lab._sqlite.version,
            "sqlite_source_id": self.lab._sqlite.source_id,
            "implementation_sha256": {
                Path(module.__file__).name: sha(Path(module.__file__).read_bytes())
                for module in (
                    brain,
                    intervention_encoding,
                    intervention_memory,
                    sqlite_lab,
                    task_brain,
                )
            }
            | {Path(__file__).name: sha(Path(__file__).read_bytes())},
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            marker = self.directory / "SQLITE-NEURAL-LAB.json"
            if marker.exists():
                if json.loads(marker.read_text()) != self.terms:
                    raise ValueError("Existing lab identity or implementation differs")
            else:
                if any(self.directory.iterdir()):
                    raise ValueError(
                        "An unrelated or unmarked history cannot become a lab"
                    )
                with marker.open("xb") as stream:
                    os.chmod(marker, 0o600)
                    stream.write(canonical(self.terms))
                    stream.flush()
                    os.fsync(stream.fileno())
            self.lock = (self.directory / "writer.lock").open("a")
            try:
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another writer owns this isolated lab") from None
            self.journal = Journal(self.directory)
            if not self.journal.verify(full=True)["valid"]:
                raise RuntimeError("Isolated intervention journal integrity failed")
            self.brain = InterventionBrain(self.directory / "neural")
            restored = self.journal.restore()
            if restored:
                metadata, network = restored
                if metadata["terms_sha256"] != digest(self.terms):
                    raise ValueError("Checkpoint belongs to a different lab")
                self.brain.restore(network, metadata["brain"])
                self.policy = InterventionMemory(
                    source_id, 23, metadata["policy"], discount=discount, alpha=alpha
                )
                self.last_sequence = metadata["last_sequence"]
                self.previous_histogram = metadata["previous_histogram"]
                self.pending_feedback = metadata["pending_feedback"]
                self.next_feedback_sequence = metadata["next_feedback_sequence"]
                self.finished = metadata["finished"]
                self.accounting_uncertain = metadata["accounting_uncertain"]
                self._validate_restored()
            else:
                self.save()
            if self._unfinished_attempts():
                self.accounting_uncertain = True
        except BaseException:
            self.close()
            raise

    def _validate_restored(self):
        if self.last_sequence is not None and (
            type(self.last_sequence) is not int
            or not 0 <= self.last_sequence <= 2**53 - 1
        ):
            raise ValueError("Checkpoint source cursor is malformed")
        if (
            type(self.previous_histogram) is not list
            or len(self.previous_histogram) != 3
            or any(
                type(value) not in (int, float)
                or not np.isfinite(value)
                or not 0 <= value <= 1
                for value in self.previous_histogram
            )
            or not np.isclose(
                sum(self.previous_histogram), 0 if self.last_sequence is None else 1
            )
            or type(self.pending_feedback) is not list
            or len(self.pending_feedback) > self.feedback_delay + 1
            or type(self.finished) is not bool
            or type(self.accounting_uncertain) is not bool
            or type(self.next_feedback_sequence) is not int
            or self.next_feedback_sequence
            != self.policy.completed + len(self.pending_feedback) + 1
            or len(self.pending_feedback) != len(self.policy.pending)
        ):
            raise ValueError("Checkpoint intervention state is malformed")
        for offset, pending in enumerate(self.pending_feedback, 1):
            if (
                type(pending) is not dict
                or set(pending) != {"receipt", "trace", "due_step"}
                or pending["receipt"].get("intent_id") not in self.policy.pending
                or pending["receipt"].get("sequence") != self.policy.completed + offset
                or type(pending["due_step"]) is not int
                or pending["due_step"] < 0
            ):
                raise ValueError("Retained delayed receipt is malformed")

    def _attempt(self, payload):
        path = self.directory / "attempts.jsonl"
        with path.open("ab") as stream:
            os.chmod(path, 0o600)
            stream.write(canonical(payload) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _unfinished_attempts(self):
        path = self.directory / "attempts.jsonl"
        if not path.exists():
            return 0
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Local attempt disclosure exceeds its inspection bound")
        pending = set()
        for line in path.read_bytes().splitlines():
            value = json.loads(line)
            if value.get("status") == "started":
                pending.add(value["attempt_id"])
            elif value.get("status") == "committed":
                pending.discard(value["attempt_id"])
            else:
                raise ValueError("Local attempt disclosure is malformed")
        return len(pending)

    def _credit(self, step, *, force=False):
        applied = []
        while self.pending_feedback and (
            force or self.pending_feedback[0]["due_step"] <= step
        ):
            pending = self.pending_feedback.pop(0)
            receipt = pending["receipt"]
            if not self.policy.learn(receipt):
                raise RuntimeError(
                    "Delayed lab receipt was unexpectedly consumed twice"
                )
            reward = self.policy.utility(receipt["cost"], receipt["reference_cost"])
            credit = self.brain.task_reward(pending["trace"], reward)
            applied.append(
                {"receipt": receipt, "utility": reward, "neural_credit": credit}
            )
        return applied

    def observe(self, value):
        if self.closed or self.failed:
            raise RuntimeError("Interrupted or closed lab must be reopened")
        value = validate_input(value, self.source_id)
        ident = f"{self.source_id}:{value['sequence']}"
        input_sha256 = digest(value)
        prior = self.journal.db.execute(
            "SELECT event_order,payload FROM effects WHERE input_id=?", (ident,)
        ).fetchone()
        if prior:
            record = json.loads(prior[1])
            if prior[0] != value["sequence"] or record["input_sha256"] != input_sha256:
                raise ValueError("A committed workload prefix cannot change")
            return record
        if self.finished:
            raise ValueError("A finalized lab cannot accept additional workloads")
        if (
            self.last_sequence is not None
            and value["sequence"] != self.last_sequence + 1
        ):
            raise ValueError(
                "Workload source sequence changed order or skipped an input"
            )
        attempt = secrets.token_hex(16)
        self._attempt(
            {"attempt_id": attempt, "status": "started", "input_sha256": input_sha256}
        )
        try:
            before = self.brain.state_hash()
            credited = self._credit(value["sequence"])
            queries = value["queries"]
            histogram = [
                sum(query["family"] == family for query in queries) / len(queries)
                for family in range(3)
            ]
            count = len(queries) / 32
            raw_context = histogram + self.previous_histogram + [count]
            frame = self.brain.advance(
                {"context": raw_context}, duration_ms=20, context={"energy": 1}
            )
            pooled = np.asarray(frame["features"]).reshape(16, 4).mean(axis=1).tolist()
            intent = self.policy.choose(raw_context + pooled, value["sequence"])
            trace = self.brain.task_trace(ACTION_BY_ARM[intent["arm"]])
            selected = self.lab.run(intent["arm"], queries)
            reference = self.lab.run(0, queries)
            if selected["results"] != reference["results"]:
                raise RuntimeError(
                    "Intervention changed the independently checked SQL answer"
                )
            artifact = {
                "schema": "nuria.sqlite-index-artifact.v1",
                "dataset_sha256": self.lab.dataset_sha256,
                "arm": intent["arm"],
                "index_columns": list(FAMILIES[intent["arm"] - 1])
                if intent["arm"]
                else [],
                "capacity": "one registered index or none",
            }
            receipt = {
                "intent_id": intent["id"],
                "source_id": self.source_id,
                "arm": intent["arm"],
                "cost": selected["total_steps"],
                "reference_cost": self.reference_cost,
                "artifact_sha256": digest(artifact),
                "result_sha256": selected["results_sha256"],
                "verified": True,
                "sequence": self.next_feedback_sequence,
            }
            self.next_feedback_sequence += 1
            self.pending_feedback.append(
                {
                    "receipt": receipt,
                    "trace": trace,
                    "due_step": value["sequence"] + self.feedback_delay,
                }
            )
            if self.feedback_delay == 0:
                credited.extend(self._credit(value["sequence"]))
            self.previous_histogram = histogram
            self.last_sequence = value["sequence"]
            payload = {
                "source_id": self.source_id,
                "input_sequence": value["sequence"],
                "input_sha256": input_sha256,
                "input": value,
                "before_neural_state_sha256": before,
                "decision_neural_state_sha256": frame["after_state_sha256"],
                "after_neural_state_sha256": self.brain.state_hash(),
                "spikes_sha256": frame["spikes_sha256"],
                "intent": intent,
                "originating_eligibility_sha256": digest(trace),
                "artifact": artifact,
                "receipt": receipt,
                "selected": selected,
                "verification": {
                    "correct": True,
                    "reference_results_sha256": reference["results_sha256"],
                    "common_overhead_vm_steps": reference["total_steps"],
                    "common_overhead_cpu_seconds": reference["cpu_seconds"],
                    "common_overhead_wall_seconds": reference["wall_seconds"],
                    "scope": "Unindexed answer verification; overhead reported separately, not a hidden counterfactual learning signal",
                },
                "applied_delayed_feedback": credited,
                "accounting_uncertain": self.accounting_uncertain,
                "financial_transactions": 0,
                "scope": "Real isolated SQLite computation and Brian2 wiring; no paid purchase, production workload or demonstrated neural performance advantage",
            }
            record = self.journal.append(
                "sqlite_intervention", payload, frame["spikes"]
            )
            self.journal.db.execute(
                "INSERT INTO effects VALUES(?,?,?)",
                (ident, value["sequence"], canonical(record).decode()),
            )
            self.save()
            self._attempt(
                {
                    "attempt_id": attempt,
                    "status": "committed",
                    "record_hash": record["hash"],
                }
            )
            return record
        except BaseException:
            self.journal.db.rollback()
            self.failed = True
            self.accounting_uncertain = True
            raise

    def finish(self):
        if self.closed or self.failed:
            raise RuntimeError("Interrupted or closed lab must be reopened")
        if self.finished:
            return self.summary()
        try:
            before = self.brain.state_hash()
            credited = self._credit(self.last_sequence or 0, force=True)
            self.finished = True
            self.journal.append(
                "sqlite_intervention_completion",
                {
                    "source_id": self.source_id,
                    "before_neural_state_sha256": before,
                    "after_neural_state_sha256": self.brain.state_hash(),
                    "applied_delayed_feedback": credited,
                    "accounting_uncertain": self.accounting_uncertain,
                },
            )
            self.save()
            return self.summary()
        except BaseException:
            self.journal.db.rollback()
            self.failed = True
            raise

    def save(self):
        network = self.brain.checkpoint()
        self.journal.save(
            {
                "terms_sha256": digest(self.terms),
                "record_seq": self.journal.seq,
                "record_head": self.journal.head,
                "brain": {
                    "model": MODEL_VERSION,
                    "network_sha256": sha(network),
                    "topology_sha256": self.brain.topology["sha256"],
                    "offset": self.brain.offset,
                    "frames": self.brain.frames,
                    "total_spikes": self.brain.total_spikes,
                    "rate_history": self.brain.rate_history,
                },
                "policy": self.policy.state(),
                "last_sequence": self.last_sequence,
                "previous_histogram": self.previous_histogram,
                "pending_feedback": self.pending_feedback,
                "next_feedback_sequence": self.next_feedback_sequence,
                "finished": self.finished,
                "accounting_uncertain": self.accounting_uncertain,
            },
            network,
        )

    def state(self):
        return copy.deepcopy(
            {
                "policy": self.policy.state(),
                "neural_state_sha256": self.brain.state_hash(),
                "last_sequence": self.last_sequence,
                "previous_histogram": self.previous_histogram,
                "pending_feedback": self.pending_feedback,
                "finished": self.finished,
            }
        )

    def summary(self):
        rows = [
            json.loads(row[0])
            for row in self.journal.db.execute(
                "SELECT payload FROM effects ORDER BY event_order"
            )
        ]
        return {
            "schema": VERSION,
            "terms": copy.deepcopy(self.terms),
            "observations": len(rows),
            "last_sequence": self.last_sequence,
            "finished": self.finished,
            "completed_receipts": self.policy.completed,
            "pending_receipts": len(self.pending_feedback),
            "neural_state_sha256": self.brain.state_hash(),
            "policy_sha256": digest(self.policy.state()),
            "capabilities": self.policy.capabilities,
            "committed_selected_vm_steps": sum(
                row["selected"]["total_steps"] for row in rows
            ),
            "committed_validation_vm_steps": sum(
                row["verification"]["common_overhead_vm_steps"] for row in rows
            ),
            "all_results_verified": all(row["verification"]["correct"] for row in rows),
            "accounting_uncertain": self.accounting_uncertain,
            "unfinished_attempts": self._unfinished_attempts(),
            "integrity": self.journal.verify(full=True),
            "financial_transactions": 0,
            "scope": "Selected local computation and joint neural/controller continuity. Committed VM counts exclude parsing, cloning, plans and neural computation. Interrupted physical compute may repeat and is not claimed exact. Separate evaluation is required for useful learning or neural advantage.",
        }

    def close(self):
        if getattr(self, "closed", False):
            return
        self.closed = True
        if self.journal is not None:
            self.journal.db.close()
        if self.lock is not None:
            self.lock.close()
        self.lab.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
