"""Adversarial checks for independent measured-work result verification."""

import copy
import unittest

from scripts.verify_intervention_memory import (
    METHODS,
    IndependentPolicy,
    canonical,
    digest,
    equal,
    neural_features,
    reconstructed_summary,
    selected_work,
)


class InterventionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.query = {"family": 0, "a": 1, "b": 2}
        self.kernel = {
            "table": {
                canonical(self.query): [
                    {
                        "index_build_steps": arm * 10,
                        "query_steps": 100 - arm * 10,
                        "results": [[42, 3]],
                        "index_bytes": arm * 4096,
                    }
                    for arm in range(5)
                ]
            }
        }
        self.protocol = {
            "discount": 0.98,
            "alpha": 0.75,
            "reward_scale_vm_steps": 2000000,
        }

    def initial(self):
        context = [0.0] * 7
        uncertainty = 1.0
        intent = {
            "method": "intervention-memory-v1",
            "source_id": "sqlite:changing:4001",
            "step": 0,
            "arm": 0,
            "context": context,
            "scores": [
                {
                    "arm": arm,
                    "expected_utility": 0.0,
                    "uncertainty": uncertainty,
                    "score": 0.75,
                }
                for arm in range(4)
            ],
            "completed_before": 0,
            "selection": "initial_coverage",
            "covered_arms_before": [],
        }
        intent["id"] = digest(intent)
        row = {
            "task": "changing",
            "seed": 4001,
            "step": 0,
            "arm": 0,
            "decision": intent,
            "intent_sha256": intent["id"],
            "completed_before": 0,
            "delay": 3,
            "total_steps": 100,
        }
        return row, context

    def test_construction_is_charged_once_per_block(self):
        work = selected_work(self.kernel, 2, [self.query] * 3)
        self.assertEqual(work["index_build_steps"], 20)
        self.assertEqual(work["query_steps"], 240)
        self.assertEqual(work["total_steps"], 260)
        self.assertEqual(work["results_sha256"], digest([[42, 3]] * 3))

    def test_query_outside_measured_kernel_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unregistered selected query"):
            selected_work(self.kernel, 0, [{"family": 0, "a": 2, "b": 2}])

    def test_inconsistent_index_cost_is_refused(self):
        other = {"family": 0, "a": 2, "b": 2}
        self.kernel["table"][canonical(other)] = copy.deepcopy(
            self.kernel["table"][canonical(self.query)]
        )
        self.kernel["table"][canonical(other)][2]["index_build_steps"] = 21
        with self.assertRaisesRegex(ValueError, "construction cost"):
            selected_work(self.kernel, 2, [self.query, other])

    def test_spike_features_are_derived_from_counts_not_trusted(self):
        counts = [0] * 1024
        counts[:16] = [4] * 16
        self.assertEqual(neural_features(counts), [0.25] + [0.0] * 15)
        counts[:64] = [100] * 64
        self.assertEqual(neural_features(counts)[0], 1.0)
        with self.assertRaises(ValueError):
            neural_features([0] * 1023)
        with self.assertRaises(ValueError):
            neural_features([-1] * 1024)

    def test_original_coverage_decision_verifies(self):
        row, context = self.initial()
        policy = IndependentPolicy("raw_origin", self.protocol)
        policy.check_decision(row, context)
        self.assertEqual(policy.clock, 0)
        policy.arrive(2, context)
        self.assertEqual(policy.clock, 0)
        policy.arrive(3, context)
        self.assertEqual(policy.clock, 1)
        self.assertEqual(policy.models[0][2], 1)

    def test_changed_committed_decision_is_refused(self):
        row, context = self.initial()
        row["decision"]["arm"] = 1
        with self.assertRaisesRegex(ValueError, "commitment"):
            IndependentPolicy("raw_origin", self.protocol).check_decision(row, context)

    def test_rehashed_invented_score_is_refused(self):
        row, context = self.initial()
        row["decision"]["scores"][0]["score"] = 8.0
        row["decision"]["id"] = digest(
            {k: v for k, v in row["decision"].items() if k != "id"}
        )
        row["intent_sha256"] = row["decision"]["id"]
        with self.assertRaisesRegex(ValueError, "UCB scores"):
            IndependentPolicy("raw_origin", self.protocol).check_decision(row, context)

    def test_future_feedback_is_refused_even_with_rehashed_intent(self):
        row, context = self.initial()
        row["decision"]["completed_before"] = row["completed_before"] = 1
        row["decision"]["id"] = digest(
            {k: v for k, v in row["decision"].items() if k != "id"}
        )
        with self.assertRaisesRegex(ValueError, "Future or missing feedback"):
            IndependentPolicy("raw_origin", self.protocol).check_decision(row, context)

    def test_origin_and_delivery_context_are_distinct(self):
        row, original = self.initial()
        policy = IndependentPolicy("raw_origin", self.protocol)
        policy.check_decision(row, original)
        policy.arrive(3, [1.0] * 7)
        self.assertEqual(policy.models[0][0][1, 1], 1.0)
        self.assertGreater(policy.models[0][0][0, 0], 1.0)

    def test_paired_scores_and_tampered_summary(self):
        protocol = {
            "tasks": ["changing"],
            "development_seeds": [1, 2, 3],
            "critical_95": 2.0,
        }
        trials = [
            {
                "task": "changing",
                "method": method,
                "seed": seed,
                "total_vm_steps": 100 if method == "neural_origin" else 200,
            }
            for method in METHODS
            for seed in [1, 2, 3]
        ]
        summary = reconstructed_summary(trials, protocol, "development")
        contrast = summary["changing"]["contrasts"]["neural_origin__raw_origin"]
        self.assertEqual(contrast["relative_reduction"], 0.5)
        self.assertEqual(contrast["lower_95"], 0.5)
        tampered = copy.deepcopy(summary)
        tampered["changing"]["means"]["neural_origin"] = 1.0
        with self.assertRaisesRegex(ValueError, "differs"):
            equal(tampered, summary)


if __name__ == "__main__":
    unittest.main()
