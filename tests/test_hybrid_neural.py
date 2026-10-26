import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cognition.hybrid_neural import HybridNeuralBridge, select_strategy, tool_features
from cognition.procedural_memory import digest


class HybridNeuralTests(unittest.TestCase):
    def directory(self):
        # Retained private fixture histories also permit inspection after tests.
        return Path(tempfile.mkdtemp(prefix="nuria-hybrid-neural-test-"))

    def open(self, directory, **changes):
        return HybridNeuralBridge(
            directory,
            source_id=changes.pop("source_id", "public-fixture-data@" + "a" * 40),
            model_id="fixture-reasoner@" + "b" * 40 + ":q4",
            tool_version="select-only-fixture-v1",
            **changes,
        )

    def choose(self, bridge, job="job-1"):
        return bridge.choose(
            job, digest("actual-schema-fixture"), "How much revenue was reported?", {}
        )

    def test_measured_spikes_can_change_the_enforced_strategy(self):
        utilities = {"list_tables": 0.5, "read_knowledge": 0.5}
        tables = select_strategy(
            utilities, {"list_tables": 9, "read_knowledge": 0}, "neural"
        )
        knowledge = select_strategy(
            utilities, {"list_tables": 0, "read_knowledge": 9}, "neural"
        )
        self.assertEqual(tables["first_tool"], "list_tables")
        self.assertEqual(knowledge["first_tool"], "read_knowledge")
        fixed = select_strategy(
            utilities, {"list_tables": 0, "read_knowledge": 9}, "fixed"
        )
        self.assertEqual(fixed["first_tool"], "list_tables")
        self.assertEqual(fixed["neural_selection_weight"], 0)
        self.assertEqual(fixed["scores"]["list_tables"], 1)
        with self.assertRaises(ValueError):
            select_strategy(
                utilities, {"list_tables": -1, "read_knowledge": 0}, "neural"
            )

    def test_unknown_cost_observations_stay_unknown(self):
        rows = {
            "read_knowledge": {
                "tool_name": "read_knowledge",
                "observations": 1,
                "tool_successes": 0,
                "tool_errors": 1,
                "total_cost_units": None,
                "cost_known_count": 0,
                "cost_unknown_count": 1,
                "known_cost_units": 0,
                "mean_cost_units": None,
            }
        }
        normalized = tool_features(rows)
        self.assertTrue(normalized["read_knowledge"]["cost_unknown"])
        self.assertIsNone(normalized["read_knowledge"]["mean_cost_units"])
        self.assertEqual(normalized["read_knowledge"]["unknown_cost_count"], 1)
        with self.assertRaises(ValueError):
            tool_features({"future_answer": 1})
        with self.assertRaises(ValueError):
            tool_features(
                {
                    "list_tables": {
                        "observations": 1,
                        "tool_successes": 1,
                        "tool_errors": 1,
                    }
                }
            )

    def test_actual_brian_state_and_origin_credit_resume_exactly(self):
        reference = self.open(self.directory())
        first = self.choose(reference)
        credited = reference.settle("job-1", True, digest("checker-receipt-1"))
        after_first = np.asarray(reference.brain.exc.w[:]).copy()
        second = self.choose(reference, "job-2")
        reference.settle("job-2", False, digest("checker-receipt-2"))
        final_weights = np.asarray(reference.brain.exc.w[:]).copy()
        final_state = reference.brain.state_hash()
        reference.close()

        directory = self.directory()
        interrupted = self.open(directory)
        repeated_first = self.choose(interrupted)
        self.assertEqual(first["spikes"], repeated_first["spikes"])
        self.assertEqual(first["action_spikes"], repeated_first["action_spikes"])
        pending_hash = interrupted.brain.state_hash()
        pending_weights = np.asarray(interrupted.brain.exc.w[:]).copy()
        interrupted.close()

        resumed = self.open(directory)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.brain.state_hash(), pending_hash)
        np.testing.assert_array_equal(
            np.asarray(resumed.brain.exc.w[:]), pending_weights
        )
        actual = resumed.settle("job-1", True, digest("checker-receipt-1"))
        self.assertEqual(actual["credit"], credited["credit"])
        self.assertGreater(actual["credit"]["changed_synapses"], 0)
        np.testing.assert_array_equal(np.asarray(resumed.brain.exc.w[:]), after_first)
        saved_head = dict(resumed.head)
        self.assertEqual(
            resumed.settle("job-1", True, digest("checker-receipt-1")), actual
        )
        self.assertEqual(saved_head, resumed.head)
        resumed_second = self.choose(resumed, "job-2")
        self.assertEqual(resumed_second["spikes"], second["spikes"])
        resumed.settle("job-2", False, digest("checker-receipt-2"))
        self.assertEqual(resumed.brain.state_hash(), final_state)
        np.testing.assert_array_equal(np.asarray(resumed.brain.exc.w[:]), final_weights)
        with self.assertRaises(ValueError):
            resumed.settle("job-1", False, digest("checker-receipt-1"))

    def test_capsules_are_immutable_and_closed_or_changed_history_is_refused(self):
        directory = self.directory()
        bridge = self.open(directory)
        self.addCleanup(bridge.close)
        original = {
            str(path.relative_to(directory)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in (directory / "capsules").rglob("*")
            if path.is_file()
        }
        decision = self.choose(bridge)
        self.assertTrue(decision["spikes"])
        self.assertEqual(decision["window_ms"], 100)
        self.assertNotIn("side", decision)
        self.assertNotIn("quote_amount", decision)
        for relative, expected in original.items():
            self.assertEqual(
                hashlib.sha256((directory / relative).read_bytes()).hexdigest(),
                expected,
            )
        before = dict(bridge.head)
        self.assertEqual(self.choose(bridge), decision)
        self.assertEqual(before, bridge.head)
        saved_source = bridge.terms["source_id"]
        bridge.terms["source_id"] = "changed"
        with self.assertRaises(ValueError):
            self.choose(bridge)
        bridge.terms["source_id"] = saved_source
        with self.assertRaises(ValueError):
            bridge.choose(
                "job-1", digest("changed-schema"), "How much revenue was reported?", {}
            )
        with self.assertRaises(BlockingIOError):
            self.open(directory)
        bridge.close()
        with self.assertRaises(RuntimeError):
            self.choose(bridge, "after-close")
        with self.assertRaises(ValueError):
            self.open(directory, source_id="different-source")
        head = json.loads((directory / "HEAD.json").read_bytes())
        path = directory / "capsules" / head["capsule"] / "network.pkl"
        with path.open("ab") as stream:
            stream.write(b"corruption")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.open(directory)

    def test_failed_checkpoint_requires_reopen_and_preserves_previous_head(self):
        directory = self.directory()
        bridge = self.open(directory)
        head = (directory / "HEAD.json").read_bytes()
        with patch.object(
            bridge, "_commit", side_effect=OSError("Injected persistence failure")
        ):
            with self.assertRaises(OSError):
                self.choose(bridge)
        failed_decision = bridge.state["jobs"]["job-1"]["decision"]
        self.assertEqual((directory / "HEAD.json").read_bytes(), head)
        with self.assertRaises(RuntimeError):
            self.choose(bridge)
        bridge.close()
        restored = self.open(directory)
        self.addCleanup(restored.close)
        self.assertEqual(restored.state["jobs"], {})
        recovered_decision = self.choose(restored)
        self.assertEqual(
            recovered_decision["first_tool"], failed_decision["first_tool"]
        )
        self.assertEqual(recovered_decision["spikes"], failed_decision["spikes"])

    def test_late_feedback_credits_the_saved_origin_not_current_eligibility(self):
        bridge = self.open(self.directory())
        self.addCleanup(bridge.close)
        self.choose(bridge, "early-job")
        origin = bridge.state["jobs"]["early-job"]["eligibility"]
        self.assertTrue(origin["indices"])
        self.choose(bridge, "later-job")
        before = np.asarray(bridge.brain.exc.w[:]).copy()
        expected = before.copy()
        indices = origin["indices"]
        expected[indices] = np.clip(
            before[indices] + 0.002 * np.asarray(origin["eligibility"]), 0.002, 0.18
        )
        receipt = bridge.settle("early-job", True, digest("verified-early-job"))
        np.testing.assert_array_equal(np.asarray(bridge.brain.exc.w[:]), expected)
        self.assertEqual(receipt["credit"]["tagged_synapses"], len(indices))
        self.assertIsNone(bridge.state["jobs"]["later-job"]["settlement"])

    def test_ablation_does_not_change_the_encoded_job_or_initial_spikes(self):
        neural = self.open(self.directory(), mode="neural")
        neural_choice = self.choose(neural)
        neural.close()
        fixed = self.open(self.directory(), mode="fixed")
        self.addCleanup(fixed.close)
        fixed_choice = self.choose(fixed)
        self.assertEqual(neural_choice["sensory_drive"], fixed_choice["sensory_drive"])
        self.assertEqual(
            neural_choice["memory_features"], fixed_choice["memory_features"]
        )
        self.assertEqual(neural_choice["utilities"], fixed_choice["utilities"])
        self.assertEqual(neural_choice["spikes"], fixed_choice["spikes"])
        self.assertEqual(fixed_choice["first_tool"], "list_tables")


if __name__ == "__main__":
    unittest.main()
