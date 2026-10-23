import copy
import json
import math
import unittest

import numpy as np

from cognition.intervention_memory import (
    MAX_PENDING,
    MAX_RECEIPTS,
    InterventionMemory,
)


def receipt(intent, sequence, *, cost=25, reference=100):
    return {
        "intent_id": intent["id"],
        "source_id": intent["source_id"],
        "arm": intent["arm"],
        "cost": cost,
        "reference_cost": reference,
        "artifact_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "verified": True,
        "sequence": sequence,
    }


class InterventionMemoryTests(unittest.TestCase):
    def test_explicit_noop_and_same_step_idempotence(self):
        memory = InterventionMemory("source-a", 2)
        selected = memory.choose([0.5, -0.5], 0)
        self.assertEqual(selected["arm"], 0)
        self.assertEqual([s["arm"] for s in selected["scores"]], [0, 1, 2, 3])
        self.assertEqual(selected, memory.choose([0.5, -0.5], 0))
        self.assertEqual(len(memory.pending), 1)
        with self.assertRaises(ValueError):
            memory.choose([0.5, 0.0], 0)
        memory.choose([0.0, 0.0], 2)
        with self.assertRaises(ValueError):
            memory.choose([0.5, -0.5], 1)

    def test_delayed_learning_uses_origin_not_latest_context(self):
        memory = InterventionMemory("source-a", 2)
        first = memory.choose([1.0, 0.0], 0)
        memory.choose([0.0, 1.0], 1)
        self.assertTrue(memory.learn(receipt(first, 1)))
        model = memory.models[str(first["arm"])]
        np.testing.assert_array_equal(model["b"], [0.75, 0.75, 0])
        np.testing.assert_array_equal(
            model["a"], np.eye(3) + np.outer([1, 1, 0], [1, 1, 0])
        )
        for other in set(range(4)) - {first["arm"]}:
            np.testing.assert_array_equal(memory.models[str(other)]["a"], np.eye(3))
            self.assertEqual(memory.models[str(other)]["n"], 0)

    def test_delivery_order_can_differ_from_decision_order(self):
        memory = InterventionMemory("source-a", 1)
        first = memory.choose([0.2], 1)
        second = memory.choose([0.8], 2)
        memory.learn(receipt(second, 1))
        memory.learn(receipt(first, 2))
        self.assertEqual(memory.completed, 2)
        self.assertFalse(memory.pending)
        self.assertEqual(memory.feedback_cursor, 2)

    def test_no_utility_evidence_explores_unobserved_arms(self):
        memory = InterventionMemory("source-a", 1)
        arms = []
        for step in range(4):
            chosen = memory.choose([0], step)
            arms.append(chosen["arm"])
            memory.learn(receipt(chosen, step + 1, cost=100))
        self.assertEqual(arms, [0, 1, 2, 3])
        self.assertEqual(set(memory.capabilities), {"0", "1", "2", "3"})

    def test_initial_coverage_counts_pending_delayed_observations(self):
        memory = InterventionMemory("source-a", 1)
        intents = [memory.choose([0.5], step) for step in range(5)]
        self.assertEqual([intent["arm"] for intent in intents[:4]], [0, 1, 2, 3])
        self.assertEqual(
            [intent["selection"] for intent in intents[:4]],
            ["initial_coverage"] * 4,
        )
        self.assertEqual(intents[4]["selection"], "ridge_ucb")
        self.assertEqual(memory.completed, 0)
        restored = InterventionMemory("source-a", 1, memory.state())
        self.assertEqual(restored.state(), memory.state())

    def test_positive_first_outcome_does_not_prevent_initial_coverage(self):
        memory = InterventionMemory("source-a", 1)
        arms = []
        for step in range(4):
            selected = memory.choose([0.5], step)
            arms.append(selected["arm"])
            memory.learn(receipt(selected, step + 1, cost=0))
        self.assertEqual(arms, [0, 1, 2, 3])

    def test_duplicate_exact_and_changed_receipt(self):
        memory = InterventionMemory("source-a", 1)
        chosen = memory.choose([0.5], 0)
        delivered = receipt(chosen, 3)
        self.assertTrue(memory.learn(delivered))
        state = memory.state()
        self.assertFalse(memory.learn(copy.deepcopy(delivered)))
        self.assertEqual(memory.state(), state)
        for field, value in (
            ("cost", 26),
            ("sequence", 4),
            ("result_sha256", "c" * 64),
        ):
            changed = {**delivered, field: value}
            with self.assertRaises(ValueError):
                memory.learn(changed)
            self.assertEqual(memory.state(), state)

    def test_receipt_sequence_cannot_be_reassigned(self):
        memory = InterventionMemory("source-a", 1)
        first = memory.choose([0.1], 0)
        second = memory.choose([0.2], 1)
        memory.learn(receipt(first, 2))
        state = memory.state()
        with self.assertRaises(ValueError):
            memory.learn(receipt(second, 2))
        self.assertEqual(memory.state(), state)

    def test_source_and_origin_are_mandatory(self):
        a, b = InterventionMemory("a", 1), InterventionMemory("b", 1)
        chosen = a.choose([0.4], 0)
        for invalid in (
            receipt(chosen, 1),
            {**receipt(chosen, 1), "source_id": "b"},
        ):
            with self.assertRaises(ValueError):
                b.learn(invalid)
        state = a.state()
        for field, value in (("arm", 1), ("verified", False), ("intent_id", "f" * 64)):
            with self.assertRaises(ValueError):
                a.learn({**receipt(chosen, 1), field: value})
            self.assertEqual(a.state(), state)
        with self.assertRaises(ValueError):
            InterventionMemory("b", 1, state)

    def test_utility_and_cost_validation(self):
        self.assertEqual(InterventionMemory.utility(0, 100), 1)
        self.assertEqual(InterventionMemory.utility(100, 100), 0)
        self.assertEqual(InterventionMemory.utility(300, 100), -1)
        self.assertEqual(InterventionMemory.utility(50, 100), 0.5)
        memory = InterventionMemory("a", 1)
        chosen = memory.choose([0.2], 0)
        state = memory.state()
        for field, bad in (
            ("cost", -1),
            ("cost", math.inf),
            ("cost", math.nan),
            ("cost", True),
            ("reference_cost", 0),
            ("reference_cost", math.inf),
            ("artifact_sha256", "not-a-hash"),
            ("sequence", True),
            ("arm", True),
        ):
            with self.assertRaises(ValueError):
                memory.learn({**receipt(chosen, 1), field: bad})
            self.assertEqual(memory.state(), state)

    def test_context_and_terms_validation(self):
        for kwargs in (
            {"dimension": 0},
            {"dimension": True},
            {"dimension": 65},
            {"source_id": ""},
            {"discount": 0},
            {"discount": math.nan},
            {"alpha": -1},
            {"alpha": math.inf},
        ):
            args = {"source_id": "a", "dimension": 2, **kwargs}
            with self.assertRaises(ValueError):
                InterventionMemory(**args)
        memory = InterventionMemory("a", 2)
        for context in ([1], [math.nan, 0], [math.inf, 0], [2, 0], [True, 0]):
            with self.assertRaises(ValueError):
                memory.choose(context, 0)
        self.assertEqual(memory.last_step, -1)

    def test_recovery_is_exact_with_pending_and_capabilities(self):
        memory = InterventionMemory("source-a", 2)
        first = memory.choose([1.0, 0.0], 0)
        delayed = memory.choose([0.0, 1.0], 1)
        memory.learn(receipt(first, 1))
        recovered = InterventionMemory(
            "source-a", 2, json.loads(json.dumps(memory.state()))
        )
        self.assertEqual(recovered.state(), memory.state())
        self.assertEqual(
            recovered.choose([-0.5, 0.2], 2), memory.choose([-0.5, 0.2], 2)
        )
        memory.learn(receipt(delayed, 2, cost=150))
        recovered.learn(receipt(delayed, 2, cost=150))
        self.assertEqual(recovered.state(), memory.state())
        capability = recovered.capabilities[str(delayed["arm"])]
        self.assertEqual(capability["origin_intent"], delayed["id"])
        self.assertEqual(capability["observed_cost"], 150)
        capability["observed_cost"] = 0
        self.assertEqual(
            recovered.capabilities[str(delayed["arm"])]["observed_cost"], 150
        )

    def test_discount_keeps_identity_ridge_and_changes_old_evidence(self):
        memory = InterventionMemory("a", 1, discount=0.5, alpha=0)
        first = memory.choose([1.0], 0)
        for step in range(1, 4):
            memory.choose([1.0], step)
        memory.learn(receipt(first, 1, cost=0))
        second = memory.choose([1.0], 4)
        self.assertEqual(second["arm"], first["arm"])
        memory.learn(receipt(second, 2, cost=100))
        model = memory.models[str(first["arm"])]
        np.testing.assert_array_equal(model["a"], np.eye(2) + 1.5 * np.ones((2, 2)))
        np.testing.assert_array_equal(model["b"], [0.5, 0.5])
        with self.assertRaises(ValueError):
            InterventionMemory("a", 1, memory.state(), discount=0.98, alpha=0)

    def test_malformed_checkpoint_is_refused(self):
        memory = InterventionMemory("a", 1)
        chosen = memory.choose([0.5], 0)
        memory.learn(receipt(chosen, 1))
        state = memory.state()
        mutations = [
            lambda s: s.update(discount=math.nan),
            lambda s: s.update(completed=2),
            lambda s: s["models"]["0"]["a"][0].__setitem__(0, 0),
            lambda s: s["models"]["0"]["b"].__setitem__(0, math.nan),
            lambda s: s["models"]["0"].update(updated=10),
            lambda s: s["last_intent"]["context"].__setitem__(0, 0.7),
            lambda s: s["capabilities"]["0"].update(utility=1),
            lambda s: s["receipts"][chosen["id"]].update(sequence=20),
        ]
        for mutate in mutations:
            bad = copy.deepcopy(state)
            mutate(bad)
            with self.assertRaises(ValueError):
                InterventionMemory("a", 1, bad)

    def test_pending_bound_and_state_copy_isolation(self):
        memory = InterventionMemory("a", 1)
        for step in range(MAX_PENDING):
            memory.choose([0.0], step)
        with self.assertRaises(ValueError):
            memory.choose([0.0], MAX_PENDING)
        state = memory.state()
        state["pending"].clear()
        self.assertEqual(len(memory.pending), MAX_PENDING)
        restored = InterventionMemory("a", 1, memory.state())
        self.assertEqual(len(restored.pending), MAX_PENDING)

    def test_bounded_receipt_window_refuses_unverifiable_old_duplicates(self):
        memory = InterventionMemory("a", 1)
        oldest = None
        for step in range(MAX_RECEIPTS + 2):
            selected = memory.choose([0.0], step)
            delivered = receipt(selected, step + 1)
            oldest = oldest or delivered
            memory.learn(delivered)
        self.assertEqual(len(memory.receipts), MAX_RECEIPTS)
        self.assertLessEqual(len(memory.capabilities), 4)
        self.assertGreater(len(memory.capabilities), 0)
        self.assertFalse(memory.learn(delivered))
        with self.assertRaises(ValueError):
            memory.learn(oldest)
        restored = InterventionMemory("a", 1, memory.state())
        self.assertEqual(restored.state(), memory.state())

    def test_source_cannot_receive_another_sources_learned_state(self):
        a, b = InterventionMemory("a", 1), InterventionMemory("b", 1)
        choice_a, choice_b = a.choose([0.5], 0), b.choose([0.5], 0)
        self.assertNotEqual(choice_a["id"], choice_b["id"])
        a.learn(receipt(choice_a, 1))
        self.assertEqual(b.completed, 0)
        np.testing.assert_array_equal(b.models["0"]["b"], [0, 0])


if __name__ == "__main__":
    unittest.main()
