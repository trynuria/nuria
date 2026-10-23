"""Delayed task eligibility tags around the retained spiking circuit."""

import numpy as np

from cognition.brain import ACTIONS, AdaptiveBrain


class TaskLearningBrain(AdaptiveBrain):
    def task_trace(self, action: str) -> dict:
        """Tag bounded action-region eligibility at the originating decision."""
        if action not in ACTIONS:
            raise ValueError("Unknown neural task action")
        start = 768 + ACTIONS.index(action) * 16
        posts = np.asarray(self.exc.j[:])
        eligibility = np.asarray(self.exc.eligibility[:])
        choices = np.flatnonzero(
            (posts >= start) & (posts < start + 16) & (np.abs(eligibility) > 0)
        )
        choices = sorted(
            choices.tolist(), key=lambda index: (-abs(eligibility[index]), index)
        )[:256]
        return {
            "topology_sha256": self.topology["sha256"],
            "action": action,
            "indices": choices,
            "eligibility": eligibility[choices].tolist(),
        }

    def task_reward(self, trace: dict, reward: float) -> dict:
        """Credit the retained eligibility tag, never the current unrelated trace."""
        if (
            trace.get("topology_sha256") != self.topology["sha256"]
            or trace.get("action") not in ACTIONS
            or type(reward) not in (int, float)
            or not np.isfinite(reward)
            or not -1 <= reward <= 1
        ):
            raise ValueError("Delayed neural task credit is incompatible")
        indices, eligibility = trace.get("indices"), trace.get("eligibility")
        if (
            not isinstance(indices, list)
            or not isinstance(eligibility, list)
            or len(indices) != len(eligibility)
            or len(indices) > 256
            or len(set(indices)) != len(indices)
        ):
            raise ValueError("Delayed eligibility tag is malformed")
        start = 768 + ACTIONS.index(trace["action"]) * 16
        posts = np.asarray(self.exc.j[:])
        if any(
            type(index) is not int
            or not 0 <= index < len(self.exc)
            or not start <= posts[index] < start + 16
            for index in indices
        ) or any(
            type(value) not in (int, float)
            or not np.isfinite(value)
            or not -1 <= value <= 1
            for value in eligibility
        ):
            raise ValueError(
                "Delayed eligibility is outside the selected action region"
            )
        before_hash = self.state_hash()
        before = np.asarray(self.exc.w[:])[indices].copy()
        after = np.clip(before + 0.002 * reward * np.asarray(eligibility), 0.002, 0.18)
        if indices:
            self.exc.w[indices] = after
        return {
            "before_state_sha256": before_hash,
            "after_state_sha256": self.state_hash(),
            "tagged_synapses": len(indices),
            "changed_synapses": int(np.count_nonzero(before != after)),
            "scope": "Bounded engineering eligibility-tag credit to the originating action region; benefit requires causal evaluation",
        }
