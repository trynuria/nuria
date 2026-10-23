"""Adversarial checks for independent measured-work result verification."""

import copy
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.verify_intervention_memory import (
    METHODS,
    IndependentPolicy,
    canonical,
    committed_source,
    digest,
    equal,
    neural_features,
    reconstructed_summary,
    selected_work,
    verify,
)


class MemoryFile:
    def __init__(self, value):
        self.value = value if isinstance(value, bytes) else json.dumps(value).encode()

    def read_text(self):
        return self.value.decode()

    def read_bytes(self):
        return self.value


class MemoryDirectory:
    def __init__(self, files):
        self.files = files

    def __truediv__(self, name):
        return self.files[name]


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

    def simultaneous_coverage(self, policy):
        """Two original decisions settle together, with reverse legacy hashes."""
        context = [0.0] * (policy.width - 1)
        for step in range(2):
            row, _ = self.initial()
            intent = row["decision"]
            row["step"] = intent["step"] = step
            row["arm"] = intent["arm"] = step
            intent["context"] = context
            intent["covered_arms_before"] = list(range(step))
            intent["id"] = digest({k: v for k, v in intent.items() if k != "id"})
            row["intent_sha256"] = intent["id"]
            row["delay"] = 4 - step
            policy.check_decision(row, context)
        return context

    def test_v2_simultaneous_feedback_order_is_method_independent(self):
        protocol = {**self.protocol, "feedback_order": "due_step,origin_step"}
        for method in (
            "raw_origin",
            "raw_receipt_time",
            "neural_origin",
            "neural_receipt_time",
        ):
            with self.subTest(method=method):
                policy = IndependentPolicy(method, protocol)
                context = self.simultaneous_coverage(policy)
                self.assertEqual([item[1] for item in policy.pending], [0, 1])
                policy.arrive(4, context)
                self.assertEqual(policy.models[0][3], 1)
                self.assertEqual(policy.models[1][3], 2)

    def test_v1_simultaneous_feedback_keeps_intent_hash_order(self):
        policy = IndependentPolicy("raw_origin", self.protocol)
        context = self.simultaneous_coverage(policy)
        self.assertGreater(policy.pending[0][1], policy.pending[1][1])
        policy.arrive(4, context)
        self.assertEqual(policy.models[1][3], 1)
        self.assertEqual(policy.models[0][3], 2)

    def test_unregistered_feedback_order_is_refused(self):
        with self.assertRaisesRegex(ValueError, "feedback order"):
            IndependentPolicy("raw_origin", {**self.protocol, "feedback_order": "x"})

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

    def evidence_header(self):
        protocol = {"data": {"csv_sha256": "1" * 64}}
        artifacts = {
            name: MemoryFile(b"original retained bytes")
            for name in ("kernel.json", "traces.jsonl.gz", "batch-audits.json")
        }
        result = {
            "phase": "development",
            "protocol_sha256": digest(protocol),
            "dataset_sha256": "1" * 64,
            "methods": list(METHODS),
            "financial_transactions": 0,
            "all_answers_equal": True,
            "artifacts": {
                name: hashlib.sha256(file.read_bytes()).hexdigest()
                for name, file in artifacts.items()
            },
        }
        return protocol, result, artifacts

    def test_changed_trace_bytes_are_refused_before_parsing(self):
        protocol, result, files = self.evidence_header()
        files["traces.jsonl.gz"] = MemoryFile(b"changed retained trace")
        files["result.json"] = MemoryFile(result)
        with self.assertRaisesRegex(
            ValueError, "Evidence artifact changed: traces.jsonl.gz"
        ):
            verify(MemoryDirectory(files), MemoryFile(protocol))

    def test_removed_branch_is_refused_before_scoring(self):
        protocol, result, files = self.evidence_header()
        result["methods"].remove("raw_origin")
        files["result.json"] = MemoryFile(result)
        with self.assertRaisesRegex(ValueError, "registered method"):
            verify(MemoryDirectory(files), MemoryFile(protocol))

    def test_historical_source_uses_explicit_immutable_revision(self):
        revision, name = "a" * 40, "cognition/intervention_memory.py"
        with patch(
            "scripts.verify_intervention_memory.subprocess.run",
            return_value=SimpleNamespace(stdout=b"historical source"),
        ) as run:
            self.assertEqual(committed_source(name, revision), b"historical source")
        self.assertEqual(run.call_args.args[0], ["git", "show", f"{revision}:{name}"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_invalid_source_revision_and_paths_are_refused(self):
        with patch("scripts.verify_intervention_memory.subprocess.run") as run:
            for revision in ("HEAD", "a" * 39, "A" * 40, "a" * 40 + "; echo x"):
                with self.subTest(revision=revision):
                    with self.assertRaisesRegex(ValueError, "full lowercase commit"):
                        verify(MemoryDirectory({}), source_ref=revision)
            for name in ("/etc/file", "../file", "./file", "cognition/../file", "a//b"):
                with self.subTest(name=name):
                    with self.assertRaisesRegex(ValueError, "source commitment path"):
                        committed_source(name, "a" * 40)
            run.assert_not_called()

    def test_historical_source_mismatch_is_refused_before_scoring(self):
        protocol, result, files = self.evidence_header()
        name = "cognition/intervention_memory.py"
        result["source_hashes"] = {
            name: hashlib.sha256(b"committed source").hexdigest()
        }
        files["result.json"] = MemoryFile(result)
        with patch(
            "scripts.verify_intervention_memory.subprocess.run",
            return_value=SimpleNamespace(stdout=b"different source"),
        ):
            with self.assertRaisesRegex(ValueError, "Evaluated source changed"):
                verify(MemoryDirectory(files), MemoryFile(protocol), "a" * 40)


if __name__ == "__main__":
    unittest.main()
