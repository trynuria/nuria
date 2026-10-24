"""Only disclosed observations and actual neural features enter the policy."""

import copy
import json
import unittest

from cognition.routed_text_search import RoutedTextLibrary
from cognition.routing_features import DIMENSION, OBSERVABLES, encode
from cognition.spiking_policy import digest
from scripts.build_guarded_skill_tasks import CONFIG


class RoutingFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.library = RoutedTextLibrary("test:features", CONFIG).dump()
        self.audits = [
            {
                "input": json.dumps(["  Ada  ", "other"]),
                "output": "Ada",
                "receipt_sha256": digest("audit0"),
            },
            {
                "input": json.dumps(["  Grace  ", "different"]),
                "output": "Grace",
                "receipt_sha256": digest("audit1"),
            },
        ]
        self.caps = {"default": 50000, "slice_first": 50000}

    def run_encoder(self, audits=None, brain=None):
        return encode(
            self.audits if audits is None else audits,
            self.library,
            CONFIG,
            [0.0] * 64 if brain is None else brain,
            self.caps,
        )

    def test_observables_use_all_disclosed_rows_and_reserve_no_fake_neural_data(self):
        features = self.run_encoder(brain=[i / 63 for i in range(64)])
        self.assertEqual(len(features), DIMENSION)
        self.assertEqual(features[OBSERVABLES.index("strip_max")], 1.0)
        self.assertEqual(features[OBSERVABLES.index("exact_max")], 0.0)
        self.assertEqual(
            features[len(OBSERVABLES) : 64], [0.0] * (64 - len(OBSERVABLES))
        )
        self.assertEqual(features[64:], [i / 63 for i in range(64)])
        self.assertTrue(all(0 <= x <= 1 for x in features))

    def test_fewer_actual_audits_use_actual_denominator(self):
        result = self.run_encoder(self.audits[:1])
        self.assertEqual(result[OBSERVABLES.index("distinct_output_fraction")], 1.0)
        self.assertEqual(result[OBSERVABLES.index("strip_max")], 1.0)

    def test_final_rows_or_other_unreviewed_keys_are_rejected(self):
        audits = copy.deepcopy(self.audits)
        audits[0]["score_outputs"] = ["hidden"]
        with self.assertRaises(ValueError):
            self.run_encoder(audits)
        with self.assertRaises(ValueError):
            self.run_encoder([self.audits[0], self.audits[0]])

    def test_invalid_or_fabricated_neural_vector_is_rejected(self):
        for brain in (
            [0.0] * 17,
            [0.0] * 63 + [float("nan")],
            [True] * 64,
            [-1.0] * 64,
        ):
            with self.assertRaises(ValueError):
                self.run_encoder(brain=brain)

    def test_recorded_inputs_neural_state_and_library_are_not_mutated(self):
        before = copy.deepcopy((self.audits, self.library, self.caps))
        self.run_encoder()
        self.assertEqual(before, (self.audits, self.library, self.caps))


if __name__ == "__main__":
    unittest.main()
