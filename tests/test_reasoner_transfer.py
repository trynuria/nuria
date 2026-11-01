"""Public-commitment and frozen-information controls; no model calls."""

import hashlib
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.outcome_worker import OutcomeStore
from scripts.evaluate_reasoner_transfer import grade, proposals, seal


class TransferControls(unittest.TestCase):
    def setUp(self):
        # Keep inspection artifacts; this test never deletes a workspace.
        self.root = Path("/tmp") / ("nuria-transfer-check-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700)
        self.protocol = {"conditions": {"sol-low": {}, "astra-medium": {}}}
        self.tasks = [
            {"task_id": "Example/1", "family": "text", "checker": "assert True"}
        ]
        self.patch = patch(
            "scripts.evaluate_reasoner_transfer.enrolled",
            return_value=(self.protocol, self.tasks, []),
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def commit(self, name):
        store = OutcomeStore(self.root / (name + ".sqlite"))
        store.begin(name, {"condition": name})
        store.finish(
            name,
            {
                "job_id": name,
                "task_id": "Example/1",
                "family": "text",
                "code": "pass",
                "checker": None,
                "accepted": None,
            },
        )

    def test_incomplete_arm_cannot_be_sealed_or_graded(self):
        self.commit("sol-low")
        with self.assertRaisesRegex(ValueError, "Every fresh"):
            proposals(self.root)
        self.assertFalse((self.root / "proposals-before-grade.json").exists())

    def test_seal_is_immutable_and_has_no_terminal_results(self):
        for name in self.protocol["conditions"]:
            self.commit(name)
        result = seal(self.root)
        raw = (self.root / "proposals-before-grade.json").read_bytes()
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        with self.assertRaises(FileExistsError):
            seal(self.root)
        self.assertFalse(json.loads(raw)["terminal_checks_revealed"])

    def test_missing_or_mismatched_public_proof_refuses_before_execution(self):
        for name in self.protocol["conditions"]:
            self.commit(name)
        seal(self.root)
        with patch("scripts.evaluate_reasoner_transfer.runtime") as executor:
            with self.assertRaises(FileNotFoundError):
                grade(self.root)
            (self.root / "public-proposals-proof.json").write_text(
                json.dumps(
                    {
                        "repository": "trynuria/nuria",
                        "commit": "a" * 40,
                        "sha256": "0" * 64,
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "public proposal"):
                grade(self.root)
            executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
