"""Bounded contextual intervention choices and buyer-verified delayed credit.

This is a conventional discounted ridge/UCB controller, not a new learning
algorithm. Arm zero explicitly means no intervention. No SQL, executable
artifact, external account or payment authority enters its checkpoint.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

import numpy as np

VERSION = "intervention-memory-v1"
ARMS = (0, 1, 2, 3)
MAX_PENDING = 512
MAX_RECEIPTS = 512
MAX_DIMENSION = 64
MAX_COUNTER = 2**53 - 1
HASH = re.compile(r"[a-f0-9]{64}\Z")
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")


def _hash(value):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_COUNTER


class InterventionMemory:
    """Keep source-specific selected feedback and measured artifact evidence.

    Contexts are bounded input vectors; an intercept is added internally.
    Previously observed evidence decays once per completed verified receipt,
    including receipts for another arm. Unselected outcomes never enter state.
    The first assignment to each arm is a disclosed coverage rule; pending
    assignments count toward coverage, so delayed feedback does not repeat it.
    Receipt sequence numbers are monotonic, but delivery order may differ from
    originating decision order. A retained receipt is exactly idempotent;
    receipts older than the bounded deduplication window are refused.
    """

    def __init__(
        self,
        source_id: str,
        dimension: int,
        state: dict | None = None,
        *,
        discount=0.98,
        alpha=0.25,
    ):
        if not isinstance(source_id, str) or not SOURCE.fullmatch(source_id):
            raise ValueError("A bounded source identity is required")
        if type(dimension) is not int or not 1 <= dimension <= MAX_DIMENSION:
            raise ValueError("Intervention context dimension is invalid")
        if not _finite(discount) or not 0 < discount <= 1:
            raise ValueError("Intervention discount must be in (0, 1]")
        if not _finite(alpha) or not 0 <= alpha <= 10:
            raise ValueError("Intervention exploration coefficient is invalid")
        self.source_id, self.dimension = source_id, dimension
        self.discount, self.alpha = float(discount), float(alpha)
        width = dimension + 1
        supplied = copy.deepcopy(state) if state is not None else None
        if supplied is None:
            supplied = {
                "version": VERSION,
                "source_id": source_id,
                "dimension": dimension,
                "discount": self.discount,
                "alpha": self.alpha,
                "models": {
                    str(arm): {
                        "a": np.eye(width).tolist(),
                        "b": [0.0] * width,
                        "n": 0,
                        "updated": 0,
                    }
                    for arm in ARMS
                },
                "pending": {},
                "completed": 0,
                "feedback_cursor": 0,
                "receipts": {},
                "capabilities": {},
                "last_step": -1,
                "last_intent": None,
            }
        required = {
            "version",
            "source_id",
            "dimension",
            "discount",
            "alpha",
            "models",
            "pending",
            "completed",
            "feedback_cursor",
            "receipts",
            "capabilities",
            "last_step",
            "last_intent",
        }
        if (
            not isinstance(supplied, dict)
            or set(supplied) != required
            or supplied["version"] != VERSION
            or supplied["source_id"] != source_id
            or type(supplied["dimension"]) is not int
            or supplied["dimension"] != dimension
            or not _finite(supplied["discount"])
            or supplied["discount"] != self.discount
            or not _finite(supplied["alpha"])
            or supplied["alpha"] != self.alpha
        ):
            raise ValueError("Intervention checkpoint terms differ or are malformed")
        self.completed = supplied["completed"]
        self.feedback_cursor = supplied["feedback_cursor"]
        self.last_step = supplied["last_step"]
        if (
            not _integer(self.completed)
            or not _integer(self.feedback_cursor)
            or self.completed > self.feedback_cursor
            or not _integer(self.last_step, -1)
        ):
            raise ValueError("Intervention checkpoint counters are invalid")
        self.models = supplied["models"]
        if not isinstance(self.models, dict) or set(self.models) != {
            str(arm) for arm in ARMS
        }:
            raise ValueError("Intervention arm models are invalid")
        observations = 0
        for model in self.models.values():
            if not isinstance(model, dict) or set(model) != {"a", "b", "n", "updated"}:
                raise ValueError("Intervention model fields are invalid")
            try:
                a = np.asarray(model["a"])
                b = np.asarray(model["b"])
            except (TypeError, ValueError):
                raise ValueError("Intervention model arrays are malformed") from None
            if (
                a.shape != (width, width)
                or b.shape != (width,)
                or a.dtype.kind not in "fiu"
                or b.dtype.kind not in "fiu"
                or not np.isfinite(a).all()
                or not np.isfinite(b).all()
                or not np.allclose(a, a.T, rtol=0, atol=1e-10)
                or np.linalg.eigvalsh(a).min() < 1 - 1e-9
                or not _integer(model["n"])
                or not _integer(model["updated"])
                or model["updated"] > self.completed
            ):
                raise ValueError("Intervention model learning state is invalid")
            observations += model["n"]
        if observations != self.completed:
            raise ValueError("Intervention observation counts disagree")
        self.pending = supplied["pending"]
        self.receipts = supplied["receipts"]
        self._capabilities = supplied["capabilities"]
        self.last_intent = supplied["last_intent"]
        if (
            not isinstance(self.pending, dict)
            or len(self.pending) > MAX_PENDING
            or not isinstance(self.receipts, dict)
            or len(self.receipts) > MAX_RECEIPTS
            or len(self.receipts) != min(self.completed, MAX_RECEIPTS)
            or not isinstance(self._capabilities, dict)
            or set(self._capabilities)
            != {arm for arm, model in self.models.items() if model["n"] > 0}
        ):
            raise ValueError("Intervention checkpoint exceeds its bounds")
        pending_steps = set()
        for ident, intent in self.pending.items():
            self._validate_intent(intent)
            if (
                intent["id"] != ident
                or intent["step"] in pending_steps
                or ident in self.receipts
            ):
                raise ValueError("Intervention pending identity is inconsistent")
            pending_steps.add(intent["step"])
        sequences = set()
        for ident, receipt in self.receipts.items():
            if (
                not isinstance(ident, str)
                or not HASH.fullmatch(ident)
                or not isinstance(receipt, dict)
                or set(receipt) != {"sequence", "sha256"}
                or not _integer(receipt["sequence"], 1)
                or receipt["sequence"] > self.feedback_cursor
                or receipt["sequence"] in sequences
                or not isinstance(receipt["sha256"], str)
                or not HASH.fullmatch(receipt["sha256"])
            ):
                raise ValueError("Intervention receipt cache is malformed")
            sequences.add(receipt["sequence"])
        if sequences and max(sequences) != self.feedback_cursor:
            raise ValueError("Intervention receipt cursor is inconsistent")
        for arm, evidence in self._capabilities.items():
            self._validate_capability(int(arm), evidence)
        if self.last_intent is None:
            if self.last_step != -1 or self.pending or self.completed:
                raise ValueError("Intervention latest decision is missing")
        else:
            self._validate_intent(self.last_intent)
            if self.last_intent["step"] != self.last_step or (
                self.last_intent["id"] not in self.pending
                and self.last_intent["id"] not in self.receipts
            ):
                raise ValueError("Intervention latest decision step is inconsistent")

    def _context(self, context):
        if (
            not isinstance(context, list)
            or len(context) != self.dimension
            or any(not _finite(v) or not -1 <= v <= 1 for v in context)
        ):
            raise ValueError("Intervention context must be a finite bounded vector")
        return np.asarray([1.0, *context], dtype=float)

    def _validate_intent(self, intent):
        required = {
            "id",
            "method",
            "source_id",
            "step",
            "arm",
            "context",
            "scores",
            "completed_before",
            "selection",
            "covered_arms_before",
        }
        if (
            not isinstance(intent, dict)
            or set(intent) != required
            or intent["method"] != VERSION
            or intent["source_id"] != self.source_id
            or type(intent["arm"]) is not int
            or intent["arm"] not in ARMS
            or not _integer(intent["step"])
            or intent["step"] > self.last_step
            or not _integer(intent["completed_before"])
            or intent["completed_before"] > self.completed
            or not isinstance(intent["scores"], list)
            or len(intent["scores"]) != len(ARMS)
        ):
            raise ValueError("Intervention originating decision is malformed")
        self._context(intent["context"])
        covered = intent["covered_arms_before"]
        if (
            not isinstance(covered, list)
            or any(type(arm) is not int or arm not in ARMS for arm in covered)
            or covered != sorted(set(covered))
        ):
            raise ValueError("Intervention initial coverage evidence is malformed")
        for arm, score in zip(ARMS, intent["scores"], strict=True):
            if (
                not isinstance(score, dict)
                or set(score) != {"arm", "expected_utility", "uncertainty", "score"}
                or type(score["arm"]) is not int
                or score["arm"] != arm
                or any(
                    not _finite(score[key])
                    for key in ("expected_utility", "uncertainty", "score")
                )
                or score["uncertainty"] < 0
                or not math.isclose(
                    score["score"],
                    score["expected_utility"] + self.alpha * score["uncertainty"],
                    rel_tol=0,
                    abs_tol=1e-10,
                )
            ):
                raise ValueError("Intervention originating scores are malformed")
        uncovered = [arm for arm in ARMS if arm not in covered]
        expected_selection = "initial_coverage" if uncovered else "ridge_ucb"
        expected_arm = (
            uncovered[0]
            if uncovered
            else max(intent["scores"], key=lambda row: (row["score"], -row["arm"]))[
                "arm"
            ]
        )
        if intent["arm"] != expected_arm or intent["selection"] != expected_selection:
            raise ValueError("Intervention selected arm differs from its scores")
        if intent["id"] != _hash({k: v for k, v in intent.items() if k != "id"}):
            raise ValueError("Intervention originating decision commitment changed")

    def _effective(self, model):
        elapsed = self.completed - model["updated"]
        factor = self.discount**elapsed
        identity = np.eye(self.dimension + 1)
        return (
            identity + factor * (np.asarray(model["a"]) - identity),
            factor * np.asarray(model["b"]),
        )

    def choose(self, context: list[float], step: int) -> dict:
        x = self._context(context)
        if not _integer(step):
            raise ValueError("Intervention decision step is invalid")
        if step == self.last_step:
            if self.last_intent["context"] != context:
                raise ValueError("A committed intervention step cannot change context")
            return copy.deepcopy(self.last_intent)
        if step < self.last_step:
            raise ValueError("Intervention decision steps cannot move backwards")
        if len(self.pending) >= MAX_PENDING:
            raise ValueError("Too many unresolved intervention decisions")
        scores = []
        for arm in ARMS:
            a, b = self._effective(self.models[str(arm)])
            expected = float(x @ np.linalg.solve(a, b))
            uncertainty = math.sqrt(max(0.0, float(x @ np.linalg.solve(a, x))))
            scores.append(
                {
                    "arm": arm,
                    "expected_utility": expected,
                    "uncertainty": uncertainty,
                    "score": expected + self.alpha * uncertainty,
                }
            )
        # Explicit initial coverage prevents a positive first outcome from
        # indefinitely excluding all untried interventions. This is an
        # engineering exploration rule, not a learned or neural choice.
        covered = sorted(
            {
                arm
                for arm in ARMS
                if self.models[str(arm)]["n"] > 0
                or any(intent["arm"] == arm for intent in self.pending.values())
            }
        )
        uncovered = [arm for arm in ARMS if arm not in covered]
        # After coverage, stable UCB ties prefer no intervention.
        arm = (
            uncovered[0]
            if uncovered
            else max(scores, key=lambda row: (row["score"], -row["arm"]))["arm"]
        )
        intent = {
            "method": VERSION,
            "source_id": self.source_id,
            "step": step,
            "arm": arm,
            "context": [float(v) for v in context],
            "scores": scores,
            "completed_before": self.completed,
            "selection": "initial_coverage" if uncovered else "ridge_ucb",
            "covered_arms_before": covered,
        }
        intent["id"] = _hash(intent)
        self.pending[intent["id"]] = copy.deepcopy(intent)
        self.last_step = step
        self.last_intent = copy.deepcopy(intent)
        return intent

    @staticmethod
    def utility(cost, reference_cost):
        """Normalize only the selected verified cost against a declared scale.

        The caller supplies the fixed public scale. This method neither requests
        nor infers the cost of an unselected counterfactual intervention.
        """
        if (
            not _finite(cost)
            or not 0 <= cost <= 1e18
            or not _finite(reference_cost)
            or not 0 < reference_cost <= 1e18
        ):
            raise ValueError("Verified intervention costs are invalid")
        return max(-1.0, min(1.0, 1.0 - cost / reference_cost))

    def learn(self, receipt: dict) -> bool:
        required = {
            "intent_id",
            "source_id",
            "arm",
            "cost",
            "reference_cost",
            "artifact_sha256",
            "result_sha256",
            "verified",
            "sequence",
        }
        if (
            not isinstance(receipt, dict)
            or set(receipt) != required
            or receipt["source_id"] != self.source_id
            or type(receipt["arm"]) is not int
            or receipt["arm"] not in ARMS
            or receipt["verified"] is not True
            or not _integer(receipt["sequence"], 1)
            or any(
                not isinstance(receipt[key], str) or not HASH.fullmatch(receipt[key])
                for key in ("intent_id", "artifact_sha256", "result_sha256")
            )
        ):
            raise ValueError("A source-bound verified intervention receipt is required")
        utility = self.utility(receipt["cost"], receipt["reference_cost"])
        ident, sequence = receipt["intent_id"], receipt["sequence"]
        digest = _hash(receipt)
        previous = self.receipts.get(ident)
        if previous:
            if previous != {"sequence": sequence, "sha256": digest}:
                raise ValueError("Previously learned intervention receipt changed")
            return False
        if sequence <= self.feedback_cursor:
            raise ValueError(
                "Old receipt is outside the verifiable deduplication window"
            )
        intent = self.pending.get(ident)
        if intent is None or intent["arm"] != receipt["arm"]:
            raise ValueError("Receipt has no matching originating intervention")
        x = self._context(intent["context"])
        model = self.models[str(intent["arm"])]
        a, b = self._effective(model)
        identity = np.eye(self.dimension + 1)
        next_model = {
            "a": (identity + self.discount * (a - identity) + np.outer(x, x)).tolist(),
            "b": (self.discount * b + utility * x).tolist(),
            "n": model["n"] + 1,
            "updated": self.completed + 1,
        }
        evidence = {
            "arm": intent["arm"],
            "source_id": self.source_id,
            "artifact_sha256": receipt["artifact_sha256"],
            "result_sha256": receipt["result_sha256"],
            "observed_cost": receipt["cost"],
            "reference_cost": receipt["reference_cost"],
            "utility": utility,
            "origin_intent": ident,
            "sequence": sequence,
            "observations": model["n"] + 1,
        }
        self.models[str(intent["arm"])] = next_model
        self._capabilities[str(intent["arm"])] = evidence
        self.pending.pop(ident)
        self.receipts[ident] = {"sequence": sequence, "sha256": digest}
        while len(self.receipts) > MAX_RECEIPTS:
            oldest = min(self.receipts, key=lambda key: self.receipts[key]["sequence"])
            self.receipts.pop(oldest)
        self.feedback_cursor = sequence
        self.completed += 1
        return True

    def _validate_capability(self, arm, evidence):
        required = {
            "arm",
            "source_id",
            "artifact_sha256",
            "result_sha256",
            "observed_cost",
            "reference_cost",
            "utility",
            "origin_intent",
            "sequence",
            "observations",
        }
        if (
            not isinstance(evidence, dict)
            or set(evidence) != required
            or type(evidence["arm"]) is not int
            or evidence["arm"] != arm
            or evidence["source_id"] != self.source_id
            or not _integer(evidence["sequence"], 1)
            or evidence["sequence"] > self.feedback_cursor
            or not _integer(evidence["observations"], 1)
            or evidence["observations"] != self.models[str(arm)]["n"]
            or any(
                not isinstance(evidence[key], str) or not HASH.fullmatch(evidence[key])
                for key in ("artifact_sha256", "result_sha256", "origin_intent")
            )
            or not _finite(evidence["utility"])
            or evidence["utility"]
            != self.utility(evidence["observed_cost"], evidence["reference_cost"])
        ):
            raise ValueError("Retained intervention capability evidence is malformed")

    @property
    def capabilities(self):
        """Copies of latest buyer-verified evidence, at most one record per arm."""
        return copy.deepcopy(self._capabilities)

    def state(self):
        return copy.deepcopy(
            {
                "version": VERSION,
                "source_id": self.source_id,
                "dimension": self.dimension,
                "discount": self.discount,
                "alpha": self.alpha,
                "models": self.models,
                "pending": self.pending,
                "completed": self.completed,
                "feedback_cursor": self.feedback_cursor,
                "receipts": self.receipts,
                "capabilities": self._capabilities,
                "last_step": self.last_step,
                "last_intent": self.last_intent,
            }
        )
