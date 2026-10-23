"""Source-specific ordered memory around the preserved neural/statistical council.

These are bounded statistical predictors, not learned TVB connectivity or a
claim that additional neural dynamics caused their gains. Labels train only the
features and probabilities stored before that label arrived.
"""

from __future__ import annotations

import copy
import math
from collections import deque

import numpy as np

from cognition.forecast import VERSION as LEGACY_VERSION
from cognition.forecast import ForecastCouncil, context

VERSION = "forecast-memory-v1"
MAX_LAG = 16
MAX_DEPTH = 8
MAX_CELLS = 1024
DECAYS = {"slow": 0.999, "fast": 0.98}
EXPERTS = ("current",) + tuple(
    f"{name}_{speed}"
    for speed in DECAYS
    for name in (
        *(f"lag{lag:02d}" for lag in range(1, MAX_LAG + 1)),
        "suffix",
        "amount",
        "fee",
        "joint",
    )
)


class OutcomeMemory:
    """Lazy discounted counts with a finite, public context vocabulary."""

    def __init__(self, decay: float, state: dict | None = None, clock: int = 0):
        self.decay = decay
        self.cells = copy.deepcopy((state or {}).get("cells", {}))
        if len(self.cells) > MAX_CELLS:
            raise ValueError("Outcome memory exceeds its bound")
        for key, values in self.cells.items():
            if not isinstance(key, str) or len(key) > 64 or len(values) != 3:
                raise ValueError("Malformed outcome memory")
            positive, total, updated = values
            if (
                not all(math.isfinite(x) for x in values)
                or not 0 <= positive <= total <= 10000
                or type(updated) is not int
                or not 0 <= updated <= clock
            ):
                raise ValueError("Invalid outcome counts or clock")

    def counts(self, key: str, clock: int) -> tuple[float, float]:
        positive, total, updated = self.cells.get(key, (0.0, 0.0, clock))
        discount = self.decay ** (clock - updated)
        return positive * discount, total * discount

    def probability(self, key: str, clock: int) -> float:
        positive, total = self.counts(key, clock)
        return (positive + 1) / (total + 2)

    def learn(self, key: str, outcome: int, clock: int) -> None:
        if key not in self.cells and len(self.cells) >= MAX_CELLS:
            raise RuntimeError("Outcome vocabulary exceeded its reviewed bound")
        positive, total = self.counts(key, clock)
        self.cells[key] = [positive + outcome, total + 1, clock]

    def state(self) -> dict:
        return {"cells": copy.deepcopy(self.cells)}


class AdaptiveForecast:
    """Keep the current council available while learning longer, changing rules.

    The legacy model starts with 90% mixture weight. Forty bounded memory
    specialists share the remainder. Discounted log-loss weighting and a 1%
    fixed share toward that conservative prior allow recovery after shifts.
    This heuristic claims no CTW or fixed-share regret theorem.
    """

    def __init__(
        self,
        state: dict | None = None,
        *,
        sequence: bool | None = None,
        fast: bool | None = None,
    ):
        supplied = copy.deepcopy(state or {})
        legacy = supplied.get("version") == LEGACY_VERSION
        if supplied and supplied.get("version") not in (VERSION, LEGACY_VERSION):
            raise ValueError("Unsupported adaptive forecast checkpoint")
        saved = {} if legacy else supplied
        options = saved.get("options", {"sequence": True, "fast": True})
        self.sequence_enabled = options["sequence"] if sequence is None else sequence
        self.fast_enabled = options["fast"] if fast is None else fast
        if (
            type(self.sequence_enabled) is not bool
            or type(self.fast_enabled) is not bool
        ):
            raise ValueError("Forecast options must be boolean")
        if saved and options != self.options():
            raise ValueError("Forecast mechanism configuration changed")
        self.base = ForecastCouncil(supplied if legacy else saved.get("base"))
        self.steps = saved.get("steps", 0)
        self.clock = saved.get("clock", 0)
        self.updates = saved.get("updates", 0)
        if (
            type(self.steps) is not int
            or type(self.clock) is not int
            or not 0 <= self.clock <= self.steps
            or type(self.updates) is not int
            or not 0 <= self.updates <= self.clock
        ):
            raise ValueError("Invalid forecast clock")
        self.banks = {
            speed: OutcomeMemory(decay, saved.get("banks", {}).get(speed), self.clock)
            for speed, decay in DECAYS.items()
        }
        history = saved.get("observed_sides", [])
        if len(history) > MAX_LAG or any(
            type(x) is not int or x not in (0, 1) for x in history
        ):
            raise ValueError("Invalid ordered observation buffer")
        self.observed_sides = deque(history, maxlen=MAX_LAG)
        self.pending = saved.get("pending")
        self.source = saved.get("source")
        self.stats = saved.get(
            "stats",
            {
                "n": 0,
                "correct": 0,
                "loss": 0.0,
                "baseline_loss": 0.0,
                "current_loss": 0.0,
                "calibration_bins": [[0, 0.0, 0.0] for _ in range(10)],
            },
        )
        self.expert_losses = saved.get("expert_losses", {name: 0.0 for name in EXPERTS})
        self.history = deque(saved.get("history", []), maxlen=120)
        self.started_input = saved.get("started_input")
        self.migration = saved.get("migration")
        if legacy:
            self.migration = {
                "from": LEGACY_VERSION,
                "legacy_events": self.base.steps,
                "legacy_evaluated": self.base.stats["n"],
                "legacy_pending_after_input": (self.base.pending or {}).get(
                    "after_input"
                ),
                "scope": "Legacy parameters, pending forecast and metrics retained; ordered memory and new metrics start with the next observed input",
            }
        self.prior = self._prior()
        self.mixture = np.asarray(
            saved.get("mixture", self.prior.tolist()), dtype=float
        )
        if (
            self.mixture.shape != (len(EXPERTS),)
            or not np.isfinite(self.mixture).all()
            or np.any(self.mixture < 0)
            or not math.isclose(float(self.mixture.sum()), 1.0, abs_tol=1e-12)
            or np.any(self.mixture[self.prior == 0] != 0)
        ):
            raise ValueError("Invalid mixture weights")
        if self.pending:
            probabilities = self.pending.get("experts", {})
            if (
                set(probabilities) != set(EXPERTS)
                or any(
                    not math.isfinite(p) or not 0 <= p <= 1
                    for p in probabilities.values()
                )
                or self.pending.get("source") != self.source
                or not 0 <= self.pending.get("probability", -1) <= 1
                or len(self.pending.get("lags", [])) > MAX_LAG
                or len(self.pending.get("suffixes", [])) > MAX_DEPTH
            ):
                raise ValueError("Invalid pending forecast")

    def options(self) -> dict:
        return {"sequence": self.sequence_enabled, "fast": self.fast_enabled}

    def _prior(self) -> np.ndarray:
        active = np.ones(len(EXPERTS), dtype=bool)
        for index, name in enumerate(EXPERTS):
            if (not self.fast_enabled and name.endswith("_fast")) or (
                not self.sequence_enabled
                and (name.startswith("lag") or name.startswith("suffix"))
            ):
                active[index] = False
        prior = active.astype(float) * (0.1 / (active.sum() - 1))
        prior[0] = 0.9
        return prior

    def _features(self, side: int, amount: int, fee: int) -> dict:
        bits = list(self.observed_sides)
        return {
            "context": [side, amount, fee],
            "lags": [f"l:{lag}:{bits[-lag]}" for lag in range(1, len(bits) + 1)]
            if self.sequence_enabled
            else [],
            "suffixes": [
                "s:" + "".join(map(str, bits[-depth:]))
                for depth in range(1, min(MAX_DEPTH, len(bits)) + 1)
            ]
            if self.sequence_enabled
            else [],
        }

    def _learn_memory(self, outcome: int) -> None:
        previous, amount, fee = self.pending["context"]
        for speed, bank in self.banks.items():
            if speed == "fast" and not self.fast_enabled:
                continue
            for key in ("buy", *self.pending["lags"], *self.pending["suffixes"]):
                bank.learn(key, outcome, self.clock)
            for key in (f"a:{amount}", f"f:{fee}", f"j:{amount}:{fee}"):
                bank.learn(key, int(outcome == previous), self.clock)

    def _predictions(self, features: dict) -> dict:
        side, amount, fee = features["context"]
        probabilities = {"current": self.base.pending["probability"]}
        for speed, bank in self.banks.items():
            for lag in range(1, MAX_LAG + 1):
                key = (
                    features["lags"][lag - 1] if lag <= len(features["lags"]) else "buy"
                )
                probabilities[f"lag{lag:02d}_{speed}"] = bank.probability(
                    key, self.clock
                )
            suffix = bank.probability("buy", self.clock)
            for key in features["suffixes"]:
                positive, total = bank.counts(key, self.clock)
                suffix = (positive + 4 * suffix) / (total + 4)
            probabilities[f"suffix_{speed}"] = suffix
            for name, key in (
                ("amount", f"a:{amount}"),
                ("fee", f"f:{fee}"),
                ("joint", f"j:{amount}:{fee}"),
            ):
                repeated = bank.probability(key, self.clock)
                probabilities[f"{name}_{speed}"] = repeated if side else 1 - repeated
        return probabilities

    def observe(
        self, event: dict, neural_probability: float = 0.5, *, learn: bool = True
    ) -> dict:
        side, amount, fee = context(event)
        source = event.get("source", "unknown")
        if (
            not isinstance(source, str)
            or not 0 < len(source) <= 128
            or not isinstance(event.get("id"), str)
            or not 0 < len(event["id"]) <= 512
            or not math.isfinite(neural_probability)
            or not 0 <= neural_probability <= 1
            or (self.source is not None and source != self.source)
            or (self.base.pending and source != self.base.pending["source"])
        ):
            raise ValueError("Invalid or foreign-source forecast input")
        feedback = None
        if self.pending:
            p = self.pending["probability"]
            probability_vector = np.asarray(
                [self.pending["experts"][name] for name in EXPERTS]
            )
            errors = (probability_vector - side) ** 2
            loss = (p - side) ** 2
            baseline_loss = (self.pending["repeat_probability"] - side) ** 2
            self.stats["n"] += 1
            self.stats["correct"] += int((p >= 0.5) == bool(side))
            self.stats["loss"] += loss
            self.stats["baseline_loss"] += baseline_loss
            self.stats["current_loss"] += float(errors[0])
            bucket = self.stats["calibration_bins"][min(9, int(p * 10))]
            bucket[0] += 1
            bucket[1] += p
            bucket[2] += side
            for name, error in zip(EXPERTS, errors):
                self.expert_losses[name] += float(error)
            self.history.append(
                {
                    "id": event["id"],
                    "source": source,
                    "predicted_buy": round(p, 5),
                    "actual_buy": side,
                    "brier": round(loss, 6),
                    "baseline_brier": round(baseline_loss, 6),
                    "current_council_brier": round(float(errors[0]), 6),
                }
            )
            if learn:
                self.updates += 1
                active = self.prior > 0
                likelihood = probability_vector if side else 1 - probability_vector
                log_weights = 0.995 * np.log(
                    np.maximum(self.mixture[active], 1e-300)
                ) + np.log(np.clip(likelihood[active], 1e-6, 1 - 1e-6))
                log_weights -= log_weights.max()
                weights = np.exp(log_weights)
                weights /= weights.sum()
                self.mixture[:] = 0
                self.mixture[active] = 0.99 * weights + 0.01 * self.prior[active]
                self._learn_memory(side)
            feedback = {
                "probability": p,
                "error": side - p,
                "loss": loss,
                "baseline_loss": baseline_loss,
                "improvement": baseline_loss - loss,
                "current_loss": float(errors[0]),
            }
        # Preserve the original pending prediction and updates, including on the
        # first observation after migration. No old score becomes a new score.
        self.base.observe(event, neural_probability, learn=learn)
        self.steps += 1
        self.clock += int(learn)
        self.source = source
        self.started_input = self.started_input or event["id"]
        self.observed_sides.append(side)
        features = self._features(side, amount, fee)
        probabilities = self._predictions(features)
        p = float(self.mixture @ np.asarray([probabilities[name] for name in EXPERTS]))
        p = min(1 - 1e-9, max(1e-9, p))
        self.pending = {
            **features,
            "source": source,
            "after_input": event["id"],
            "probability": p,
            "experts": probabilities,
            "weights": dict(zip(EXPERTS, self.mixture.tolist())),
            "repeat_probability": self.base.pending["experts"]["repeat"],
        }
        return {
            "prediction_buy": p,
            "uncertainty": -(p * math.log2(p) + (1 - p) * math.log2(1 - p)),
            "feedback": feedback,
            "attention": self.pending["weights"],
            "evaluation_source": source,
        }

    def metrics(self) -> dict:
        n = self.stats["n"]
        return {
            "version": VERSION,
            "events": self.steps,
            "trained_transitions": self.updates,
            "evaluated": n,
            "accuracy": self.stats["correct"] / n if n else None,
            "brier": self.stats["loss"] / n if n else None,
            "baseline_brier": self.stats["baseline_loss"] / n if n else None,
            "brier_improvement": (self.stats["baseline_loss"] - self.stats["loss"]) / n
            if n
            else None,
            "current_council_brier": self.stats["current_loss"] / n if n else None,
            "current_council_improvement": (
                self.stats["current_loss"] - self.stats["loss"]
            )
            / n
            if n
            else None,
            "attention": dict(zip(EXPERTS, self.mixture.tolist())),
            "expert_brier": {
                name: loss / n if n else None
                for name, loss in self.expert_losses.items()
            },
            "memory_cells": sum(len(bank.cells) for bank in self.banks.values()),
            "max_memory_cells": 2 * MAX_CELLS,
            "history_capacity": MAX_LAG,
            "started_input": self.started_input,
            "migration": self.migration,
            "legacy_metrics": self.base.metrics(),
            "calibration": [
                {
                    "n": b[0],
                    "predicted": b[1] / b[0] if b[0] else None,
                    "observed": b[2] / b[0] if b[0] else None,
                }
                for b in self.stats["calibration_bins"]
            ],
            "mechanism": "Ordered outcome memory and multiple forgetting speeds around the retained neural/statistical council; not a demonstrated synaptic or TVB advantage",
        }

    def state(self) -> dict:
        return copy.deepcopy(
            {
                "version": VERSION,
                "options": self.options(),
                "base": self.base.state(),
                "steps": self.steps,
                "clock": self.clock,
                "updates": self.updates,
                "source": self.source,
                "banks": {speed: bank.state() for speed, bank in self.banks.items()},
                "observed_sides": list(self.observed_sides),
                "pending": self.pending,
                "mixture": self.mixture.tolist(),
                "stats": self.stats,
                "expert_losses": self.expert_losses,
                "history": list(self.history),
                "started_input": self.started_input,
                "migration": self.migration,
            }
        )
