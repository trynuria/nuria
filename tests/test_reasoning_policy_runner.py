"""Real SQLite lifecycle checks with a scripted protocol fixture, not intelligence."""

from __future__ import annotations

import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cognition.analyst_workspace import SCHEMA
from cognition.reasoner_client import ReasonerError, digest
from scripts.evaluate_reasoning_policy import (
    CourseReasoner,
    SharedTrainingReasoner,
    UsageReasoner,
    evaluate,
    sha,
)
from scripts.prepare_policy_course import prepare_policy_course


class ProtocolFixture:
    """Produces declared JSON actions; no runtime inference takes place."""

    model = "offline-protocol-fixture"
    enable_thinking = True
    timeout = 1
    configuration = {"model": model, "kind": "scripted-test-fixture"}

    def __init__(self, failure=None):
        self.failure = failure
        self.messages = []
        self.proposals = 0

    def complete(self, messages, *, max_tokens, seed):
        self.messages.append(messages)
        if self.failure is not None:
            raise self.failure
        if messages[0]["content"].startswith("Improve a data analyst"):
            self.proposals += 1
            value = {"suffix": "Check query grain and NULLs. " + str(self.proposals)}
        elif any(message["role"] == "assistant" for message in messages):
            value = {
                "tool": "final",
                "arguments": {"sql": "SELECT 1"},
                "why": "Executed exact query",
            }
        else:
            value = {
                "tool": "query",
                "arguments": {"sql": "SELECT 1"},
                "why": "Read source",
            }
        content = json.dumps(value)
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "request_sha256": digest(messages),
            "response_sha256": digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0.0,
        }


class PolicyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]
        self.root = (
            self.repository / ".test-state" / ("policy-runner-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)

    def course(self, reasoner):
        databases = {}
        for index in range(6):
            database = self.root / (str(index) + ".sqlite")
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE events(value INTEGER)")
            document = self.root / (str(index) + ".txt")
            document.write_text("CREATE TABLE events(value INTEGER)")
            databases["database_" + str(index)] = {
                "database": {"path": database.name, "sha256": sha(database)},
                "documents": {
                    section: {"path": document.name, "sha256": sha(document)}
                    for section in ("schema", "business", "columns")
                },
            }
        registry = self.root / "registry.json"
        registry.write_text(
            json.dumps({"schema": SCHEMA, "revision": "a" * 40, "databases": databases})
        )
        dataset = self.root / "spider-fixture.json"
        dataset.write_text(
            json.dumps(
                [
                    {
                        "question": "Question " + str(index),
                        "query": "SELECT 1",
                        "db_id": "database_" + str(index % 6),
                        "sql": {},
                    }
                    for index in range(72)
                ]
            )
        )
        prior = self.root / "prior.json"
        prior.write_text(json.dumps(["spider-dev-0000"]))
        return prepare_policy_course(
            dataset,
            registry,
            [prior],
            [self.repository / "scripts/evaluate_reasoning_policy.py", Path(__file__)],
            self.root / "freeze",
            repository=self.repository,
            model_identity=digest(reasoner.configuration),
        )

    def test_full_fixture_checks_all_54_jobs_and_delays_test_oracle(self):
        from scripts import prepare_policy_course as preparation

        reasoner = ProtocolFixture()
        course = self.course(reasoner)
        output = self.root / "run"
        original = preparation.load_selected_reference
        test_reads = []

        def checked_reference(*args, **kwargs):
            if kwargs["phase"] == "test":
                self.assertTrue((output / "test-proposals-complete.json").exists())
                self.assertEqual(len(kwargs["terminal_jobs"]), 36)
                test_reads.append(args[2])
            return original(*args, **kwargs)

        with patch.object(preparation, "load_selected_reference", checked_reference):
            report = evaluate(
                course["private_protocol"],
                course["private_protocol_sha256"],
                output,
                reasoner,
                model_identity=digest(reasoner.configuration),
            )
        self.assertIsNone(report["stop_reason"])
        self.assertEqual(report["all_selected"]["selected"], 54)
        self.assertEqual(report["all_selected"]["accepted"], 54)
        self.assertEqual(len(test_reads), 36)
        self.assertEqual(report["critic_proposals_attempted"], 2)
        self.assertEqual(
            report["global_usage_including_policy_proposals"]["model_call_attempts"],
            report["all_selected"]["model_call_attempts"] + 2,
        )
        frozen = json.loads((output / "selected-policy.json").read_bytes())
        self.assertFalse(frozen["candidate_promoted"])
        self.assertEqual(frozen["test_effectiveness"], "unmeasured")
        for method in report["test_methods"].values():
            self.assertEqual(method["accepted"], 12)
            self.assertEqual(method["selected"], 12)

    def test_interrupted_fixture_preserves_full_selected_denominator(self):
        reasoner = ProtocolFixture(ReasonerError("model_transport_error"))
        course = self.course(reasoner)
        report = evaluate(
            course["private_protocol"],
            course["private_protocol_sha256"],
            self.root / "run",
            reasoner,
            model_identity=digest(reasoner.configuration),
        )
        self.assertIsNotNone(report["stop_reason"])
        self.assertEqual(report["all_selected"]["selected"], 54)
        self.assertEqual(report["all_selected"]["attempted"], 1)
        self.assertEqual(report["all_selected"]["unexecuted"], 53)
        self.assertTrue(
            report["global_usage_including_policy_proposals"][
                "unknown_additional_usage"
            ]
        )
        self.assertEqual(report["critic_proposals_attempted"], 0)
        for method in report["test_methods"].values():
            self.assertEqual(method["selected"], 12)
            self.assertEqual(method["attempted"], 0)

    def test_actual_adapter_identity_is_checked_before_output(self):
        reasoner = ProtocolFixture()
        course = self.course(reasoner)
        reasoner.configuration = {"model": "different"}
        with self.assertRaisesRegex(ValueError, "Actual adapter"):
            evaluate(
                course["private_protocol"],
                course["private_protocol_sha256"],
                self.root / "run",
                reasoner,
                model_identity=json.loads(Path(course["public_protocol"]).read_bytes())[
                    "model_identity"
                ],
            )
        self.assertFalse((self.root / "run").exists())
        self.assertEqual(reasoner.messages, [])

    def test_global_token_limit_covers_all_calls(self):
        reasoner = ProtocolFixture()
        metered = CourseReasoner(
            reasoner, self.root, maximum_completion=5, maximum_seconds=30
        )
        messages = [{"role": "system", "content": "Fixture"}]
        metered.complete(messages, max_tokens=5)
        with self.assertRaisesRegex(ValueError, "completion allowance"):
            metered.complete(messages, max_tokens=1)
        self.assertEqual(len(reasoner.messages), 1)

    def test_wall_limit_prevents_request(self):
        reasoner = ProtocolFixture()
        metered = CourseReasoner(
            reasoner, self.root, maximum_completion=100, maximum_seconds=0
        )
        with self.assertRaisesRegex(ValueError, "wall allowance"):
            metered.complete([{"role": "system", "content": "Fixture"}], max_tokens=5)
        self.assertEqual(reasoner.messages, [])

    def test_shared_wall_stop_marks_remaining_course_unexecuted(self):
        reasoner = ProtocolFixture()
        reasoner.timeout = 6000
        course = self.course(reasoner)
        report = evaluate(
            course["private_protocol"],
            course["private_protocol_sha256"],
            self.root / "run",
            reasoner,
            model_identity=digest(reasoner.configuration),
        )
        self.assertEqual(report["stop_reason"], "global_wall_limit")
        self.assertEqual(report["all_selected"]["attempted"], 1)
        self.assertEqual(report["all_selected"]["unexecuted"], 53)
        self.assertEqual(reasoner.messages, [])

    def test_receipt_write_failure_preserves_observed_usage(self):
        reasoner = ProtocolFixture()
        metered = CourseReasoner(
            reasoner, self.root, maximum_completion=100, maximum_seconds=30
        )
        with patch(
            "scripts.evaluate_reasoning_policy.write",
            side_effect=ValueError("retention"),
        ):
            with self.assertRaises(ReasonerError) as context:
                metered.complete(
                    [{"role": "system", "content": "Fixture"}], max_tokens=5
                )
        self.assertEqual(context.exception.usage["completion_tokens"], 5)
        self.assertEqual(metered.snapshot()["completion_tokens"], 5)
        self.assertEqual(metered.snapshot()["unresolved_completion_ceiling"], 0)
        self.assertEqual(metered.halt_reason, "model_receipt_retention_failure")

    def test_preflight_is_not_ambiguous_paid_usage(self):
        reasoner = ProtocolFixture(ValueError("preflight"))
        metered = UsageReasoner(reasoner)
        with self.assertRaises(ValueError):
            metered.complete(
                [{"role": "system", "content": "Fixture"}], max_tokens=1, seed=0
            )
        self.assertFalse(metered.usage()["unknown_additional_usage"])
        self.assertEqual(metered.usage()["completion_tokens"], 0)

    def test_shared_training_context_preserves_callers_messages(self):
        reasoner = ProtocolFixture()
        messages = [
            {"role": "system", "content": "Fixture"},
            {"role": "user", "content": "Question"},
        ]
        wrapped = SharedTrainingReasoner(reasoner, [{"task_id": "training-only"}])
        wrapped.complete(messages, max_tokens=5, seed=0)
        self.assertEqual(len(messages), 2)
        self.assertIn("training-only", reasoner.messages[0][1]["content"])
        self.assertEqual(reasoner.messages[0][-1], messages[-1])


if __name__ == "__main__":
    unittest.main()
