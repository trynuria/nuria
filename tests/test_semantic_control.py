"""Scripted-vector boundaries for an optional conventional comparator.

No encoder, language model, external course or task oracle is used here. These
tests establish implementation behavior, not measured useful-task performance.
"""

import copy
import hashlib
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cognition import semantic_control as control


class SemanticControlTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".test-state"
        self.assertGreaterEqual(shutil.disk_usage(parent.parent).free, 10 * 1024**2)
        self.root = parent / ("semantic-control-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700, parents=True)
        self.encoder = {
            "schema": "explicit-unit-fixture",
            "dimensions": 384,
            "model": "scripted-vectors",
        }

    def tearDown(self):
        # Evidence stays retained; no fixture cleanup or file deletion occurs.
        total = sum(
            path.stat().st_size
            for path in self.root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        self.assertLessEqual(total, 10 * 1024**2)

    def controller(self, name="lab", **changes):
        arguments = {
            "source_id": "fixture-source",
            "model_id": "fixture-reasoner",
            "tool_version": "fixture-tools",
            "encoder_binding": self.encoder,
        }
        arguments.update(changes)
        return control.SemanticLinearControl(self.root / name, **arguments)

    def observation(self, index=0, encoder=None):
        vector = np.zeros(384, dtype="<f4")
        vector[index] = 1
        receipt = {
            "schema": "nuria.semantic-observation.v1",
            "dimensions": 384,
            "binding_sha256": control.digest(
                self.encoder if encoder is None else encoder
            ),
            "text_sha256": hashlib.sha256(
                ("fixture-question-" + str(index)).encode()
            ).hexdigest(),
            "vector_float32_sha256": hashlib.sha256(vector.tobytes()).hexdigest(),
            "fixture": True,
        }
        receipt["receipt_sha256"] = control.digest(receipt)
        return vector.tolist(), receipt

    def choose(self, instance, job="job-a", index=0, **kwargs):
        vector, receipt = self.observation(index)
        return instance.choose(
            job, "1" * 64, vector, receipt, kwargs.get("statistics", {})
        )

    def test_fixed_full_semantic_features_and_metadata_invariance(self):
        left, right = (
            self.controller("left"),
            self.controller(
                "right", source_id="other-source", model_id="other-reasoner"
            ),
        )
        try:
            vector, receipt = self.observation(383)
            first = left.choose("original-job", "1" * 64, vector, receipt, {})
            other = right.choose("different-job", "2" * 64, vector, receipt, {})
            self.assertEqual(first["scores"], other["scores"])
            self.assertEqual(first["feature_sha256"], other["feature_sha256"])
            self.assertEqual(first["first_tool"], other["first_tool"])
            self.assertNotEqual(first["decision_sha256"], other["decision_sha256"])
            features = control._features(
                vector, control._statistics({})[control.TOOLS[0]]
            )
            self.assertEqual(features.shape, (389,))
            self.assertGreater(features[383], 0)
            self.assertEqual(features[0], 0)
            self.assertAlmostEqual(float(np.linalg.norm(features)), 1)
        finally:
            left.close()
            right.close()

    def test_verified_rejection_changes_following_choice(self):
        instance = self.controller()
        try:
            first = self.choose(instance)
            self.assertEqual(first["first_tool"], "list_tables")
            instance.settle("job-a", False, "3" * 64)
            second = self.choose(instance, "job-b")
            self.assertEqual(second["first_tool"], "read_knowledge")
            instance.settle("job-b", True, "4" * 64)
            self.assertEqual(
                self.choose(instance, "job-c")["first_tool"], "read_knowledge"
            )
            self.assertEqual(instance.verify()["settled"], 2)
        finally:
            instance.close()

    def test_delayed_outcome_uses_original_vector_once(self):
        instance = self.controller()
        try:
            self.choose(instance, "origin", 0)
            self.choose(instance, "later", 1)
            terminal = instance.settle("origin", True, "3" * 64)
            model, jobs, _, _ = instance._replay()
            origin = jobs["origin"]
            vector = control._features(
                origin["vector"], origin["statistics"][origin["decision"]["first_tool"]]
            )
            target = model[origin["decision"]["first_tool"]][1]
            np.testing.assert_array_equal(target, vector)
            self.assertNotEqual(target[0], target[1])
            head = instance.verify()
            self.assertEqual(instance.settle("origin", True, "3" * 64), terminal)
            self.assertEqual(instance.verify(), head)
        finally:
            instance.close()

    def test_recovery_and_duplicate_choice_keep_exact_selection(self):
        instance = self.controller()
        original = self.choose(instance)
        instance.settle("job-a", True, "3" * 64)
        head = instance.verify()
        instance.close()
        recovered = self.controller(expected_head=head["head_sha256"])
        try:
            self.assertEqual(self.choose(recovered), original)
            self.assertEqual(recovered.verify(), head)
            self.assertEqual(
                self.choose(recovered, "job-b")["first_tool"], "list_tables"
            )
        finally:
            recovered.close()

    def test_changed_duplicate_and_contradictory_terminal_refuse(self):
        instance = self.controller()
        self.choose(instance)
        with self.assertRaisesRegex(ValueError, "Changed duplicate"):
            self.choose(instance, index=1)
        instance.close()
        instance = self.controller()
        instance.settle("job-a", True, "3" * 64)
        with self.assertRaisesRegex(ValueError, "Contradictory"):
            instance.settle("job-a", False, "3" * 64)
        instance.close()

    def test_receipt_vector_and_statistics_are_checked_before_commit(self):
        instance = self.controller()
        try:
            vector, receipt = self.observation()
            invalid = []
            bad = copy.deepcopy(receipt)
            bad["binding_sha256"] = "4" * 64
            invalid.append((vector, bad, {}))
            bad_vector = vector.copy()
            bad_vector[0] = True
            invalid.append((bad_vector, receipt, {}))
            bad_vector = vector.copy()
            bad_vector[0] = float("nan")
            invalid.append((bad_vector, receipt, {}))
            invalid.append(
                (
                    vector,
                    receipt,
                    {
                        "list_tables": {
                            "observations": 1,
                            "tool_successes": 1,
                            "tool_errors": 1,
                        }
                    },
                )
            )
            for values, observation, statistics in invalid:
                with self.assertRaises(ValueError):
                    instance.choose(
                        "invalid", "1" * 64, values, observation, statistics
                    )
            self.assertEqual(instance.verify()["events"], 0)
        finally:
            instance.close()

    def test_changed_source_encoder_and_retained_head_refuse(self):
        instance = self.controller()
        self.choose(instance)
        instance.close()
        for change in (
            {"source_id": "changed"},
            {"encoder_binding": {**self.encoder, "revision": "changed"}},
            {"expected_head": "0" * 64},
        ):
            with self.assertRaises(ValueError):
                self.controller(**change)

    def test_exclusive_writer_and_private_permissions(self):
        instance = self.controller()
        try:
            with self.assertRaises(BlockingIOError):
                self.controller()
            self.choose(instance)
            self.assertEqual((self.root / "lab").stat().st_mode & 0o777, 0o700)
            for path in (self.root / "lab").iterdir():
                self.assertFalse(path.stat().st_mode & 0o077)
        finally:
            instance.close()
        (self.root / "bad-mode").mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "0700"):
            self.controller("bad-mode")

    def test_atomic_failure_before_commit_leaves_no_decision(self):
        instance = self.controller()
        with patch.object(
            instance,
            "_commit",
            side_effect=RuntimeError("Explicit fixture interruption"),
        ):
            with self.assertRaises(RuntimeError):
                self.choose(instance)
        instance.close()
        recovered = self.controller()
        try:
            self.assertEqual(recovered.verify()["events"], 0)
            self.choose(recovered)
            self.assertEqual(recovered.verify()["events"], 1)
        finally:
            recovered.close()

    def test_atomic_failure_after_commit_recovers_origin_without_relearning(self):
        instance = self.controller()
        original = self.choose(instance)
        commit = instance._commit

        def committed_then_interrupted():
            commit()
            raise RuntimeError("Explicit post-commit fixture interruption")

        with patch.object(instance, "_commit", side_effect=committed_then_interrupted):
            with self.assertRaises(RuntimeError):
                instance.settle("job-a", True, "3" * 64)
        instance.close()
        recovered = self.controller()
        try:
            self.assertEqual(self.choose(recovered), original)
            head = recovered.verify()
            recovered.settle("job-a", True, "3" * 64)
            self.assertEqual(recovered.verify(), head)
            self.assertEqual(head["events"], 2)
        finally:
            recovered.close()

    def test_corrupted_journal_and_mutated_terms_refuse(self):
        instance = self.controller()
        self.choose(instance)
        instance.db.execute("UPDATE control_events SET payload=? WHERE seq=1", ("{}",))
        with self.assertRaises(ValueError):
            instance.verify()
        instance.close()
        with self.assertRaises(ValueError):
            self.controller()
        instance = self.controller("terms")
        try:
            with patch.object(control, "EXPLORATION", 0.8):
                with self.assertRaisesRegex(ValueError, "terms"):
                    self.choose(instance)
        finally:
            instance.close()

    def test_pending_bound_and_unknown_cost_feature(self):
        with patch.multiple(control, MAX_JOBS=3, MAX_PENDING=2):
            instance = self.controller()
            try:
                self.choose(instance, "a")
                self.choose(instance, "b")
                with self.assertRaisesRegex(ValueError, "bound"):
                    self.choose(instance, "c")
            finally:
                instance.close()
        unknown = control._statistics({})["list_tables"]
        self.assertTrue(unknown["cost_unknown"])
        self.assertIsNone(unknown["mean_cost_units"])
        vector, _ = self.observation()
        self.assertGreater(control._features(vector, unknown)[-1], 0)

    def test_unrelated_database_is_not_changed_before_refusal(self):
        directory = self.root / "unrelated"
        directory.mkdir(mode=0o700)
        path = directory / "control.sqlite"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES('preserved')")
        connection.commit()
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "Not a controller"):
            self.controller("unrelated")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0], mode
            )
            self.assertEqual(
                connection.execute("SELECT * FROM unrelated").fetchall(),
                [("preserved",)],
            )
        finally:
            connection.close()

    def test_nonregular_writer_lock_refuses_without_database_creation(self):
        directory = self.root / "fifo-lock"
        directory.mkdir(mode=0o700)
        os.mkfifo(directory / "writer.lock", 0o600)
        with self.assertRaisesRegex(ValueError, "regular"):
            self.controller("fifo-lock")
        self.assertFalse((directory / "control.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
