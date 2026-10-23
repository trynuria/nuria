"""Causal coupling, baseline equality, delayed history and trusted recovery."""

import importlib.util
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cognition.brain import AdaptiveBrain, sha
from cognition.multiscale import CoupledBrain, CouplingConfig
from cognition.multiscale_loop import MultiscaleExperiment

ROOT = Path(__file__).resolve().parents[1]
HAS_TVB = importlib.util.find_spec("tvb") is not None


def fresh():
    path = ROOT / ".test-state" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def event(i):
    return {
        "id": "multiscale-fixture-" + str(i),
        "side": "buy" if i % 2 else "sell",
        "quote_amount": 0.25 if i % 3 else 2.0,
        "creator_fee": 0.001,
        "source": "synthetic:multiscale",
    }


class DriveBoundaryTests(unittest.TestCase):
    def test_invalid_external_drive_leaves_base_state_unchanged(self):
        brain = AdaptiveBrain(fresh())
        before = brain.state_hash()
        for drive in [np.zeros(76), np.full(1024, np.nan), np.full(1024, 0.201)]:
            with self.assertRaises(ValueError):
                brain.advance(event(1), regional_drive=drive)
            self.assertEqual(brain.state_hash(), before)

    def test_invalid_model_parameters_are_rejected(self):
        for config in [
            dict(region_to_spike_gain=1),
            dict(dt_ms=0.5),
            dict(global_gain=float("nan")),
        ]:
            with self.assertRaises(ValueError):
                CouplingConfig(**config)


@unittest.skipUnless(HAS_TVB, "Install research/multiscale/requirements.txt")
class MultiscaleTests(unittest.TestCase):
    def test_zero_return_matches_the_existing_neural_circuit(self):
        baseline = AdaptiveBrain(fresh())
        expected = [baseline.advance(event(i), duration_ms=20) for i in range(8)]
        coupled = CoupledBrain(fresh(), CouplingConfig(region_to_spike_gain=0))
        actual = [coupled.advance(event(i)) for i in range(8)]
        self.assertEqual(
            [r["spikes_sha256"] for r in actual], [r["spikes_sha256"] for r in expected]
        )
        self.assertEqual(coupled.brain.state_hash(), baseline.state_hash())

    def test_exchange_uses_previous_window_and_real_spikes_change_next_drive(self):
        coupled = CoupledBrain(fresh())
        first = coupled.advance(event(1))
        second = coupled.advance(event(2))
        self.assertEqual(
            first["multiscale"]["regional"]["used_previous_spike_rates"], [0.0] * 6
        )
        self.assertEqual(
            second["multiscale"]["regional"]["used_previous_spike_rates"],
            first["regional_rates"],
        )
        self.assertGreater(second["multiscale"]["regional_drive_max_abs"], 0)
        self.assertLessEqual(second["multiscale"]["regional_drive_max_abs"], 0.2)

    def test_disabling_return_changes_measured_spiking_trajectory(self):
        current = AdaptiveBrain(fresh())
        baseline = [
            current.advance(event(i), duration_ms=20)["spikes_sha256"] for i in range(8)
        ]
        coupled = CoupledBrain(fresh())
        hybrid = [coupled.advance(event(i))["spikes_sha256"] for i in range(8)]
        self.assertTrue(any(a != b for a, b in zip(baseline, hybrid)))

    def test_joint_restore_preserves_delayed_state_and_next_spikes(self):
        coupled = CoupledBrain(fresh())
        for i in range(6):
            coupled.advance(event(i))
        raw = coupled.checkpoint()
        expected = [coupled.advance(event(i)) for i in range(6, 10)]
        resumed = CoupledBrain(fresh())
        resumed.restore(raw, sha(raw))
        actual = [resumed.advance(event(i)) for i in range(6, 10)]
        for a, b in zip(actual, expected):
            self.assertEqual(a["spikes_sha256"], b["spikes_sha256"])
            self.assertEqual(
                a["multiscale"]["after_sha256"], b["multiscale"]["after_sha256"]
            )

    def test_corrupt_or_different_model_checkpoint_is_refused(self):
        coupled = CoupledBrain(fresh())
        raw = coupled.checkpoint()
        with self.assertRaises(ValueError):
            coupled.restore(raw + b"changed", sha(raw))
        other = CoupledBrain(fresh(), CouplingConfig(region_to_spike_gain=0))
        with self.assertRaises(ValueError):
            other.restore(raw, sha(raw))

    def test_invalid_input_does_not_advance_either_clock(self):
        coupled = CoupledBrain(fresh())
        before = coupled.state_hash()
        with self.assertRaises(ValueError):
            coupled.advance({**event(1), "quote_amount": float("nan")})
        self.assertEqual(coupled.state_hash(), before)
        with self.assertRaises(ValueError):
            coupled.advance(event(1), duration_ms=100)
        self.assertEqual(coupled.state_hash(), before)

    def test_partial_step_requires_recovery_instead_of_silent_continuation(self):
        coupled = CoupledBrain(fresh())
        with patch.object(
            coupled.regional, "advance", side_effect=RuntimeError("interrupted")
        ):
            with self.assertRaises(RuntimeError):
                coupled.advance(event(1))
        with self.assertRaises(RuntimeError):
            coupled.advance(event(2))
        with self.assertRaises(RuntimeError):
            coupled.checkpoint()

    def test_shuffled_topology_is_a_distinct_pinned_experiment(self):
        reference = CoupledBrain(fresh())
        shuffled = CoupledBrain(fresh(), CouplingConfig(shuffle_seed=9021))
        self.assertNotEqual(reference.identity_sha256, shuffled.identity_sha256)
        self.assertEqual(
            np.count_nonzero(reference.regional.sim.connectivity.weights),
            np.count_nonzero(shuffled.regional.sim.connectivity.weights),
        )
        self.assertNotEqual(
            reference.regional.connectivity_sha256,
            shuffled.regional.connectivity_sha256,
        )

    def test_journal_recovers_learning_and_deduplicates_source_inputs(self):
        directory = fresh()
        model = MultiscaleExperiment(directory)
        first = model.observe(0, event(1))
        expected_state = model.path.model.state_hash()
        model.close()
        resumed = MultiscaleExperiment(directory)
        try:
            self.assertEqual(resumed.path.model.state_hash(), expected_state)
            self.assertEqual(resumed.observe(0, event(1)), first)
            self.assertEqual(resumed.path.events, 1)
            self.assertEqual(resumed.journal.seq, 1)
            with self.assertRaises(ValueError):
                resumed.observe(0, {**event(1), "side": "sell"})
            resumed.observe(1, event(2))
            self.assertEqual(resumed.path.events, 2)
            self.assertTrue(resumed.journal.verify(full=True)["valid"])
        finally:
            resumed.close()

    def test_failed_commit_rolls_back_both_circuits_learning_and_input_cursor(self):
        directory = fresh()
        model = MultiscaleExperiment(directory)
        model.observe(0, event(1))
        previous = model.path.model.state_hash()
        with patch.object(
            model.journal, "save", side_effect=RuntimeError("disk fault")
        ):
            with self.assertRaises(RuntimeError):
                model.observe(1, event(2))
        with self.assertRaises(RuntimeError):
            model.observe(2, event(3))
        model.close()
        resumed = MultiscaleExperiment(directory)
        try:
            self.assertEqual(resumed.path.events, 1)
            self.assertEqual(resumed.path.model.state_hash(), previous)
            self.assertEqual(resumed.journal.seq, 1)
            resumed.observe(1, event(2))
            self.assertEqual(resumed.path.events, 2)
        finally:
            resumed.close()

    def test_existing_history_and_competing_writer_are_refused(self):
        directory = fresh()
        (directory / "unrelated-history.txt").write_text("preserve")
        with self.assertRaises(ValueError):
            MultiscaleExperiment(directory)
        directory = fresh()
        owner = MultiscaleExperiment(directory)
        try:
            with self.assertRaises(RuntimeError):
                MultiscaleExperiment(directory)
        finally:
            owner.close()


if __name__ == "__main__":
    unittest.main()
