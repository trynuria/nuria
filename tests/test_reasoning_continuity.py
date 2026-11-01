"""Offline continuity, accounting and opacity controls; no model intelligence."""

import io
import json
import os
import unittest
import uuid
from pathlib import Path

from cognition.hosted_reasoner import OpenAIReasoner, SharedReasoningBudget
from cognition.reasoner_client import ReasonerError
from cognition.reasoning_continuity import ContinuitySession, ContinuityTransport


def packet(*, context="all_turns", prompt=10, completion=10):
    return json.dumps(
        {
            "model": "gpt-6.1-sol-fixture",
            "status": "completed",
            "error": None,
            "service_tier": "default",
            "reasoning": {"context": context},
            "usage": {
                "input_tokens": prompt,
                "output_tokens": completion,
                "total_tokens": prompt + completion,
                "output_tokens_details": {"reasoning_tokens": min(6, completion)},
            },
            "output": [
                {
                    "id": "rs_fixture",
                    "type": "reasoning",
                    "summary": [],
                    "encrypted_content": "opaque-fixture-not-real-reasoning",
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": '{"fixture":true}'}],
                },
            ],
        }
    ).encode()


class Opener:
    def __init__(self, response):
        self.response, self.requests, self.before = response, [], None

    def open(self, request, timeout):
        self.requests.append(request)
        if self.before:
            self.before()
        if isinstance(self.response, Exception):
            raise self.response
        return io.BytesIO(self.response)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".test-state") / ("continuity-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True, mode=0o700)
        self.key = self.root / "fixture.key"
        self.key.write_text("fixture-only-offline-credential")
        os.chmod(self.key, 0o600)
        self.budget = SharedReasoningBudget(
            self.root / "budget.sqlite", max_cost_micro_usd=20_000_000, max_requests=20
        )
        self.messages = [
            {"role": "system", "content": "Fixture work"},
            {"role": "user", "content": "Inspect"},
        ]

    def client(self, *, keep=True, response=None):
        base = OpenAIReasoner(
            self.key,
            "gpt-6.1-sol",
            reasoning_effort="low",
            budget=self.budget,
            input_micro_usd_per_million_tokens=2_000_000,
            output_micro_usd_per_million_tokens=10_000_000,
        )
        base._opener = Opener(packet() if response is None else response)
        transport = ContinuityTransport(base)
        return ContinuitySession(transport, keep_reasoning=keep), base._opener

    def next_messages(self, reply):
        return self.messages + [
            {"role": "assistant", "content": reply["content"]},
            {"role": "user", "content": "Actual fixture execution result"},
        ]

    def test_complete_output_items_are_carried_without_exposing_opaque_contents(self):
        client, opener = self.client()
        first = client.complete(self.messages, max_tokens=128)
        self.assertNotIn("opaque-fixture", json.dumps(first))
        self.assertNotIn("output_items", first)
        client.complete(self.next_messages(first), max_tokens=128)
        body = json.loads(opener.requests[1].data)
        self.assertEqual(body["input"][2]["type"], "reasoning")
        self.assertEqual(body["input"][3]["role"], "assistant")
        self.assertEqual(len(body["input"]), 5)
        self.assertFalse(body["store"])
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_same_visible_control_omits_only_provider_output_items(self):
        candidate, candidate_wire = self.client()
        control, control_wire = self.client(keep=False)
        for client in [candidate, control]:
            reply = client.complete(self.messages, max_tokens=128)
            client.complete(self.next_messages(reply), max_tokens=128)
        self.assertEqual(candidate_wire.requests[0].data, control_wire.requests[0].data)
        body = json.loads(control_wire.requests[1].data)
        self.assertEqual(len(body["input"]), 4)
        self.assertFalse(any(x.get("type") == "reasoning" for x in body["input"]))

    def test_maximum_context_reservation_precedes_transport_and_long_rates_settle(self):
        client, opener = self.client(response=packet(prompt=300000))
        observed = []
        opener.before = lambda: observed.append(self.budget.snapshot())
        reply = client.complete(self.messages, max_tokens=128)
        self.assertTrue(observed[0]["blocked"])
        self.assertGreater(observed[0]["unresolved_liability_micro_usd"], 4_600_000)
        self.assertEqual(reply["accounted_cost_micro_usd"], 1_500_150)
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_edited_visible_history_is_rejected_before_another_request(self):
        client, opener = self.client()
        reply = client.complete(self.messages, max_tokens=128)
        changed = self.next_messages(reply)
        changed[0] = {"role": "system", "content": "Changed"}
        with self.assertRaisesRegex(ValueError, "history changed"):
            client.complete(changed, max_tokens=128)
        self.assertEqual(len(opener.requests), 1)

    def test_context_must_be_observed_and_unknown_usage_never_retries(self):
        client, opener = self.client(response=packet(context="current_turn"))
        with self.assertRaises(ReasonerError) as caught:
            client.complete(self.messages, max_tokens=128)
        self.assertEqual(caught.exception.code, "continuity_context_unconfirmed")
        self.assertEqual(len(opener.requests), 1)
        self.assertFalse(self.budget.snapshot()["blocked"])
        client, opener = self.client(response=TimeoutError("private fixture detail"))
        with self.assertRaises(ReasonerError):
            client.complete(self.messages, max_tokens=128)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            client.complete(self.messages, max_tokens=128)
        self.assertEqual(len(opener.requests), 1)

    def test_total_completion_allowance_is_shared_across_steps(self):
        client, opener = self.client(response=packet(completion=2400))
        messages = self.messages
        for _ in range(3):
            reply = client.complete(messages, max_tokens=2400)
            messages += [
                {"role": "assistant", "content": reply["content"]},
                {"role": "user", "content": "Next fixture observation"},
            ]
        with self.assertRaisesRegex(ValueError, "envelope is exhausted"):
            client.complete(messages, max_tokens=128)
        self.assertEqual(len(opener.requests), 3)

    def test_known_truncation_keeps_its_charge_and_can_receive_error_feedback(self):
        raw = json.loads(packet(completion=2400))
        raw.update(status="incomplete", output=[])
        client, opener = self.client(response=json.dumps(raw).encode())
        with self.assertRaises(ReasonerError) as caught:
            client.complete(self.messages, max_tokens=2400)
        self.assertEqual(caught.exception.code, "model_output_incomplete")
        self.assertEqual(client.completions, 2400)
        self.assertFalse(self.budget.snapshot()["blocked"])
        opener.response = packet()
        reply = client.complete(
            self.messages
            + [{"role": "user", "content": "Return concise complete JSON"}],
            max_tokens=2400,
        )
        self.assertEqual(reply["reasoning_context"], "all_turns")
        self.assertEqual(client.completions, 2410)
        self.assertEqual(len(opener.requests), 2)


if __name__ == "__main__":
    unittest.main()
