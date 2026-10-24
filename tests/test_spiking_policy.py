"""Selected outcomes, real spike control and exact pending-state recovery."""

import copy
import unittest
from unittest.mock import patch

import numpy as np

from cognition.spiking_policy import SpikingDecisionPolicy, digest


class SpikingPolicyTests(unittest.TestCase):
    def model(self, **overrides):
        config = dict(
            source_id="test:routing-v1",
            feature_dimension=2,
            arm_ids=["first", "second"],
            width=8,
            ticks=8,
            updates=2,
            batch_size=4,
            warmup=0,
            exploration=0,
        )
        config.update(overrides)
        return SpikingDecisionPolicy(**config)

    def decision(self, model, index=0, features=None, legal=None):
        return model.choose(
            features or [1.0, 0.25], digest(["job", index]), legal or list(model.arms)
        )

    def receipt(self, model, choice, reward=0.9, seq=None):
        return {
            "seq": model.feedback_cursor + 1 if seq is None else seq,
            "decision_id": choice["decision_id"],
            "arm": choice["arm"],
            "receipt_id": digest(["buyer", choice["decision_id"]]),
            "reward": reward,
        }

    def restore(self, model, **overrides):
        return SpikingDecisionPolicy(
            **(model.configuration | overrides), state=model.state()
        )

    def test_actual_hard_spikes_determine_greedy_choice(self):
        model = self.model(width=2, ticks=4)
        model.W[:] = [[2, -2], [-2, 2]]
        model.H[:] = 0
        model.V[:] = [[1, -1], [-1, 1]]
        model.C[:] = 0
        first = self.decision(model, features=[1.0, 0.0])
        second = self.decision(model, 1, features=[0.0, 1.0])
        self.assertEqual(first["spike_counts"], [4, 0])
        self.assertEqual(second["spike_counts"], [0, 4])
        self.assertEqual((first["arm"], second["arm"]), ("first", "second"))
        self.assertEqual(first["reason"], "learned_value")

    def test_checked_origin_trains_input_and_selected_readout(self):
        model = self.model(updates=1)
        choice = self.decision(model, legal=["first"])
        before_w, before_v = model.W.copy(), model.V.copy()
        result = model.learn(self.receipt(model, choice))
        self.assertFalse(np.array_equal(before_w, model.W))
        self.assertFalse(np.array_equal(before_v[:, 0], model.V[:, 0]))
        np.testing.assert_array_equal(before_v[:, 1], model.V[:, 1])
        self.assertEqual(result["origin_features_sha256"], choice["features_sha256"])
        self.assertNotEqual(result["before_model_sha256"], result["after_model_sha256"])

    def test_delayed_feedback_uses_its_origin_not_newest_context(self):
        model = self.model()
        first = self.decision(model, features=[1.0, 0.0])
        second = self.decision(model, 1, features=[0.0, 1.0])
        model.learn(self.receipt(model, first))
        self.assertEqual(model.replay[0]["features"], [1.0, 0.0])
        self.assertIn(second["decision_id"], model.pending)
        self.assertNotIn(first["decision_id"], model.pending)

    def test_unchanged_request_and_outcome_retries_have_no_effect(self):
        model = self.model(exploration=1)
        choice = self.decision(model)
        state = model.state_hash()
        self.assertEqual(choice, self.decision(model))
        self.assertEqual(state, model.state_hash())
        receipt = self.receipt(model, choice)
        learned = model.learn(receipt)
        state = model.state_hash()
        self.assertEqual(learned, model.learn(receipt))
        self.assertEqual(choice, self.decision(model))
        self.assertEqual(state, model.state_hash())
        with self.assertRaises(ValueError):
            self.decision(model, features=[0.0, 1.0])

    def test_restored_pending_rng_optimizer_and_replay_continue_exactly(self):
        original = self.model(exploration=0.5)
        first = self.decision(original)
        original.learn(self.receipt(original, first))
        second = self.decision(original, 1, features=[0.3, 1.0])
        restored = self.restore(original)
        receipt = self.receipt(original, second, reward=-0.1)
        self.assertEqual(original.learn(receipt), restored.learn(receipt))
        self.assertEqual(self.decision(original, 2), self.decision(restored, 2))
        self.assertEqual(original.state(), restored.state())

    def test_invalid_receipt_does_not_change_model_or_pending_state(self):
        model = self.model()
        choice = self.decision(model)
        valid = self.receipt(model, choice)
        for mutation in (
            {"seq": 2},
            {"arm": "missing"},
            {"reward": float("nan")},
            {"reward": True},
            {"decision_id": "0" * 64},
            {"verified": True},
        ):
            before = model.state_hash()
            with self.assertRaises(ValueError):
                model.learn(valid | mutation)
            self.assertEqual(before, model.state_hash())

    def test_failure_after_numerical_update_rolls_back_everything(self):
        model = self.model()
        choice = self.decision(model)
        before = model.state()
        original_update = model._update

        def interrupted(records):
            original_update(records)
            raise RuntimeError("interrupted update")

        with patch.object(model, "_update", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                model.learn(self.receipt(model, choice))
        self.assertEqual(before, model.state())

    def test_frozen_input_control_never_changes_input_weights_or_moments(self):
        model = self.model(frozen_input=True)
        first_w, first_h = model.W.copy(), model.H.copy()
        choice = self.decision(model)
        model.learn(self.receipt(model, choice))
        np.testing.assert_array_equal(first_w, model.W)
        np.testing.assert_array_equal(first_h, model.H)
        self.assertFalse(np.any(model.first["W"]))
        self.assertEqual(model.state(), self.restore(model).state())
        bad = model.state()
        bad["weights"]["W"][0][0] += 0.001
        with self.assertRaises(ValueError):
            SpikingDecisionPolicy(**model.configuration, state=bad)

    def test_legal_mask_is_authoritative_during_exploration(self):
        model = self.model(exploration=1)
        for i in range(10):
            choice = self.decision(model, i, legal=["second"])
            self.assertEqual(choice["arm"], "second")
            model.learn(self.receipt(model, choice))

    def test_configuration_receipts_replay_and_counters_are_checked(self):
        model = self.model()
        model.learn(self.receipt(model, self.decision(model)))
        with self.assertRaises(ValueError):
            self.restore(model, source_id="different:source")
        for mutate in (
            lambda s: s["replay"][0].update(reward=-1),
            lambda s: s.update(feedback_cursor=2),
            lambda s: s["optimizer"].update(step=1),
            lambda s: s["receipts"].clear(),
            lambda s: s["rng"].update(bit_generator="unreviewed"),
            lambda s: s["weights"]["V"][0].__setitem__(0, float("inf")),
        ):
            bad = copy.deepcopy(model.state())
            mutate(bad)
            with self.assertRaises(ValueError):
                SpikingDecisionPolicy(**model.configuration, state=bad)

    def test_reused_buyer_receipt_cannot_train_another_pending_job(self):
        model = self.model(replay_capacity=1)
        first = self.decision(model)
        receipt = self.receipt(model, first)
        model.learn(receipt)
        second = self.decision(model, 1)
        model.learn(self.receipt(model, second))
        third = self.decision(model, 2)
        with self.assertRaises(ValueError):
            model.learn(
                self.receipt(model, third) | {"receipt_id": receipt["receipt_id"]}
            )

    def test_normalized_features_and_architecture_bounds(self):
        for bad in (
            [float("nan"), 0],
            [1, -1],
            [True, False],
            [1.0, False],
            [0],
            [[0, 1]],
        ):
            with self.assertRaises(ValueError):
                self.model().choose(bad, digest("invalid"), ["first"])
        for config in (
            {"updates": 0},
            {"width": 257},
            {"exploration": 2},
            {"mechanism": "pickle"},
            {"replay_capacity": 513},
        ):
            with self.assertRaises(ValueError):
                self.model(**config)

    def test_replay_cannot_be_removed_duplicated_or_reordered(self):
        model = self.model()
        for index in range(3):
            model.learn(self.receipt(model, self.decision(model, index)))
        for mutate in (
            lambda s: s.update(replay=[]),
            lambda s: s["replay"].append(copy.deepcopy(s["replay"][-1])),
            lambda s: s["replay"].reverse(),
        ):
            state = model.state()
            mutate(state)
            with self.assertRaises(ValueError):
                SpikingDecisionPolicy(**model.configuration, state=state)

    def test_pending_and_credited_origin_cannot_overlap_or_claim_future_ordinal(self):
        model = self.model()
        first = self.decision(model)
        model.learn(self.receipt(model, first))
        second = self.decision(model, 1)
        state = model.state()
        credited = copy.deepcopy(state["receipts"]["1"]["origin"])
        state["pending"] = {first["decision_id"]: credited}
        with self.assertRaises(ValueError):
            SpikingDecisionPolicy(**model.configuration, state=state)
        state = model.state()
        state["pending"][second["decision_id"]]["selection"]["ordinal"] = 999
        with self.assertRaises(ValueError):
            SpikingDecisionPolicy(**model.configuration, state=state)

    def test_changed_weights_and_broken_learning_chain_are_rejected(self):
        model = self.model()
        for index in range(2):
            model.learn(self.receipt(model, self.decision(model, index)))
        changed = model.state()
        changed["weights"]["W"][0][0] += 0.001
        with self.assertRaises(ValueError):
            SpikingDecisionPolicy(**model.configuration, state=changed)
        changed = model.state()
        changed["receipts"]["2"]["result"]["before_model_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            SpikingDecisionPolicy(**model.configuration, state=changed)

    def test_mixed_boolean_state_and_forward_arrays_are_rejected(self):
        model = self.model()
        with self.assertRaises(ValueError):
            model.forward([[1.0, False]])
        changed = model.state()
        changed["weights"]["W"][0][0] = False
        with self.assertRaises(ValueError):
            SpikingDecisionPolicy(**model.configuration, state=changed)


if __name__ == "__main__":
    unittest.main()
