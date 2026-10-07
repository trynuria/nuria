"""Prequential prediction, calibrated uncertainty and outcome-driven readouts."""

from __future__ import annotations

import math
from collections import deque

import numpy as np

FEATURES = 72


class OnlineReadout:
    """Learn the next event's side only after that side has become observable."""

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.weights = np.asarray(state.get("weights", [0.0] * FEATURES), dtype=float)
        self.pending = state.get("pending")
        self.samples = int(state.get("samples", 0))
        self.momentum = float(state.get("momentum", 0))
        self.stats = state.get("stats", {})
        self.history = deque(state.get("history", []), maxlen=120)

    def probability(self, features: list | np.ndarray) -> float:
        x = np.asarray(features, dtype=float)
        if x.shape != (FEATURES,) or not np.isfinite(x).all():
            raise ValueError("Prediction feature vector is invalid")
        score = float(np.clip(self.weights @ x, -12, 12))
        return 1 / (1 + math.exp(-score))

    def learn(self, features: list | np.ndarray, outcome: int) -> dict:
        x = np.asarray(features, dtype=float)
        p = self.probability(x)
        rate = 0.18 / math.sqrt(1 + self.samples / 2000)
        error = float(outcome) - p
        self.weights = np.clip(
            self.weights
            + rate * error * x / (1 + float(x @ x) * 0.15)
            - rate * 0.0001 * self.weights,
            -8,
            8,
        )
        self.samples += 1
        return {"probability": p, "error": error, "loss": (p - outcome) ** 2}

    def features(
        self, event: dict, neural: list, energy: float, uncertainty: float
    ) -> np.ndarray:
        side = 1 if event["side"] == "buy" else -1
        self.momentum = 0.9 * self.momentum + 0.1 * side
        structured = [
            1,
            side,
            min(2, math.log1p(float(event["quote_amount"]))) / 2,
            self.momentum,
            min(1, float(event.get("creator_fee", 0)) * 100),
            energy,
            uncertainty,
            1 if event.get("source") == "test" else 0,
        ]
        return np.concatenate(
            (np.asarray(neural, dtype=float), np.asarray(structured, dtype=float))
        )

    def observe(self, event: dict, features: np.ndarray) -> dict:
        source = event.get("source", "unknown")
        stats = self.stats.setdefault(
            source,
            {
                "evaluated": 0,
                "correct": 0,
                "loss_sum": 0.0,
                "baseline_loss_sum": 0.0,
                "buys": 0,
                "events": 0,
                "calibration_bins": [[0, 0.0, 0.0] for _ in range(10)],
            },
        )
        y = 1 if event["side"] == "buy" else 0
        feedback = None
        if self.pending and self.pending["source"] == source:
            p = float(self.pending["probability"])
            baseline = float(self.pending["baseline"])
            feedback = self.learn(self.pending["features"], y)
            feedback["baseline_loss"] = (baseline - y) ** 2
            feedback["improvement"] = (baseline - y) ** 2 - (p - y) ** 2
            stats["evaluated"] += 1
            stats["correct"] += int((p >= 0.5) == bool(y))
            stats["loss_sum"] += (p - y) ** 2
            stats["baseline_loss_sum"] += (baseline - y) ** 2
            bucket = stats["calibration_bins"][min(9, int(p * 10))]
            bucket[0] += 1
            bucket[1] += p
            bucket[2] += y
            self.history.append(
                {
                    "id": event["id"],
                    "source": source,
                    "predicted_buy": round(p, 5),
                    "actual_buy": y,
                    "brier": round((p - y) ** 2, 6),
                    "baseline_brier": round((baseline - y) ** 2, 6),
                }
            )
        stats["events"] += 1
        stats["buys"] += y
        p = self.probability(features)
        baseline = (stats["buys"] + 1) / (stats["events"] + 2)
        self.pending = {
            "after_input": event["id"],
            "source": source,
            "features": features.tolist(),
            "probability": p,
            "baseline": baseline,
        }
        entropy = -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
        return {
            "prediction_buy": round(p, 6),
            "uncertainty": round(entropy, 6),
            "feedback": feedback,
            "evaluation_source": source,
        }

    def metrics(self) -> dict:
        result = {}
        for source, stats in self.stats.items():
            n = stats["evaluated"]
            result[source] = {
                "events": stats["events"],
                "evaluated": n,
                "accuracy": round(stats["correct"] / n, 5) if n else None,
                "brier": round(stats["loss_sum"] / n, 6) if n else None,
                "baseline_brier": round(stats["baseline_loss_sum"] / n, 6)
                if n
                else None,
                "brier_improvement": round(
                    (stats["baseline_loss_sum"] - stats["loss_sum"]) / n, 6
                )
                if n
                else None,
                "calibration": [
                    {
                        "n": b[0],
                        "predicted": round(b[1] / b[0], 5) if b[0] else None,
                        "observed": round(b[2] / b[0], 5) if b[0] else None,
                    }
                    for b in stats["calibration_bins"]
                ],
            }
        return result

    def state(self) -> dict:
        return {
            "weights": self.weights.tolist(),
            "pending": self.pending,
            "samples": self.samples,
            "momentum": self.momentum,
            "stats": self.stats,
            "history": list(self.history),
        }


def benchmark(seed: int = 51, train: int = 600, test: int = 240) -> dict:
    """Held-out controlled tasks; results are not evidence of market forecasting."""
    rng = np.random.default_rng(seed)
    results = []
    for task in (
        "alternating_sequence",
        "persistent_sequence",
        "delayed_cue",
        "regime_reversal",
    ):
        model = OnlineReadout()
        previous = int(rng.integers(0, 2))
        cue = 0
        examples = []
        for i in range(train + test):
            if task == "alternating_sequence":
                current = 1 - previous if rng.random() < 0.9 else previous
            elif task == "persistent_sequence":
                current = previous if rng.random() < 0.9 else 1 - previous
            elif task == "delayed_cue":
                if i % 8 == 0:
                    cue = int(rng.integers(0, 2))
                current = cue if rng.random() < 0.9 else 1 - cue
            else:
                persistent = i < (train + test) // 2
                current = (
                    (previous if persistent else 1 - previous)
                    if rng.random() < 0.9
                    else int(rng.integers(0, 2))
                )
            x = np.zeros(FEATURES)
            x[64] = 1
            x[65] = 2 * previous - 1
            x[66] = 2 * cue - 1 if task == "delayed_cue" else 0
            x[:64] = rng.uniform(0, 0.08, 64)
            if i < train:
                model.learn(x, current)
            else:
                p = model.probability(x)
                examples.append((p, current, x.copy()))
            previous = current
        score = float(np.mean([(p >= 0.5) == bool(y) for p, y, _ in examples]))
        brier = float(np.mean([(p - y) ** 2 for p, y, _ in examples]))
        without_memory = []
        without_neural = []
        for _, y, x in examples:
            ablated = x.copy()
            ablated[65:67] = 0
            without_memory.append((model.probability(ablated) - y) ** 2)
            ablated = x.copy()
            ablated[:64] = 0
            without_neural.append((model.probability(ablated) - y) ** 2)
        results.append(
            {
                "task": task,
                "trained_samples": train,
                "held_out_samples": test,
                "accuracy": round(score, 5),
                "brier": round(brier, 6),
                "chance_brier": 0.25,
                "memory_ablation_brier": round(float(np.mean(without_memory)), 6),
                "feature_ablation_brier": round(float(np.mean(without_neural)), 6),
                "scope": "Controlled readout benchmark; random reservoir-feature fixture, not a neural causal test or live market result",
            }
        )
    return {
        "seed": seed,
        "tasks": results,
        "claims": "Learning and memory mechanisms under controlled data; no consciousness, profitability or general intelligence inference",
    }
