"""Qualification barriers tested without reading any upstream test examples."""

import argparse
import copy
import gzip
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cognition.spiking_digits import SpikingDigits
from scripts import evaluate_neural_digits as evaluation


class QualificationBarrierTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nuria-digit-evaluation-"))

    def test_input_projection_does_not_parse_sealed_target(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "sealed.csv", ",".join(["3"] * 64 + ["SEALED_LABEL"]) + "\n"
            )
        raw = buffer.getvalue()
        path = self.root / "data.zip"
        path.write_bytes(raw)
        protocol = {
            "data": {
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "test_member": "sealed.csv",
            }
        }
        with patch.object(evaluation, "ROWS", 1):
            self.assertEqual(evaluation.dataset(path, protocol), [[3] * 64])
            with self.assertRaises(ValueError):
                evaluation.dataset(path, protocol, labels=True)

    def test_changed_source_stops_before_dataset_or_publication(self):
        path = self.root / "protocol.json"
        path.write_text("{}")
        args = argparse.Namespace(
            protocol=path,
            archive=None,
            models=None,
            output=self.root / "run",
            freeze_commit="a" * 40,
        )
        with (
            patch.object(evaluation, "source_check", side_effect=ValueError("changed")),
            patch.object(evaluation, "dataset") as read,
            patch.object(evaluation, "freeze_check") as publish,
        ):
            with self.assertRaisesRegex(ValueError, "changed"):
                evaluation.predict(args)
            read.assert_not_called()
            publish.assert_not_called()
        self.assertFalse(args.output.exists())

    def test_neural_inference_does_not_change_optimizer_or_weights(self):
        model = SpikingDigits()
        state = model.state()
        item = {
            "id": "fixed",
            "kind": "snn",
            "method": "snn_trained",
            "seed": 202611,
            "model_sha256": model.model_hash(),
            "state_sha256": model.state_hash(),
        }
        pixels = [[value] * 64 for value in (0, 4, 8, 16)]
        result = evaluation.infer(item, {"model": state}, pixels)
        self.assertEqual(result["state_before_sha256"], result["state_after_sha256"])
        self.assertEqual(
            result["labels"],
            np.argmax(model.forward(np.asarray(pixels) / 16)[0], axis=1).tolist(),
        )
        self.assertEqual(model.state(), state)

    def test_source_and_model_identity_changes_are_refused(self):
        model = SpikingDigits()
        item = {
            "id": "fixed",
            "kind": "snn",
            "method": "snn_trained",
            "seed": 202611,
            "model_sha256": "0" * 64,
            "state_sha256": model.state_hash(),
        }
        with self.assertRaisesRegex(ValueError, "freeze"):
            evaluation.infer(item, {"model": model.state()}, [[8] * 64])

    def test_evidence_write_never_replaces_previous_attempt(self):
        path = self.root / "receipt.json"
        evaluation.write_new(path, {"first": True})
        with self.assertRaises(FileExistsError):
            evaluation.write_new(path, {"replacement": True})
        self.assertEqual(json.loads(path.read_text()), {"first": True})

    def test_malformed_commitments_stop_before_label_projection(self):
        identity = {"commit": "a" * 40, "protocol_sha256": "p"}
        models = [{"id": "m" + str(i)} for i in range(11)]
        prediction = {
            "schema": "nuria.neural-digit-predictions.v1",
            "rows": 1797,
            "models": models,
            "public_freeze": identity,
            "protocol_sha256": "p",
            "test_learning_enabled": False,
        }
        commitment = {
            "schema": "nuria.neural-digit-prediction-commit.v1",
            "rows": 1797,
            "models": 11,
            "public_freeze": identity,
            "protocol_sha256": "p",
            "predictions_sha256": "h",
            "scoring_labels_read": False,
        }
        frozen = {item["id"]: (item, {}) for item in models}
        evaluation.validate_commitment("p", prediction, "h", commitment, frozen)
        changes = [
            ("rows", 1796),
            ("models", 10),
            ("scoring_labels_read", True),
            ("schema", "other"),
            ("public_freeze", {"commit": "b" * 40, "protocol_sha256": "p"}),
        ]
        for key, value in changes:
            altered = copy.deepcopy(commitment)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                evaluation.validate_commitment("p", prediction, "h", altered, frozen)
        args = argparse.Namespace(
            protocol="protocol",
            models="models",
            output=self.root,
            freeze_commit="a" * 40,
        )
        invalid = copy.deepcopy(commitment)
        invalid["scoring_labels_read"] = True
        with (
            patch.object(
                evaluation,
                "read_json",
                side_effect=[({}, "p"), (prediction, "h"), (invalid, "x")],
            ),
            patch.object(evaluation, "source_check"),
            patch.object(
                evaluation,
                "model_files",
                return_value=[(item, {}, 0) for item in models],
            ),
            patch.object(evaluation, "dataset") as labels,
        ):
            with self.assertRaises(ValueError):
                evaluation.score(args)
            labels.assert_not_called()

    def test_compressed_artifact_keeps_raw_identity_and_size_bound(self):
        raw = b'{"a": "' + b"x" * 256 + b'"}'
        path = self.root / "artifact.json.gz"
        path.write_bytes(gzip.compress(raw))
        value, identity = evaluation.read_json(self.root / "artifact.json")
        self.assertEqual(identity, hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(value["a"]), 256)
        with self.assertRaises(ValueError):
            evaluation.read_json(path, maximum=32)

    def test_prediction_metadata_and_label_types_are_bound(self):
        item = {
            "id": "m",
            "kind": "softmax",
            "method": "linear",
            "seed": None,
            "model_sha256": "m",
            "state_sha256": "s",
        }
        row = {
            **item,
            "state_before_sha256": "s",
            "state_after_sha256": "s",
            "checkpoint_file_bytes": 20,
            "inference_cpu_seconds": 0.1,
            "labels": [0] * 1797,
        }
        evaluation.prediction_identity(row, item, 20)
        for key, value in (
            ("method", "other"),
            ("seed", 1),
            ("state_after_sha256", "x"),
            ("checkpoint_file_bytes", 21),
            ("inference_cpu_seconds", -1),
            ("inference_cpu_seconds", float("nan")),
            ("labels", [False] * 1797),
        ):
            changed = copy.deepcopy(row)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                evaluation.prediction_identity(changed, item, 20)

    def test_score_retains_all_controls_and_uses_matched_seed_gates(self):
        pairs = [("linear", None), ("knn5", None)] + [
            (method, seed)
            for method in ("snn_trained", "snn_input_frozen", "continuous")
            for seed in (1, 2, 3)
        ]
        registry, outputs = [], []
        for method, seed in pairs:
            ident = method + str(seed)
            kind = "snn" if method.startswith("snn") else "softmax"
            item = {
                "id": ident,
                "kind": kind,
                "method": method,
                "seed": seed,
                "model_sha256": "m",
                "state_sha256": "s",
            }
            checkpoint = {
                "weights": {"C": [0] * 10},
                "model": {"configuration": {"seed": seed}},
            }
            registry.append((item, checkpoint, 20))
            label = 1 if method in ("linear", "snn_input_frozen") else 0
            outputs.append(
                {
                    **item,
                    "labels": [label],
                    "logits": [[0] * 10],
                    "spike_counts": [[0] * 64],
                    "state_before_sha256": "s",
                    "state_after_sha256": "s",
                    "checkpoint_file_bytes": 20,
                    "inference_cpu_seconds": 0.1,
                }
            )
        public = {"commit": "a" * 40, "protocol_sha256": "p"}
        pixels = [[0] * 64]
        prediction = {
            "schema": "nuria.neural-digit-predictions.v1",
            "rows": 1,
            "models": outputs,
            "public_freeze": public,
            "protocol_sha256": "p",
            "test_learning_enabled": False,
            "inputs_sha256": evaluation.digest(pixels),
        }
        commitment = {
            "schema": "nuria.neural-digit-prediction-commit.v1",
            "rows": 1,
            "models": 11,
            "public_freeze": public,
            "protocol_sha256": "p",
            "predictions_sha256": "h",
            "scoring_labels_read": False,
        }
        protocol = {
            "gates": {"minimum_accuracy_gain": 0.01},
            "scope": "synthetic fixture",
        }
        args = argparse.Namespace(
            protocol="p",
            models="m",
            output=self.root,
            archive="a",
            freeze_commit="a" * 40,
        )
        by_id = {item["id"]: item for item in outputs}

        def replay(item, checkpoint, value):
            return by_id[item["id"]]

        def data(path, specification, *, labels=False):
            return [0] if labels else pixels

        with (
            patch.object(evaluation, "ROWS", 1),
            patch.object(
                evaluation,
                "read_json",
                side_effect=[(protocol, "p"), (prediction, "h"), (commitment, "c")],
            ),
            patch.object(evaluation, "source_check"),
            patch.object(evaluation, "model_files", return_value=registry),
            patch.object(evaluation, "freeze_check"),
            patch.object(evaluation, "dataset", side_effect=data),
            patch.object(
                evaluation,
                "verify_model_predictions",
                side_effect=lambda *args: {"total_hidden_spikes": 0},
            ),
            patch.object(evaluation, "brian_parity", return_value={"verified": True}),
            patch.object(
                evaluation, "independent_predict", return_value={"labels": [0]}
            ),
            patch.object(evaluation, "infer", side_effect=replay),
        ):
            result = evaluation.score(args)
        self.assertTrue(all(result["gates"].values()))
        evidence = json.loads((self.root / "result.json").read_text())
        self.assertEqual(len(evidence["models"]), 11)
        self.assertEqual(evidence["linear_accuracy"], 0)
        self.assertEqual(evidence["neural_mean_accuracy"], 1)
        self.assertTrue(
            all(
                evidence["paired"][item]["knn5"]["first_only_correct"] == 0
                for item in evidence["paired"]
            )
        )


if __name__ == "__main__":
    unittest.main()
