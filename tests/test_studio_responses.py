"""Real response accounting with offline multi-phase provider fixtures."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from cognition.hosted_reasoner import SharedReasoningBudget
from cognition.reasoner_client import ReasonerError
from studio.responses import StudioReasoner
from tests.test_hosted_reasoner import Opener, packet


def message(phase, content):
    return {
        "type": "message",
        "role": "assistant",
        "phase": phase,
        "content": [{"type": "output_text", "text": content}],
    }


class StudioResponseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        key = root / "fixture.key"
        key.write_text("fixture-only-credential-for-offline-tests")
        os.chmod(key, 0o600)
        self.budget = SharedReasoningBudget(
            root / "allowance.sqlite", max_cost_micro_usd=100000, max_requests=8
        )
        self.client = StudioReasoner(
            key,
            "explicit-model-snapshot",
            reasoning_effort="low",
            budget=self.budget,
            input_micro_usd_per_million_tokens=1_000_000,
            output_micro_usd_per_million_tokens=2_000_000,
        )

    def ask(self, raw):
        opener = Opener(raw)
        self.client._opener = opener
        try:
            return self.client.complete([{"role": "user", "content": "Inspect"}])
        finally:
            self.assertIs(self.client._opener, opener)
            self.assertEqual(len(opener.requests), 1)

    def test_only_final_action_is_returned_but_visible_phases_and_raw_hash_remain(self):
        first, final = '{"kind":"exec","command":"first"}', '{"kind":"done"}'
        raw = packet(
            output=[
                {"type": "reasoning", "summary": [{"text": "private reasoning"}]},
                message("commentary", first),
                message("final_answer", final),
            ]
        )
        result = self.ask(raw)
        self.assertEqual(result["content"], final)
        self.assertEqual(result["output_messages"][0]["content"], first)
        self.assertEqual(result["response_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn("private reasoning", json.dumps(result))
        self.assertEqual(self.budget.snapshot()["requests"], 1)
        self.assertEqual(self.budget.snapshot()["settled_cost_micro_usd"], 130)
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_single_unlabelled_response_stays_compatible(self):
        self.assertEqual(self.ask(packet("one answer"))["content"], "one answer")

    def test_ambiguous_or_missing_final_fails_closed_after_usage_settles(self):
        for phases in (
            ["commentary"],
            ["final_answer", "final_answer"],
            [None, None],
            [None, "final_answer"],
            ["unknown", "final_answer"],
            ["final_answer", "commentary"],
        ):
            with (
                self.subTest(phases=phases),
                self.assertRaises(ReasonerError) as caught,
            ):
                self.ask(packet(output=[message(p, "action") for p in phases]))
            self.assertEqual(caught.exception.code, "model_protocol_error")
            self.assertFalse(caught.exception.usage_unknown)
            self.assertFalse(self.budget.snapshot()["blocked"])
        self.assertEqual(self.budget.snapshot()["requests"], 6)
        self.assertEqual(self.budget.snapshot()["settled_cost_micro_usd"], 6 * 130)

    def test_empty_final_cannot_execute_commentary(self):
        with self.assertRaises(ReasonerError):
            self.ask(
                packet(
                    output=[
                        message("commentary", "action"),
                        message("final_answer", " "),
                    ]
                )
            )
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_incomplete_output_never_becomes_an_action(self):
        with self.assertRaises(ReasonerError) as caught:
            self.ask(
                packet(status="incomplete", output=[message("final_answer", "partial")])
            )
        self.assertEqual(caught.exception.code, "model_output_incomplete")
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_transport_failure_preserves_original_unresolved_liability(self):
        with self.assertRaises(ReasonerError) as caught:
            self.ask(TimeoutError("offline failure"))
        self.assertEqual(caught.exception.code, "model_transport_error")
        self.assertTrue(self.budget.snapshot()["blocked"])
        self.assertEqual(self.budget.snapshot()["requests"], 1)


if __name__ == "__main__":
    unittest.main()
