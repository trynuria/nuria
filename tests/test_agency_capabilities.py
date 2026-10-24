"""Source-scoped value reuse without weakening individual receipt identities."""

import copy
import unittest
import uuid
from pathlib import Path

from test_action_loop import decision, task
from test_skill_tasks import execution_task

from cognition.agency import (
    CAPABILITY_VERSION,
    VERSION,
    NeuralTaskPolicy,
    capability_identity,
)
from commerce.action_loop import ActionLoop
from commerce.ledger import Ledger
from commerce.tasks import digest


def choose(policy, goal, tick=5):
    return policy.select([goal], [0.2] * 64, "b" * 64, tick, 0)


def receipt(intent, seq, *, reward=0.8):
    return {
        "seq": seq,
        "intent_id": intent["id"],
        "goal_id": intent["goal_id"],
        "contract_sha256": intent["contract_sha256"],
        "action": intent["action"],
        "reward": reward,
        "result_sha256": "c" * 64,
        "evidence_level": "verified_outcome",
    }


class AgencyCapabilityTests(unittest.TestCase):
    def test_verified_value_transfers_to_a_new_goal_but_legacy_keys_do_not(self):
        first, second = task("batch-one"), task("batch-two")
        second["evaluation_nonce"] = "d" * 64
        second["acceptance"]["targets"] = [1, 0] * 4
        policy = NeuralTaskPolicy(source_id="nuria:capability-test")
        origin = choose(policy, first)
        self.assertEqual(origin["expected_utility"], 0)
        self.assertTrue(policy.learn(receipt(origin, 1)))
        later = choose(policy, second, 10)
        self.assertNotEqual(origin["contract_sha256"], later["contract_sha256"])
        self.assertEqual(origin["model_key"], later["model_key"])
        self.assertGreater(later["expected_utility"], 0)
        self.assertEqual(len(policy.models), 1)
        self.assertEqual(later["learning_schema"], CAPABILITY_VERSION)

        legacy = NeuralTaskPolicy()
        old = choose(legacy, first)
        legacy.learn(receipt(old, 1))
        new = choose(legacy, second, 10)
        self.assertNotEqual(old["model_key"], new["model_key"])
        self.assertEqual(new["expected_utility"], 0)

    def test_new_goal_names_do_not_exhaust_the_capability_model_bound(self):
        policy = NeuralTaskPolicy(source_id="nuria:bounded-capability")
        for index in range(40):
            intent = choose(policy, task(f"batch-{index}"), 5 * (index + 1))
            self.assertIsNotNone(intent)
            policy.learn(receipt(intent, index + 1))
        self.assertEqual(len(policy.models), 1)
        self.assertEqual(next(iter(policy.models.values()))["n"], 40)
        self.assertEqual(policy.completed, 40)

    def test_provider_action_kind_dataset_and_contract_terms_stay_separate(self):
        base = task("batch-one")
        identity = digest(capability_identity(base, "nuria:source-one"))
        mutations = (
            lambda value: value.update(provider="other-provider"),
            lambda value: value.update(action="compare"),
            lambda value: value.update(kind="human"),
            lambda value: value.update(maximum_micro_usd=3_000_000),
            lambda value: value.update(delivery_seconds=7200),
            lambda value: value["acceptance"].update(dataset_sha256="d" * 64),
            lambda value: value["acceptance"].update(minimum_gain=0.05),
            lambda value: value.update(
                outcome={
                    "type": "http_availability",
                    "endpoint": "https://example.org/source-one",
                    "expected_sha256": "e" * 64,
                    "samples": 3,
                    "interval_seconds": 60,
                    "maximum_latency_ms": 1000,
                }
            ),
        )
        for mutate in mutations:
            changed = copy.deepcopy(base)
            mutate(changed)
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    digest(capability_identity(changed, "nuria:source-one")), identity
                )
        self.assertNotEqual(
            digest(capability_identity(base, "nuria:source-two")), identity
        )

    def test_declared_skill_source_language_and_skill_identity_do_not_mix(self):
        base = execution_task("a" * 64)
        first = capability_identity(base, "nuria:execution-scope")
        other_rows = execution_task("a" * 64, ident="another-job", offset=80)
        self.assertEqual(
            first, capability_identity(other_rows, "nuria:execution-scope")
        )
        for field, value in (
            ("source_id", "nuria:another-data-source"),
            ("skill_id", "b" * 64),
        ):
            changed = copy.deepcopy(base)
            changed["acceptance"][field] = value
            self.assertNotEqual(
                first, capability_identity(changed, "nuria:execution-scope")
            )
        changed = copy.deepcopy(base)
        changed["acceptance"]["config"]["max_programs"] += 1
        self.assertNotEqual(
            first, capability_identity(changed, "nuria:execution-scope")
        )
        self.assertNotIn("inputs", first["acceptance"])
        self.assertNotIn("expected_outputs", first["acceptance"])

    def test_pending_jobs_share_a_model_without_sharing_receipt_authority(self):
        policy = NeuralTaskPolicy(source_id="nuria:receipt-isolation")
        first = choose(policy, task("first"))
        second = choose(policy, task("second"), 10)
        self.assertEqual(first["model_key"], second["model_key"])
        original = policy.state()
        record = receipt(first, 1)
        for change in (
            {"goal_id": second["goal_id"]},
            {"contract_sha256": second["contract_sha256"]},
            {"intent_id": second["id"]},
            {"action": "compare"},
        ):
            with self.assertRaises(ValueError):
                policy.learn({**record, **change})
            self.assertEqual(policy.state(), original)
        self.assertTrue(policy.learn(record))
        self.assertIn(second["id"], policy.pending)
        self.assertTrue(policy.learn(receipt(second, 2, reward=-0.5)))
        self.assertEqual(policy.completed, 2)
        self.assertEqual(next(iter(policy.models.values()))["n"], 2)

    def test_checkpoint_restores_pending_source_and_exact_duplicate_identity(self):
        policy = NeuralTaskPolicy(source_id="nuria:durable-capability")
        intent = choose(policy, task())
        restored = NeuralTaskPolicy(policy.state())
        self.assertEqual(restored.state(), policy.state())
        record = receipt(intent, 7, reward=-0.8)
        self.assertTrue(restored.learn(record))
        restored = NeuralTaskPolicy(
            restored.state(), source_id="nuria:durable-capability"
        )
        before = restored.state()
        self.assertFalse(restored.learn(record))
        self.assertEqual(restored.state(), before)
        with self.assertRaises(ValueError):
            restored.learn({**record, "reward": 0.8})
        self.assertEqual(restored.state(), before)
        later = choose(restored, task("fresh-goal"), 20)
        self.assertLess(later["expected_utility"], 0)

    def test_version_and_source_changes_cannot_coalesce_existing_models(self):
        legacy = NeuralTaskPolicy()
        intent = choose(legacy, task())
        legacy.learn(receipt(intent, 3))
        previous = legacy.state()
        self.assertEqual(previous["version"], VERSION)
        self.assertEqual(NeuralTaskPolicy(previous).state(), previous)
        with self.assertRaisesRegex(ValueError, "silently"):
            NeuralTaskPolicy(previous, source_id="nuria:new-scope")

        fresh = NeuralTaskPolicy(source_id="nuria:source-one")
        choose(fresh, task())
        state = fresh.state()
        with self.assertRaises(ValueError):
            NeuralTaskPolicy(state, source_id="nuria:source-two")
        damaged = copy.deepcopy(state)
        damaged["source_id"] = "nuria:source-two"
        with self.assertRaises(ValueError):
            NeuralTaskPolicy(damaged)
        damaged = copy.deepcopy(state)
        damaged["version"] = VERSION
        with self.assertRaises(ValueError):
            NeuralTaskPolicy(damaged)
        for source in ("", "x" * 129, "invalid source", 2):
            with self.assertRaises(ValueError):
                NeuralTaskPolicy(source_id=source)

    def test_cancelled_work_does_not_transfer_unearned_value(self):
        policy = NeuralTaskPolicy(source_id="nuria:cancelled-capability")
        origin = choose(policy, task())
        record = receipt(origin, 1)
        record.update(evidence_level="not_executed", task_state="cancelled")
        self.assertTrue(policy.learn(record))
        self.assertEqual(policy.completed, 0)
        later = choose(policy, task("next-batch"), 10)
        self.assertEqual(later["expected_utility"], 0)
        self.assertEqual(next(iter(policy.models.values()))["n"], 0)

    def test_capability_registry_and_pending_action_corruption_fail_closed(self):
        policy = NeuralTaskPolicy(source_id="nuria:checkpoint-binding")
        origin = choose(policy, task())
        state = policy.state()
        for changed in ("provider", "source_id", "action", "acceptance"):
            damaged = copy.deepcopy(state)
            identity = damaged["capabilities"][origin["model_key"]]
            identity[changed] = {} if changed == "acceptance" else "other"
            with self.assertRaises(ValueError):
                NeuralTaskPolicy(damaged)
        damaged = copy.deepcopy(state)
        damaged["pending"][origin["id"]]["action"] = "compare"
        with self.assertRaises(ValueError):
            NeuralTaskPolicy(damaged)
        damaged = copy.deepcopy(state)
        damaged["models"][origin["model_key"]]["n"] = 1
        with self.assertRaises(ValueError):
            NeuralTaskPolicy(damaged)

    def test_explicit_capability_mode_preserves_the_commerce_intent_envelope(self):
        directory = Path(".test-state") / ("capability-envelope-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        ledger = Ledger(directory / "commerce.sqlite3")
        self.addCleanup(ledger.db.close)
        policy = NeuralTaskPolicy(source_id="nuria:commerce-envelope")
        goal = task()
        original = decision(policy, [goal], 1792790000.0)
        intent = original["intent"]
        self.assertEqual(intent["schema"], VERSION)
        self.assertEqual(intent["learning_schema"], CAPABILITY_VERSION)
        ident = ActionLoop(ledger).propose(goal, original, 1792790000.0)
        self.assertIsNotNone(ident)
        self.assertEqual(ActionLoop(ledger).row(ident)["intent_id"], intent["id"])


if __name__ == "__main__":
    unittest.main()
