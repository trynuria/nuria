"""End-to-end semantic entry-point fixtures; no pretrained or reasoning calls."""

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import ReasonerError
from scripts import ask_semantic_analyst as cli
from tests import test_ask_grounded_analyst as grounded_fixtures
from tests import test_semantic_neural as neural_fixtures


class AskSemanticAnalystTests(unittest.TestCase):
    def setUp(self):
        self.fixture = grounded_fixtures.AskGroundedAnalystTests()
        self.fixture.setUp()
        self.encoders = []
        self.fail_encoder = False

    def encoder(self, *args, **kwargs):
        self.assertEqual(kwargs["max_calls"], 1)
        self.assertEqual(kwargs["timeout_seconds"], 30)
        instance = neural_fixtures.FixtureEncoder(fail=self.fail_encoder)
        original = instance.observe

        def observed(question):
            attempt = next((self.fixture.state / "jobs").glob("*.attempt.json"))
            reserved = json.loads(attempt.read_bytes())
            self.assertEqual(reserved["reserved_cpu_encodes"], 1)
            self.assertEqual(reserved["reserved_completion_tokens"], 32)
            return original(question)

        instance.observe = observed
        self.encoders.append(instance)
        return instance

    def invoke(self, factory=None, **changes):
        arguments = {
            "registry": self.fixture.manifest,
            "state": self.fixture.state,
            "model_identity": "explicit-scripted-fixture",
            "job_id": "semantic-one",
            "db_id": "events",
            "question": self.fixture.question,
            "cpu_python": "host-configured-test-interpreter",
            "model_directory": "host-configured-fixture-directory",
            "semantic_manifest": "host-configured-fixture-manifest",
            "worker_sha256": "a" * 64,
            "max_output_tokens": 32,
            "encoder_factory": self.encoder,
            "reasoner_factory": factory
            or grounded_fixtures.OfflineFactory(self.fixture.actions()),
        }
        arguments.update(changes)
        return cli.ask(**arguments)

    def test_actual_sql_and_first_tool_are_retained_without_self_grading(self):
        report = self.invoke()
        artifact = json.loads(Path(report["paths"]["artifact"]).read_bytes())
        decision = json.loads(Path(report["paths"]["decision"]).read_bytes())
        self.assertEqual(report["rows"], [[3]])
        self.assertEqual(artifact["trace"][0]["kind"], "host_initial_action")
        self.assertEqual(artifact["trace"][0]["action"]["tool"], decision["first_tool"])
        self.assertEqual(
            report["semantic_decision_sha256"], decision["decision_sha256"]
        )
        self.assertEqual(report["completed_cpu_encodes"], 1)
        self.assertEqual(len(self.encoders[0].calls), 1)
        self.assertIsNone(report["semantic_accepted"])
        self.assertFalse(report["neural_credited"])
        self.assertEqual(report["unresolved_completion_ceiling"], 0)
        self.assertEqual(
            report["result_sha256"],
            cli.digest({k: v for k, v in report.items() if k != "result_sha256"}),
        )
        memory = ProceduralMemory(
            self.fixture.state / "memory.sqlite",
            source_id="0" * 40,
            model_id="explicit-scripted-fixture",
            tool_version="nuria.analyst-workspace.v1",
        )
        try:
            self.assertIsNone(memory.feedback(report["episode_id"]))
        finally:
            memory.close()
        self.fixture.assert_private_state()

    def test_duplicate_identity_never_repeats_either_inference(self):
        factory = grounded_fixtures.OfflineFactory(self.fixture.actions())
        self.invoke(factory)
        before = factory.inference_calls
        with self.assertRaisesRegex(ValueError, "already reserved"):
            self.invoke(factory)
        self.assertEqual(factory.inference_calls, before)
        self.assertEqual(self.encoders[-1].calls, [])

    def test_failed_encoding_keeps_both_reservations_and_blocks_restart(self):
        self.fail_encoder = True
        factory = grounded_fixtures.OfflineFactory(self.fixture.actions())
        with self.assertRaises(RuntimeError):
            self.invoke(factory)
        self.assertEqual(factory.inference_calls, 0)
        key = hashlib.sha256(b"semantic-one").hexdigest()
        marker = json.loads(
            (self.fixture.state / "jobs" / (key + ".unresolved.json")).read_bytes()
        )
        self.assertEqual(marker["reserved_cpu_encodes"], 1)
        self.assertEqual(marker["reserved_completion_tokens"], 32)
        self.fail_encoder = False
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            self.invoke(factory, job_id="different-explicit-job")
        self.assertEqual(self.encoders[-1].calls, [])

    def test_unknown_model_usage_remains_pending_and_uncredited(self):
        factory = grounded_fixtures.OfflineFactory([ReasonerError("transport_error")])
        report = self.invoke(factory)
        self.assertTrue(report["model_usage"]["unknown_additional_usage"])
        self.assertGreater(report["unresolved_completion_ceiling"], 0)
        self.assertIsNone(report["semantic_accepted"])
        self.assertFalse(report["neural_credited"])

    def test_completion_and_worker_caps_refuse_before_any_construction(self):
        for changes in (
            {"max_output_tokens": 6001},
            {"max_output_tokens": True},
            {"worker_sha256": "not-a-sha"},
            {"question": ""},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(cli, "AnalystWorkspace") as workspace,
            ):
                with self.assertRaises(ValueError):
                    self.invoke(**changes)
                workspace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
