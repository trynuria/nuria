"""Task causality, payout/delivery separation, shared limits and restart recovery."""

import copy
import hashlib
import json
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from solders.signature import Signature

from cognition.agency import NeuralTaskPolicy
from commerce.action_loop import ActionLoop
from commerce.ledger import Ledger, canonical
from commerce.tasks import contractor_brief, digest


def task(ident="sequence-specialist"):
    return {
        "id": ident,
        "title": "Evaluate a held-out sequence",
        "purpose": "Improve a committed held-out classification task",
        "brief": "Return probabilities for the supplied feature rows using the committed delivery envelope.",
        "kind": "agent",
        "provider": "fixture-agent",
        "action": "experiment",
        "maximum_micro_usd": 2_000_000,
        "delivery_seconds": 3600,
        "evaluation_nonce": "0123456789abcdef" * 4,
        "acceptance": {
            "type": "predictions",
            "dataset_sha256": "a" * 64,
            "targets": [0, 1] * 4,
            "baseline": [0.5] * 8,
            "minimum_gain": 0.01,
        },
    }


def decision(policy, tasks, now, features=None, tick=5):
    intent = policy.select(
        tasks, [0.0] * 64 if features is None else features, "b" * 64, tick, 0
    )
    value = {
        "utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "tick": tick,
        "intent": intent,
        "previous_hash": "0" * 64,
    }
    value["hash"] = hashlib.sha256(
        (
            value["previous_hash"]
            + canonical({k: v for k, v in value.items() if k != "previous_hash"})
        ).encode()
    ).hexdigest()
    return value


class ActionLoopTests(unittest.TestCase):
    def setUp(self):
        root = Path(".test-state") / ("actions-" + uuid.uuid4().hex)
        root.mkdir(parents=True)
        self.path = root / "commerce.sqlite3"
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        self.policy = NeuralTaskPolicy()
        self.task = task()
        self.now = 1792790000.0
        self.decision = decision(self.policy, [self.task], self.now)
        self.ident = self.loop.propose(self.task, self.decision, self.now)
        self.controls = {
            "enabled": True,
            "providers": ["fixture-agent"],
            "lifetime_micro_usd": 25_000_000,
        }
        self.money = SimpleNamespace(
            enabled=True,
            per_job_micro_usdc=2_000_000,
            per_day_micro_usdc=25_000_000,
            reserve_micro_usdc=1_000_000,
            maximum_unresolved_jobs=3,
            cooldown_seconds=60,
            spending_wallet="fixture-account",
        )
        self.quote = {
            "provider": "fixture-agent",
            "account": "fixture-account",
            "offer_id": "fixture-offer",
            "total_micro_usd": 1_000_000,
            "expires_at": self.now + 60,
            "checked_at": self.now,
            "destination": {"wallet": "fixture-recipient"},
            "evidence_sha256": "c" * 64,
            "payment_basis": "exact_usdc",
        }
        self.balance = {
            "account": "fixture-account",
            "available_micro_usd": 25_000_000,
            "checked_at": self.now,
            "verified": True,
        }

    def tearDown(self):
        self.ledger.db.close()

    def reserve(self):
        self.loop.reserve(
            self.ident, self.quote, self.balance, self.money, self.controls, self.now
        )

    def dispatch(self):
        self.reserve()
        self.assertTrue(
            self.loop.operate(
                self.ident,
                "dispatch",
                {"brief": "fixture"},
                lambda *_: {"job_id": self.ident, "external_id": "fixture-order"},
                self.now + 1,
            )
        )

    def deliver(self, values=None):
        raw = canonical(
            {
                "schema": "nuria.task-result.v1",
                "job_id": self.ident,
                "contract_sha256": digest(self.task),
                "result": [0.1, 0.9] * 4 if values is None else values,
            }
        ).encode()
        return self.loop.receive(self.ident, raw, self.now + 2)

    def settle(self):
        self.loop.settle(
            self.ident,
            {
                "job_id": self.ident,
                "account": "fixture-account",
                "external_id": "fixture-order",
                "verified": True,
                "basis": "finalized_chain_transfer",
                "total_micro_usd": 1_000_000,
                "checked_at": self.now + 3,
                "evidence_sha256": "d" * 64,
                "transaction": str(Signature.from_bytes(bytes([1]) * 64)),
                "slot": 100,
                "payer": "fixture-account",
                "recipient_wallet": "fixture-recipient",
            },
            self.now + 3,
        )

    def test_neural_intent_is_required_and_immutable(self):
        changed = copy.deepcopy(self.decision)
        changed["intent"]["goal_id"] = "other"
        with self.assertRaises(ValueError):
            self.loop.propose(self.task, changed, self.now)
        self.ledger.db.execute(
            "UPDATE action_tasks SET contract=? WHERE id=?",
            (canonical({**self.task, "title": "changed"}), self.ident),
        )
        with self.assertRaises(ValueError):
            self.loop.row(self.ident)

    def test_brief_and_public_projection_exclude_private_targets(self):
        brief = contractor_brief(self.task, self.ident)
        self.assertNotIn("targets", brief["acceptance"])
        self.assertNotIn("baseline", brief["acceptance"])
        public = canonical(self.loop.public())
        self.assertNotIn(self.task["brief"], public)
        self.assertNotIn('"targets"', public)

    def test_disabled_policy_and_total_budget_refuse_reservation(self):
        self.money.enabled = False
        with self.assertRaises(ValueError):
            self.reserve()
        self.money.enabled = True
        self.controls["lifetime_micro_usd"] = 999999
        with self.assertRaises(ValueError):
            self.reserve()
        self.assertEqual(self.loop.row(self.ident)["state"], "proposed")

    def test_unknown_balance_expired_quote_and_changed_account_refuse(self):
        for change in (
            {"verified": False},
            {"checked_at": self.now - 31},
            {"account": "other"},
            {"available_micro_usd": None},
        ):
            with self.assertRaises(ValueError):
                self.loop.reserve(
                    self.ident,
                    self.quote,
                    {**self.balance, **change},
                    self.money,
                    self.controls,
                    self.now,
                )
        self.quote["expires_at"] = self.now
        with self.assertRaises(ValueError):
            self.reserve()

    def test_ambiguous_write_survives_restart_and_cannot_repeat(self):
        self.reserve()
        calls = []

        def timeout(*args):
            calls.append(args)
            raise TimeoutError("Fixture disconnected after disclosure")

        self.assertFalse(
            self.loop.operate(
                self.ident, "dispatch", {"brief": "fixture"}, timeout, self.now + 1
            )
        )
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        self.assertEqual(self.loop.row(self.ident)["state"], "uncertain")
        with self.assertRaises(ValueError):
            self.loop.operate(self.ident, "dispatch", {}, timeout, self.now + 2)
        self.assertEqual(len(calls), 1)

    def test_delivery_is_not_payment_and_payment_is_not_usefulness(self):
        self.dispatch()
        self.deliver()
        self.assertIsNone(self.loop.evaluate(self.ident, self.now + 2))
        self.assertEqual(self.loop.feedback(), [])
        self.settle()
        outcome = self.loop.evaluate(self.ident, self.now + 4)
        self.assertAlmostEqual(outcome["reward"], 0.23)
        records = self.loop.feedback()
        self.assertEqual(len(records), 1)
        self.assertTrue(self.policy.learn(records[0]))
        self.assertFalse(self.policy.learn(records[0]))
        restored = NeuralTaskPolicy(self.policy.state())
        self.assertEqual(restored.completed, 1)
        self.assertEqual(restored.pending, {})

    def test_wrong_delivery_and_wrong_payout_never_credit_learning(self):
        self.dispatch()
        with self.assertRaises(ValueError):
            self.loop.receive(
                self.ident,
                canonical(
                    {
                        "schema": "nuria.task-result.v1",
                        "job_id": "wrong",
                        "contract_sha256": digest(self.task),
                        "result": [0.1, 0.9] * 4,
                    }
                ).encode(),
                self.now + 2,
            )
        with self.assertRaises(ValueError):
            self.loop.settle(self.ident, {"job_id": "wrong"}, self.now + 2)
        self.assertEqual(self.loop.feedback(), [])

    def test_paid_missing_delivery_gets_negative_credit_without_inventing_artifact(
        self,
    ):
        self.dispatch()
        self.settle()
        self.assertFalse(self.loop.delivery_deadline(self.ident, self.now + 100))
        self.assertTrue(self.loop.delivery_deadline(self.ident, self.now + 3601))
        outcome = self.loop.evaluate(self.ident, self.now + 3601)
        self.assertEqual(outcome["reward"], -1)
        self.assertIsNone(self.loop.row(self.ident)["acceptance"]["artifact_sha256"])
        self.assertEqual(len(outcome["result_sha256"]), 64)

    def test_unsettled_timeout_does_not_release_a_financial_liability(self):
        self.dispatch()
        self.assertFalse(self.loop.delivery_deadline(self.ident, self.now + 3601))
        self.assertEqual(self.loop.row(self.ident)["amount"], 1_000_000)
        self.assertEqual(self.loop.feedback(), [])

    def test_failed_paid_result_receives_negative_delayed_credit(self):
        self.dispatch()
        self.assertFalse(self.deliver([1, 0] * 4)["passed"])
        self.settle()
        result = self.loop.evaluate(self.ident, self.now + 4)
        self.assertLess(result["reward"], 0)
        self.assertTrue(self.policy.learn(self.loop.feedback()[0]))

    def test_old_merchant_commitment_shares_new_task_daily_ceiling(self):
        day = datetime.fromtimestamp(self.now, timezone.utc).strftime("%Y-%m-%d")
        self.ledger.db.execute(
            "INSERT INTO jobs VALUES('legacy','legacy','fixture','predict',24500000,?,?,'settled','{}',NULL,NULL,NULL)",
            (day, self.now - 120),
        )
        with self.assertRaises(ValueError):
            self.reserve()

    def test_new_task_commitment_blocks_legacy_merchant_overspend(self):
        self.reserve()
        self.money.per_day_micro_usdc = 1500000
        job = {
            "id": "new",
            "decision_hash": "new",
            "provider": "fixture",
            "action": "predict",
            "amount": 1000000,
        }
        with self.assertRaises(ValueError):
            self.ledger.reserve(
                job,
                self.money,
                {"checked_at": self.now + 120, "micro_usdc": 25000000},
                self.now + 120,
            )

    def test_linked_exact_payment_is_counted_once(self):
        self.reserve()
        self.money.per_day_micro_usdc = 1000000
        self.money.maximum_unresolved_jobs = 1
        job = {
            "id": "linked",
            "decision_hash": "linked",
            "provider": "fixture-agent",
            "action": "experiment",
            "amount": 1000000,
            "action_task_id": self.ident,
            "task_contract": self.task,
        }
        self.ledger.reserve(
            job, self.money, {"checked_at": self.now, "micro_usdc": 25000000}, self.now
        )
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1
        )


class NeuralTaskPolicyTests(unittest.TestCase):
    def test_observed_outcomes_teach_context_specific_choices(self):
        policy = NeuralTaskPolicy()
        goals = [task("specialist-a"), task("specialist-b")]
        seq = 0
        # Same available tools and costs. Only the recorded neural context tells
        # which specialist is useful. Fixtures are not external hiring results.
        for step in range(400):
            context = [1.0] * 32 + [0.0] * 32 if step % 2 else [0.0] * 32 + [1.0] * 32
            intent = policy.select(goals, context, "b" * 64, step, 0)
            correct = "specialist-a" if step % 2 else "specialist-b"
            seq += 1
            policy.learn(
                {
                    "seq": seq,
                    "intent_id": intent["id"],
                    "goal_id": intent["goal_id"],
                    "contract_sha256": intent["contract_sha256"],
                    "reward": 1.0 if intent["goal_id"] == correct else -1.0,
                    "result_sha256": "c" * 64,
                    "evidence_level": "verified_outcome",
                }
            )
        state = policy.state()
        picks = []
        for context in ([1.0] * 32 + [0.0] * 32, [0.0] * 32 + [1.0] * 32):
            picks.append(
                NeuralTaskPolicy(state).select(goals, context, "b" * 64, 500, 0)[
                    "goal_id"
                ]
            )
        self.assertEqual(picks, ["specialist-a", "specialist-b"])
        zero = NeuralTaskPolicy(state).select(goals, [0.0] * 64, "b" * 64, 500, 0)[
            "goal_id"
        ]
        self.assertEqual(sum(pick == zero for pick in picks), 1)

    def test_invalid_or_unmatched_feedback_cannot_change_model(self):
        policy = NeuralTaskPolicy()
        intent = policy.select([task()], [0.1] * 64, "b" * 64, 5, 0)
        before = policy.state()
        for change in (
            {"intent_id": "wrong"},
            {"reward": float("nan")},
            {"evidence_level": "payment_only"},
        ):
            record = {
                "seq": 1,
                "intent_id": intent["id"],
                "goal_id": intent["goal_id"],
                "contract_sha256": intent["contract_sha256"],
                "reward": 1.0,
                "result_sha256": "c" * 64,
                "evidence_level": "verified_outcome",
                **change,
            }
            with self.assertRaises(ValueError):
                policy.learn(record)
            self.assertEqual(policy.state(), before)

    def test_checkpoint_roundtrip_preserves_pending_context_exactly(self):
        policy = NeuralTaskPolicy()
        policy.select([task()], [0.1] * 64, "b" * 64, 5, 0)
        state = policy.state()
        restored = NeuralTaskPolicy(json.loads(canonical(state)))
        self.assertEqual(restored.state(), state)
        state["pending"].clear()
        self.assertEqual(len(restored.pending), 1)

    def test_context_rejects_missing_or_fabricated_feature_shape(self):
        for value in ([0] * 63, [float("nan")] * 64, [-1] * 64):
            with self.assertRaises(ValueError):
                NeuralTaskPolicy.context(value)
        self.assertEqual(NeuralTaskPolicy.context(np.zeros(64)).shape, (17,))
