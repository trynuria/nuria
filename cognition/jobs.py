"""Bounded local experiments with measured outcomes and explicit cost evidence."""

from __future__ import annotations

import hashlib
import json
import time

import numpy as np

from cognition.forecast import diagnose


class Experiments:
    def __init__(self, state: dict | None = None):
        state = state or {}
        self.completed = int(state.get("completed", 0))
        self.latest = state.get("latest")
        self.last_tick = int(state.get("last_tick", -100))

    def run(self, action: str, brain, memory, tick: int) -> dict:
        start = time.monotonic()
        cpu = time.process_time()
        if action == "experiment":
            if tick - self.last_tick < 30:
                return {
                    "status": "deferred",
                    "reason": "Experiment cooldown is active",
                    "reward": 0,
                    "actual_spend_lamports": 0,
                }
            source = (
                memory.recent[0]["event"].get("source", "unknown")
                if memory.recent
                else None
            )
            rows = (
                memory.db.execute(
                    "SELECT payload FROM episodes WHERE source=? ORDER BY event_order DESC LIMIT 512",
                    (source,),
                ).fetchall()
                if source
                else []
            )
            if len(rows) < 64:
                return {
                    "status": "unavailable",
                    "reason": "At least 64 recorded inputs from one source are needed for a diagnostic",
                    "reward": 0,
                    "actual_spend_lamports": 0,
                }
            result = diagnose([json.loads(row[0]) for row in reversed(rows)])
            reward = float(np.clip(result["improvement_over_repeat"], -1, 1))
            self.last_tick = tick
        elif action == "compare":
            if not memory.recent:
                return {
                    "status": "unavailable",
                    "reason": "No episode is available for matched replay",
                    "reward": 0,
                    "actual_spend_lamports": 0,
                }
            result = brain.counterfactual(memory.recent[0]["event"])
            reward = 0.05 if result["state_changed_by_input"] else 0
        elif action == "replay":
            if not memory.recent:
                return {
                    "status": "unavailable",
                    "reason": "No episode is available for replay",
                    "reward": 0,
                    "actual_spend_lamports": 0,
                }
            chosen = memory.recent[0]
            frame = brain.advance(chosen["event"], duration_ms=20)
            result = {
                "input_id": chosen["id"],
                "spikes": frame["spike_count"],
                "before_state_sha256": frame["before_state_sha256"],
                "after_state_sha256": frame["after_state_sha256"],
                "scope": "Re-stimulation of a recorded episode; no new market event",
            }
            reward = 0.01
        else:
            return {"status": "idle", "reward": 0, "actual_spend_lamports": 0}
        self.completed += 1
        raw = json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        self.latest = {
            "job_id": f"job-{self.completed:08d}",
            "action": action,
            "status": "completed",
            "tick": tick,
            "wall_seconds": round(time.monotonic() - start, 6),
            "cpu_seconds": round(time.process_time() - cpu, 6),
            "actual_spend_lamports": 0,
            "billing_basis": "Included capacity on the existing Nuria server",
            "result_sha256": hashlib.sha256(raw).hexdigest(),
            "result": result,
            "reward": round(reward, 6),
        }
        return self.latest

    def state(self) -> dict:
        return {
            "completed": self.completed,
            "latest": self.latest,
            "last_tick": self.last_tick,
        }
