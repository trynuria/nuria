"""Feedback-integrity checks, not model intelligence measurements."""

import copy
import hashlib
import json
import unittest
import uuid
from pathlib import Path

from cognition.outcome_consistency import ConsistentOutcomeStore
from cognition.outcome_worker import OutcomeStore, identity
from scripts.run_consistent_course import run_course
from tests.test_experience_navigation import Client as NavigationClient
from tests.test_experience_navigation import Executor as NavigationExecutor
from tests.test_experience_navigation import task as navigation_task
from tests.test_outcome_worker import FixtureExecutor, FixtureModel


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("consistency-" + uuid.uuid4().hex)
        )
        self.store = ConsistentOutcomeStore(self.root / "outcomes.sqlite")
        self.checker = {
            "passed": False,
            "code_sha256": hashlib.sha256(b"def task_func(): return 1").hexdigest(),
            "checks_sha256": hashlib.sha256(b"assert task_func()==1").hexdigest(),
        }
        self.record = {
            "job_id": "original",
            "task_id": "fixture/feedback",
            "source_sha256": "a" * 64,
            "prompt": "Return one",
            "code": "def task_func(): return 1",
            "lesson": "Unverified proposed lesson",
            "accepted": False,
            "checker": self.checker,
        }
        self.store.begin("original", {"task": "fixture/feedback", "inputs": {}})
        self.store.finish("original", self.record)

    def observe(self, name="replay", **changes):
        return self.store.observe(
            "original",
            name,
            {**self.checker, "passed": True, **changes},
            result_sha256=identity(self.record),
        )

    def test_conflict_withholds_both_signs_without_changing_raw_receipt(self):
        before = identity(self.store.raw_experiences())
        self.observe()
        for i in range(3):
            self.observe("extra-" + str(i))
        self.assertEqual(self.store.experiences(), [])
        self.assertEqual(identity(self.store.raw_experiences()), before)
        self.assertFalse(self.store.raw_experiences()[0]["accepted"])
        report = ConsistentOutcomeStore(self.store.path).learning_report()
        self.assertEqual(report["withheld_job_ids"], ["original"])
        self.assertEqual(report["additional_observations"], 4)

    def test_observation_replay_is_idempotent_and_rebinding_refused(self):
        self.assertFalse(self.observe()["replayed"])
        journal = self.store.verify()
        self.assertTrue(self.observe()["replayed"])
        self.assertEqual(self.store.verify(), journal)
        with self.assertRaisesRegex(ValueError, "rebound"):
            self.observe(passed=False)

    def test_program_checker_and_original_receipt_must_match(self):
        before = self.store.verify()
        for changes in [
            {"code_sha256": "b" * 64},
            {"checks_sha256": "b" * 64},
            {"passed": None},
        ]:
            with self.assertRaises(ValueError):
                self.observe(**changes)
        with self.assertRaisesRegex(ValueError, "original receipt"):
            self.store.observe(
                "original", "wrong", self.checker, result_sha256="b" * 64
            )
        self.assertEqual(self.store.verify(), before)

    def test_uncertain_job_does_not_remove_other_checked_or_unknown_jobs(self):
        stable = {**copy.deepcopy(self.record), "job_id": "stable"}
        unknown = {
            **copy.deepcopy(self.record),
            "job_id": "unknown",
            "accepted": None,
            "checker": None,
        }
        for row in [stable, unknown]:
            self.store.begin(row["job_id"], {"job": row["job_id"]})
            self.store.finish(row["job_id"], row)
        self.observe()
        self.assertEqual([r["job_id"] for r in self.store.experiences()], ["stable"])
        self.assertEqual(self.store.learning_report()["unknown_job_ids"], ["unknown"])
        self.assertEqual(len(self.store.raw_experiences()), 3)

    def test_automatic_course_memory_omits_conflict_but_identical_job_replays(self):
        self.observe()
        task = {
            "task_id": "fixture/add",
            "source_sha256": "c" * 64,
            "prompt": "Return a+b",
            "checker": "assert task_func(2,3)==5",
        }
        model, executor = (
            FixtureModel(
                [
                    {
                        "code": "def task_func(a,b): return a+b",
                        "checks": "assert task_func(1,2)==3",
                        "lesson": "Preserve addition semantics.",
                    }
                ]
            ),
            FixtureExecutor(),
        )
        jobs = [{"job_id": "later", "task": task}]
        receipt = run_course(model, executor, OutcomeStore(self.store.path), jobs)
        self.assertEqual(json.loads(model.calls[0][1]["content"])["experience"], [])
        self.assertEqual(receipt["withheld_experience_ids"], ["original"])
        calls = len(executor.calls)
        replay = run_course(model, executor, OutcomeStore(self.store.path), jobs)
        self.assertTrue(replay["results"][0]["replayed"])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(executor.calls), calls)
        self.assertTrue(self.store.raw_experiences()[0]["accepted"])

    def test_tampered_observation_journal_is_rejected(self):
        self.observe()
        with self.store.connect() as db:
            db.execute(
                "UPDATE events SET body='{}' WHERE seq=(SELECT MAX(seq) FROM events)"
            )
        with self.assertRaisesRegex(ValueError, "journal"):
            self.store.experiences()

    def test_navigation_course_also_withholds_uncertain_memory(self):
        self.observe()
        client, executor = NavigationClient(), NavigationExecutor()
        result = run_course(
            client,
            executor,
            self.store,
            [{"job_id": "nav", "task": navigation_task()}],
            method="navigation",
        )
        self.assertTrue(result["results"][0]["accepted"])
        self.assertEqual(result["results"][0]["experience_ids"], [])
        self.assertEqual(result["withheld_experience_ids"], ["original"])
        for request in client.messages:
            self.assertEqual(json.loads(request[1]["content"])["experience"], [])

    def test_unchecked_proposal_cannot_receive_learning_observation(self):
        row = {**self.record, "job_id": "ungraded", "accepted": None, "checker": None}
        self.store.begin(row["job_id"], {"job": row["job_id"]})
        self.store.finish(row["job_id"], row)
        with self.assertRaises(ValueError):
            self.store.observe(
                row["job_id"], "unchecked", self.checker, result_sha256=identity(row)
            )


if __name__ == "__main__":
    unittest.main()
