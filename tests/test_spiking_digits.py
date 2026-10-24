"""Neural digit learning, exact recovery and malformed-state boundaries."""

import copy
import json
import unittest

import numpy as np

from cognition.spiking_digits import SpikingDigits


class SpikingDigitsTests(unittest.TestCase):
    def test_json_recovery_preserves_predictions_and_next_update(self):
        network = SpikingDigits(seed=91, width=12, ticks=8)
        batch = np.array([[0.0] * 64, [1.0] * 64])
        network.train_batch(batch, [2, 7], verified=True)
        state = json.loads(json.dumps(network.state(), allow_nan=False))
        restored = SpikingDigits(**state["configuration"], state=state)
        self.assertEqual(restored.predict([16] * 64), network.predict([16] * 64))
        network.train_batch(batch, [2, 7], verified=True)
        restored.train_batch(batch, [2, 7], verified=True)
        self.assertEqual(restored.state(), network.state())

    def test_verified_outcomes_train_inputs_and_output_decisions(self):
        network = SpikingDigits(seed=17, width=16, ticks=8)
        before = network.model_weights()
        batch = np.array([[0.0] * 64, [1.0] * 64])
        for _ in range(100):
            network.train_batch(
                batch, [0, 1], verified=True, learning_rate=0.01, record_hashes=False
            )
        self.assertEqual(network.predict([0] * 64)["label"], 0)
        self.assertEqual(network.predict([16] * 64)["label"], 1)
        self.assertNotEqual(before["W"], network.model_weights()["W"])
        self.assertNotEqual(before["V"], network.model_weights()["V"])

    def test_frozen_inputs_remain_exact_while_head_learns(self):
        network = SpikingDigits(seed=18, frozen_input=True)
        before = network.model_weights()
        network.train_batch([[1.0] * 64], [5], verified=True)
        after = network.model_weights()
        self.assertEqual(before["W"], after["W"])
        self.assertEqual(before["H"], after["H"])
        self.assertNotEqual(before["V"], after["V"])

    def test_hard_spike_counts_produce_the_reported_logits(self):
        network = SpikingDigits(seed=19, width=8, ticks=5)
        pixels = [16 if i % 3 else 0 for i in range(64)]
        result = network.predict(pixels)
        counts = np.asarray(result["spike_counts"])
        self.assertEqual(int(counts.sum()), result["total_spikes"])
        self.assertTrue(np.all((counts >= 0) & (counts <= 5)))
        np.testing.assert_array_equal(
            counts / 5 @ network.V + network.C, result["logits"]
        )
        self.assertEqual(result["label"], int(np.argmax(result["logits"])))

    def test_prediction_does_not_change_weights_or_optimizer(self):
        network = SpikingDigits()
        before = network.state_hash()
        result = network.predict([4] * 64)
        self.assertEqual(before, network.state_hash())
        self.assertEqual(result["state_sha256"], before)
        self.assertAlmostEqual(sum(result["probabilities"]), 1.0)

    def test_unverified_or_malformed_labels_cannot_mutate_state(self):
        network = SpikingDigits()
        before = network.state()
        for labels, verified in (
            ([0], False),
            ([True], True),
            ([10], True),
            ([], True),
        ):
            with self.subTest(labels=labels, verified=verified):
                with self.assertRaises(ValueError):
                    network.train_batch([[0.0] * 64], labels, verified=verified)
                self.assertEqual(before, network.state())

    def test_malformed_inputs_are_rejected(self):
        network = SpikingDigits()
        for pixels in ([0] * 63, [True] * 64, [0.5] * 64, [17] * 64):
            with self.subTest(pixels=pixels[:2]):
                with self.assertRaises(ValueError):
                    network.predict(pixels)
        for batch in ([], [[-0.1] * 64], [[float("nan")] * 64], [[2.0] * 64]):
            with self.subTest(batch=batch[:1]):
                with self.assertRaises(ValueError):
                    network.forward(batch)

    def test_restore_rejects_changed_configuration_and_bad_arrays(self):
        network = SpikingDigits()
        state = network.state()
        with self.assertRaises(ValueError):
            SpikingDigits(seed=202612, state=state)
        for mutation in ("shape", "nan", "negative_second", "identity_type", "step"):
            bad = copy.deepcopy(state)
            if mutation == "shape":
                bad["weights"]["V"] = [[0.0]]
            elif mutation == "nan":
                bad["weights"]["W"][0][0] = float("nan")
            elif mutation == "negative_second":
                bad["optimizer"]["second_moment"]["H"][0] = -0.1
            elif mutation == "identity_type":
                bad["configuration"]["width"] = 64.0
            else:
                bad["optimizer"]["step"] = True
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    SpikingDigits(state=bad)

    def test_continuous_control_has_no_spikes(self):
        network = SpikingDigits(kind="continuous")
        prediction = network.predict([16] * 64)
        self.assertEqual(prediction["total_spikes"], 0)
        self.assertEqual(prediction["spike_counts"], [0] * 64)


if __name__ == "__main__":
    unittest.main()
