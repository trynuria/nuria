"""Outcome memory and loss-based selection of next-side forecast specialists.

This is a statistical learner alongside the spiking circuit. Its gains must not
be attributed to synaptic plasticity without a separate neural ablation.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

VERSION = "forecast-council-v1"
EXPERTS = (
    "frequency",
    "repeat",
    "amount",
    "fee_rate",
    "joint_context",
    "context_logistic",
    "neural",
)


def context(event: dict) -> tuple[int, int, int]:
    amount = float(event["quote_amount"])
    fee = float(event.get("creator_fee", 0))
    if event["side"] not in ("buy", "sell") or not math.isfinite(amount) or amount <= 0:
        raise ValueError("Invalid forecast input")
    if not math.isfinite(fee) or fee < 0:
        raise ValueError("Invalid fee signal")
    # Fixed public encodings, not fitted to evaluation labels or wallet identity.
    amount_bin = min(15, max(0, int(math.floor(math.log2(amount * 16)))))
    fee_bin = min(15, int(fee / amount * 10000 / 5))
    return int(event["side"] == "buy"), amount_bin, fee_bin


class OutcomeTable:
    """Bounded conditional transition counts, with lazy exponential forgetting."""

    def __init__(self, state: dict | None = None, decay: float = 0.999):
        self.cells = (state or {}).get("cells", {})
        self.decay = decay

    def counts(self, key: str, step: int) -> tuple[float, float]:
        positive, total, updated = self.cells.get(key, [0.0, 0.0, step])
        discount = self.decay ** max(0, step - updated)
        return positive * discount, total * discount

    def probability(self, key: str, step: int) -> float:
        positive, total = self.counts(key, step)
        return (positive + 1) / (total + 2)

    def learn(self, key: str, outcome: int, step: int) -> None:
        positive, total = self.counts(key, step)
        self.cells[key] = [positive + outcome, total + 1, step]

    def state(self) -> dict:
        return {"cells": self.cells}


class ContextLogistic:
    """Online logistic transition specialist with one-hot public contexts."""

    def __init__(self, state: dict | None = None):
        self.weights = np.asarray((state or {}).get("weights", [0.0] * 33))

    @staticmethod
    def features(amount: int, fee: int) -> np.ndarray:
        x = np.zeros(33)
        x[0] = x[1 + amount] = x[17 + fee] = 1
        return x

    def probability(self, amount: int, fee: int) -> float:
        score = float(np.clip(self.weights @ self.features(amount, fee), -12, 12))
        return 1 / (1 + math.exp(-score))

    def learn(self, amount: int, fee: int, repeated: int) -> None:
        p = self.probability(amount, fee)
        self.weights += 0.25 * (
            (repeated - p) * self.features(amount, fee) - 0.0001 * self.weights
        )

    def state(self) -> dict:
        return {"weights": self.weights.tolist()}


class ForecastCouncil:
    """Predict first; score the stored forecast when the following side arrives.

    One instance belongs to one source. The caller separates test/live sources.
    Every expert receives the same observed outcome. Attention uses discounted
    exponential loss weights; it has no access to task names or future labels.
    """

    def __init__(
        self,
        state: dict | None = None,
        *,
        memory: bool = True,
        attention: bool = True,
        neural: bool = True,
    ):
        state = state or {}
        if state and state.get("version") != VERSION:
            raise ValueError("Unsupported forecast checkpoint")
        self.memory_enabled = memory
        self.attention_enabled = attention
        self.neural_enabled = neural
        self.table = OutcomeTable(state.get("table"))
        self.logistic = ContextLogistic(state.get("logistic"))
        self.log_weights = np.asarray(state.get("log_weights", [0.0] * len(EXPERTS)))
        self.steps = int(state.get("steps", 0))
        self.updates = int(state.get("updates", 0))
        self.clock = int(state.get("clock", self.steps))
        self.pending = state.get("pending")
        self.stats = state.get(
            "stats", {"n": 0, "correct": 0, "loss": 0.0, "baseline_loss": 0.0}
        )
        self.stats.setdefault("calibration_bins", [[0, 0.0, 0.0] for _ in range(10)])
        self.expert_losses = state.get("expert_losses", {name: 0.0 for name in EXPERTS})
        self.history = deque(state.get("history", []), maxlen=120)

    def weights(self) -> np.ndarray:
        available = np.ones(len(EXPERTS))
        available[-1] = int(self.neural_enabled)
        if not self.attention_enabled:
            return available / available.sum()
        raw = (
            np.exp(self.log_weights - self.log_weights[available == 1].max())
            * available
        )
        # A fixed share keeps suppressed specialists available after a shift.
        return 0.99 * raw / raw.sum() + 0.01 * available / available.sum()

    def predict(self, event: dict, neural_probability: float = 0.5) -> dict:
        side, amount, fee = context(event)
        if not math.isfinite(neural_probability) or not 0 <= neural_probability <= 1:
            raise ValueError("Invalid neural forecast")
        repeat = self.table.probability("repeat", self.clock)
        keys = {
            "amount": f"a:{amount}",
            "fee_rate": f"f:{fee}",
            "joint_context": f"j:{amount}:{fee}",
        }
        probabilities = {"frequency": self.table.probability("buy", self.clock)}
        for name in ("repeat", "amount", "fee_rate", "joint_context"):
            p_repeat = (
                self.table.probability(keys[name], self.clock)
                if name in keys and self.memory_enabled
                else repeat
            )
            probabilities[name] = p_repeat if side else 1 - p_repeat
        probabilities["neural"] = float(neural_probability)
        logistic = (
            self.logistic.probability(amount, fee) if self.memory_enabled else repeat
        )
        probabilities["context_logistic"] = logistic if side else 1 - logistic
        weights = self.weights()
        probability = float(
            weights @ np.asarray([probabilities[name] for name in EXPERTS])
        )
        return {
            "probability": probability,
            "experts": probabilities,
            "weights": dict(zip(EXPERTS, weights.tolist())),
            "context": [side, amount, fee],
        }

    def observe(
        self, event: dict, neural_probability: float = 0.5, *, learn: bool = True
    ) -> dict:
        side, _, _ = context(event)
        if not math.isfinite(neural_probability) or not 0 <= neural_probability <= 1:
            raise ValueError("Invalid neural forecast")
        feedback = None
        if self.pending:
            if self.pending["source"] != event.get("source", "unknown"):
                raise ValueError("Forecast source changed; use a separate learner")
            p = self.pending["probability"]
            probabilities = self.pending["experts"]
            loss = (p - side) ** 2
            baseline_loss = (probabilities["repeat"] - side) ** 2
            self.stats["n"] += 1
            self.stats["correct"] += int((p >= 0.5) == bool(side))
            self.stats["loss"] += loss
            self.stats["baseline_loss"] += baseline_loss
            bucket = self.stats["calibration_bins"][min(9, int(p * 10))]
            bucket[0] += 1
            bucket[1] += p
            bucket[2] += side
            errors = np.asarray([(probabilities[name] - side) ** 2 for name in EXPERTS])
            for name, error in zip(EXPERTS, errors):
                self.expert_losses[name] += float(error)
            self.history.append(
                {
                    "id": event["id"],
                    "source": event.get("source"),
                    "predicted_buy": round(p, 5),
                    "actual_buy": side,
                    "brier": round(loss, 6),
                    "baseline_brier": round(baseline_loss, 6),
                }
            )
            previous, amount, fee = self.pending["context"]
            if learn:
                self.updates += 1
                self.log_weights = 0.995 * self.log_weights - 2 * errors
                self.table.learn("buy", side, self.clock)
                repeated = int(side == previous)
                self.table.learn("repeat", repeated, self.clock)
                if self.memory_enabled:
                    for key in (f"a:{amount}", f"f:{fee}", f"j:{amount}:{fee}"):
                        self.table.learn(key, repeated, self.clock)
                    self.logistic.learn(amount, fee, repeated)
            feedback = {
                "probability": p,
                "error": side - p,
                "loss": loss,
                "baseline_loss": baseline_loss,
                "improvement": baseline_loss - loss,
            }
        self.steps += 1
        self.clock += int(learn)
        prediction = self.predict(event, neural_probability)
        self.pending = {
            **prediction,
            "source": event.get("source", "unknown"),
            "after_input": event["id"],
        }
        p = prediction["probability"]
        entropy = -(
            p * math.log2(max(p, 1e-12)) + (1 - p) * math.log2(max(1 - p, 1e-12))
        )
        return {
            "prediction_buy": p,
            "uncertainty": entropy,
            "feedback": feedback,
            "attention": prediction["weights"],
            "evaluation_source": event.get("source", "unknown"),
        }

    def metrics(self) -> dict:
        n = self.stats["n"]
        return {
            "events": self.steps,
            "trained_transitions": self.updates,
            "evaluated": n,
            "accuracy": self.stats["correct"] / n if n else None,
            "brier": self.stats["loss"] / n if n else None,
            "baseline_brier": self.stats["baseline_loss"] / n if n else None,
            "brier_improvement": (self.stats["baseline_loss"] - self.stats["loss"]) / n
            if n
            else None,
            "baseline": "online learned repeat probability",
            "expert_brier": {
                name: loss / n if n else None
                for name, loss in self.expert_losses.items()
            },
            "attention": dict(zip(EXPERTS, self.weights().tolist())),
            "memory_cells": len(self.table.cells),
            "calibration": [
                {
                    "n": b[0],
                    "predicted": b[1] / b[0] if b[0] else None,
                    "observed": b[2] / b[0] if b[0] else None,
                }
                for b in self.stats["calibration_bins"]
            ],
            "mechanism": "Conditional outcome memory and learned forecast attention; separate from neural STDP and workspace broadcast",
        }

    def state(self) -> dict:
        return {
            "version": VERSION,
            "table": self.table.state(),
            "logistic": self.logistic.state(),
            "log_weights": self.log_weights.tolist(),
            "steps": self.steps,
            "updates": self.updates,
            "clock": self.clock,
            "pending": self.pending,
            "stats": self.stats,
            "expert_losses": self.expert_losses,
            "history": list(self.history),
        }


def diagnose(records: list[dict]) -> dict:
    """Chronological component comparison on one recorded source, not a trial.

    Neural forecasts cannot be reconstructed from event payloads alone, so this
    diagnostic excludes the neural specialist in every branch. No new event is
    invented or fed into the running circuit.
    """
    if len(records) < 64:
        raise ValueError("At least 64 recorded inputs are required")
    if len({r.get("source", "unknown") for r in records}) != 1:
        raise ValueError("Diagnostics require one source")
    models = {
        "adaptive": ForecastCouncil(neural=False),
        "no_outcome_memory": ForecastCouncil(memory=False, neural=False),
        "uniform_attention": ForecastCouncil(attention=False, neural=False),
        "frozen_after_warmup": ForecastCouncil(neural=False),
    }
    warmup = len(records) // 3
    losses = {name: [] for name in (*models, "repeat", "context_logistic")}
    previous = None
    for i, event in enumerate(records):
        for name, model in models.items():
            result = model.observe(
                event, learn=name != "frozen_after_warmup" or i <= warmup
            )
            if i > warmup:
                losses[name].append(result["feedback"]["loss"])
        if i > warmup:
            outcome = int(event["side"] == "buy")
            for name in ("repeat", "context_logistic"):
                losses[name].append((previous["experts"][name] - outcome) ** 2)
        previous = models["adaptive"].pending
    means = {name: float(np.mean(values)) for name, values in losses.items()}
    return {
        "question": "Does conditional outcome memory and learned forecast attention reduce next-side error on recent recorded inputs?",
        "source": records[0].get("source", "unknown"),
        "inputs": len(records),
        "warmup": warmup,
        "scored": len(losses["adaptive"]),
        "first_input": records[0]["id"],
        "last_input": records[-1]["id"],
        "brier": means,
        "improvement_over_repeat": means["repeat"] - means["adaptive"],
        "final_attention": models["adaptive"].metrics()["attention"],
        "scope": "Retrospective chronological forecast-component diagnostic; all branches exclude neural readout. Not randomized causal evidence, a new hypothesis, live out-of-sample evidence or a test of consciousness.",
    }
