"""Contractor lifecycle, ambiguous writes and independent outcome checks."""

import copy
import hashlib
import json
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from commerce.commissioning import BASE_USDC, Commissioner, validate_goal
from commerce.ledger import Ledger, canonical
from commerce.marketplaces import OFFER_FIELDS, agent_offer, agent_order


class CommissionTests(unittest.TestCase):
    def setUp(self):
        directory = Path(".test-state") / ("commission-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        self.path = directory / "ledger.sqlite3"
        self.ledger = Ledger(self.path)
        self.controller = Commissioner(self.ledger)
        self.now = 1791475000.0
        self.goal = {
            "id": "retention",
            "kind": "agent",
            "action": "experiment",
            "title": "Evaluate retained information",
            "purpose": "Improve a committed held-out prediction task",
            "brief": "Return structured probabilities for the buyer-provided feature dataset.",
            "schema": "nuria.predictions.v1",
            "evaluation": {
                "dataset_sha256": "a" * 64,
                "targets": [0, 1] * 4,
                "baseline": [0.5] * 8,
                "minimum_improvement": 0.05,
            },
        }
        self.decision = {
            "utc": datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
            "decision": {"action": "experiment"},
            "previous_hash": "0" * 64,
        }
        payload = {k: v for k, v in self.decision.items() if k != "previous_hash"}
        self.decision["hash"] = hashlib.sha256(
            ("0" * 64 + canonical(payload)).encode()
        ).hexdigest()
        self.ident = self.controller.propose(self.goal, self.decision, self.now)
        self.quote = {
            "provider": "1f916",
            "offer_id": "offer-1",
            "offer_sha256": "b" * 64,
            "chain_id": 8453,
            "token": BASE_USDC,
            "amount_micro_usdc": 1000000,
            "fees_micro_usdc": 100000,
            "verifier_micro_usdc": 99000,
            "gas_micro_usdc": 1000,
            "expires_at": self.now + 300,
            "delivery_seconds": 3600,
            "recipient": "0x" + "1" * 40,
        }
        self.controls = {
            "enabled": True,
            "providers": ["1f916"],
            "wallet": "0x" + "2" * 40,
        }
        self.policy = SimpleNamespace(
            enabled=True,
            per_job_micro_usdc=2000000,
            per_day_micro_usdc=25000000,
            reserve_micro_usdc=5000000,
            maximum_unresolved_jobs=3,
            cooldown_seconds=60,
        )
        self.balance = {
            "chain_id": 8453,
            "wallet": self.controls["wallet"],
            "token": BASE_USDC,
            "checked_at": self.now,
            "micro_usdc": 25000000,
            "finalized": True,
            "block": 100,
        }

    def tearDown(self):
        self.ledger.db.close()

    def reserve(self):
        self.controller.reserve(
            self.ident, self.quote, self.controls, self.policy, self.balance, self.now
        )

    def submit(self):
        self.reserve()
        self.controller.dispatch(
            self.ident,
            agent_order,
            lambda intent: {"external_id": "listing-1", "offer_sha256": "b" * 64},
            self.now + 1,
        )

    def deliver(self, predictions):
        raw = json.dumps(
            {
                "schema": "nuria.predictions.v1",
                "commission_id": self.ident,
                "dataset_sha256": "a" * 64,
                "predictions": predictions,
            }
        ).encode()
        self.controller.receive(self.ident, raw, self.now + 2)

    def test_goal_and_decision_bind_proposal_without_spending(self):
        self.assertEqual(
            self.controller.propose(self.goal, self.decision, self.now), self.ident
        )
        self.assertEqual(len(self.controller.public()), 1)
        self.assertEqual(self.controller.public()[0]["committed_micro_usdc"], 0)
        changed = copy.deepcopy(self.decision)
        changed["decision"]["action"] = "compare"
        with self.assertRaises(ValueError):
            self.controller.propose(self.goal, changed, self.now)
        with self.assertRaises(ValueError):
            self.controller.propose(self.goal, self.decision, self.now + 31)

    def test_disabled_policy_cannot_commit(self):
        self.policy.enabled = False
        with self.assertRaises(ValueError):
            self.reserve()
        self.assertEqual(self.controller.public()[0]["state"], "proposed")

    def test_fees_and_verifier_costs_share_the_job_ceiling(self):
        self.quote["fees_micro_usdc"] = 1000001
        with self.assertRaises(ValueError):
            self.reserve()

    def test_stale_or_wrong_chain_balance_cannot_commit(self):
        for changes in (
            {"checked_at": self.now - 31},
            {"chain_id": 1},
            {"micro_usdc": None},
        ):
            balance = {**self.balance, **changes}
            with self.assertRaises(ValueError):
                self.controller.reserve(
                    self.ident,
                    self.quote,
                    self.controls,
                    self.policy,
                    balance,
                    self.now,
                )

    def test_daily_commitment_includes_existing_api_jobs(self):
        self.policy.per_day_micro_usdc = 1200000
        day = datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m-%d")
        self.ledger.db.execute(
            "INSERT INTO jobs(id,decision_hash,provider,action,amount,day,created,status,terms) VALUES('merchant','merchant','fixture','compare',1,?,?,'reserved','{}')",
            (day, self.now),
        )
        with self.assertRaises(ValueError):
            self.reserve()

    def test_ambiguous_order_survives_restart_without_resubmission(self):
        self.reserve()
        sent = []

        def failed(intent):
            sent.append(intent)
            raise TimeoutError("Fixture disconnect after disclosure")

        self.controller.dispatch(self.ident, agent_order, failed, self.now + 1)
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.controller = Commissioner(self.ledger)
        self.assertEqual(self.controller.public()[0]["state"], "uncertain")
        with self.assertRaises(ValueError):
            self.controller.dispatch(self.ident, agent_order, failed, self.now + 2)
        with self.assertRaises(ValueError):
            self.controller.cancel(self.ident, self.now + 2)
        self.assertEqual(len(sent), 1)
        self.controller.confirm(
            self.ident,
            {"external_id": "listing-1", "offer_sha256": "b" * 64},
            self.now + 3,
        )
        self.assertEqual(self.controller.public()[0]["state"], "submitted")

    def test_expired_quote_is_not_disclosed(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.controller.dispatch(
                self.ident,
                agent_order,
                lambda _: self.fail("Unexpected send"),
                self.now + 301,
            )
        self.controller.cancel(self.ident, self.now + 301)
        self.assertEqual(self.controller.public()[0]["committed_micro_usdc"], 0)

    def test_overdue_work_keeps_its_full_liability(self):
        self.submit()
        self.controller.expire(self.now + 3601)
        row = self.controller.public()[0]
        self.assertEqual(row["state"], "overdue")
        self.assertEqual(row["committed_micro_usdc"], 1200000)

    def test_acceptance_recomputes_loss_and_retains_failed_results(self):
        self.submit()
        self.deliver([1, 0] * 4)
        result = self.controller.accept(self.ident, self.now + 3)
        self.assertFalse(result["passed"])
        self.assertLess(result["improvement"], 0)
        self.assertEqual(self.controller.public()[0]["state"], "rejected")

    def test_acceptance_is_not_payment_or_paid_learning(self):
        self.submit()
        self.deliver([0.1, 0.9] * 4)
        self.controller.accept(self.ident, self.now + 3)
        with self.assertRaises(ValueError):
            self.controller.evaluate(self.ident, self.now + 4)
        row = self.controller.public()[0]
        self.assertIsNone(row["settlement"])
        self.assertIsNone(row["outcome"])

    def test_fixture_end_to_end_has_separate_accepted_paid_and_measured_records(self):
        self.submit()
        self.deliver([0.1, 0.9] * 4)
        self.controller.accept(self.ident, self.now + 3)

        def inspector(tx, row):
            return {
                "finalized": True,
                "chain_id": 8453,
                "amount_micro_usdc": 1000000,
                "offer_sha256": "b" * 64,
                "transaction": tx,
                "token": BASE_USDC,
                "sender": self.controls["wallet"],
                "recipient": self.quote["recipient"],
                "block": 101,
            }

        self.controller.settlement(self.ident, "0x" + "f" * 64, inspector, self.now + 4)
        outcome = self.controller.evaluate(self.ident, self.now + 5)
        self.assertAlmostEqual(outcome["reward"], 0.228)
        self.assertEqual(self.controller.public()[0]["state"], "evaluated")
        with self.assertRaises(ValueError):
            self.controller.evaluate(self.ident, self.now + 6)
        self.assertTrue(self.ledger.verify()["valid"])

    def test_public_projection_does_not_disclose_targets_baseline_or_predictions(self):
        self.submit()
        self.deliver([0.1, 0.9] * 4)
        raw = json.dumps(self.controller.public())
        for key in ('"targets"', '"baseline"', '"predictions"', '"brief"'):
            self.assertNotIn(key, raw)
        request = agent_order(self.controller._row(self.ident))
        self.assertNotIn('"targets"', json.dumps(request))
        self.assertNotIn('"baseline"', json.dumps(request))

    def test_wrong_or_executable_delivery_is_refused(self):
        self.submit()
        for body in (
            {"schema": "shell", "command": "echo unsafe"},
            {
                "schema": "nuria.predictions.v1",
                "commission_id": self.ident,
                "dataset_sha256": "c" * 64,
                "predictions": [0.5] * 8,
            },
        ):
            with self.assertRaises(ValueError):
                self.controller.receive(
                    self.ident, json.dumps(body).encode(), self.now + 2
                )

    def payment(self, tx, row):
        return {
            "finalized": True,
            "chain_id": 8453,
            "amount_micro_usdc": 1000000,
            "offer_sha256": "b" * 64,
            "transaction": tx,
            "token": BASE_USDC,
            "sender": self.controls["wallet"],
            "recipient": self.quote["recipient"],
            "block": 101,
            "private_authorization": "not-public",
        }

    def test_changed_private_acceptance_contract_fails_its_public_commitment(self):
        changed = {
            **self.goal,
            "evaluation": {**self.goal["evaluation"], "minimum_improvement": 0},
        }
        self.ledger.db.execute(
            "UPDATE commissions SET contract=? WHERE id=?",
            (canonical(changed), self.ident),
        )
        with self.assertRaises(ValueError):
            self.controller.public()
        with self.assertRaises(ValueError):
            self.reserve()

    def test_same_transaction_cannot_pay_two_commissions(self):
        import sqlite3

        self.submit()
        self.controller.settlement(
            self.ident, "0x" + "f" * 64, self.payment, self.now + 4
        )
        second = "e" * 64
        row = self.controller._row(self.ident)
        self.ledger.db.execute(
            "INSERT INTO commissions(id,goal,kind,decision_hash,created,updated,state,contract,quote,amount,day) VALUES(?,?,?,?,?,?,'submitted',?,?,?,?)",
            (
                second,
                "another",
                "agent",
                "d" * 64,
                self.now + 100,
                self.now + 100,
                canonical(row["contract"]),
                canonical(row["quote"]),
                row["amount"],
                row["day"],
            ),
        )
        self.ledger.event(
            {
                "commission_id": second,
                "commission_state": "proposed",
                "contract_sha256": hashlib.sha256(
                    canonical(row["contract"]).encode()
                ).hexdigest(),
            }
        )
        before = self.ledger.verify()
        with self.assertRaises(sqlite3.IntegrityError):
            self.controller.settlement(
                second, "0x" + "f" * 64, self.payment, self.now + 101
            )
        self.assertEqual(self.ledger.verify(), before)
        self.assertIsNone(self.controller._row(second)["settlement"])

    def test_unknown_recipient_gas_or_finality_is_not_spendable(self):
        for changes in ({"recipient": None}, {"gas_micro_usdc": 0}):
            quote = {**self.quote, **changes}
            with self.assertRaises(ValueError):
                self.controller.reserve(
                    self.ident,
                    quote,
                    self.controls,
                    self.policy,
                    self.balance,
                    self.now,
                )
        for changes in ({"finalized": False}, {"block": None}):
            with self.assertRaises(ValueError):
                self.controller.reserve(
                    self.ident,
                    self.quote,
                    self.controls,
                    self.policy,
                    {**self.balance, **changes},
                    self.now,
                )

    def test_failed_paid_delivery_gets_negative_credit_and_public_receipt_is_whitelisted(
        self,
    ):
        self.submit()
        self.deliver([1, 0] * 4)
        self.controller.accept(self.ident, self.now + 3)
        self.controller.settlement(
            self.ident, "0x" + "f" * 64, self.payment, self.now + 4
        )
        outcome = self.controller.evaluate(self.ident, self.now + 5)
        self.assertFalse(outcome["accepted"])
        self.assertLess(outcome["reward"], 0)
        ranked = self.controller.rank_quotes(
            "retention",
            [self.quote, {**self.quote, "provider": "another", "offer_id": "other"}],
        )
        self.assertEqual(ranked[0]["quote"]["provider"], "another")
        self.assertEqual(self.controller.provider_memory()[0]["evaluated"], 1)
        self.assertNotIn("not-public", json.dumps(self.controller.public()))

    def test_unpaid_and_cancelled_work_cannot_record_settlement(self):
        with self.assertRaises(ValueError):
            self.controller.settlement(
                self.ident, "0x" + "f" * 64, self.payment, self.now
            )
        self.controller.cancel(self.ident, self.now)
        with self.assertRaises(ValueError):
            self.controller.settlement(
                self.ident, "0x" + "f" * 64, self.payment, self.now
            )

    def test_merchant_jobs_respect_contractor_commitments_and_breaker(self):
        self.reserve()
        self.policy.cooldown_seconds = 60
        self.policy.per_day_micro_usdc = 1200000
        with self.assertRaises(ValueError):
            self.ledger.reserve({"amount": 1}, self.policy, self.balance, self.now)
        self.policy.per_day_micro_usdc = 25000000
        self.policy.maximum_unresolved_jobs = 1
        with self.assertRaises(ValueError):
            self.ledger.reserve({"amount": 1}, self.policy, self.balance, self.now)

    def test_invalid_goal_targets_and_credentials_are_refused(self):
        for change in (
            {"brief": "https://example.com/?api_key=secret"},
            {"kind": "unknown"},
        ):
            with self.assertRaises(ValueError):
                validate_goal({**self.goal, **change})


class OfferTests(unittest.TestCase):
    def offer(self):
        raw = {
            "id": "offer-1",
            "version": "1f916.offer.v1",
            "seller": "fixture",
            "title": "Résultat",
            "terms": "Structured work",
            "amount_atomic": "1000000",
            "chain_id": 8453,
            "token": BASE_USDC,
            "asset": "USDC",
            "delivery_window_seconds": 3600,
            "expiry": 1791475300,
            "commit_nonce": "fixture",
            "payload_hash_recipe": {
                "algorithm": "sha256",
                "fields": list(OFFER_FIELDS),
            },
        }
        raw["payload_hash"] = hashlib.sha256(
            json.dumps(
                [raw[k] for k in OFFER_FIELDS],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return raw

    def test_utf8_commitment_and_exact_price_are_preserved(self):
        result = agent_offer(self.offer(), 1791475000)
        self.assertEqual(result["amount_micro_usdc"], 1000000)
        self.assertIsNone(result["recipient"])

    def test_changed_price_recipe_or_chain_is_refused(self):
        for change in (
            {"amount_atomic": "2000000"},
            {"chain_id": 1},
            {"payload_hash_recipe": {"fields": []}},
        ):
            with self.assertRaises(ValueError):
                agent_offer({**self.offer(), **change}, 1791475000)


if __name__ == "__main__":
    unittest.main()
