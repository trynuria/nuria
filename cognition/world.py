"""A deterministic, partially observed habitat for testing embodied action outcomes."""

from __future__ import annotations

import hashlib

import numpy as np

SIZE = 12


class Habitat:
    def __init__(self, state: dict | None = None):
        state = state or {}
        self.seed = int(state.get("seed", 510051))
        self.position = list(state.get("position", [SIZE // 2, SIZE // 2]))
        self.visits = np.asarray(
            state.get("visits", np.zeros((SIZE, SIZE), dtype=int).tolist()), dtype=int
        )
        self.resources = state.get("resources", [[2, 2], [9, 2], [2, 9], [9, 9]])
        self.moves = int(state.get("moves", 0))
        self.collected = int(state.get("collected", 0))
        self.reward_total = float(state.get("reward_total", 0))
        self.climate = float(state.get("climate", 0))
        self.last = state.get("last")

    def signal(self, event: dict) -> None:
        direction = 1 if event["side"] == "buy" else -1
        self.climate = float(np.clip(0.95 * self.climate + 0.05 * direction, -1, 1))

    def act(self, action: str) -> dict:
        before = list(self.position)
        x, y = self.position
        options = [
            [min(SIZE - 1, x + 1), y],
            [max(0, x - 1), y],
            [x, min(SIZE - 1, y + 1)],
            [x, max(0, y - 1)],
        ]
        options = [p for p in options if p != self.position]
        reward = 0.0
        if action == "explore":
            target = min(options, key=lambda p: (self.visits[p[1], p[0]], p))
            reward = 0.08 if self.visits[target[1], target[0]] == 0 else 0.005
            self.position = target
        elif action == "forage":
            # The planner uses observed resource locations; neural arbitration
            # chooses when this planner is invoked.
            nearest = min(self.resources, key=lambda p: abs(p[0] - x) + abs(p[1] - y))
            self.position = min(
                options, key=lambda p: abs(p[0] - nearest[0]) + abs(p[1] - nearest[1])
            )
            reward = 0.01
        if self.position != before:
            self.moves += 1
            self.visits[self.position[1], self.position[0]] += 1
            if self.position in self.resources:
                self.collected += 1
                reward += 0.25
                index = self.resources.index(self.position)
                raw = hashlib.sha256(
                    f"{self.seed}:{self.collected}:{index}".encode()
                ).digest()
                self.resources[index] = [raw[0] % SIZE, raw[1] % SIZE]
        self.reward_total += reward
        self.last = {
            "action": action,
            "from": before,
            "to": list(self.position),
            "reward": round(reward, 6),
        }
        return self.last

    def state(self) -> dict:
        return {
            "seed": self.seed,
            "size": SIZE,
            "position": self.position,
            "visits": self.visits.tolist(),
            "resources": self.resources,
            "moves": self.moves,
            "collected": self.collected,
            "reward_total": self.reward_total,
            "climate": self.climate,
            "last": self.last,
            "scope": "Software habitat with virtual resources; moves and rewards are experimental outcomes, not financial transfers",
        }
