"""A public work view must not convert payments into learning claims."""

import json
import unittest

from commerce.ledger import Ledger, canonical
from commerce.work import snapshot


class WorkProjectionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(":memory:")
        self.addCleanup(self.ledger.db.close)
        self.commerce = {
            "updated_utc": "2026-10-08T16:00:00+00:00",
            "phase": "guarded",
            "financial_execution": False,
            "reserved_micro_usdc": 0,
            "settled_micro_usdc": 0,
            "policy": {"enabled": False, "providers": []},
            "integrity": self.ledger.verify(),
            "ledger_index": {"pages": 0},
        }

    def job(
        self, ident="fixture", state="reserved", delivered=None, paid=None, reward=None
    ):
        terms = {
            "schema": "nuria.forecast.v1",
            "decision_hash": "a" * 64,
            "accepted": {"payTo": "public-fixture-recipient"},
            "private_authorization": "must-not-be-published",
        }
        self.ledger.db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ident,
                ident + "-decision",
                "fixture-provider",
                "predict",
                10000,
                "2026-10-08",
                1,
                state,
                canonical(terms),
                canonical(delivered) if delivered else None,
                canonical(paid) if paid else None,
                reward,
            ),
        )

    def test_empty_projection_retains_unknown_balance_and_gated_integrations(self):
        result = snapshot(self.ledger, self.commerce)
        self.assertEqual(result["total_jobs"], 0)
        self.assertIsNone(result["money"]["balance"])
        self.assertEqual(
            [item["status"] for item in result["catalog"]],
            ["gated", "prepared", "planned", "planned"],
        )

    def test_paid_job_is_not_delivery_or_measured_learning(self):
        self.job(
            state="settled",
            paid={"transaction": "public-fixture-transaction", "slot": 1},
        )
        job = snapshot(self.ledger, self.commerce)["jobs"][0]
        self.assertEqual(job["payment_state"], "settled")
        self.assertEqual(job["job_state"], "awaiting_delivery")
        self.assertEqual(job["outcome_state"], "pending")
        self.assertIsNone(job["reward"])

    def test_disclosed_ambiguous_payment_stays_unresolved(self):
        self.job(state="uncertain")
        job = snapshot(self.ledger, self.commerce)["jobs"][0]
        self.assertEqual(job["payment_state"], "unresolved")
        self.assertNotEqual(job["job_state"], "closed")

    def test_signed_or_private_terms_never_leave_the_projection(self):
        self.job()
        raw = json.dumps(snapshot(self.ledger, self.commerce))
        self.assertNotIn("must-not-be-published", raw)
        self.assertNotIn("private_authorization", raw)

    def test_projection_is_bounded_with_honest_coverage(self):
        for index in range(83):
            self.job(ident=f"fixture-{index:03}")
        result = snapshot(self.ledger, self.commerce)
        self.assertEqual(len(result["jobs"]), 80)
        self.assertEqual(result["total_jobs"], 83)
        self.assertTrue(result["coverage"]["truncated"])
        self.assertEqual(result["jobs"][0]["id"], "fixture-000")
        for invalid in (0, 81, True):
            with self.assertRaises(ValueError):
                snapshot(self.ledger, self.commerce, invalid)

    def test_incomplete_outcome_fails_instead_of_claiming_success(self):
        self.job(state="evaluated", delivered={"sha256": "b" * 64}, reward=1)
        with self.assertRaises(ValueError):
            snapshot(self.ledger, self.commerce)

    def test_measured_reward_including_zero_requires_delivery_and_payment(self):
        self.job(
            state="evaluated",
            delivered={"sha256": "b" * 64, "schema": "nuria.forecast.v1"},
            paid={"transaction": "public-fixture-transaction", "slot": 1},
            reward=0,
        )
        job = snapshot(self.ledger, self.commerce)["jobs"][0]
        self.assertEqual(job["outcome_state"], "measured")
        self.assertEqual(job["reward"], 0)
