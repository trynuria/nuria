"""Cognitive continuity, causal probes, learning and spending boundaries."""

import base64
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from cognition.brain import AdaptiveBrain, sha
from cognition.fee_observer import observe, rpc
from cognition.journal import Journal, canonical
from cognition.learning import FEATURES, OnlineReadout, benchmark
from cognition.payments import PaymentGate
from cognition.treasury import SpendingPolicy, public_treasury
from cognition.worker import Organism

ROOT = Path(__file__).resolve().parents[1]


def fresh():
    path = ROOT / ".test-state" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def event(i=1, source="test"):
    return {
        "id": f"fixture-{i}",
        "source": source,
        "side": "buy" if i % 2 else "sell",
        "quote_amount": 0.5,
        "creator_fee": 0.001,
    }


class LearningTests(unittest.TestCase):
    def test_prediction_is_evaluated_before_new_outcome_training(self):
        model = OnlineReadout()
        features = np.zeros(FEATURES)
        features[64] = 1
        first = model.observe(event(), features)
        self.assertIsNone(first["feedback"])
        self.assertEqual(model.samples, 0)
        previous = model.pending["probability"]
        second = model.observe(event(2), features)
        self.assertEqual(second["feedback"]["probability"], previous)
        self.assertEqual(model.samples, 1)

    def test_foreign_source_does_not_evaluate_previous_prediction(self):
        model = OnlineReadout()
        x = np.zeros(FEATURES)
        model.observe(event(), x)
        self.assertIsNone(model.observe(event(2, "pump"), x)["feedback"])
        self.assertEqual(model.samples, 0)

    def test_controlled_readout_beats_chance_with_memory(self):
        result = benchmark()
        for task in result["tasks"][:3]:
            self.assertLess(task["brier"], 0.2)
            self.assertGreater(task["memory_ablation_brier"], task["brier"])
            self.assertEqual(task["held_out_samples"], 240)

    def test_nonfinite_features_are_rejected(self):
        x = np.zeros(FEATURES)
        x[0] = np.nan
        with self.assertRaises(ValueError):
            OnlineReadout().probability(x)


class JournalTests(unittest.TestCase):
    def fixture(self):
        journal = Journal(fresh())
        self.addCleanup(journal.db.close)
        spikes = [[0.001, 7]]
        journal.append("decision", {"spikes_sha256": sha(canonical(spikes))}, spikes)
        return journal

    def test_spike_tampering_is_detected(self):
        journal = self.fixture()
        journal.db.execute("UPDATE records SET spikes=x'789c030000000001'")
        self.assertFalse(journal.verify(full=True)["valid"])

    def test_kind_is_bound_to_hash(self):
        journal = self.fixture()
        journal.db.execute("UPDATE records SET kind='payment'")
        self.assertFalse(journal.verify(full=True)["valid"])

    def test_incremental_verification_and_full_audit(self):
        journal = self.fixture()
        self.assertTrue(journal.verify()["valid"])
        journal.append("outcome", {"value": 1})
        self.assertEqual(journal.verify()["checked"], 2)
        journal.db.execute("UPDATE records SET payload='{}' WHERE seq=1")
        self.assertFalse(journal.verify(full=True)["valid"])

    def test_history_without_checkpoint_is_not_reset(self):
        journal = self.fixture()
        journal.db.commit()
        with self.assertRaises(RuntimeError):
            journal.restore()


class NeuralTests(unittest.TestCase):
    def test_checkpoint_and_next_frame_restore_exactly(self):
        directory = fresh()
        organism = Organism(directory, "a" * 64)
        self.addCleanup(organism.journal.db.close)
        for i in range(1, 6):
            organism.cycle([(i, event(i))])
        before = organism.brain.state_hash()
        raw = organism.journal.restore()[1]
        self.assertGreater(len(raw), 1000)
        expected = organism.brain.advance(event(6), duration_ms=20)
        organism.journal.db.close()
        resumed = Organism(directory, "a" * 64)
        self.addCleanup(resumed.journal.db.close)
        self.assertEqual(resumed.brain.state_hash(), before)
        actual = resumed.brain.advance(event(6), duration_ms=20)
        self.assertEqual(actual["spikes"], expected["spikes"])
        self.assertEqual(actual["after_state_sha256"], expected["after_state_sha256"])
        self.assertEqual(resumed.cursor, 5)

    def test_matched_probe_restores_actual_branch_and_changes_state(self):
        brain = AdaptiveBrain(fresh())
        result = brain.counterfactual(event())
        self.assertTrue(result["state_changed_by_input"])
        self.assertEqual(brain.state_hash(), result["actual_state_sha256"])

    def test_reward_changes_eligible_synapses(self):
        brain = AdaptiveBrain(fresh())
        brain.exc.eligibility = 0.25
        result = brain.reward(0.5)
        self.assertGreater(result["changed_synapses"], 0)
        self.assertNotEqual(
            result["before_weights_sha256"], result["after_weights_sha256"]
        )

    def test_frozen_test_synapses_do_not_learn_inside_a_window(self):
        brain = AdaptiveBrain(fresh())
        brain.advance(event(), duration_ms=20)
        brain.exc.plastic_gain = 0
        before = np.asarray(brain.exc.w[:]).copy()
        for i in range(2, 7):
            brain.advance(event(i), duration_ms=20)
        np.testing.assert_array_equal(np.asarray(brain.exc.w[:]), before)

    def test_duplicate_input_is_idempotent_and_changed_input_rejected(self):
        organism = Organism(fresh(), "a" * 64)
        self.addCleanup(organism.journal.db.close)
        organism.perceive(1, event())
        before = organism.brain.state_hash()
        organism.perceive(1, event())
        self.assertEqual(organism.brain.state_hash(), before)
        with self.assertRaises(ValueError):
            organism.perceive(1, dict(event(), quote_amount=4))

    def test_test_training_cannot_change_live_readout(self):
        organism = Organism(fresh(), "a" * 64)
        self.addCleanup(organism.journal.db.close)
        organism.perceive(1, event(1, "pump"))
        baseline = organism.readouts["pump"].weights.copy()
        organism.perceive(2, event(2))
        organism.perceive(3, event(3))
        np.testing.assert_array_equal(organism.readouts["pump"].weights, baseline)
        self.assertEqual(organism.readouts["pump"].samples, 0)
        self.assertEqual(organism.readouts["test"].samples, 1)

    def test_crash_rolls_back_input_and_neural_checkpoint_together(self):
        directory = fresh()
        organism = Organism(directory, "a" * 64)
        self.addCleanup(organism.journal.db.close)
        saved_hash = organism.brain.state_hash()
        organism.cycle([(1, event())], durable=False)
        organism.journal.db.close()
        recovered = Organism(directory, "a" * 64)
        self.addCleanup(recovered.journal.db.close)
        self.assertEqual(recovered.cursor, 0)
        self.assertEqual(recovered.brain.state_hash(), saved_hash)
        self.assertEqual(recovered.memory.summary()["episodes"], 0)


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.recipient = "11111111111111111111111111111111"
        self.policy = SpendingPolicy(1000, 2000, 500, (self.recipient,), True)
        self.intent = {
            "id": "p1",
            "job_id": "job1",
            "asset": "SOL",
            "recipient": self.recipient,
            "lamports": 700,
            "maximum_fee_lamports": 100,
            "expires_at": self.now + 60,
        }
        self.treasury = {"balance_lamports": 3000, "checked_at": self.now}

    def test_defaults_reject_spending(self):
        with self.assertRaises(ValueError):
            SpendingPolicy().validate(self.intent, self.treasury, now=self.now)

    def test_recipient_amount_caps_expiry_and_staleness(self):
        self.policy.validate(self.intent, self.treasury, now=self.now)
        for alteration in (
            {"recipient": "bad"},
            {"lamports": True},
            {"lamports": 1001},
            {"expires_at": self.now - 1},
            {"maximum_fee_lamports": 100001},
        ):
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                self.policy.validate(
                    dict(self.intent, **alteration), self.treasury, now=self.now
                )
        with self.assertRaises(ValueError):
            self.policy.validate(
                self.intent, dict(self.treasury, checked_at=self.now - 61), now=self.now
            )

    def test_duplicate_intent_and_job_fail_closed(self):
        gate = PaymentGate(fresh() / "payments.sqlite3", self.policy)
        gate.reserve(self.intent, self.treasury, self.now)
        for intent in (
            self.intent,
            dict(self.intent, lamports=701),
            dict(self.intent, id="p2"),
        ):
            with self.assertRaises((ValueError, __import__("sqlite3").IntegrityError)):
                gate.reserve(intent, self.treasury, self.now)

    def test_reserved_balance_and_daily_cap_survive_restart(self):
        path = fresh() / "payments.sqlite3"
        PaymentGate(path, self.policy).reserve(self.intent, self.treasury, self.now)
        gate = PaymentGate(path, self.policy)
        gate.reserve(dict(self.intent, id="p2", job_id="job2"), self.treasury, self.now)
        with self.assertRaises(ValueError):
            gate.reserve(
                dict(self.intent, id="p3", job_id="job3"), self.treasury, self.now
            )

    def test_unsigned_message_is_one_system_transfer(self):
        payer = str(Pubkey.from_bytes(bytes([7]) * 32))
        result = PaymentGate.unsigned_transfer(
            self.intent, payer, "11111111111111111111111111111111"
        )
        transaction = Transaction.from_bytes(
            base64.b64decode(result["transaction_base64"])
        )
        self.assertEqual(len(transaction.message.instructions), 1)
        self.assertFalse(result["signed"])
        self.assertFalse(result["broadcast"])

    def test_missing_or_stale_fee_evidence_is_unknown(self):
        self.assertIsNone(public_treasury()["balance_lamports"])
        value = public_treasury(
            {
                "wallet": "configured",
                "checked_at": self.now - 100,
                "balance_lamports": 0,
            }
        )
        self.assertIsNone(value["balance_lamports"])
        self.assertEqual(value["phase"], "unknown")

    def test_fee_balance_is_not_attributed_as_creator_income(self):
        mint = str(Pubkey.from_bytes(bytes([7]) * 32))
        value = observe(
            {"mint": mint, "creator_wallet": self.recipient},
            lambda *_: {"value": 100, "context": {"slot": 42}},
        )
        self.assertEqual(value["balance_lamports"], 100)
        self.assertIsNone(value["verified_fee_receipts"])
        self.assertEqual(value["slot"], 42)

    def test_rpc_error_does_not_expose_provider_url(self):
        with (
            patch.dict("os.environ", {"HELIUS_API_KEY": "secret-fixture"}),
            patch("urllib.request.urlopen", side_effect=RuntimeError("secret-fixture")),
        ):
            with self.assertRaises(RuntimeError) as error:
                rpc("getBalance", [])
        self.assertNotIn("secret-fixture", str(error.exception))
