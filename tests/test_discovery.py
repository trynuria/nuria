"""Information boundaries, delayed rewards, cost policy and durable continuation."""

import json
import unittest
import uuid
from pathlib import Path

import numpy as np

from cognition.discovery import (
    CONTEXTS,
    CueDecoder,
    DecisionLearner,
    DiscoveryLab,
    HiddenWorld,
)


class DiscoveryTests(unittest.TestCase):
    def test_hidden_world_observation_contains_no_solution(self):
        trial = HiddenWorld(51).trial(10, "reversal")
        observation = HiddenWorld.observation(trial)
        self.assertFalse(
            {"context", "rewards", "means", "phase", "seed"} & observation.keys()
        )
        self.assertEqual(trial, HiddenWorld(51).trial(10, "reversal"))

    def test_rewards_cannot_train_before_due(self):
        learner = DecisionLearner("symbolic_memory", 42)
        trial = HiddenWorld(51).trial(10, "association")
        d = learner.decide(
            HiddenWorld.observation(trial),
            np.eye(CONTEXTS)[trial["context"]],
            10,
            lambda: None,
        )
        learner.schedule(d, trial)
        self.assertEqual(learner.settle(10), [])
        self.assertEqual(float(learner.success.sum() + learner.failure.sum()), 0)
        learner.settle(20)
        self.assertEqual(learner.settled, 1)
        self.assertAlmostEqual(float(learner.success.sum() + learner.failure.sum()), 1)
        self.assertEqual(learner.settle(21), [])

    def test_probe_is_costed_and_optional(self):
        learner = DecisionLearner("symbolic_memory", 42)
        learner.success[:4, 0], learner.failure[:4, 1:] = 100, 100
        learner.success[4:, 1], learner.failure[4:, 0] = 100, 100
        observation = {"id": "probe", "probe_cost": 0.05, "arm_cost": 0.02}
        before = learner.balance
        d = learner.decide(observation, np.ones(8) / 8, 1, lambda: np.eye(8)[0])
        self.assertTrue(d["probe"])
        self.assertAlmostEqual(before - learner.balance, 0.07)
        observation["probe_cost"] = 1
        self.assertFalse(
            learner.decide(
                observation,
                np.ones(8) / 8,
                2,
                lambda: self.fail("Expensive probe executed"),
            )["probe"]
        )

    def test_no_probes_branch_never_calls_reveal(self):
        learner = DecisionLearner("no_probes", 42)
        learner.decide(
            {"id": "x", "probe_cost": 0, "arm_cost": 0.02},
            np.ones(8) / 8,
            1,
            lambda: self.fail("Control probed"),
        )
        self.assertEqual(learner.probes, 0)

    def test_budget_cannot_be_overdrawn(self):
        learner = DecisionLearner("spike_memory", 42, {"balance": 0.001})
        d = learner.decide(
            {"id": "x", "probe_cost": 0.1, "arm_cost": 0.025},
            np.ones(8) / 8,
            1,
            lambda: None,
        )
        self.assertEqual(d["arm"], -1)
        self.assertEqual(learner.balance, 0.001)

    def test_decoder_has_no_hidden_rule_or_reward_input(self):
        decoder = CueDecoder()
        x = np.zeros(32)
        x[2] = 1
        decoder.observe(2, x.tolist())
        self.assertEqual(int(np.argmax(decoder.belief(x.tolist()))), 2)
        self.assertTrue(np.allclose(decoder.belief(None), np.ones(8) / 8))
        with self.assertRaises(ValueError):
            decoder.observe(2, [float("nan")] * 32)

    @staticmethod
    def sensor(cue):
        x = np.zeros(32)
        if cue is not None:
            x[cue] = 1
        return {"features": x.tolist(), "spike_count": 0}

    def test_branch_decoders_do_not_share_probe_learning(self):
        lab = DiscoveryLab(51)
        lab.decoders["spike_memory"].observe(1, self.sensor(1)["features"])
        self.assertEqual(lab.decoders["no_probes"].counts.sum(), 0)

    def test_full_roundtrip_retains_pending_credit_and_choices(self):
        lab = DiscoveryLab(51)
        for _ in range(15):
            lab.step(self.sensor, "reversal")
        restored = DiscoveryLab(51, json.loads(json.dumps(lab.state())))
        self.assertEqual(
            lab.step(self.sensor, "reversal"), restored.step(self.sensor, "reversal")
        )
        self.assertEqual(lab.state(), restored.state())

    def test_curriculum_does_not_use_hidden_oracle(self):
        lab = DiscoveryLab(51)
        original = lab.select_task()
        for task in lab.learners.values():
            task["spike_memory"].total_regret = 1e9
        self.assertEqual(lab.select_task(), original)

    def test_actual_brian_sensor_checkpoint_preserves_rng(self):
        from cognition.lab_worker import LabRuntime

        root = Path(__file__).resolve().parents[1] / ".test-state" / uuid.uuid4().hex
        runtime = LabRuntime(root, "a" * 64)
        runtime.step()
        before = runtime.lab.state()
        runtime.save()
        expected = runtime.sensor(3)
        recorded_counts = np.bincount(
            [neuron for _, neuron in expected["spikes"]], minlength=256
        )
        self.assertEqual(recorded_counts.tolist(), expected["counts"])
        reconstructed = np.concatenate(
            (
                recorded_counts.reshape(16, 16).mean(axis=1) / 4,
                expected["pool_voltages"],
            )
        )
        self.assertEqual(reconstructed.tolist(), expected["features"])
        restored = LabRuntime(root, "a" * 64)
        self.assertEqual(restored.lab.state(), before)
        self.assertEqual(restored.sensor(3), expected)
        self.assertTrue(restored.journal.verify(full=True)["valid"])

    def test_matched_branches_share_exploration_draws(self):
        a, b = (
            DecisionLearner("spike_memory", 51),
            DecisionLearner("symbolic_memory", 51),
        )
        observation = {"id": "matched", "probe_cost": 0.1, "arm_cost": 0.025}
        for index in range(60):
            first = a.decide(observation, np.eye(8)[0], index, lambda: None)
            second = b.decide(observation, np.eye(8)[0], index, lambda: None)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
