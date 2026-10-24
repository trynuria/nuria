"""Tamper boundaries for the additional offline frozen-results checker."""

import copy
import gzip
import hashlib
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np

from scripts import check_neural_digit_results as check


class NeuralDigitResultsTests(unittest.TestCase):
    def test_gzip_identity_is_uncompressed_and_decompression_is_bounded(self):
        root = Path(".test-state") / ("digit-result-reader-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        raw = json.dumps({"x": "q" * 500}).encode()
        path = root / "artifact.json.gz"
        path.write_bytes(gzip.compress(raw))
        value, identity, size = check.read_json(root / "artifact.json")
        self.assertEqual(value["x"], "q" * 500)
        self.assertEqual(identity, hashlib.sha256(raw).hexdigest())
        self.assertEqual(size, len(raw))
        with self.assertRaises(ValueError):
            check.read_json(path, maximum=20)

    def test_logits_require_shape_finite_real_values_and_agreement(self):
        check.logits_match([[0, 1]], [[0, 1]])
        for actual in ([0, 1], [[0, 2]], [[False, True]], [[0, float("nan")]]):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                check.logits_match(actual, [[0, 1]])

    def test_labels_reject_booleans_floats_and_missing_coverage(self):
        for value in ([False], [1.0], [10], [], [0, 1]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                check.labels(value, rows=1)
        np.testing.assert_array_equal(check.labels([0], rows=1), [0])

    def test_mean_cannot_hide_one_matched_seed_regression(self):
        rows = []
        for ident, (method, _, seed) in check.EXPECTED.items():
            accuracy = 0.6
            if method == "snn_trained":
                accuracy = 0.99 if seed != check.SEEDS[-1] else 0.7
            elif method == "snn_input_frozen":
                accuracy = 0.5 if seed != check.SEEDS[-1] else 0.8
            rows.append({"id": ident, "accuracy": accuracy, "rows": check.ROWS})
        gates, mean, frozen = check.calculate_gates(rows, 0.01)
        self.assertGreater(mean - frozen, 0.01)
        self.assertFalse(gates["learned_input_contribution"])
        self.assertTrue(gates["neural_over_linear"])

    def test_commitment_rejects_changed_flags_freeze_hash_and_membership(self):
        entries, outputs = {}, []
        for ident, (method, kind, seed) in check.EXPECTED.items():
            item = {
                "id": ident,
                "method": method,
                "kind": kind,
                "seed": seed,
                "model_sha256": "m",
                "state_sha256": "s",
            }
            entries[ident] = {"item": item, "bytes": 2}
            outputs.append(
                {
                    **item,
                    "state_before_sha256": "s",
                    "state_after_sha256": "s",
                    "checkpoint_file_bytes": 2,
                    "inference_cpu_seconds": 0.1,
                    "labels": [0],
                }
            )
        public = {"commit": check.FREEZE_COMMIT, "protocol_sha256": "p"}
        prediction = {
            "schema": "nuria.neural-digit-predictions.v1",
            "public_freeze": public,
            "rows": 1,
            "models": outputs,
            "protocol_sha256": "p",
            "test_learning_enabled": False,
        }
        commitment = {
            "schema": "nuria.neural-digit-prediction-commit.v1",
            "public_freeze": public,
            "rows": 1,
            "models": 11,
            "protocol_sha256": "p",
            "predictions_sha256": "h",
            "scoring_labels_read": False,
        }
        with patch.object(check, "ROWS", 1):
            check.check_commitment(prediction, "h", commitment, "p", entries)
            for key, value in (
                ("scoring_labels_read", True),
                ("predictions_sha256", "changed"),
                ("models", 10),
            ):
                changed = copy.deepcopy(commitment)
                changed[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    check.check_commitment(prediction, "h", changed, "p", entries)
            changed = copy.deepcopy(prediction)
            changed["models"][-1] = changed["models"][0]
            with self.assertRaises(ValueError):
                check.check_commitment(changed, "h", commitment, "p", entries)


if __name__ == "__main__":
    unittest.main()
