"""Partially observed, delayed-feedback decision experiments with paired controls.

The environment owns hidden rules. Controllers receive observations and only the
outcome of the arm they chose. Oracle/counterfactual data is audit evidence and
never enters controller updates. All credits and probe costs are virtual.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque

import numpy as np

CONTEXTS = 8
ARMS = 4
TASKS = ("association", "occlusion", "reversal", "scarcity")
BRANCHES = (
    "spike_memory",
    "symbolic_memory",
    "no_memory",
    "no_probes",
    "no_adaptation",
    "frozen_learning",
    "random",
)
VERSION = "discovery-v1"


def keyed(seed: int, *parts) -> np.random.Generator:
    raw = hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).digest()
    return np.random.default_rng(int.from_bytes(raw[:8], "big"))


class HiddenWorld:
    """The learner does not receive seeds, phases, mappings or unused rewards."""

    def __init__(self, seed: int, reversal_period: int = 320):
        self.seed = seed
        self.reversal_period = reversal_period

    def trial(self, index: int, task: str) -> dict:
        if task not in TASKS:
            raise ValueError("Unknown discovery task")
        rng = keyed(self.seed, task, index)
        context = int(rng.integers(CONTEXTS))
        phase = index // self.reversal_period if task in ("reversal", "scarcity") else 0
        mapping = keyed(self.seed, task, "mapping", phase).integers(ARMS, size=CONTEXTS)
        means = np.full(ARMS, 0.12)
        means[mapping[context]] = 0.88
        visibility = 1 if task == "association" else 0.55
        visible = bool(rng.random() < visibility)
        cost = float(rng.choice([0.06, 0.22, 0.46])) if task == "scarcity" else 0.12
        return {
            "id": f"{task}:{index}",
            "index": index,
            "task": task,
            "context": context,
            "visible": visible,
            "probe_cost": cost,
            "arm_cost": 0.025,
            "delay": int(rng.integers(2, 6)),
            "distractor": int(rng.integers(CONTEXTS)),
            "means": means.tolist(),
            "rewards": (rng.random(ARMS) < means).astype(int).tolist(),
            "phase": phase,
        }

    @staticmethod
    def observation(trial: dict) -> dict:
        return {k: trial[k] for k in ("id", "task", "probe_cost", "arm_cost", "delay")}


class CueDecoder:
    """Learn a sensory code from observed cue labels and actual spike features."""

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.means = np.asarray(state.get("means", np.zeros((CONTEXTS, 32))), float)
        self.counts = np.asarray(state.get("counts", np.zeros(CONTEXTS)), float)

    def observe(self, cue: int, features: list) -> None:
        x = np.asarray(features, float)
        if x.shape != (32,) or not np.isfinite(x).all() or not 0 <= cue < CONTEXTS:
            raise ValueError("Invalid spike cue observation")
        self.counts[cue] += 1
        rate = 1 / min(32, self.counts[cue])
        self.means[cue] += rate * (x - self.means[cue])

    def belief(self, features: list | None) -> np.ndarray:
        if features is None or not np.any(self.counts):
            return np.full(CONTEXTS, 1 / CONTEXTS)
        x = np.asarray(features, float)
        if x.shape != (32,) or not np.isfinite(x).all():
            raise ValueError("Invalid neural representation")
        distance = np.mean((self.means - x) ** 2, axis=1)
        score = -distance / 0.002
        score[self.counts == 0] = -40
        score -= score.max()
        probability = np.exp(score)
        return probability / probability.sum()

    def state(self) -> dict:
        return {"means": self.means.tolist(), "counts": self.counts.tolist()}


class DecisionLearner:
    """Contextual outcome model with costed sensing and delayed credit assignment.

    Fractional context counts are an approximation, not an exact Bayesian
    posterior. The detector is a bounded surprise heuristic, not a GLR test.
    """

    def __init__(self, branch: str, seed: int, state: dict | None = None):
        if branch not in BRANCHES:
            raise ValueError("Unknown control branch")
        state = state or {}
        self.branch, self.seed = branch, seed
        self.success = np.asarray(
            state.get("success", np.zeros((CONTEXTS, ARMS))), float
        )
        self.failure = np.asarray(
            state.get("failure", np.zeros((CONTEXTS, ARMS))), float
        )
        self.settled = int(state.get("settled", 0))
        self.total_net = float(state.get("total_net", 0))
        self.total_regret = float(state.get("total_regret", 0))
        self.probes = int(state.get("probes", 0))
        self.change_score = float(state.get("change_score", 0))
        self.change_events = int(state.get("change_events", 0))
        self.balance = float(state.get("balance", 12))
        self.pending = state.get("pending", [])
        self.recent = deque(state.get("recent", []), maxlen=80)
        self.loss_sum = float(state.get("loss_sum", 0))
        self.predictions = int(state.get("predictions", 0))

    def estimates(self) -> tuple[np.ndarray, np.ndarray]:
        a, z = self.success + 1, self.failure + 1
        mean = a / (a + z)
        variance = a * z / ((a + z) ** 2 * (a + z + 1))
        return mean, variance

    def decide(self, observation: dict, belief: np.ndarray, index: int, reveal) -> dict:
        if self.branch == "no_memory":
            belief = np.full(CONTEXTS, 1 / CONTEXTS)
        mean, variance = self.estimates()
        current = belief @ mean
        # One-step value of learning the context, before its possible outcome.
        voi = max(0.0, float(belief @ mean.max(axis=1) - current.max()))
        masked = bool(np.max(belief) < 0.65)
        probe = (
            self.branch not in ("no_probes", "random")
            and masked
            and voi > observation["probe_cost"]
            and self.balance > observation["probe_cost"] + observation["arm_cost"]
        )
        if probe:
            belief = np.asarray(reveal(), float)
            current = belief @ mean
            self.probes += 1
        uncertainty = np.sqrt(belief @ variance)
        # A bounded uncertainty bonus and 8% deterministic seeded exploration.
        scores = current + 0.45 * uncertainty - observation["arm_cost"]
        rng = keyed(self.seed, "decision", observation["id"], index)
        exploration = rng.random() < 0.08
        arm = (
            int(rng.integers(ARMS))
            if self.branch == "random" or exploration
            else int(np.argmax(scores))
        )
        cost = observation["arm_cost"] + (observation["probe_cost"] if probe else 0)
        if self.balance < cost:
            arm, cost, probe = -1, 0.0, False
        self.balance -= cost
        return {
            "trial_id": observation["id"],
            "arm": arm,
            "probe": probe,
            "probe_cost": observation["probe_cost"] if probe else 0,
            "cost": cost,
            "context_belief": belief.tolist(),
            "predicted_success": float(current[arm]) if arm >= 0 else None,
            "uncertainty": uncertainty.tolist(),
            "action_scores": scores.tolist(),
            "value_of_information": voi,
            "exploration": exploration,
            "settled_before_choice": self.settled,
        }

    def schedule(self, decision: dict, trial: dict) -> None:
        arm = decision["arm"]
        reward = trial["rewards"][arm] if arm >= 0 else 0
        expected = trial["means"][arm] if arm >= 0 else 0
        self.pending.append(
            {
                "due": trial["index"] + trial["delay"],
                "decision": decision,
                "reward": reward,
                "net": reward - decision["cost"],
                "regret": max(trial["means"]) - expected + decision["cost"],
            }
        )

    def settle(self, index: int) -> list:
        due = [row for row in self.pending if row["due"] <= index]
        self.pending = [row for row in self.pending if row["due"] > index]
        for row in due:
            d, y = row["decision"], row["reward"]
            self.settled += 1
            self.total_net += row["net"]
            self.total_regret += row["regret"]
            self.balance = min(20.0, self.balance + 0.06 + 0.15 * y)
            p = d["predicted_success"]
            self.loss_sum += (p - y) ** 2 if p is not None else 0
            self.predictions += int(p is not None)
            if d["arm"] >= 0 and not (
                self.branch == "frozen_learning" and self.settled > 240
            ):
                self.change_score = max(0, self.change_score + ((p - y) ** 2 - 0.22))
                if self.branch != "no_adaptation":
                    self.success *= 0.998
                    self.failure *= 0.998
                    if self.change_score > 2.5:
                        self.success *= 0.25
                        self.failure *= 0.25
                        self.change_score = 0
                        self.change_events += 1
                credit = np.asarray(d["context_belief"])
                self.success[:, d["arm"]] += credit * y
                self.failure[:, d["arm"]] += credit * (1 - y)
            self.recent.append(
                {
                    "id": d["trial_id"],
                    "arm": d["arm"],
                    "reward": y,
                    "net": row["net"],
                    "regret": row["regret"],
                    "probe": d["probe"],
                }
            )
        return due

    def public(self) -> dict:
        n = self.settled
        mean, variance = self.estimates()
        return {
            "branch": self.branch,
            "settled": n,
            "mean_net_reward": self.total_net / n if n else None,
            "mean_oracle_regret": self.total_regret / n if n else None,
            "brier": self.loss_sum / self.predictions if self.predictions else None,
            "predictions_evaluated": self.predictions,
            "probes": self.probes,
            "detected_changes": self.change_events,
            "virtual_balance": self.balance,
            "pending_rewards": len(self.pending),
            "learned_success": mean.round(5).tolist(),
            "outcome_uncertainty": np.sqrt(variance).round(5).tolist(),
            "recent": list(self.recent)[-24:],
        }

    def state(self) -> dict:
        return {
            "success": self.success.tolist(),
            "failure": self.failure.tolist(),
            "settled": self.settled,
            "total_net": self.total_net,
            "total_regret": self.total_regret,
            "probes": self.probes,
            "change_score": self.change_score,
            "change_events": self.change_events,
            "balance": self.balance,
            "pending": self.pending,
            "recent": list(self.recent),
            "loss_sum": self.loss_sum,
            "predictions": self.predictions,
        }


class DiscoveryLab:
    """Persistent paired branches and a deficit-driven bounded curriculum."""

    def __init__(self, seed: int, state: dict | None = None):
        state = state or {}
        self.seed = seed
        self.trials = int(state.get("trials", 0))
        self.decoders = {
            branch: CueDecoder(state.get("decoders", {}).get(branch))
            for branch in BRANCHES
        }
        self.world = HiddenWorld(seed)
        self.learners = {
            task: {
                branch: DecisionLearner(
                    branch, seed, state.get("learners", {}).get(task, {}).get(branch)
                )
                for branch in BRANCHES
            }
            for task in TASKS
        }
        self.task_visits = state.get("task_visits", dict.fromkeys(TASKS, 0))
        self.last = state.get("last")

    def select_task(self) -> str:
        total = sum(self.task_visits.values()) + 1
        scores = {}
        for task in TASKS:
            learner = self.learners[task]["spike_memory"]
            deficit = (
                1 - np.mean([r["net"] for r in learner.recent])
                if learner.recent
                else 0.9
            )
            coverage = 0.3 * math.sqrt(
                math.log(total + 1) / (self.task_visits[task] + 1)
            )
            scores[task] = float(deficit + coverage)
        return max(TASKS, key=lambda task: scores[task])

    def step(
        self, sensor, task: str | None = None, coupling: dict | None = None
    ) -> dict:
        task = task or self.select_task()
        index = self.task_visits[task]
        trial = self.world.trial(index, task)
        self.trials += 1
        self.task_visits[task] += 1
        # Live signal is a public, bounded perturbation of virtual probe cost.
        coupling = coupling or {}
        pressure = float(np.clip(coupling.get("rate_hz", 0) / 100, 0, 0.1))
        trial["probe_cost"] += pressure
        observation = self.world.observation(trial)
        cue = trial["context"]
        representation = sensor(cue if trial["visible"] else None)
        # A separate distractor window precedes decision. Only memory branches
        # retain the cue representation; no-memory does not receive its label.
        distractor = sensor(trial["distractor"])
        symbolic = (
            np.eye(CONTEXTS)[cue]
            if trial["visible"]
            else np.full(CONTEXTS, 1 / CONTEXTS)
        )
        revealed = None

        def reveal_neural(decoder):
            nonlocal revealed
            if revealed is None:
                revealed = sensor(cue)
            decoder.observe(cue, revealed["features"])
            return decoder.belief(revealed["features"])

        decisions = {}
        settlements = {}
        for branch, learner in self.learners[task].items():
            decoder = self.decoders[branch]
            if trial["visible"]:
                decoder.observe(cue, representation["features"])
            belief = decoder.belief(
                representation["features"] if trial["visible"] else None
            )
            settlements[branch] = learner.settle(index)
            context = symbolic if branch == "symbolic_memory" else belief
            reveal = (
                (lambda: np.eye(CONTEXTS)[cue])
                if branch == "symbolic_memory"
                else (lambda decoder=decoder: reveal_neural(decoder))
            )
            decision = learner.decide(observation, context.copy(), index, reveal)
            learner.schedule(decision, trial)
            decisions[branch] = decision
        self.last = {
            "trial": trial,
            "observation": observation,
            "decisions": decisions,
            "settlements": settlements,
            "cue_spikes": representation["spike_count"],
            "distractor_spikes": distractor["spike_count"],
            "cue_evidence": representation,
            "distractor_evidence": distractor,
            "probe_evidence": revealed,
            "cue_features_sha256": hashlib.sha256(
                np.asarray(representation["features"], dtype="<f8").tobytes()
            ).hexdigest(),
            "coupling": coupling,
            "scope": "Hidden-world audit fields are withheld from learners; unused rewards are counterfactual audit evidence only",
        }
        return self.last

    def public(self) -> dict:
        return {
            "version": VERSION,
            "trials": self.trials,
            "task_visits": self.task_visits,
            "tasks": {
                task: {branch: model.public() for branch, model in models.items()}
                for task, models in self.learners.items()
            },
            "last_trial": self.last,
            "curriculum_rule": "highest observed net-reward deficit + bounded coverage bonus; hidden oracle is never used",
            "scope": "Separate software decision laboratory; virtual rewards and credits; no SOL spending or consciousness inference",
        }

    def state(self) -> dict:
        return {
            "seed": self.seed,
            "trials": self.trials,
            "decoders": {
                branch: decoder.state() for branch, decoder in self.decoders.items()
            },
            "task_visits": self.task_visits,
            "last": self.last,
            "learners": {
                task: {branch: model.state() for branch, model in models.items()}
                for task, models in self.learners.items()
            },
        }
