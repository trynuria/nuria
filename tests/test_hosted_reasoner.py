import io
import json
import os
import sqlite3
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

from cognition.hosted_reasoner import ENDPOINT, OpenAIReasoner, SharedReasoningBudget
from cognition.reasoner_client import ReasonerError


def packet(content='{"tool":"list_tables","arguments":{}}', **updates):
    value = {
        "model": "explicit-model-snapshot",
        "status": "completed",
        "service_tier": "default",
        "error": None,
        "usage": {
            "input_tokens": 30,
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 40},
            "total_tokens": 80,
        },
        "output": [
            {"type": "reasoning", "summary": [{"text": "private reasoning"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            },
        ],
    }
    value.update(updates)
    return json.dumps(value).encode()


class Opener:
    def __init__(self, result, before=None):
        self.result, self.before = result, before
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.before:
            self.before()
        if isinstance(self.result, Exception):
            raise self.result
        return io.BytesIO(self.result)


class HostedReasonerTests(unittest.TestCase):
    def setUp(self):
        # Keep additive test artifacts; no original state or credentials are used.
        self.root = Path(".test-state") / ("hosted-reasoner-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.key = self.root / "fixture.key"
        self.key.write_text("fixture-only-credential-for-offline-tests")
        os.chmod(self.key, 0o600)
        self.budget = SharedReasoningBudget(
            self.root / "allowance.sqlite", max_cost_micro_usd=100000, max_requests=8
        )

    def client(self, response=None, **updates):
        settings = {
            "reasoning_effort": "high",
            "budget": self.budget,
            "input_micro_usd_per_million_tokens": 1_000_000,
            "output_micro_usd_per_million_tokens": 2_000_000,
        }
        settings.update(updates)
        client = OpenAIReasoner(self.key, "explicit-model-snapshot", **settings)
        client._opener = Opener(packet() if response is None else response)
        return client

    def ask(self, client, **updates):
        return client.complete([{"role": "user", "content": "Inspect"}], **updates)

    def test_responses_contract_and_reasoning_inclusive_usage(self):
        client = self.client()
        result = self.ask(client, max_tokens=5000, seed=77)
        self.assertEqual(result["usage"]["completion_tokens"], 50)
        self.assertEqual(result["usage"]["reasoning_tokens"], 40)
        request, timeout = client._opener.requests[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertEqual(body["max_output_tokens"], 5000)
        self.assertFalse(body["store"])
        self.assertFalse(body["stream"])
        self.assertEqual(body["service_tier"], "default")
        self.assertEqual(body["truncation"], "disabled")
        self.assertNotIn("seed", body)
        self.assertNotIn("tools", body)
        self.assertEqual(timeout, 180)
        self.assertEqual(self.budget.snapshot()["settled_cost_micro_usd"], 130)
        self.assertEqual(self.budget.snapshot()["unresolved_liability_micro_usd"], 0)
        self.assertEqual(len(result["contract_sha256"]), 64)
        self.assertNotIn("private reasoning", json.dumps(result))
        self.assertNotIn(self.key.read_text(), json.dumps(result))
        self.assertNotIn(str(self.key), json.dumps(client.configuration))
        database = sqlite3.connect(self.budget.path)
        try:
            (contract,) = database.execute("SELECT value FROM contracts").fetchone()
        finally:
            database.close()
        self.assertEqual(json.loads(contract), client.configuration)
        self.assertNotIn(self.key.read_text(), contract)
        self.assertEqual(result["accounted_cost_micro_usd"], 130)

    def test_reservation_is_committed_before_transport_and_blocks_competing_client(
        self,
    ):
        client = self.client()
        observed = []

        def during_request():
            reopened = SharedReasoningBudget(
                self.budget.path, max_cost_micro_usd=100000, max_requests=8
            )
            snapshot = reopened.snapshot()
            observed.append(snapshot)
            self.assertTrue(snapshot["blocked"])
            competitor = self.client(budget=reopened)
            with self.assertRaisesRegex(ValueError, "unresolved"):
                self.ask(competitor)
            self.assertEqual(competitor._opener.requests, [])

        client._opener.before = during_request
        self.ask(client)
        self.assertEqual(observed[0]["requests"], 1)
        self.assertGreater(observed[0]["unresolved_liability_micro_usd"], 0)
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_transport_failure_preserves_full_liability_and_never_retries(self):
        client = self.client(TimeoutError("fixture-private-detail"))
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_transport_error")
        self.assertTrue(caught.exception.usage_unknown)
        self.assertEqual(len(client._opener.requests), 1)
        self.assertNotIn("fixture-private-detail", str(caught.exception))
        before = self.budget.snapshot()
        self.assertTrue(before["blocked"])
        self.assertGreater(before["unresolved_liability_micro_usd"], 0)
        reopened = SharedReasoningBudget(
            self.budget.path, max_cost_micro_usd=100000, max_requests=8
        )
        next_client = self.client(budget=reopened)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.ask(next_client)
        self.assertEqual(next_client._opener.requests, [])
        self.assertEqual(reopened.snapshot(), before)

    def test_http_failure_is_ambiguous_and_does_not_expose_response_or_credentials(
        self,
    ):
        error = urllib.error.HTTPError(
            ENDPOINT, 429, "private-provider-body", {}, io.BytesIO(b"provider-secret")
        )
        client = self.client(error)
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_http_429")
        self.assertTrue(self.budget.snapshot()["blocked"])
        self.assertNotIn("provider-secret", json.dumps(caught.exception.diagnostic))
        self.assertNotIn("private-provider-body", str(caught.exception))

    def test_unknown_usage_stops_future_calls(self):
        client = self.client(packet(usage={}))
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_usage_unknown")
        self.assertTrue(caught.exception.usage_unknown)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.ask(client)
        self.assertEqual(len(client._opener.requests), 1)

    def test_post_request_settlement_failure_preserves_known_usage_and_liability(self):
        client = self.client()
        with (
            mock.patch.object(
                self.budget,
                "settle",
                side_effect=ValueError("fixture accounting overrun"),
            ),
            self.assertRaises(ReasonerError) as caught,
        ):
            self.ask(client)
        error = caught.exception
        self.assertEqual(error.code, "model_cost_accounting_error")
        self.assertFalse(error.usage_unknown)
        self.assertEqual(error.usage["completion_tokens"], 50)
        self.assertEqual(error.usage["reasoning_tokens"], 40)
        self.assertEqual(len(client._opener.requests), 1)
        self.assertTrue(self.budget.snapshot()["blocked"])
        self.assertGreater(self.budget.snapshot()["unresolved_liability_micro_usd"], 0)
        self.assertNotIn("fixture accounting overrun", str(error))
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.ask(client)
        self.assertEqual(len(client._opener.requests), 1)

    def test_input_output_and_total_usage_envelopes_are_enforced(self):
        variations = [
            {"input_tokens": True},
            {"input_tokens": 1_000_000},
            {"output_tokens": 4097},
            {"total_tokens": 81},
            {"output_tokens_details": {"reasoning_tokens": 51}},
        ]
        for index, change in enumerate(variations):
            with self.subTest(change=change):
                usage = json.loads(packet())["usage"]
                usage.update(change)
                budget = SharedReasoningBudget(
                    self.root / f"usage-{index}.sqlite", max_cost_micro_usd=100000
                )
                client = self.client(packet(usage=usage), budget=budget)
                with self.assertRaises(ReasonerError) as caught:
                    self.ask(client)
                self.assertTrue(caught.exception.usage_unknown)
                self.assertTrue(budget.snapshot()["blocked"])

    def test_valid_usage_settles_even_when_reasoning_exhausts_output(self):
        client = self.client(packet(status="incomplete", output=[]))
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_output_incomplete")
        self.assertFalse(caught.exception.usage_unknown)
        self.assertEqual(caught.exception.usage["completion_tokens"], 50)
        self.assertEqual(self.budget.snapshot()["settled_cost_micro_usd"], 130)
        self.assertFalse(self.budget.snapshot()["blocked"])

    def test_unexpected_tool_output_is_rejected_but_usage_is_charged(self):
        client = self.client(
            packet(output=[{"type": "function_call", "arguments": "private"}])
        )
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_protocol_error")
        self.assertFalse(caught.exception.usage_unknown)
        self.assertNotIn("private", json.dumps(caught.exception.diagnostic))
        self.assertEqual(self.budget.snapshot()["settled_cost_micro_usd"], 130)

    def test_response_byte_limit_keeps_unknown_liability(self):
        client = self.client(packet("x" * 2000), max_response_bytes=1024)
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_response_limit")
        self.assertTrue(self.budget.snapshot()["blocked"])

    def test_unpriced_service_tier_keeps_full_liability(self):
        client = self.client(packet(service_tier="priority"))
        with self.assertRaises(ReasonerError) as caught:
            self.ask(client)
        self.assertEqual(caught.exception.code, "model_usage_unknown")
        self.assertTrue(self.budget.snapshot()["blocked"])
        self.assertGreater(self.budget.snapshot()["unresolved_liability_micro_usd"], 0)

    def test_opener_disables_proxy_inheritance_and_redirects(self):
        client = OpenAIReasoner(
            self.key,
            "explicit-model-snapshot",
            reasoning_effort="high",
            budget=self.budget,
            input_micro_usd_per_million_tokens=1_000_000,
            output_micro_usd_per_million_tokens=2_000_000,
        )
        handlers = client._opener.handlers
        self.assertFalse(
            any(isinstance(h, urllib.request.ProxyHandler) for h in handlers)
        )
        redirect = next(
            h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
        )
        self.assertIsNone(
            redirect.redirect_request(
                None, None, 302, "", {}, "https://elsewhere.invalid/"
            )
        )

    def test_input_and_key_checks_happen_before_budget_or_network(self):
        client = self.client(max_input_bytes=1024)
        with self.assertRaises(ValueError):
            client.complete([{"role": "user", "content": "x" * 2000}])
        self.assertEqual(self.budget.snapshot()["requests"], 0)
        self.assertEqual(client._opener.requests, [])
        os.chmod(self.key, 0o644)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            self.ask(client)
        self.assertEqual(self.budget.snapshot()["requests"], 0)
        self.assertEqual(client._opener.requests, [])

    def test_symlink_credentials_and_shared_directory_are_refused(self):
        link = self.root / "fixture-link.key"
        link.symlink_to(self.key.name)
        client = self.client()
        client.key_file = link
        with self.assertRaises(ValueError):
            self.ask(client)
        self.assertEqual(client._opener.requests, [])
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        os.chmod(shared, 0o755)
        with self.assertRaises(ValueError):
            SharedReasoningBudget(shared / "budget.sqlite", max_cost_micro_usd=100)

    def test_cap_is_shared_and_does_not_renew_on_reopening(self):
        budget = SharedReasoningBudget(
            self.root / "small.sqlite", max_cost_micro_usd=2000, max_requests=8
        )
        client = self.client(budget=budget)
        self.ask(client, max_tokens=100)
        self.assertEqual(budget.snapshot()["settled_cost_micro_usd"], 130)
        # A second job with a larger output reservation cannot borrow beyond cap.
        reopened = SharedReasoningBudget(
            budget.path, max_cost_micro_usd=2000, max_requests=8
        )
        second = self.client(budget=reopened)
        with self.assertRaises(ValueError):
            self.ask(second, max_tokens=400)
        self.assertEqual(second._opener.requests, [])
        self.assertEqual(reopened.snapshot()["settled_cost_micro_usd"], 130)
        with self.assertRaisesRegex(ValueError, "cannot be silently changed"):
            SharedReasoningBudget(budget.path, max_cost_micro_usd=5000, max_requests=8)

    def test_interrupted_reservation_and_request_limit_survive_restart(self):
        pending = SharedReasoningBudget(
            self.root / "interrupted.sqlite", max_cost_micro_usd=10000
        )
        pending.reserve("a" * 64, "b" * 64, 1000)
        reopened = SharedReasoningBudget(pending.path, max_cost_micro_usd=10000)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            reopened.reserve("c" * 64, "d" * 64, 1000)
        finite = SharedReasoningBudget(
            self.root / "one.sqlite", max_cost_micro_usd=100000, max_requests=1
        )
        client = self.client(budget=finite)
        self.ask(client)
        with self.assertRaisesRegex(ValueError, "exhausted"):
            self.ask(client)
        self.assertEqual(len(client._opener.requests), 1)

    def test_invalid_configuration_never_constructs_a_network_request(self):
        for options in (
            {"reasoning_effort": "unbounded"},
            {"input_micro_usd_per_million_tokens": 0},
            {"output_micro_usd_per_million_tokens": float("nan")},
            {"budget": None},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.client(**options)


if __name__ == "__main__":
    unittest.main()
