"""Exact feedback identity survives recovery without resetting learned policy."""

import copy
import unittest

from test_action_loop import task

from cognition.agency import RECEIPT_WINDOW, NeuralTaskPolicy


def outcome(policy, seq, *, cancelled=False):
    goal = task()
    goal["evaluation_nonce"] = f"{seq:064x}"
    intent = policy.select([goal], [0.1] * 64, "b" * 64, seq * 5, 0)
    return {
        "seq": seq,
        "intent_id": intent["id"],
        "goal_id": intent["goal_id"],
        "contract_sha256": intent["contract_sha256"],
        "action": intent["action"],
        "reward": 0.25,
        "result_sha256": "c" * 64,
        "evidence_level": "not_executed" if cancelled else "verified_outcome",
        "task_state": "cancelled" if cancelled else "evaluated",
    }


class AgencyReceiptTests(unittest.TestCase):
    def test_exact_duplicate_survives_checkpoint_but_changed_receipt_is_refused(self):
        policy = NeuralTaskPolicy()
        record = outcome(policy, 10)
        self.assertTrue(policy.learn(record))
        restored = NeuralTaskPolicy(policy.state())
        before = restored.state()
        self.assertFalse(restored.learn(dict(reversed(list(record.items())))))
        for changed in (
            {"reward": -0.9},
            {"intent_id": "d" * 64},
            {"contract_sha256": "e" * 64},
            {"result_sha256": "f" * 64},
        ):
            with self.assertRaises(ValueError):
                restored.learn({**record, **changed})
            self.assertEqual(restored.state(), before)

    def test_cancelled_receipt_is_also_exact_and_does_not_train(self):
        policy = NeuralTaskPolicy()
        record = outcome(policy, 1, cancelled=True)
        self.assertTrue(policy.learn(record))
        self.assertEqual(policy.completed, 0)
        self.assertFalse(policy.learn(record))
        with self.assertRaises(ValueError):
            policy.learn({**record, "task_state": "evaluated"})

    def test_legacy_cursor_and_models_are_preserved_without_invented_receipts(self):
        policy = NeuralTaskPolicy()
        record = outcome(policy, 3)
        policy.learn(record)
        legacy = policy.state()
        legacy.pop("feedback_receipts")
        legacy.pop("receipt_floor")
        legacy.pop("legacy_feedback_floor")
        restored = NeuralTaskPolicy(legacy)
        self.assertEqual(restored.models, legacy["models"])
        self.assertEqual(restored.feedback_cursor, 3)
        self.assertEqual(restored.completed, 1)
        with self.assertRaisesRegex(ValueError, "outside"):
            restored.learn(record)
        self.assertTrue(restored.learn(outcome(restored, 9)))

    def test_evicted_receipts_are_unknown_rather_than_accepted_duplicates(self):
        policy = NeuralTaskPolicy()
        first = None
        for seq in range(1, RECEIPT_WINDOW + 2):
            record = outcome(policy, seq)
            first = record if first is None else first
            policy.learn(record)
        self.assertEqual(len(policy.receipts), RECEIPT_WINDOW)
        self.assertEqual(policy.receipt_floor, 1)
        with self.assertRaisesRegex(ValueError, "outside"):
            policy.learn(first)
        self.assertFalse(policy.learn(record))

    def test_corrupt_retained_receipt_fails_closed(self):
        policy = NeuralTaskPolicy()
        policy.learn(outcome(policy, 1))
        original = policy.state()
        for key, value in (("sha256", "not-a-hash"), ("extra", "d" * 64)):
            damaged = copy.deepcopy(original)
            damaged["feedback_receipts"]["1"][key] = value
            with self.assertRaises(ValueError):
                NeuralTaskPolicy(damaged)

    def test_bad_sequence_and_oversized_feedback_do_not_change_state(self):
        policy = NeuralTaskPolicy()
        record = outcome(policy, 1)
        before = policy.state()
        for changed in ({"seq": True}, {"seq": 0}, {"padding": "x" * 65536}):
            with self.assertRaises(ValueError):
                policy.learn({**record, **changed})
            self.assertEqual(policy.state(), before)

    def test_unexecuted_transform_can_retry_but_credited_contract_cannot_repeat(self):
        from test_skill_tasks import task as transform_task

        goal = transform_task()
        policy = NeuralTaskPolicy()
        first = policy.select([goal], [0.1] * 64, "b" * 64, 5, 0)
        policy.learn(
            {
                "seq": 1,
                "intent_id": first["id"],
                "goal_id": goal["id"],
                "contract_sha256": first["contract_sha256"],
                "evidence_level": "not_executed",
                "task_state": "cancelled",
            }
        )
        second = policy.select([goal], [0.1] * 64, "b" * 64, 10, 0)
        self.assertIsNotNone(second)
        policy.learn(
            {
                "seq": 2,
                "intent_id": second["id"],
                "goal_id": goal["id"],
                "contract_sha256": second["contract_sha256"],
                "evidence_level": "verified_outcome",
                "reward": -0.1,
                "result_sha256": "d" * 64,
            }
        )
        self.assertIsNone(policy.select([goal], [0.1] * 64, "b" * 64, 15, 0))
