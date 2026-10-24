"""Neural-context task selection with exact delayed-outcome attribution."""

import copy
import math

import numpy as np

from commerce.ledger import canonical
from commerce.tasks import HASH, digest, finite, validate_task

VERSION = "neural-task-policy-v1"
DIM = 17
RECEIPT_WINDOW = 512


class NeuralTaskPolicy:
    def __init__(self, state=None):
        state = copy.deepcopy(state or {})
        if state.get("version", VERSION) != VERSION:
            raise ValueError("Task policy version is incompatible")
        self.models = state.get("models", {})
        self.pending = state.get("pending", {})
        self.feedback_cursor = state.get("feedback_cursor", 0)
        self.completed = state.get("completed", 0)
        # Old checkpoints preserve their learned models and cursor. They cannot
        # authenticate receipts preceding the introduction of this bounded log.
        self.receipt_floor = state.get("receipt_floor", self.feedback_cursor)
        self.receipts = state.get("feedback_receipts", {})
        self.legacy_feedback_floor = state.get(
            "legacy_feedback_floor",
            self.feedback_cursor if "feedback_receipts" not in state else 0,
        )
        if (
            type(self.feedback_cursor) is not int
            or self.feedback_cursor < 0
            or type(self.completed) is not int
            or self.completed < 0
            or type(self.receipt_floor) is not int
            or not 0 <= self.receipt_floor <= self.feedback_cursor
            or type(self.legacy_feedback_floor) is not int
            or not 0 <= self.legacy_feedback_floor <= self.receipt_floor
        ):
            raise ValueError("Task policy counters are invalid")
        if len(self.models) > 32 or len(self.pending) > 32:
            raise ValueError("Task policy state exceeds its bounds")
        if not isinstance(self.receipts, dict) or len(self.receipts) > RECEIPT_WINDOW:
            raise ValueError("Task feedback receipt history exceeds its bounds")
        for seq, receipt in self.receipts.items():
            if (
                not isinstance(seq, str)
                or not seq.isascii()
                or not seq.isdecimal()
                or str(int(seq)) != seq
                or not self.receipt_floor < int(seq) <= self.feedback_cursor
                or not isinstance(receipt, dict)
                or set(receipt)
                != {"intent_id", "contract_sha256", "sha256", "executed"}
                or any(
                    not isinstance(value, str) or not HASH.fullmatch(value)
                    for key, value in receipt.items()
                    if key != "executed"
                )
                or type(receipt["executed"]) is not bool
            ):
                raise ValueError("Retained task feedback identity is malformed")
        for model in self.models.values():
            a, b = np.asarray(model["a"]), np.asarray(model["b"])
            if (
                a.shape != (DIM, DIM)
                or b.shape != (DIM,)
                or not np.isfinite(a).all()
                or not np.isfinite(b).all()
                or not np.allclose(a, a.T)
                or np.linalg.eigvalsh(a).min() < 0.999
            ):
                raise ValueError("Task policy learning state is malformed")
        for pending in self.pending.values():
            context = np.asarray(pending.get("neural_context", []))
            if (
                pending.get("model_key") not in self.models
                or context.shape != (DIM,)
                or not np.isfinite(context).all()
                or context[0] != 1
                or np.any(context < 0)
                or np.any(context > 1)
            ):
                raise ValueError("Pending neural task context is malformed")

    @staticmethod
    def context(features):
        values = np.asarray(features, dtype=float)
        if (
            values.shape != (64,)
            or not np.isfinite(values).all()
            or np.any(values < 0)
            or np.any(values > 1)
        ):
            raise ValueError("Recorded normalized neural features are required")
        return np.r_[1.0, values.reshape(16, 4).mean(axis=1)]

    def select(self, tasks, features, neural_hash, tick, cursor, trace_factory=None):
        if not tasks or len(self.pending) >= 32:
            return None
        if len(tasks) > 16 or len({task["id"] for task in tasks}) != len(tasks):
            raise ValueError("Task catalog exceeds its bound or repeats an identity")
        if (
            not isinstance(neural_hash, str)
            or not HASH.fullmatch(neural_hash)
            or type(tick) is not int
            or tick < 0
            or type(cursor) is not int
            or cursor < 0
        ):
            raise ValueError("Neural task origin is invalid")
        x = self.context(features)
        candidates = []
        for task in tasks:
            validate_task(task)
            contract = digest(task)
            if task["acceptance"]["type"] in (
                "transform_program",
                "transform_execution",
                "transform_active",
            ) and any(
                receipt["contract_sha256"] == contract and receipt["executed"]
                for receipt in self.receipts.values()
            ):
                continue
            key = digest(
                {
                    "goal_id": task["id"],
                    "provider": task["provider"],
                    "kind": task["kind"],
                    "action": task["action"],
                    "verifier": task["acceptance"]["type"],
                    "outcome": (task.get("outcome") or {}).get("type"),
                }
            )
            if key not in self.models:
                if len(self.models) >= 32:
                    continue
                self.models[key] = {"a": np.eye(DIM).tolist(), "b": [0.0] * DIM, "n": 0}
            if any(item["goal_id"] == task["id"] for item in self.pending.values()):
                continue
            model = self.models[key]
            a, b = np.asarray(model["a"]), np.asarray(model["b"])
            inverse_x = np.linalg.solve(a, x)
            expected = float(x @ np.linalg.solve(a, b))
            exploration = 0.15 * math.sqrt(max(0, float(x @ inverse_x)))
            # A disclosed engineering prior provides a neural readout before the
            # first outcome. Delayed feedback subsequently learns these weights.
            prior = np.asarray(
                [int(key[i : i + 2], 16) / 127.5 - 1 for i in range(0, 32, 2)]
            )
            neural = float(prior @ x[1:]) / 16 * 0.05 / (1 + model["n"])
            score = expected + exploration + neural
            candidates.append(
                {
                    "goal_id": task["id"],
                    "contract_sha256": contract,
                    "model_key": key,
                    "action": task["action"],
                    "provider": task["provider"],
                    "expected_utility": expected,
                    "exploration": exploration,
                    "initial_neural_readout": neural,
                    "score": score,
                }
            )
        if not candidates:
            return None
        chosen = max(candidates, key=lambda item: (item["score"], item["goal_id"]))
        trace = trace_factory(chosen["action"]) if trace_factory else None
        intent = {
            "schema": VERSION,
            **chosen,
            "tick": tick,
            "source_cursor": cursor,
            "neural_state_sha256": neural_hash,
            "neural_context": x.tolist(),
            "alternatives": candidates,
            "eligibility_sha256": digest(trace) if trace else None,
        }
        intent["id"] = digest(intent)
        self.pending[intent["id"]] = {
            k: intent[k]
            for k in (
                "contract_sha256",
                "model_key",
                "neural_context",
                "goal_id",
                "action",
            )
        }
        self.pending[intent["id"]]["synaptic_trace"] = trace
        return intent

    def learn(self, record):
        if not isinstance(record, dict) or len(canonical(record).encode()) > 65536:
            raise ValueError("Task feedback is malformed or oversized")
        seq = record.get("seq")
        if type(seq) is not int or not 1 <= seq <= 2**53 - 1:
            raise ValueError("Task feedback sequence is invalid")
        fingerprint = digest(record)
        if seq <= self.feedback_cursor:
            retained = self.receipts.get(str(seq))
            if retained is None:
                raise ValueError("Task feedback is outside the retained receipt window")
            if retained["sha256"] != fingerprint:
                raise ValueError("An already credited task receipt changed")
            return False
        ident = record.get("intent_id")
        pending = self.pending.get(ident)
        if (
            not pending
            or pending["contract_sha256"] != record.get("contract_sha256")
            or pending["goal_id"] != record.get("goal_id")
            or pending["action"] != record.get("action", pending["action"])
        ):
            raise ValueError("Outcome has no matching retained neural decision")
        if (
            record.get("evidence_level") != "verified_outcome"
            or not finite(record.get("reward"))
            or not -1 <= record["reward"] <= 1
        ):
            if (
                record.get("evidence_level") == "not_executed"
                and record.get("task_state") == "cancelled"
            ):
                self.pending.pop(ident)
                self.feedback_cursor = seq
                self._remember(record, fingerprint)
                return True
            raise ValueError("Unverified or malformed task outcome")
        if not isinstance(record.get("result_sha256"), str) or not HASH.fullmatch(
            record["result_sha256"]
        ):
            raise ValueError("Task outcome evidence is missing")
        x = np.asarray(pending["neural_context"])
        model = self.models[pending["model_key"]]
        model["a"] = (
            np.eye(DIM)
            + 0.995 * (np.asarray(model["a"]) - np.eye(DIM))
            + np.outer(x, x)
        ).tolist()
        model["b"] = (0.995 * np.asarray(model["b"]) + record["reward"] * x).tolist()
        model["n"] += 1
        self.pending.pop(ident)
        self.feedback_cursor = seq
        self.completed += 1
        self._remember(record, fingerprint)
        return True

    def _remember(self, record, fingerprint):
        self.receipts[str(record["seq"])] = {
            "intent_id": record["intent_id"],
            "contract_sha256": record["contract_sha256"],
            "sha256": fingerprint,
            "executed": record.get("evidence_level") == "verified_outcome",
        }
        if len(self.receipts) > RECEIPT_WINDOW:
            oldest = min(self.receipts, key=int)
            self.receipt_floor = max(self.receipt_floor, int(oldest))
            self.receipts.pop(oldest)

    def state(self):
        return copy.deepcopy(
            {
                "version": VERSION,
                "models": self.models,
                "pending": self.pending,
                "feedback_cursor": self.feedback_cursor,
                "completed": self.completed,
                "receipt_floor": self.receipt_floor,
                "feedback_receipts": self.receipts,
                "legacy_feedback_floor": self.legacy_feedback_floor,
            }
        )

    def public(self):
        return {
            "method": VERSION,
            "pending": len(self.pending),
            "completed": self.completed,
            "feedback_cursor": self.feedback_cursor,
            "receipt_floor": self.receipt_floor,
            "retained_feedback_receipts": len(self.receipts),
            "legacy_feedback_floor": self.legacy_feedback_floor,
            "scope": "Neural-context ridge/UCB task selection. Exact originating context receives verified delayed utility; this is distinct from a proven synaptic or consciousness advantage.",
        }
