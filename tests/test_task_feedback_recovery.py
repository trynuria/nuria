"""Only durable task acknowledgement can advance the producer's feedback cursor."""

import unittest
import uuid
from pathlib import Path

from test_action_loop import task

from cognition.agency import NeuralTaskPolicy
from cognition.worker import Organism


class TaskFeedbackRecoveryTests(unittest.TestCase):
    def organism(self):
        directory = Path(".test-state") / ("feedback-ack-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        organism = Organism(directory, "feedback-recovery-check")
        return directory, organism

    def prepare(self, organism):
        organism.agency_tasks = [task()]
        for _ in range(5):
            organism.cycle([])
        intent = organism.last_decision["intent"]
        return {
            "seq": 10,
            "intent_id": intent["id"],
            "contract_sha256": intent["contract_sha256"],
            "goal_id": intent["goal_id"],
            "action": intent["action"],
            "reward": 0.2,
            "result_sha256": "c" * 64,
            "evidence_level": "verified_outcome",
        }

    def test_uncommitted_ack_does_not_skip_feedback_after_crash(self):
        directory, organism = self.organism()
        record = self.prepare(organism)
        organism.save()
        origin = organism.genesis
        organism.task_feedback([record])
        expected_neural = organism.brain.state_hash()
        self.assertEqual(organism.snapshot()["agency"]["feedback_cursor"], 10)
        self.assertEqual(organism.snapshot()["agency"]["committed_feedback_cursor"], 0)
        organism.journal.db.close()  # roll back the uncommitted outcome
        organism = Organism(directory, "feedback-recovery-check")
        self.addCleanup(organism.journal.db.close)
        self.assertEqual(organism.genesis, origin)
        self.assertEqual(organism.agency.feedback_cursor, 0)
        organism.task_feedback([record])
        self.assertEqual(organism.brain.state_hash(), expected_neural)
        organism.save()
        self.assertEqual(organism.snapshot()["agency"]["committed_feedback_cursor"], 10)
        organism.task_feedback([record])
        self.assertEqual(organism.brain.state_hash(), expected_neural)

    def test_legacy_semantic_receipt_is_rechecked_without_new_credit(self):
        _, organism = self.organism()
        self.addCleanup(organism.journal.db.close)
        record = self.prepare(organism)
        organism.task_feedback([record])
        organism.save()
        state = organism.agency.state()
        for key in ("feedback_receipts", "receipt_floor", "legacy_feedback_floor"):
            state.pop(key)
        organism.agency = NeuralTaskPolicy(state)
        before = organism.brain.state_hash()
        organism.task_feedback([record])
        self.assertEqual(organism.brain.state_hash(), before)
        self.assertEqual(organism.agency.completed, 1)
        with self.assertRaises(ValueError):
            organism.task_feedback([{**record, "reward": -0.7}])
        self.assertEqual(organism.brain.state_hash(), before)

    def test_omitted_action_uses_retained_origin_without_post_credit_key_error(self):
        _, organism = self.organism()
        self.addCleanup(organism.journal.db.close)
        record = self.prepare(organism)
        action = record.pop("action")
        organism.task_feedback([record])
        saved = organism.journal.recent("task_outcome")[0]
        self.assertEqual(saved["action"], action)
        self.assertEqual(organism.agency.completed, 1)
