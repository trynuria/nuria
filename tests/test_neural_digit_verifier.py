"""Independent digit dynamics, malformed evidence and actual Brian2 parity."""

import copy
import unittest

import numpy as np

from scripts.verify_neural_digits import (
    brian_parity,
    independent_predict,
    paired_correctness,
    validated_pixels,
    validated_weights,
    verify_model_predictions,
)


def fixture():
    weights = {
        "W": np.zeros((64, 64)),
        "H": np.zeros(64),
        "V": np.zeros((64, 10)),
        "C": np.zeros(10),
    }
    weights["W"][0, 0] = 1.25
    weights["W"][1, 1] = 0.75
    weights["V"][0, 3] = 4
    weights["V"][1, 7] = 4
    weights["C"][1] = 0.1
    pixels = np.zeros((3, 64), dtype=np.int64)
    pixels[0, 0] = 16
    pixels[1, 1] = 16
    return {key: value.tolist() for key, value in weights.items()}, pixels.tolist()


class NeuralDigitVerifierTests(unittest.TestCase):
    def test_actual_threshold_reset_counts_drive_answers(self):
        weights, pixels = fixture()
        result = independent_predict(weights, pixels)
        self.assertEqual(result["labels"], [3, 7, 1])
        self.assertEqual(result["spike_counts"][0][0], 16)
        self.assertEqual(result["spike_counts"][1][1], 10)
        published = {key: result[key] for key in ("labels", "spike_counts", "logits")}
        proof = verify_model_predictions(weights, pixels, published)
        self.assertTrue(proof["verified"])
        self.assertEqual(proof["rows"], 3)
        self.assertTrue(proof["checked_published_spike_counts"])
        self.assertTrue(proof["checked_published_logits"])
        self.assertEqual(proof["interventions"]["zero_spikes_changed_labels"], 2)
        self.assertEqual(proof["interventions"]["permuted_spikes_changed_labels"], 2)
        self.assertTrue(proof["interventions"]["restoration_exact"])

    def test_equal_threshold_fires_and_logit_ties_choose_lowest_class(self):
        weights, pixels = fixture()
        weights["W"][0][0] = 1.0
        weights["V"] = np.zeros((64, 10)).tolist()
        weights["C"] = [0.0] * 10
        result = independent_predict(weights, pixels[:1])
        self.assertEqual(result["spike_counts"][0][0], 16)
        self.assertEqual(result["labels"], [0])

    def test_forged_labels_counts_logits_and_unknown_fields_are_rejected(self):
        weights, pixels = fixture()
        reference = independent_predict(weights, pixels)
        for field in ("labels", "spike_counts", "logits"):
            value = {
                key: reference[key] for key in ("labels", "spike_counts", "logits")
            }
            value = copy.deepcopy(value)
            if field == "labels":
                value[field][0] = 9
            else:
                value[field][0][0] += 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_model_predictions(weights, pixels, value)
        for published in ({}, {"labels": [3, 7, 1], "targets": [3, 7, 1]}):
            with self.assertRaises(ValueError):
                verify_model_predictions(weights, pixels, published)

    def test_pixel_types_ranges_and_batch_bounds_are_strict(self):
        for replacement in (True, 1.0, "1", float("nan"), float("inf"), -1, 17):
            _, pixels = fixture()
            pixels[0][0] = replacement
            with self.subTest(value=replacement), self.assertRaises(ValueError):
                validated_pixels(pixels)
        invalid = ([], [[0] * 63], [[0] * 65], [0] * 64, [[0] * 64, [0] * 63])
        for pixels in invalid:
            with self.subTest(pixels=pixels), self.assertRaises(ValueError):
                validated_pixels(pixels)
        with self.assertRaises(ValueError):
            validated_pixels([[0] * 64] * 3, maximum_rows=2)

    def test_weight_shapes_finiteness_magnitude_and_architecture_are_strict(self):
        for name, replacement in (
            ("W", [[0] * 63] * 64),
            ("H", [0] * 63),
            ("V", [[0] * 9] * 64),
            ("C", [0] * 9),
        ):
            weights, _ = fixture()
            weights[name] = replacement
            with self.subTest(name=name), self.assertRaises(ValueError):
                validated_weights(weights)
        for replacement in (True, "0", float("nan"), float("inf"), 1_000_001):
            weights, _ = fixture()
            weights["H"][0] = replacement
            with self.subTest(value=replacement), self.assertRaises(ValueError):
                validated_weights(weights)
        weights, _ = fixture()
        for changed in (
            {**weights, "ticks": 15},
            {**weights, "ticks": True},
            {**weights, "code": "pass"},
        ):
            with self.assertRaises(ValueError):
                validated_weights(changed)

    def test_paired_counts_keep_wins_regressions_and_every_row(self):
        result = paired_correctness([0, 1, 2, 3], [0, 1, 4, 4], [0, 4, 2, 4])
        self.assertEqual(result["both_correct"], 1)
        self.assertEqual(result["first_only_correct"], 1)
        self.assertEqual(result["second_only_correct"], 1)
        self.assertEqual(result["neither_correct"], 1)
        self.assertEqual(result["accuracy_difference"], 0)
        with self.assertRaises(ValueError):
            paired_correctness([0], [0, 1], [0])
        with self.assertRaises(ValueError):
            paired_correctness([0], [True], [0])
        with self.assertRaises(ValueError):
            paired_correctness([0], {0: "not a prediction"}, [0])

    def test_actual_brian2_matches_nontrivial_and_negative_drives(self):
        weights, pixels = fixture()
        weights["H"][5] = -0.4
        result = brian_parity(weights, pixels)
        self.assertTrue(result["verified"])
        self.assertTrue(result["matched_spike_counts"])
        self.assertTrue(result["matched_predictions"])
        self.assertEqual(result["total_hidden_spikes"], 26)
        self.assertLess(result["maximum_membrane_error"], 1e-10)
        with self.assertRaises(ValueError):
            brian_parity(weights, pixels * 3)


if __name__ == "__main__":
    unittest.main()
