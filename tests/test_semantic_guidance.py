"""Functional and integrity checks, not a program-search evaluation."""

import copy
import hashlib
import unittest
from unittest.mock import patch

import numpy as np

from cognition.semantic_guidance import METHODS, SemanticGuidance


def identity(value):
    return hashlib.sha256(value.encode()).hexdigest()


def examples():
    records = []
    for index in range(32):
        features = [float(index % 2), float((index // 2) % 2)] + [0.0] * 98
        records.append(
            {
                "features": features,
                "label": int(features[0] != features[1]),
                "job_id": identity(str(index)),
                "candidate_sha256": identity("candidate" + str(index)),
                "program_sha256": identity("accepted-program"),
                "receipt_evidence_sha256": identity("buyer" + str(index)),
            }
        )
    return records


class SemanticGuidanceTests(unittest.TestCase):
    def test_actual_spikes_and_input_learning_change_priorities(self):
        model = SemanticGuidance(width=32, epochs=64, learning_rate=0.03)
        before = model.W.copy()
        records = examples()
        model.fit(records)
        x = [r["features"] for r in records[:4]]
        values = model.score(x)
        self.assertTrue(np.all((values >= 0.5) == [False, True, True, False]))
        self.assertGreater(np.count_nonzero(model.W != before), 0)
        hidden, _, spikes = model._hidden(np.asarray(x))
        self.assertGreater(int(spikes.sum()), 0)
        np.testing.assert_array_equal(hidden, spikes / 16)

    def test_controls_share_training_identity_and_balanced_update_count(self):
        records = examples()
        fitted = [SemanticGuidance(method=m, epochs=2).fit(records) for m in METHODS]
        self.assertEqual(len({m.training_sha256 for m in fitted}), 1)
        self.assertEqual(
            len({m.steps for m in fitted if m.configuration["method"] != "knn"}), 1
        )
        frozen = fitted[METHODS.index("frozen_input")]
        initial = SemanticGuidance(method="frozen_input", epochs=2)
        np.testing.assert_array_equal(frozen.W, initial.W)
        np.testing.assert_array_equal(frozen.H, initial.H)
        self.assertFalse(np.array_equal(frozen.V, initial.V))

    def test_fit_and_read_only_scoring_are_deterministic(self):
        records = examples()
        model = SemanticGuidance(epochs=2).fit(records)
        before = copy.deepcopy(model.evidence())
        prediction = model.score([records[0]["features"]]).tolist()
        self.assertEqual(before, model.evidence())
        model.fit(records)
        self.assertEqual(before, model.evidence())
        self.assertEqual(prediction, model.score([records[0]["features"]]).tolist())

    def test_same_logits_with_derivatives_enabled_or_disabled(self):
        for method in ("spiking", "continuous", "frozen_input"):
            model = SemanticGuidance(method=method, epochs=1).fit(examples())
            x = np.asarray([r["features"] for r in examples()[:4]])
            no_derivative = model._hidden(x)
            derivative = model._hidden(x, derivatives=True)
            np.testing.assert_array_equal(no_derivative[0], derivative[0])
            np.testing.assert_array_equal(no_derivative[2], derivative[2])
            self.assertIsNone(no_derivative[1])

    def test_malformed_or_duplicated_evidence_cannot_replace_model(self):
        model = SemanticGuidance(epochs=1).fit(examples())
        before = model.evidence()
        alterations = [
            lambda r: r[0].update(label=True),
            lambda r: r[0].update(job_id="unbound"),
            lambda r: r[0].update(secret="unexpected"),
            lambda r: r[0]["features"].__setitem__(0, True),
            lambda r: r[0]["features"].__setitem__(0, float("nan")),
            lambda r: r.__setitem__(1, copy.deepcopy(r[0])),
            lambda r: [row.update(label=0) for row in r],
        ]
        for alter in alterations:
            records = examples()
            alter(records)
            with self.assertRaises(ValueError):
                model.fit(records)
            self.assertEqual(before, model.evidence())

    def test_training_failure_restores_previous_fitted_model(self):
        model = SemanticGuidance(epochs=1).fit(examples())
        before = model.evidence()
        with patch.object(
            SemanticGuidance, "_hidden", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                model.fit(examples())
        self.assertEqual(before, model.evidence())

    def test_nearest_neighbor_uses_same_observations(self):
        records = examples()
        model = SemanticGuidance(method="knn").fit(records)
        scores = model.score([r["features"] for r in records[:4]])
        np.testing.assert_array_equal(scores >= 0.5, [False, True, True, False])
        self.assertEqual(
            model.evidence()["reference_features"], [r["features"] for r in records]
        )

    def test_unfitted_and_invalid_scoring_fail_closed(self):
        model = SemanticGuidance()
        with self.assertRaises(ValueError):
            model.score([[0.0] * 100])
        model.fit(examples())
        for values in ([], [[0.0] * 99], [[2.0] * 100], [["0"] * 100], [[True] * 100]):
            with self.assertRaises(ValueError):
                model.score(values)

    def test_inference_checkpoint_restores_all_controls(self):
        records = examples()
        observations = [r["features"] for r in records[:4]]
        for method in METHODS:
            model = SemanticGuidance(method=method, epochs=2).fit(records)
            restored = SemanticGuidance.from_evidence(model.evidence())
            self.assertEqual(model.evidence(), restored.evidence())
            np.testing.assert_array_equal(
                model.score(observations), restored.score(observations)
            )

    def test_malformed_checkpoint_and_changed_frozen_features_are_rejected(self):
        model = SemanticGuidance(method="frozen_input", epochs=2).fit(examples())
        changes = [
            lambda e: e.update(optimizer_steps=1),
            lambda e: e.update(training_sha256="unbound"),
            lambda e: e["weights"]["W"][0].__setitem__(0, 0.5),
            lambda e: e["weights"]["frequencies"][0].__setitem__(0, 0.5),
            lambda e: e["weights"]["V"].__setitem__(0, True),
            lambda e: e.update(reference_features=[[0.0] * 100]),
        ]
        for change in changes:
            evidence = copy.deepcopy(model.evidence())
            change(evidence)
            with self.assertRaises(ValueError):
                SemanticGuidance.from_evidence(evidence)


if __name__ == "__main__":
    unittest.main()
