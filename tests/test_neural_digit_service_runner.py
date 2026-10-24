"""Pinned service identity, useful stdin inference and complete resumable courses."""

import copy
import io
import json
import sqlite3
import unittest
import uuid
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cognition.digit_service import DigitService, digest, model_digest
from cognition.spiking_digits import SpikingDigits
from scripts import run_neural_digit_service as runner


class NeuralDigitServiceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(".test-state") / ("digit-runner-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.state = SpikingDigits(seed=202611, width=64, ticks=16).state()
        self.inputs = [
            [(index + shift) % 17 for index in range(64)] for shift in (0, 2, 5)
        ]
        self.checkpoint = {"model": self.state}
        self.identity = {
            "protocol_sha256": "a" * 64,
            "training_manifest_sha256": "b" * 64,
            "checkpoint_file_sha256": "c" * 64,
            "model_id": runner.MODEL_ID,
            "model_sha256": model_digest(self.state),
            "state_sha256": digest(self.state),
            "seed": 202611,
        }

    def fixture(self):
        stack = ExitStack()
        stack.enter_context(patch.object(runner, "ROWS", 3))
        stack.enter_context(patch.object(runner, "STATE_SHA256", digest(self.state)))
        stack.enter_context(
            patch.object(runner, "MODEL_SHA256", model_digest(self.state))
        )
        stack.enter_context(
            patch.object(
                runner, "load_model", return_value=({}, self.checkpoint, self.identity)
            )
        )
        stack.enter_context(
            patch.object(runner, "load_inputs", return_value=copy.deepcopy(self.inputs))
        )
        stack.enter_context(
            patch.object(runner, "load_targets", return_value=[0, 1, 2])
        )
        return stack

    def test_caller_inference_accepts_new_pixels_without_dataset_membership_claim(self):
        directory = self.directory / "caller"
        with self.fixture():
            request = {"request_id": "caller-digit-one", "pixels": self.inputs[0]}
            decision = runner.caller_request("protocol", "models", directory, request)
            self.assertEqual(decision["source_id"], runner.CALLER_SOURCE)
            self.assertEqual(decision["dataset_sha256"], digest(runner.INPUT_CONTRACT))
            self.assertEqual(decision["purpose"], "inference")
            self.assertEqual(decision["prediction"]["state_sha256"], digest(self.state))
            self.assertEqual(
                runner.caller_request("protocol", "models", directory, request),
                decision,
            )
            comparison = runner.caller_request(
                "protocol",
                "models",
                directory,
                {"request_id": request["request_id"], "label": 3},
                compare=True,
            )
            self.assertEqual(comparison["decision_sha256"], decision["sha256"])
            self.assertEqual(
                comparison["state_sha256"], decision["prediction"]["state_sha256"]
            )
            terms = json.loads((directory / "DIGIT-RUNNER.json").read_text())
            self.assertIn("not a dataset", terms["input_identity_basis"])
            self.assertFalse(terms["training_enabled"])
            self.assertFalse(terms["wallet_signing_authority"])

    def test_stdin_shape_does_not_accept_labels_paths_authority_or_training_flags(self):
        with self.fixture():
            for request in (
                {"request_id": "one", "pixels": self.inputs[0], "label": 3},
                {"request_id": "one", "pixels": self.inputs[0], "purpose": "training"},
                {"request_id": "one", "pixels": "/private/input.csv"},
                {"request_id": "one", "pixels": [True] * 64},
                {"request_id": "one", "pixels": [17] * 64},
                {"pixels": self.inputs[0]},
            ):
                with self.subTest(request=request), self.assertRaises(ValueError):
                    runner.caller_request(
                        "protocol", "models", self.directory / "bad", request
                    )
            self.assertFalse((self.directory / "bad").exists())
            with self.assertRaises(ValueError):
                runner.caller_request(
                    "protocol",
                    "models",
                    self.directory / "bad",
                    {"request_id": "one", "label": 3, "verified": True},
                    compare=True,
                )

    def test_full_prediction_prefix_commits_before_any_targets_are_loaded(self):
        directory = self.directory / "course"
        with self.fixture():

            def checked_targets(_archive):
                db = sqlite3.connect(directory / "history/digits.sqlite3")
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 3
                )
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 0
                )
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM training").fetchone()[0], 0
                )
                db.close()
                return [0, 1, 2]

            with patch.object(runner, "load_targets", side_effect=checked_targets):
                output = runner.reproduce("protocol", "archive", "models", directory)
            result = output["receipt"]
            self.assertEqual(result["schema"], "nuria.digit-service-reproduction.v1")
            self.assertEqual(result["rows"], 3)
            self.assertEqual(result["correct"] + result["incorrect"], 3)
            self.assertEqual(result["journal"]["requests"], 3)
            self.assertEqual(result["journal"]["evaluations"], 3)
            self.assertEqual(result["journal"]["training"], 0)
            self.assertEqual(
                result["before_state_sha256"], result["after_state_sha256"]
            )
            self.assertEqual(result["last_prediction_event"]["sequence"], 4)
            self.assertEqual(
                json.loads(Path(output["receipt_path"]).read_text()), result
            )
            self.assertTrue(result["all_predictions_committed_before_target_parsing"])
            self.assertFalse(result["wallet_signing_authority"])
            self.assertEqual(result["financial_transactions"], 0)
            self.assertEqual(result["external_requests"], 0)

    def test_interrupted_prediction_prefix_resumes_without_changing_answers(self):
        directory = self.directory / "interrupted"
        submit = DigitService.submit
        calls = []

        def interrupt(service, request_id, values, **options):
            if len(calls) == 1:
                raise RuntimeError("injected before second decision")
            answer = submit(service, request_id, values, **options)
            calls.append(answer)
            return answer

        with self.fixture():
            with patch.object(
                DigitService, "submit", side_effect=interrupt, autospec=True
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    runner.reproduce("protocol", "archive", "models", directory)
            output = runner.reproduce("protocol", "archive", "models", directory)
            result = output["receipt"]
            self.assertEqual(result["existing_requests_at_start"], 1)
            self.assertEqual(result["existing_comparisons_at_start"], 0)
            duplicate = runner.reproduce("protocol", "archive", "models", directory)[
                "receipt"
            ]
            self.assertEqual(duplicate["existing_requests_at_start"], 3)
            self.assertEqual(duplicate["existing_comparisons_at_start"], 3)
            self.assertEqual(result["journal"], duplicate["journal"])
            self.assertEqual(
                result["prediction_commitment_sha256"],
                duplicate["prediction_commitment_sha256"],
            )
            self.assertEqual(
                result["predicted_labels_sha256"], duplicate["predicted_labels_sha256"]
            )
            db = sqlite3.connect(directory / "history/digits.sqlite3")
            stored = json.loads(
                db.execute(
                    "SELECT decision FROM requests ORDER BY id LIMIT 1"
                ).fetchone()[0]
            )
            db.close()
            self.assertEqual(stored, calls[0])

    def test_foreign_members_or_early_comparisons_cannot_be_adopted_as_reproduction(
        self,
    ):
        with self.fixture():
            for early in (False, True):
                directory = self.directory / ("early" if early else "foreign")
                terms = runner._terms(self.identity, "official_reproduction")
                runner._prepare(directory, terms)
                with runner._service(directory, self.checkpoint, terms) as service:
                    request = "optdigits-test-0000" if early else "foreign-input"
                    service.submit(request, self.inputs[0])
                    if early:
                        service.evaluate(request, 0)
                with self.assertRaisesRegex(ValueError, "prefix|before all"):
                    runner.reproduce("protocol", "archive", "models", directory)

    def test_caller_and_official_services_cannot_share_the_same_history(self):
        with self.fixture():
            directory = self.directory / "distinct"
            runner.caller_request(
                "protocol",
                "models",
                directory,
                {"request_id": "one", "pixels": self.inputs[0]},
            )
            with self.assertRaisesRegex(ValueError, "another mode"):
                runner.reproduce("protocol", "archive", "models", directory)

    def test_incomplete_loaded_inputs_never_reach_target_scoring(self):
        with (
            self.fixture(),
            patch.object(runner, "load_inputs", return_value=self.inputs[:2]),
            patch.object(runner, "load_targets") as targets,
        ):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                runner.reproduce(
                    "protocol", "archive", "models", self.directory / "incomplete"
                )
            targets.assert_not_called()

    def test_actual_registry_chooses_only_the_fixed_first_seed_and_original_archive(
        self,
    ):
        protocol = runner.ROOT / "research/neural-digits/protocol.json"
        models = runner.ROOT / "research/neural-digits/models"
        _, checkpoint, identity = runner.load_model(protocol, models)
        self.assertEqual(identity["seed"], 202611)
        self.assertEqual(identity["model_id"], runner.MODEL_ID)
        self.assertEqual(digest(checkpoint["model"]), runner.STATE_SHA256)
        archive = runner.ROOT / "research/neural-digits/data/optdigits-original.zip"
        inputs = runner.load_inputs(archive)
        self.assertEqual(len(inputs), 1797)
        altered = self.directory / "changed.zip"
        altered.write_bytes(archive.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "original hash-pinned"):
            runner.load_inputs(altered)
        changed_protocol = self.directory / "protocol.json"
        changed_protocol.write_bytes(protocol.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "exact frozen"):
            runner.load_model(changed_protocol, models)

    def test_reviewed_artifact_paths_refuse_escape_and_symlinks(self):
        target = self.directory / "model.json"
        target.write_text("{}")
        (self.directory / "linked.json").symlink_to(target.resolve())
        for name in ("../model.json", str(target.resolve()), "linked.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner._reviewed_path(self.directory, name)

    def test_main_prints_the_committed_decision_and_stdin_is_size_bounded(self):
        request = {"request_id": "stdin-one", "pixels": self.inputs[0]}
        fake_stdin = type(
            "Input", (), {"buffer": io.BytesIO(json.dumps(request).encode())}
        )()
        output = io.StringIO()
        with (
            self.fixture(),
            patch.object(runner.sys, "stdin", fake_stdin),
            redirect_stdout(output),
        ):
            runner.main(
                ["predict", "protocol", "models", str(self.directory / "stdin")]
            )
        decision = json.loads(output.getvalue())
        self.assertEqual(decision["request_id"], request["request_id"])
        self.assertEqual(decision["input_sha256"], digest(request["pixels"]))
        oversized = type(
            "Input", (), {"buffer": io.BytesIO(b" " * (runner.MAX_REQUEST + 1))}
        )()
        with (
            patch.object(runner.sys, "stdin", oversized),
            self.assertRaisesRegex(ValueError, "bounded"),
        ):
            runner.stdin_request()


if __name__ == "__main__":
    unittest.main()
