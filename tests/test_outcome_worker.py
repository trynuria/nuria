"""Durability, evidence separation and execution-boundary checks."""

from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path

from cognition.outcome_worker import (
    IsolatedPython,
    OutcomeStore,
    identity,
    retrieve_experience,
    run_work,
)
from cognition.reasoner_client import ReasonerError
from scripts.run_outcome_course import run_course


class FixtureModel:
    configuration = {"model": "offline-fixture-not-an-intelligence-result"}

    def __init__(self, proposals):
        self.proposals, self.calls = proposals, []

    def complete(self, messages, *, max_tokens, seed):
        self.calls.append(messages.copy())
        return {
            "content": json.dumps(self.proposals[len(self.calls) - 1]),
            "usage": {"completion_tokens": 1},
        }


class FixtureExecutor:
    def __init__(self):
        self.calls = []

    def run(self, code, checks, *, label, inputs=None):
        self.calls.append((code, checks, label))
        namespace = {}
        try:
            exec(code, namespace)
            exec(checks, namespace)
            passed = True
        except (AssertionError, ValueError):
            passed = False
        return {"passed": passed, "checks_sha256": identity(checks)}


class OutcomeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("outcome-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        self.store = OutcomeStore(self.root / "outcomes.sqlite")
        self.executor = FixtureExecutor()
        self.task = {
            "task_id": "fixture/add",
            "source_sha256": "a" * 64,
            "prompt": "Return a+b",
            "checker": "assert task_func(2,3)==5",
        }

    @staticmethod
    def proposal(
        code="def task_func(a,b): return a+b", checks="assert task_func(1,2)==3"
    ):
        return {
            "code": code,
            "checks": checks,
            "lesson": "Preserve addition semantics.",
        }

    def test_actual_checker_overrules_successful_self_check(self):
        model = FixtureModel(
            [self.proposal("def task_func(a,b): return 0", "assert True")]
        )
        result = run_work(model, self.executor, self.store, self.task, job_id="wrong")
        self.assertFalse(result["accepted"])
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(result["attempts"][0]["smoke"]["passed"])

    def test_repair_uses_executed_feedback(self):
        model = FixtureModel(
            [self.proposal("def task_func(a,b): return 0"), self.proposal()]
        )
        result = run_work(model, self.executor, self.store, self.task, job_id="repair")
        self.assertTrue(result["accepted"])
        self.assertEqual(len(model.calls), 2)
        self.assertFalse(json.loads(model.calls[1][-1]["content"])["smoke"]["passed"])

    def test_replay_runs_neither_model_nor_checker_and_rebinding_refused(self):
        model = FixtureModel([self.proposal()])
        first = run_work(model, self.executor, self.store, self.task, job_id="once")
        calls = len(self.executor.calls)
        replay = run_work(model, self.executor, self.store, self.task, job_id="once")
        self.assertTrue(first["accepted"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.executor.calls), calls)
        self.assertEqual(len(model.calls), 1)
        with self.assertRaises(ValueError):
            run_work(
                model,
                self.executor,
                self.store,
                {**self.task, "checker": "assert False"},
                job_id="once",
            )

    def test_failed_and_accepted_outcomes_both_inform_later_work(self):
        model = FixtureModel([self.proposal(), self.proposal()])
        run_work(model, self.executor, self.store, self.task, job_id="earlier")
        run_work(
            model,
            self.executor,
            self.store,
            {**self.task, "task_id": "fixture/add-fresh"},
            job_id="later",
        )
        self.assertEqual(
            json.loads(model.calls[1][1]["content"])["experience"][0]["job_id"],
            "earlier",
        )
        self.assertEqual(self.store.verify()["events"], 4)

    def test_terminal_feedback_withheld_then_settled_once(self):
        model = FixtureModel([self.proposal()])
        result = run_work(
            model, self.executor, self.store, self.task, job_id="deferred", grade=False
        )
        self.assertIsNone(result["accepted"])
        self.assertEqual(len(self.executor.calls), 1)
        report = {"passed": True, "checker_sha256": identity(self.task["checker"])}
        accepted = self.store.accept("deferred", report)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(self.store.accept("deferred", report), accepted)
        with self.assertRaises(ValueError):
            self.store.accept("deferred", {"passed": False})

    def test_interrupted_job_is_not_automatically_retried(self):
        model = FixtureModel([self.proposal()])

        def fail(*args, **kwargs):
            raise RuntimeError("fixture process loss")

        model.complete = fail
        with self.assertRaises(RuntimeError):
            run_work(model, self.executor, self.store, self.task, job_id="interrupted")
        with self.assertRaises(ValueError):
            run_work(model, self.executor, self.store, self.task, job_id="interrupted")

    def test_settled_incomplete_reply_is_a_retained_failure_without_retry(self):
        model = FixtureModel([])

        def incomplete(*args, **kwargs):
            raise ReasonerError(
                "model_output_incomplete", usage={"completion_tokens": 2400}
            )

        model.complete = incomplete
        result = run_work(
            model, self.executor, self.store, self.task, job_id="incomplete"
        )
        self.assertFalse(result["accepted"])
        self.assertFalse(result["checker"]["executed"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(self.executor.calls, [])

    def test_journal_modification_detected(self):
        model = FixtureModel([self.proposal()])
        run_work(model, self.executor, self.store, self.task, job_id="checked")
        with self.store.connect() as db:
            db.execute("UPDATE events SET body='{}' WHERE seq=1")
        with self.assertRaises(ValueError):
            self.store.verify()

    def test_ordinary_retrieval_is_bounded_and_reproducible(self):
        model = FixtureModel([self.proposal()])
        run_work(model, self.executor, self.store, self.task, job_id="retrieved")
        experience = self.store.experiences()
        self.assertEqual(
            retrieve_experience(self.task, experience),
            retrieve_experience(self.task, experience),
        )
        self.assertEqual(retrieve_experience(self.task, experience, max_bytes=1), [])

    @unittest.skipUnless(
        sys.platform == "darwin" or os.environ.get("NURIA_TEST_LINUX_ISOLATION") == "1",
        "enrolled native isolation backend",
    )
    def test_native_boundary_and_wall_timeout(self):
        runtime = IsolatedPython(self.root / "native", wall_seconds=1)
        secret = self.root / "outside-job.txt"
        secret.write_text("offline fixture, not a credential")
        code = (
            "from pathlib import Path\nimport socket,errno\n"
            + "try: Path("
            + repr(str(secret))
            + ").read_text()\n"
            "except (PermissionError,FileNotFoundError): pass\nelse: raise AssertionError('outside read')\n"
            "try: socket.socket().connect(('1.1.1.1',443))\n"
            "except OSError as e: assert e.errno in (errno.EPERM,errno.EACCES,errno.ENETUNREACH,errno.EHOSTUNREACH)\nelse: raise AssertionError('network')\n"
            "Path('artifact.txt').write_text('delivered')\n"
        )
        result = runtime.run(
            code,
            "assert Path('artifact.txt').read_text()=='delivered'",
            label="boundary",
        )
        self.assertTrue(result["passed"], result)
        result = runtime.run("while True: pass", "", label="timeout")
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["passed"])

    @unittest.skipUnless(
        sys.platform == "darwin" or os.environ.get("NURIA_TEST_LINUX_ISOLATION") == "1",
        "enrolled native isolation backend",
    )
    def test_enrolled_inputs_are_read_only(self):
        runtime = IsolatedPython(self.root / "inputs-boundary")
        result = runtime.run(
            "from pathlib import Path\nassert Path('inputs/data.json').read_text()=='[1,2]'\n"
            "try: Path('inputs/data.json').write_text('[]')\n"
            "except (PermissionError,OSError): pass\nelse: raise AssertionError('input changed')",
            "assert Path('inputs/data.json').read_text()=='[1,2]'",
            label="read-only",
            inputs={"data.json": "[1,2]"},
        )
        self.assertTrue(result["passed"], result)

    def test_bounded_sequence_retrieves_prior_outcomes_and_replays_without_calls(self):
        model = FixtureModel([self.proposal(), self.proposal()])
        jobs = [
            {"job_id": "sequence-one", "task": self.task},
            {
                "job_id": "sequence-two",
                "task": {**self.task, "task_id": "fixture/add-next"},
            },
        ]
        first = run_course(model, self.executor, self.store, jobs)
        calls = len(self.executor.calls)
        second = run_course(model, self.executor, self.store, jobs)
        self.assertTrue(all(row["accepted"] for row in first["results"]))
        self.assertIn("sequence-one", first["results"][1]["experience_ids"])
        self.assertTrue(all(row["replayed"] for row in second["results"]))
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(self.executor.calls), calls)


if __name__ == "__main__":
    unittest.main()
