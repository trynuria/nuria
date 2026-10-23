"""Provider wire fixtures are explicitly separate from funded integration tests."""

import copy
import json
import unittest
from types import SimpleNamespace

import test_action_loop as fixtures

from commerce.rentahuman import RentAHuman
from commerce.task_runtime import X402Tasks, run_actions
from commerce.tasks import digest
from commerce.x402 import delivery


def enrollment():
    fields = {
        "account": ["report", "profileId"],
        "currency": ["report", "currency"],
        "auto_topup": ["controls", "autoTopupEnabled"],
        "per_bounty_cap_cents": ["controls", "spendingCapPerBountyCents"],
        "balance_cents": ["wallet", "balanceCents"],
        "preview_total_cents": ["preview", "fundingTotalCents"],
        "bounty_id": ["bounty", "id"],
        "bounty_status": ["bounty", "status"],
        "funding_total_cents": ["totalCents"],
        "funding_wallet_cents": ["walletBalanceCents"],
        "funding_source": ["fundingSource"],
        "escrow_bounty_id": ["escrow", "bountyId"],
        "escrow_application_id": ["escrow", "applicationId"],
        "worker_payout_status": ["escrow", "workerPayoutStatus"],
        "total_charged_cents": ["escrow", "totalChargedCents"],
        "worker_id": ["escrow", "workerId"],
    }
    return {
        "reviewed": True,
        "gross_debit_cap_verified": True,
        "account": "fixture-account",
        "fields": fields,
        "fields_sha256": digest(fields),
        "worker_price_cents": 100,
    }


class HumanProviderTests(unittest.TestCase):
    def setUp(self):
        self.responses = {
            "/api/wallet/report": {
                "success": True,
                "report": {"profileId": "fixture-account", "currency": "usd"},
            },
            "/api/wallet/balance": {"success": True, "wallet": {"balanceCents": 2500}},
            "/api/wallet/controls": {
                "success": True,
                "controls": {
                    "autoTopupEnabled": False,
                    "spendingCapPerBountyCents": 200,
                },
            },
            "/api/bounties": {
                "success": True,
                "preview": {"fundingTotalCents": 118},
                "bounty": {"id": "fixture-bounty"},
            },
        }
        self.calls = []

        def http(endpoint, headers, *, method, body):
            path = endpoint.removeprefix("https://rentahuman.ai")
            self.calls.append((path, method, json.loads(body) if body else None))
            return 200, {}, json.dumps(self.responses[path]).encode()

        self.adapter = RentAHuman(enrollment(), "fixture-authentication", http)

    def test_unreviewed_account_cannot_be_activated(self):
        with self.assertRaises(ValueError):
            RentAHuman({"reviewed": False}, "fixture")
        changed = enrollment()
        changed["fields"]["balance_cents"] = ["different"]
        with self.assertRaises(ValueError):
            RentAHuman(changed, "fixture")

    def test_balance_requires_bound_account_currency_and_no_auto_topup(self):
        self.assertEqual(self.adapter.balance(100)["available_micro_usd"], 25_000_000)
        self.responses["/api/wallet/controls"]["controls"]["autoTopupEnabled"] = True
        with self.assertRaises(ValueError):
            self.adapter.balance(100)
        self.assertFalse(any(path.endswith("deposit") for path, _, _ in self.calls))

    def test_bounty_quote_reserves_gross_cap_including_fees(self):
        from test_action_loop import task

        contract = task()
        contract.update(kind="human", provider="rentahuman")
        row = {
            "id": "a" * 64,
            "contract": contract,
            "provider": "rentahuman",
            "created": 100,
        }
        quote = self.adapter.quote(row, 101)
        self.assertEqual(quote["total_micro_usd"], 2_000_000)
        self.assertIsNone(quote["destination"]["worker"])
        body = self.calls[0][2]
        self.assertTrue(body["dryRun"])
        self.assertNotIn('"targets"', body["description"])
        self.responses["/api/wallet/controls"]["controls"][
            "spendingCapPerBountyCents"
        ] = 201
        with self.assertRaises(ValueError):
            self.adapter.quote(row, 101)

    def test_changed_funding_total_or_checkout_never_becomes_funded(self):
        self.responses["/api/bounties/fixture-bounty/funding-quote"] = {
            "success": True,
            "totalCents": 201,
            "walletBalanceCents": 2500,
        }
        row = {
            "id": "a" * 64,
            "remote": {"bounty_id": "fixture-bounty"},
            "amount": 2_000_000,
        }
        with self.assertRaises(ValueError):
            self.adapter.fund(row, "fixture-operation")
        self.assertFalse(
            any(path == "/api/bounties/deposit" for path, _, _ in self.calls)
        )

    def test_queued_release_is_not_confirmed_worker_payout(self):
        escrow = {
            "bountyId": "fixture-bounty",
            "applicationId": "fixture-application",
            "workerPayoutStatus": "queued",
            "totalChargedCents": 118,
            "workerId": "fixture-worker",
        }
        self.responses["/api/escrow/fixture-escrow"] = {
            "success": True,
            "escrow": escrow,
        }
        row = {
            "id": "a" * 64,
            "account": "rentahuman:fixture-account",
            "remote": {
                "external_id": "fixture-bounty",
                "bounty_id": "fixture-bounty",
                "application_id": "fixture-application",
                "escrow_id": "fixture-escrow",
            },
        }
        self.assertIsNone(self.adapter.payout(row, 100))
        escrow["workerPayoutStatus"] = "paid"
        evidence = self.adapter.payout(row, 101)
        self.assertEqual(evidence["total_micro_usd"], 1_180_000)
        self.assertEqual(evidence["basis"], "provider_confirmed_payout")
        self.assertIsNone(evidence["recipient_wallet"])
        escrow["applicationId"] = "other"
        self.assertIsNone(self.adapter.payout(row, 102))

    def test_human_adapter_does_not_offer_signup_or_deposit(self):
        for name in ("signup", "deposit", "create_account", "auto_topup"):
            self.assertFalse(hasattr(self.adapter, name))


class TaskRuntimeTests(unittest.TestCase):
    setUp = fixtures.ActionLoopTests.setUp
    tearDown = fixtures.ActionLoopTests.tearDown
    reserve = fixtures.ActionLoopTests.reserve
    dispatch = fixtures.ActionLoopTests.dispatch
    deliver = fixtures.ActionLoopTests.deliver
    settle = fixtures.ActionLoopTests.settle

    def test_missing_human_account_is_an_activation_gate(self):
        result = run_actions(
            self.ledger,
            SimpleNamespace(),
            self.money,
            None,
            "",
            {"planning_enabled": False, "tasks": []},
            {
                "enabled": False,
                "adapters": [
                    {
                        "id": "rentahuman",
                        "type": "rentahuman",
                        "enrollment": {"reviewed": False},
                    }
                ],
            },
            {},
            [],
            self.now,
        )
        self.assertEqual(result["providers"][0]["phase"], "activation_required")
        self.assertFalse(result["execution_enabled"])

    def test_x402_reuses_exact_purchaser_with_origin_contract(self):
        self.reserve()
        captured = []
        executor = SimpleNamespace(
            purchase=lambda *args: captured.append(args) or "fixture-payment"
        )
        provider = SimpleNamespace(schema="nuria.task-result.v1", validate=lambda: None)
        adapter = X402Tasks(executor, provider, self.money, object(), "fixture-rpc")
        response = adapter.dispatch(self.loop.row(self.ident), self.now, "operation")
        selected = captured[0][1]
        self.assertEqual(selected["task"], self.task)
        self.assertEqual(selected["action_task_id"], self.ident)
        self.assertEqual(selected["invoice_sha256"], self.quote["offer_id"])
        self.assertEqual(response["external_id"], "fixture-payment")

    def test_asynchronous_agent_work_polls_without_repaying(self):
        self.reserve()
        self.loop.operate(
            self.ident,
            "dispatch",
            {},
            lambda *_: {"job_id": self.ident, "external_id": "fixture-payment"},
            self.now + 1,
        )
        ack = {
            "schema": "nuria.task-ack.v1",
            "job_id": self.ident,
            "contract_sha256": digest(self.task),
            "status": "queued",
            "result_token": "fixture-result-access-only",
            "expires_at": self.now + 300,
        }
        parsed = delivery(
            json.dumps(ack).encode(),
            None,
            self.now,
            "nuria.task-result.v1",
            task=self.task,
            task_id=self.ident,
        )
        paid = {"slot": 100, "transaction": "fixture-chain-evidence"}
        self.ledger.db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "fixture-payment",
                "fixture-decision",
                "fixture-agent",
                "experiment",
                1_000_000,
                "fixture-day",
                self.now,
                "settled",
                json.dumps(
                    {"schema": "nuria.task-result.v1", "action_task_id": self.ident}
                ),
                json.dumps(parsed),
                json.dumps(paid),
                None,
            ),
        )
        calls = []

        def http(endpoint, headers):
            calls.append((endpoint, headers))
            if len(calls) == 1:
                return 202, {}, b""
            return (
                200,
                {},
                json.dumps(
                    {
                        "schema": "nuria.task-result.v1",
                        "job_id": self.ident,
                        "contract_sha256": digest(self.task),
                        "result": [0.1, 0.9] * 4,
                    }
                ).encode(),
            )

        self.money.mint = None
        executor = SimpleNamespace(ledger=self.ledger, http=http)
        provider = SimpleNamespace(
            schema="nuria.task-result.v1",
            validate=lambda: None,
            endpoint="https://provider.example/tasks",
            recipient="fixture-recipient",
        )
        adapter = X402Tasks(executor, provider, self.money, None, "")
        self.assertIsNone(
            adapter.observe(self.loop.row(self.ident), self.now + 2)["delivery"]
        )
        observed = adapter.observe(self.loop.row(self.ident), self.now + 3)
        self.assertIsInstance(observed["delivery"], bytes)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT status FROM jobs WHERE id='fixture-payment'"
            ).fetchone()[0],
            "delivered",
        )
        self.assertTrue(all("PAYMENT-SIGNATURE" not in headers for _, headers in calls))
        self.assertEqual(
            calls[0][0], "https://provider.example/tasks/results/" + self.ident
        )
        self.assertNotIn(ack["result_token"], json.dumps(self.loop.public()))

    def test_task_acknowledgment_is_not_a_delivery_and_requires_exact_binding(self):
        ack = {
            "schema": "nuria.task-ack.v1",
            "job_id": self.ident,
            "contract_sha256": digest(self.task),
            "status": "queued",
            "result_token": "fixture-result-access-only",
            "expires_at": self.now + 300,
        }
        for change in (
            {"job_id": "wrong"},
            {"expires_at": float("nan")},
            {"result_token": "bad\nheader"},
        ):
            with self.assertRaises(ValueError):
                delivery(
                    json.dumps({**ack, **change}).encode(),
                    None,
                    self.now,
                    "nuria.task-result.v1",
                    task=self.task,
                    task_id=self.ident,
                )

    def test_document_delivery_needs_independent_usefulness_measurement(self):
        contract = copy.deepcopy(self.task)
        contract["id"] = "document-job"
        contract["acceptance"] = {
            "type": "document",
            "required_sections": ["Method", "Evidence"],
            "minimum_characters": 8,
        }
        from test_action_loop import decision

        self.task = contract
        self.policy = __import__(
            "cognition.agency", fromlist=["NeuralTaskPolicy"]
        ).NeuralTaskPolicy()
        self.decision = decision(self.policy, [contract], self.now)
        self.ident = self.loop.propose(contract, self.decision, self.now)
        self.dispatch()
        self.deliver({"text": "Method: measured. Evidence: retained."})
        self.settle()
        self.assertIsNone(self.loop.evaluate(self.ident, self.now + 5))
        self.assertEqual(self.loop.feedback(), [])

    def test_http_outcome_waits_for_a_real_observation_window(self):
        import hashlib

        from test_action_loop import decision

        from cognition.agency import NeuralTaskPolicy

        self.task["outcome"] = {
            "type": "http_availability",
            "endpoint": "https://service.example/health",
            "expected_sha256": hashlib.sha256(b"ready").hexdigest(),
            "samples": 3,
            "interval_seconds": 60,
            "maximum_latency_ms": 1000,
        }
        self.task["id"] = "service-job"
        self.policy = NeuralTaskPolicy()
        self.decision = decision(self.policy, [self.task], self.now)
        self.ident = self.loop.propose(self.task, self.decision, self.now)
        with self.assertRaises(ValueError):
            self.reserve()
        self.loop.probe(
            self.ident, "baseline", self.now, lambda _: (503, {}, b"unavailable")
        )
        self.dispatch()
        self.deliver()
        self.settle()
        self.assertIsNone(self.loop.evaluate(self.ident, self.now + 4))
        for index in range(3):
            self.assertTrue(
                self.loop.probe(
                    self.ident,
                    "after",
                    self.now + 5 + 60 * index,
                    lambda _: (200, {}, b"ready"),
                )
            )
        result = self.loop.evaluate(self.ident, self.now + 126)
        self.assertEqual(result["metric"], "http_availability_gain")
        self.assertAlmostEqual(result["reward"], 0.99)

    def test_cancel_before_disclosure_releases_intent_without_learning(self):
        self.reserve()
        self.loop.cancel_unexecuted(self.ident, self.now + 1)
        self.assertEqual(self.loop.row(self.ident)["amount"], 0)
        self.policy.learn(self.loop.feedback()[0])
        self.assertEqual(self.policy.completed, 0)
        self.assertEqual(self.policy.pending, {})

    def test_cancel_after_disclosure_is_refused(self):
        self.dispatch()
        with self.assertRaises(ValueError):
            self.loop.cancel_unexecuted(self.ident, self.now + 2)
