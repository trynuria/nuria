import copy
import unittest

import numpy as np

from cognition.semantic_guidance import SemanticGuidance
from scripts.verify_semantic_guidance import digest, validate_evidence, verify
from tests.test_semantic_guidance import examples


class SemanticGuidanceVerifierTests(unittest.TestCase):
    def fixture(self, method="spiking"):
        model = SemanticGuidance(method=method, width=8, epochs=2).fit(examples())
        features = [r["features"] for r in examples()[:8]]
        _, _, counts = model._hidden(np.asarray(features))
        return (
            model.evidence(),
            features,
            model.score(features).tolist(),
            counts.tolist(),
        )

    def test_independent_probabilities_and_counts(self):
        for method in ("spiking", "frozen_input", "continuous"):
            result = verify(*self.fixture(method), brian=False)
            self.assertTrue(result["verified"])
            self.assertLess(result["maximum_probability_error"], 1e-12)
            evidence, features, _, _ = self.fixture(method)
            self.assertEqual(result["evidence_sha256"], digest(evidence))
            self.assertEqual(
                result["configuration_sha256"], digest(evidence["configuration"])
            )
            self.assertEqual(result["features_sha256"], digest(features))
            self.assertEqual(len(result["embedded_model_sha256"]), 64)

    def test_configuration_and_complete_checkpoint_schema_are_independent_checks(self):
        evidence, features, probability, counts = self.fixture()
        for alter in (
            lambda e: e["configuration"].pop("epochs"),
            lambda e: e["configuration"].update(unreviewed=True),
            lambda e: e["configuration"].update(seed=True),
            lambda e: e["configuration"].update(regularization=float("nan")),
            lambda e: e.update(unreviewed=True),
            lambda e: e["weights"].pop("phases"),
            lambda e: e.update(training_sha256="unbound"),
            lambda e: e.update(optimizer_steps=True),
            lambda e: e.update(positive_count=e["records_count"]),
            lambda e: e.update(reference_features=[]),
        ):
            changed = copy.deepcopy(evidence)
            alter(changed)
            with self.subTest(alteration=alter), self.assertRaises(ValueError):
                verify(changed, features, probability, counts, brian=False)

    def test_frozen_inputs_and_fixed_fourier_features_are_seed_bound(self):
        evidence, features, probability, counts = self.fixture("frozen_input")
        for key in ("W", "H", "frequencies", "phases"):
            changed = copy.deepcopy(evidence)
            if key in ("W", "frequencies"):
                changed["weights"][key][0][0] += 0.001
            else:
                changed["weights"][key][0] += 0.001
            with self.subTest(weight=key), self.assertRaises(ValueError):
                verify(changed, features, probability, counts, brian=False)
        rff = SemanticGuidance(method="rff", epochs=1).fit(examples()).evidence()
        validate_evidence(rff)
        rff["weights"]["frequencies"][0][0] += 0.001
        with self.assertRaises(ValueError):
            validate_evidence(rff)

    def test_training_data_hash_and_provenance_bind_exact_supplied_records(self):
        evidence, features, probability, counts = self.fixture()
        report = verify(
            evidence,
            features,
            probability,
            counts,
            brian=False,
            training_records=examples(),
        )
        self.assertTrue(report["training_records_checked"])
        for alter in (
            lambda rows: rows[0].update(label=1),
            lambda rows: rows[0].update(receipt_evidence_sha256="0" * 64),
            lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0])),
        ):
            changed = examples()
            alter(changed)
            with self.subTest(alteration=alter), self.assertRaises(ValueError):
                verify(
                    evidence,
                    features,
                    probability,
                    counts,
                    brian=False,
                    training_records=changed,
                )

    def test_knn_reference_rows_match_supplied_training_data(self):
        evidence = SemanticGuidance(method="knn").fit(examples()).evidence()
        validate_evidence(evidence, training_records=examples())
        evidence["reference_features"][0][0] = 0.125
        with self.assertRaises(ValueError):
            validate_evidence(evidence, training_records=examples())

    def test_changed_score_count_and_boolean_are_rejected(self):
        evidence, features, probability, counts = self.fixture()
        incorrect = probability.copy()
        incorrect[0] += 0.01
        with self.assertRaises(ValueError):
            verify(evidence, features, incorrect, counts, brian=False)
        for value in (counts[0][0] + 1, True):
            changed = copy.deepcopy(counts)
            changed[0][0] = value
            with self.assertRaises(ValueError):
                verify(evidence, features, probability, changed, brian=False)

    def test_continuous_control_cannot_publish_spikes(self):
        evidence, features, probability, counts = self.fixture("continuous")
        counts[0][0] = 1
        with self.assertRaises(ValueError):
            verify(evidence, features, probability, counts, brian=False)

    def test_conventional_control_cannot_claim_lif_parity(self):
        evidence, features, probability, counts = self.fixture()
        evidence["configuration"]["method"] = "linear"
        with self.assertRaises(ValueError):
            verify(evidence, features, probability, counts, brian=False)

    def test_actual_brian2_spikes(self):
        try:
            import brian2  # noqa: F401
        except ImportError:
            self.skipTest("Brian2 is an optional independent verification dependency")
        result = verify(*self.fixture())
        self.assertIsNotNone(result["brian2"])
        self.assertTrue(result["verified"])


if __name__ == "__main__":
    unittest.main()
