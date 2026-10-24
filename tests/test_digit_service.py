"""Real neural predictions, immutable labels and atomic local recovery."""

import copy
import hashlib
import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.digit_service import (
    TRAINING_RECEIPT,
    DigitService,
    digest,
    label_commitment,
)
from cognition.spiking_digits import SpikingDigits


def image(offset=0):
    return [(index + offset) % 17 for index in range(64)]


class DigitServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(".test-state") / ("digit-service-" + uuid.uuid4().hex)
        self.initial = SpikingDigits(seed=319, width=12, ticks=4).state()
        self.source = "nuria:digit-service-test"
        self.dataset = "e" * 64

    def service(self, *, training=False, directory=None, **changes):
        arguments = {
            "dataset_sha256": self.dataset,
            "partition": "training" if training else "qualification",
            "training_enabled": training,
            **changes,
        }
        service = DigitService(
            self.directory if directory is None else directory,
            self.source,
            self.initial,
            **arguments,
        )
        self.addCleanup(service.close)
        return service

    def training_job(self, service, request="train-one", *, label=3, values=None):
        values = image() if values is None else values
        nonce = hashlib.sha256(("fixture-only:" + request).encode()).hexdigest()
        committed = label_commitment(self.source, request, digest(values), label, nonce)
        decision = service.submit(
            request, values, purpose="training", committed_label=committed
        )
        receipt = {
            "schema": TRAINING_RECEIPT,
            "source_id": self.source,
            "request_id": request,
            "input_sha256": decision["input_sha256"],
            "decision_sha256": decision["sha256"],
            "label": label,
            "nonce": nonce,
            "verified": True,
        }
        return decision, receipt

    def test_prediction_commits_before_buyer_comparison_without_training(self):
        service = self.service()
        before = digest(service.model.state())
        with self.assertRaisesRegex(ValueError, "must precede"):
            service.evaluate("not-submitted", 3)
        decision = service.submit("qualified-one", image())
        row = service.db.execute("SELECT * FROM requests").fetchone()
        self.assertEqual(row["decision_sha256"], decision["sha256"])
        self.assertEqual(json.loads(row["decision"]), decision)
        self.assertEqual(service.summary()["evaluations"], 0)
        result = service.evaluate("qualified-one", 3)
        self.assertEqual(result["decision_sha256"], decision["sha256"])
        self.assertEqual(result["correct"], decision["prediction"]["label"] == 3)
        self.assertEqual(digest(service.model.state()), before)
        self.assertEqual(service.model.step, 0)
        self.assertEqual(service.summary()["training"], 0)
        self.assertEqual(service.summary()["financial_transactions"], 0)

    def test_qualification_or_inference_request_cannot_become_training(self):
        service = self.service()
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.training_job(service)
        decision = service.submit("qualified-one", image())
        receipt = {
            "schema": TRAINING_RECEIPT,
            "source_id": self.source,
            "request_id": "qualified-one",
            "input_sha256": decision["input_sha256"],
            "decision_sha256": decision["sha256"],
            "label": 3,
            "nonce": "a" * 64,
            "verified": True,
        }
        with self.assertRaisesRegex(ValueError, "cannot train"):
            service.learn(receipt)
        with self.assertRaises(ValueError):
            self.service(
                directory=self.directory.with_name(self.directory.name + "-bad"),
                partition="qualification",
                training_enabled=True,
            )
        training = self.service(
            training=True,
            directory=self.directory.with_name(self.directory.name + "-training"),
        )
        decision = training.submit("inference-only", image())
        receipt.update(
            request_id="inference-only",
            input_sha256=decision["input_sha256"],
            decision_sha256=decision["sha256"],
        )
        with self.assertRaisesRegex(ValueError, "cannot train"):
            training.learn(receipt)

    def test_training_reveals_the_committed_label_and_updates_actual_model(self):
        service = self.service(training=True)
        decision, receipt = self.training_job(service)
        before = service.state_sha256
        comparison = service.evaluate(
            decision["request_id"], receipt["label"], nonce=receipt["nonce"]
        )
        self.assertEqual(service.state_sha256, before)
        result = service.learn(receipt)
        self.assertEqual(result["comparison"], comparison)
        self.assertEqual(result["before_state_sha256"], before)
        self.assertNotEqual(result["after_state_sha256"], before)
        self.assertNotEqual(result["after_model_sha256"], result["before_model_sha256"])
        self.assertEqual(result["decision_sha256"], decision["sha256"])
        self.assertEqual(result["statistics"]["verified_labels"], 1)
        self.assertGreater(sum(result["statistics"]["changed_weights"].values()), 0)
        self.assertEqual(service.model.step, 1)
        self.assertEqual(service.summary()["training"], 1)
        self.assertTrue(service.verify()["valid"])

    def test_exact_training_and_prediction_duplicates_survive_recovery(self):
        service = self.service(training=True)
        decision, receipt = self.training_job(service)
        result = service.learn(receipt)
        committed_state = service.model.state()
        committed_head = service.verify()["head_sha256"]
        service.close()
        restored = self.service(training=True)
        self.assertEqual(restored.model.state(), committed_state)
        self.assertEqual(restored.learn(copy.deepcopy(receipt)), result)
        self.assertEqual(
            restored.submit(
                decision["request_id"],
                image(),
                purpose="training",
                committed_label=decision["label_commitment"],
            ),
            decision,
        )
        self.assertEqual(restored.model.state(), committed_state)
        self.assertEqual(restored.verify()["head_sha256"], committed_head)
        self.assertEqual(restored.summary()["training"], 1)

    def test_changed_training_origin_label_nonce_or_source_is_refused(self):
        service = self.service(training=True)
        _, receipt = self.training_job(service)
        before = service.model.state()
        head = service.verify()["head_sha256"]
        for changes in (
            {"source_id": "nuria:wrong-source"},
            {"input_sha256": "d" * 64},
            {"decision_sha256": "d" * 64},
            {"label": 4},
            {"nonce": "b" * 64},
            {"verified": False},
            {"verified": 1},
            {"request_id": "another-request"},
            {"extra": "unreviewed"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service.learn({**receipt, **changes})
            self.assertEqual(service.model.state(), before)
            self.assertEqual(service.verify()["head_sha256"], head)
        service.learn(receipt)
        learned = service.model.state()
        with self.assertRaises(ValueError):
            service.learn({**receipt, "label": 4})
        self.assertEqual(service.model.state(), learned)

    def test_request_input_purpose_commitment_and_comparison_cannot_change(self):
        service = self.service(training=True)
        decision, receipt = self.training_job(service)
        for changes in (
            {"values": image(1)},
            {"purpose": "inference", "committed_label": None},
            {"committed_label": "f" * 64},
        ):
            arguments = {
                "values": image(),
                "purpose": "training",
                "committed_label": decision["label_commitment"],
                **changes,
            }
            with self.assertRaises(ValueError):
                service.submit(decision["request_id"], **arguments)
        with self.assertRaises(ValueError):
            service.evaluate(decision["request_id"], 4, nonce=receipt["nonce"])
        inference = service.submit("another-image", image(1))
        service.evaluate(inference["request_id"], 2)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            service.evaluate(inference["request_id"], 3)

    def test_commit_failure_restores_weights_optimizer_and_pending_decision(self):
        service = self.service(training=True)
        decision, receipt = self.training_job(service)
        before = service.model.state()
        head = service.verify()["head_sha256"]
        with patch.object(service, "_commit", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                service.learn(receipt)
        self.assertEqual(service.model.state(), before)
        self.assertEqual(service.verify()["head_sha256"], head)
        self.assertEqual(service.summary()["requests"], 1)
        self.assertEqual(service.summary()["training"], 0)
        service.close()
        restored = self.service(training=True)
        self.assertEqual(restored.model.state(), before)
        result = restored.learn(receipt)
        self.assertEqual(result["decision_sha256"], decision["sha256"])
        self.assertEqual(restored.model.step, 1)

    def test_postcommit_response_failure_does_not_repeat_training(self):
        service = self.service(training=True)
        _, receipt = self.training_job(service)
        commit = service._commit

        def commit_then_interrupt():
            commit()
            raise RuntimeError("after durable commit")

        with patch.object(service, "_commit", side_effect=commit_then_interrupt):
            with self.assertRaisesRegex(RuntimeError, "after durable"):
                service.learn(receipt)
        self.assertEqual(service.model.step, 1)
        committed = service.model.state()
        retry = service.learn(receipt)
        self.assertEqual(retry["revision"], 1)
        self.assertEqual(service.model.state(), committed)
        self.assertEqual(service.summary()["training"], 1)

    def test_training_exception_after_mutation_restores_committed_checkpoint(self):
        service = self.service(training=True)
        _, receipt = self.training_job(service)
        before = service.model.state()
        train = service.model.train_batch

        def train_then_interrupt(*args, **kwargs):
            train(*args, **kwargs)
            raise RuntimeError("after in-memory update")

        with patch.object(
            service.model, "train_batch", side_effect=train_then_interrupt
        ):
            with self.assertRaisesRegex(RuntimeError, "in-memory"):
                service.learn(receipt)
        self.assertEqual(service.model.state(), before)
        self.assertEqual(service.summary()["training"], 0)
        self.assertTrue(service.verify()["valid"])

    def test_prediction_commit_failure_and_retry_preserve_one_decision(self):
        service = self.service()
        with patch.object(service, "_commit", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                service.submit("qualified-one", image())
        self.assertEqual(service.summary()["requests"], 0)
        first = service.submit("qualified-one", image())
        self.assertEqual(service.submit("qualified-one", image()), first)
        self.assertEqual(service.summary()["requests"], 1)

    def test_source_dataset_partition_and_original_model_are_bound_on_resume(self):
        service = self.service(training=True)
        self.training_job(service)
        service.close()
        arguments = {
            "dataset_sha256": self.dataset,
            "partition": "training",
            "training_enabled": True,
        }
        for source, changes, model in (
            ("nuria:wrong-source", {}, self.initial),
            (self.source, {"dataset_sha256": "a" * 64}, self.initial),
            (
                self.source,
                {"partition": "qualification", "training_enabled": False},
                self.initial,
            ),
            (self.source, {}, SpikingDigits(seed=320, width=12, ticks=4).state()),
        ):
            with self.assertRaises(ValueError):
                DigitService(self.directory, source, model, **{**arguments, **changes})
        restored = self.service(training=True)
        self.assertEqual(restored.summary()["requests"], 1)

    def test_competing_writer_is_refused_without_interrupting_the_owner(self):
        service = self.service()
        with self.assertRaisesRegex(RuntimeError, "Another writer"):
            self.service()
        self.assertIsNotNone(service.submit("still-owned", image()))

    def test_malformed_pixels_and_unreviewed_model_values_fail_closed(self):
        service = self.service()
        for values in (image()[:-1], [True] * 64, [17] * 64, [1.0] * 64):
            with self.assertRaises(ValueError):
                service.submit("bad-image", values)
        self.assertEqual(service.summary()["requests"], 0)
        for model in ("/some/model.pkl", b"pickle", {"configuration": {}}):
            with self.assertRaises((ValueError, TypeError)):
                DigitService(
                    self.directory.with_name(self.directory.name + "-bad-model"),
                    self.source,
                    model,
                    dataset_sha256=self.dataset,
                )

    def test_saved_prediction_and_receipt_tampering_are_detected(self):
        service = self.service(training=True)
        _, receipt = self.training_job(service)
        service.learn(receipt)
        service.close()
        db = sqlite3.connect(self.directory / "digits.sqlite3")
        original = db.execute("SELECT input_receipt FROM training").fetchone()[0]
        changed = json.loads(original)
        changed["label"] = 9
        db.execute("UPDATE training SET input_receipt=?", (json.dumps(changed),))
        db.commit()
        db.close()
        with self.assertRaises(ValueError):
            self.service(training=True)

    def test_recomputed_request_table_hash_cannot_bypass_journal_binding(self):
        service = self.service()
        decision = service.submit("qualified-one", image())
        service.close()
        changed = copy.deepcopy(decision)
        changed["prediction"]["label"] = (changed["prediction"]["label"] + 1) % 10
        changed["sha256"] = digest(
            {key: value for key, value in changed.items() if key != "sha256"}
        )
        db = sqlite3.connect(self.directory / "digits.sqlite3")
        db.execute(
            "UPDATE requests SET decision=?,decision_sha256=?",
            (json.dumps(changed), changed["sha256"]),
        )
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError, "decision integrity"):
            self.service()

    def test_uncommitted_external_model_change_is_detected_and_refused(self):
        service = self.service()
        original = service.model.state()
        service.model.C[0] += 0.01
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            service.verify()
        with self.assertRaisesRegex(ValueError, "inference changed"):
            service.submit("refused-uncommitted", image())
        self.assertEqual(service.model.state(), original)
        self.assertEqual(service.summary()["requests"], 0)


if __name__ == "__main__":
    unittest.main()
