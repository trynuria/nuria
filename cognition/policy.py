"""Goal arbitration, learned action values and an explicit computational self-model."""

from __future__ import annotations

import math

import numpy as np

from cognition.brain import ACTIONS


class ResourceState:
    def __init__(self, state: dict | None = None):
        state = state or {}
        self.energy = float(state.get("energy", 0.9))
        self.fatigue = float(state.get("fatigue", 0))
        self.action_values = state.get("action_values", {a: 0.0 for a in ACTIONS})
        self.action_counts = state.get("action_counts", {a: 0 for a in ACTIONS})
        self.cost_estimates = state.get("cost_estimates", {a: 0.01 for a in ACTIONS})
        self.last_outcome = state.get("last_outcome")

    def candidates(
        self,
        uncertainty: float,
        surprise: float,
        novelty: float,
        wallet: dict,
        memory_count: int,
    ) -> dict:
        base = {
            "explore": 0.35 + 0.3 * novelty,
            "forage": 0.25 + 0.6 * (1 - self.energy),
            "predict": 0.35 + 0.3 * uncertainty,
            "replay": (0.25 + 0.35 * surprise) if memory_count else 0,
            "experiment": 0.28 + 0.3 * uncertainty,
            "rest": 0.1 + 0.9 * self.fatigue + 0.6 * max(0, 0.45 - self.energy),
            "compare": 0.22 + 0.4 * surprise,
            "reserve": 0.18 + (0.25 if wallet.get("balance_lamports") is None else 0),
        }
        total = sum(self.action_counts.values()) + 1
        for action in ACTIONS:
            exploration = 0.035 * math.sqrt(
                math.log(total + 1) / (self.action_counts[action] + 1)
            )
            base[action] = float(
                np.clip(
                    base[action]
                    + 0.15 * self.action_values[action]
                    + exploration
                    - 0.1 * self.cost_estimates[action],
                    0,
                    1,
                )
            )
        if self.energy < 0.2:
            base["rest"] = 1
            base["experiment"] *= 0.1
        return base

    def choose(self, utilities: dict, neural_scores: dict, tick: int) -> dict:
        maximum = max(neural_scores.values(), default=0)
        scores = {
            a: 0.65 * utilities[a]
            + 0.35 * (neural_scores.get(a, 0) / maximum if maximum else 0)
            for a in ACTIONS
        }
        choice = max(ACTIONS, key=lambda a: (scores[a], -ACTIONS.index(a)))
        return {
            "action": choice,
            "utility_scores": {k: round(v, 5) for k, v in utilities.items()},
            "neural_spike_scores": neural_scores,
            "combined_scores": {k: round(v, 5) for k, v in scores.items()},
            "rule": "0.65 goal utility + 0.35 normalized neural spike readout; deterministic action-order tie break",
            "tick": tick,
        }

    def outcome(self, action: str, reward: float, duration: float) -> None:
        reward = float(np.clip(reward, -1, 1))
        self.action_counts[action] += 1
        self.action_values[action] = 0.9 * self.action_values[action] + 0.1 * reward
        self.cost_estimates[action] = 0.9 * self.cost_estimates[action] + 0.1 * min(
            1, duration
        )
        if action in ("rest", "reserve"):
            self.energy = min(1, self.energy + 0.035)
            self.fatigue = max(0, self.fatigue - 0.06)
        else:
            self.energy = max(0.05, self.energy - 0.004 - 0.012 * min(1, duration))
            self.fatigue = min(1, 0.95 * self.fatigue + 0.01 + 0.05 * min(1, duration))
        self.last_outcome = {
            "action": action,
            "reward": round(reward, 6),
            "wall_seconds": round(duration, 6),
        }

    def state(self) -> dict:
        return {
            "energy": self.energy,
            "fatigue": self.fatigue,
            "action_values": self.action_values,
            "action_counts": self.action_counts,
            "cost_estimates": self.cost_estimates,
            "last_outcome": self.last_outcome,
        }

    def public(self) -> dict:
        return {
            **self.state(),
            "scope": "Computational resource-regulation variables; energy and fatigue are model state, not biological metabolism or reported feelings",
            "goals": [
                {"id": "continuity", "target": "preserve neural and learning state"},
                {
                    "id": "prediction",
                    "target": "reduce prequential error against a frequency baseline",
                },
                {
                    "id": "memory",
                    "target": "retrieve and replay relevant recorded events",
                },
                {
                    "id": "exploration",
                    "target": "test uncertain contexts with measured outcomes",
                },
                {
                    "id": "resources",
                    "target": "stay within compute and published spending constraints",
                },
            ],
        }
