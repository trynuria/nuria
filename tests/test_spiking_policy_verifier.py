"""Independent numerical reference, decision integrity and actual Brian2 parity."""

import copy
import importlib.util
import unittest

import numpy as np

from cognition.spiking_policy import SpikingDecisionPolicy
from scripts.verify_spiking_policy import (
    brian_parity,
    digest,
    independent_forward,
    model_hash,
    validated_features,
    verify_artifact,
    verify_decision,
    verify_forward,
    verify_initialization,
)


class SpikingPolicyVerifierTests(unittest.TestCase):
    def model(self, **overrides):
        configuration = dict(
            source_id="reference:tool-routing-v1",
            feature_dimension=2,
            arm_ids=["first", "second"],
            width=4,
            ticks=8,
            warmup=0,
            exploration=0,
        )
        return SpikingDecisionPolicy(**(configuration | overrides))

    def weights(self, model):
        return model.state()["weights"]

    def hard_model(self):
        model = self.model(width=2, ticks=4)
        model.W[:] = [[2, -2], [-2, 2]]
        model.H[:] = 0
        model.V[:] = [[1, -1], [-1, 1]]
        model.C[:] = 0
        return model

    def artifact(self, model, features):
        values, hidden, _, counts = model.forward(features)
        return {
            "configuration": copy.deepcopy(model.configuration),
            "weights": self.weights(model),
            "features": features,
            "forward": {
                "values": values.tolist(),
                "hidden": hidden.tolist(),
                "spike_counts": counts.tolist(),
            },
            "decisions": [
                model.choose(row, digest(["case", i]), list(model.arms))
                for i, row in enumerate(features)
            ],
        }

    def test_128_feature_initial_model_matches_seed_and_independent_arithmetic(self):
        model = self.model(feature_dimension=128, width=16, ticks=16)
        features = np.random.default_rng(100).random((4, 128)).tolist()
        artifact = self.artifact(model, features) | {"initial": True}
        report = verify_artifact(artifact, include_brian=False)
        self.assertTrue(report["initialization"]["verified"])
        self.assertEqual(
            model.model_hash(), model_hash(model.configuration, self.weights(model))
        )
        self.assertEqual(len(report["decisions"]), 4)
        self.assertTrue(all(d["greedy_choice_checked"] for d in report["decisions"]))

    def test_threshold_subtractive_reset_and_spikes_determine_legal_choice(self):
        model = self.hard_model()
        reference = independent_forward(
            model.configuration, self.weights(model), [[1, 0], [0, 1]]
        )
        self.assertEqual(reference["spike_counts"], [[4, 0], [0, 4]])
        self.assertGreater(reference["final_membrane"][0][0], 1)
        choice = model.choose([1, 0], digest("masked"), ["second"])
        result = verify_decision(
            model.configuration, self.weights(model), [1, 0], choice
        )
        self.assertEqual(result["legal_greedy_arm"], "second")
        tied = self.model()
        tied.V[:] = 0
        tied.C[:] = 0
        choice = tied.choose([0, 0], digest("tie"), list(tied.arms))
        self.assertEqual(choice["arm"], "first")
        verify_decision(tied.configuration, self.weights(tied), [0, 0], choice)

    def test_changed_counts_values_source_model_and_features_are_rejected(self):
        model = self.hard_model()
        artifact = self.artifact(model, [[1, 0]])
        for mutate in (
            lambda a: a["forward"]["spike_counts"][0].__setitem__(0, 3),
            lambda a: a["forward"]["values"][0].__setitem__(0, 0.0),
            lambda a: a["decisions"][0].update(source_id="other:source"),
            lambda a: a["decisions"][0].update(features_sha256="0" * 64),
            lambda a: a["decisions"][0].update(model_sha256="0" * 64),
            lambda a: a["decisions"][0].update(arm="second"),
        ):
            bad = copy.deepcopy(artifact)
            mutate(bad)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                verify_artifact(bad, include_brian=False)

    def test_greedy_legal_masks_and_request_id_are_strict(self):
        model = self.hard_model()
        choice = model.choose([1, 0], digest("request"), ["first", "second"])
        for mutation in (
            {"legal_arms": ["second", "first"]},
            {"legal_arms": ["first", "first"]},
            {"legal_arms": ["unknown"]},
            {"ordinal": True},
            {"decision_id": "not-a-digest"},
            {"reason": "declared_exploration"},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_decision(
                    model.configuration, self.weights(model), [1, 0], choice | mutation
                )
        with self.assertRaises(ValueError):
            verify_decision(
                model.configuration,
                self.weights(model),
                [1, 0],
                choice,
                expected_decision_id=digest("different request"),
            )

    def test_numeric_shape_bounds_and_mixed_booleans_are_rejected(self):
        model = self.model()
        for features in (
            [[True, 0.0]],
            [[-0.01, 0]],
            [[float("nan"), 0]],
            [[0]],
            [[0, 1]] * 129,
        ):
            with (
                self.subTest(features=str(features)[:50]),
                self.assertRaises(ValueError),
            ):
                validated_features(model.configuration, features)
        weights = self.weights(model)
        weights["W"][0][0] = True
        with self.assertRaises(ValueError):
            independent_forward(model.configuration, weights, [[0, 1]])
        wide = self.model(feature_dimension=512, width=1)
        self.assertEqual(
            len(
                independent_forward(
                    wide.configuration, self.weights(wide), [[0] * 512]
                )["values"]
            ),
            1,
        )

    def test_trained_weights_are_not_falsely_required_to_match_initial_seed(self):
        model = self.model(updates=1)
        choice = model.choose([1, 0.25], digest("learn"), list(model.arms))
        model.learn(
            {
                "seq": 1,
                "decision_id": choice["decision_id"],
                "arm": choice["arm"],
                "receipt_id": digest("buyer"),
                "reward": 0.9,
            }
        )
        with self.assertRaises(ValueError):
            verify_initialization(model.configuration, self.weights(model))
        artifact = self.artifact(model, [[0.1, 0.9]])
        self.assertTrue(verify_artifact(artifact, include_brian=False)["verified"])

    def test_continuous_control_is_checked_without_claiming_spikes(self):
        model = self.model(mechanism="continuous")
        artifact = self.artifact(model, [[1, 0.5]])
        report = verify_artifact(artifact)
        self.assertEqual(report["total_hidden_spikes"], 0)
        self.assertNotIn("brian_parity", report)
        with self.assertRaises(ValueError):
            brian_parity(model.configuration, self.weights(model), [[1, 0.5]])

    def test_forward_requires_integer_counts_and_complete_readout(self):
        model = self.model()
        reference = independent_forward(
            model.configuration, self.weights(model), [[0, 1]]
        )
        with self.assertRaises(ValueError):
            verify_forward(
                model.configuration,
                self.weights(model),
                [[0, 1]],
                {"values": reference["values"]},
            )
        reference["spike_counts"][0][0] = 0.0
        with self.assertRaises(ValueError):
            verify_forward(
                model.configuration, self.weights(model), [[0, 1]], reference
            )

    @unittest.skipUnless(importlib.util.find_spec("brian2"), "Brian2 is unavailable")
    def test_actual_brian2_agrees_on_bounded_variable_architecture(self):
        model = self.model(feature_dimension=7, width=5, ticks=11)
        features = np.random.default_rng(71).random((3, 7)).tolist()
        report = brian_parity(model.configuration, self.weights(model), features)
        self.assertTrue(report["matched_spike_counts"])
        self.assertTrue(report["matched_tanh_values"])
        self.assertEqual(report["rows"], 3)
        with self.assertRaises(ValueError):
            brian_parity(model.configuration, self.weights(model), features * 3)


if __name__ == "__main__":
    unittest.main()
