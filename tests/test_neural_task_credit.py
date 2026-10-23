"""Actual Brian2 decision tags, delayed synaptic credit and atomic recovery."""

import unittest
import uuid
from pathlib import Path

from test_action_loop import task

from cognition.agency import NeuralTaskPolicy
from cognition.task_brain import TaskLearningBrain
from cognition.worker import Organism
from commerce.tasks import digest


class NeuralTaskCreditTests(unittest.TestCase):
    def test_changing_neural_state_changes_task_with_other_inputs_fixed(self):
        directory = Path(".test-state") / ("neural-intervention-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        brain = TaskLearningBrain(directory)
        checkpoint = brain.checkpoint()
        import hashlib

        metadata = {
            "model": "nuria-cognition-v1",
            "network_sha256": hashlib.sha256(checkpoint).hexdigest(),
            "topology_sha256": brain.topology["sha256"],
            "offset": brain.offset,
            "frames": brain.frames,
            "total_spikes": brain.total_spikes,
            "rate_history": brain.rate_history,
        }
        contexts = []
        for start in (0, 512):
            brain.restore(checkpoint, metadata)
            brain.neurons.v[start : start + 512] = 1.1
            contexts.append(brain.advance(duration_ms=1)["features"])
        self.assertNotEqual(contexts[0], contexts[1])
        goals = [task("neural-left"), task("neural-right")]
        policy = NeuralTaskPolicy()
        for step in range(400):
            index = step % 2
            intent = policy.select(goals, contexts[index], "b" * 64, step, 0)
            policy.learn(
                {
                    "seq": step + 1,
                    "intent_id": intent["id"],
                    "goal_id": intent["goal_id"],
                    "contract_sha256": intent["contract_sha256"],
                    "reward": 1.0 if intent["goal_id"] == goals[index]["id"] else -1.0,
                    "result_sha256": "c" * 64,
                    "evidence_level": "verified_outcome",
                }
            )
        state = policy.state()
        chosen = [
            NeuralTaskPolicy(state).select(goals, context, "b" * 64, 500, 0)["goal_id"]
            for context in contexts
        ]
        self.assertEqual(chosen, ["neural-left", "neural-right"])

    def test_actual_neural_tag_receives_only_matching_delayed_credit(self):
        directory = Path(".test-state") / ("neural-task-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        organism = Organism(directory, "task-credit-fixture")
        self.addCleanup(lambda: organism.journal.db.close())
        organism.agency_tasks = [task()]
        for _ in range(5):
            organism.cycle([])
        intent = organism.last_decision["intent"]
        pending = organism.agency.pending[intent["id"]]
        trace = pending["synaptic_trace"]
        self.assertEqual(digest(trace), intent["eligibility_sha256"])
        self.assertGreater(len(trace["indices"]), 0)
        record = {
            "seq": 10,
            "intent_id": intent["id"],
            "contract_sha256": intent["contract_sha256"],
            "goal_id": intent["goal_id"],
            "action": intent["action"],
            "reward": 0.2,
            "result_sha256": "c" * 64,
            "evidence_level": "verified_outcome",
        }
        organism.save()
        original_genesis = organism.genesis
        before = organism.brain.state_hash()
        organism.task_feedback([record])
        credited = organism.brain.state_hash()
        self.assertNotEqual(before, credited)
        evidence = organism.journal.recent("task_outcome")[0]
        self.assertGreater(evidence["neural_credit"]["changed_synapses"], 0)
        organism.task_feedback([record])
        self.assertEqual(organism.brain.state_hash(), credited)
        organism.save()
        organism.journal.db.close()
        organism = Organism(directory, "task-credit-fixture")
        self.assertEqual(organism.genesis, original_genesis)
        self.assertEqual(organism.brain.state_hash(), credited)
        self.assertEqual(organism.agency.completed, 1)
        self.assertTrue(organism.journal.verify(full=True)["valid"])

    def test_crash_before_commit_restores_task_and_neural_state_together(self):
        directory = Path(".test-state") / ("neural-task-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        organism = Organism(directory, "task-credit-fixture")
        organism.agency_tasks = [task()]
        for _ in range(5):
            organism.cycle([])
        intent = organism.last_decision["intent"]
        before = organism.brain.state_hash()
        organism.task_feedback(
            [
                {
                    "seq": 10,
                    "intent_id": intent["id"],
                    "contract_sha256": intent["contract_sha256"],
                    "goal_id": intent["goal_id"],
                    "action": intent["action"],
                    "reward": -0.2,
                    "result_sha256": "c" * 64,
                    "evidence_level": "verified_outcome",
                }
            ]
        )
        organism.journal.db.close()
        restored = Organism(directory, "task-credit-fixture")
        self.addCleanup(restored.journal.db.close)
        self.assertEqual(restored.brain.state_hash(), before)
        self.assertIn(intent["id"], restored.agency.pending)
        self.assertEqual(restored.agency.completed, 0)
