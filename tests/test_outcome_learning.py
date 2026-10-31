"""Policy enrollment, artifact integrity and real optimizer fixture checks."""

import importlib.util
import json
import unittest
import uuid
from pathlib import Path

from cognition.outcome_learning import learn_policy, qualified_policy
from cognition.outcome_worker import BASE_POLICY, OutcomeStore, identity
from scripts.qualify_outcome_policies import select
from tests.test_outcome_worker import FixtureExecutor, FixtureModel


class OutcomeLearningTests(unittest.TestCase):
    def setUp(self):
        root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("policy-" + uuid.uuid4().hex)
        )
        self.store = OutcomeStore(root / "outcomes.sqlite")
        self.executor = FixtureExecutor()
        self.task = {
            "task_id": "fixture/add",
            "source_sha256": "a" * 64,
            "prompt": "Return a+b",
            "checker": "assert task_func(2,3)==5",
        }

    def test_duplicate_and_overlapping_qualification_tasks_are_refused(self):
        with self.assertRaises(ValueError):
            learn_policy(
                None,
                None,
                None,
                training=[self.task],
                validation=[self.task],
                experience=[],
            )
        with self.assertRaises(ValueError):
            learn_policy(
                None, None, None, training=[], validation=[self.task], experience=[]
            )

    def test_policy_text_cannot_change_after_qualification(self):
        artifact = {
            "version": "nuria.outcome-policy.v1",
            "policy": BASE_POLICY,
            "policy_sha256": identity(BASE_POLICY),
        }
        artifact["artifact_sha256"] = identity(artifact)
        self.assertEqual(qualified_policy(artifact), BASE_POLICY)
        with self.assertRaises(ValueError):
            qualified_policy({**artifact, "policy": "different"})

    def test_validation_ties_prefer_the_simpler_policy(self):
        scores = {
            name: 0.75
            for name in (
                "simple_retrieval",
                "simple_reflection",
                "outcome_playbook",
                "nuria_gepa",
            )
        }
        self.assertEqual(select(scores), "simple_retrieval")
        self.assertEqual(
            select({**scores, "outcome_playbook": 0.8125}), "outcome_playbook"
        )

    @unittest.skipUnless(importlib.util.find_spec("gepa"), "optional pinned optimizer")
    def test_actual_gepa_retains_the_original_on_a_qualified_tie(self):
        model = FixtureModel(
            [
                {
                    "code": "def task_func(a,b): return a+b",
                    "checks": "assert task_func(1,2)==3",
                    "lesson": "Addition.",
                }
            ]
        )
        artifact = learn_policy(
            model,
            self.executor,
            self.store,
            training=[self.task],
            validation=[{**self.task, "task_id": "fixture/add-validation"}],
            experience=[],
            max_evaluations=1,
            max_proposals=0,
        )
        self.assertEqual(qualified_policy(artifact), BASE_POLICY)
        self.assertEqual(artifact["validation_scores"], [1.0])
        self.assertFalse(artifact["activated"])
        self.assertEqual(len(model.calls), 1)

    @unittest.skipUnless(importlib.util.find_spec("gepa"), "optional pinned optimizer")
    def test_multi_example_artifact_reopens_with_its_original_identity(self):
        proposal = {
            "code": "def task_func(a,b):return a+b",
            "checks": "assert task_func(1,2)==3",
            "lesson": "Addition.",
        }
        model = FixtureModel([proposal] * 12)
        artifact = learn_policy(
            model,
            self.executor,
            self.store,
            training=[self.task],
            validation=[
                {**self.task, "task_id": "validation-" + str(i)} for i in range(12)
            ],
            experience=[],
            max_evaluations=12,
            max_proposals=0,
        )
        restored = json.loads(json.dumps(artifact))
        self.assertEqual(qualified_policy(restored), BASE_POLICY)
        self.assertEqual(restored["artifact_sha256"], artifact["artifact_sha256"])
