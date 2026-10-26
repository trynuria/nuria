"""Retained fixtures check tampering and outcome provenance, without inference."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "work_trial_replay", HERE / "verify_work_trials.py"
)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
REPOSITORY = Path.cwd()


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def retained_workspace():
    # Retained by design. The verifier never deletes generated evidence.
    return Path(tempfile.mkdtemp(prefix="nuria-work-trial-test-")) / "retained"


class WorkTrialReplayTests(unittest.TestCase):
    def test_original_artifacts_replay(self):
        result = replay.verify(HERE, REPOSITORY, retained_workspace())
        self.assertTrue(result["passed"])
        self.assertEqual(result["trials"]["semantic"]["recorded_changed_synapses"], 205)
        self.assertEqual(result["trials"]["ordinary"]["completion_tokens"], 4849)
        self.assertEqual(result["trials"]["semantic"]["completion_tokens"], 3499)
        self.assertEqual(result["model_calls"], 0)

    def test_archive_payload_corruption_refused(self):
        original = replay.load

        def altered(path):
            value = original(path)
            if Path(path).name == "manifest.json":
                value["archive"]["sha256"] = "0" * 64
            return value

        with (
            patch.object(replay, "load", side_effect=altered),
            self.assertRaisesRegex(ValueError, "Archive identity"),
        ):
            replay.read_archive(HERE)

    def test_archive_path_traversal_refused(self):
        root = Path(tempfile.mkdtemp(prefix="nuria-work-trial-path-"))
        path = root / "retained-work-trials.zip"
        with zipfile.ZipFile(path, "x") as archive:
            info = zipfile.ZipInfo("../escape")
            info.external_attr = 0o100600 << 16
            archive.writestr(info, b"test")
        raw = path.read_bytes()
        manifest = {
            "schema": "nuria.work-trial-evidence.v1",
            "archive": {
                "path": path.name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "files": {
                "../escape": {"bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Unsafe archive path"):
            replay.read_archive(root)
        self.assertFalse((root.parent / "escape").exists())

    def with_extraction_tamper(self, mutation, message):
        original = replay.materialize

        def changed(records, workspace):
            result = original(records, workspace)
            mutation(result)
            return result

        with (
            patch.object(replay, "materialize", side_effect=changed),
            self.assertRaisesRegex(ValueError, message),
        ):
            replay.verify(HERE, REPOSITORY, retained_workspace())

    def test_fabricated_usage_rejected_even_with_new_wrapper_hashes(self):
        def change(root):
            artifact_path = root / "ordinary/artifact.json"
            artifact = replay.load(artifact_path)
            artifact["model_usage"]["completion_tokens"] += 1
            artifact["model_usage"]["total_tokens"] += 1
            artifact["result_sha256"] = digest(
                {k: v for k, v in artifact.items() if k != "result_sha256"}
            )
            artifact_path.write_text(json.dumps(artifact))
            path = root / "ordinary/result-projection.json"
            projection = replay.load(path)
            projection["record"]["model_usage"] = artifact["model_usage"]
            projection["record"]["artifact_sha256"] = hashlib.sha256(
                artifact_path.read_bytes()
            ).hexdigest()
            path.write_text(json.dumps(projection))

        self.with_extraction_tamper(change, "Agent token accounting")

    def test_neural_reward_cannot_use_another_terminal(self):
        def change(root):
            path = root / "semantic/terminal-check.json"
            value = replay.load(path)
            credit = value["qualification"]["neural_credit"]
            credit["evidence_sha256"] = "0" * 64
            credit["receipt_sha256"] = digest(
                {k: v for k, v in credit.items() if k != "receipt_sha256"}
            )
            path.write_text(json.dumps(value))

        self.with_extraction_tamper(change, "Neural credit is not linked")

    def test_first_tool_substitution_rejected(self):
        def change(root):
            path = root / "semantic/decision.json"
            decision = replay.load(path)
            decision["first_tool"] = "read_knowledge"
            decision["decision_sha256"] = digest(
                {k: v for k, v in decision.items() if k != "decision_sha256"}
            )
            path.write_text(json.dumps(decision))
            path2 = root / "semantic/result-projection.json"
            projection = replay.load(path2)
            projection["record"]["first_tool"] = "read_knowledge"
            projection["record"]["semantic_decision_sha256"] = decision[
                "decision_sha256"
            ]
            projection["record"]["decision_file_sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            path2.write_text(json.dumps(projection))

        self.with_extraction_tamper(change, "Agent inspection treatment")

    def test_semantic_vector_substitution_rejected(self):
        def change(root):
            path = root / "semantic/decision.json"
            decision = replay.load(path)
            decision["semantic_vector"][0] += 0.01
            decision["decision_sha256"] = digest(
                {k: v for k, v in decision.items() if k != "decision_sha256"}
            )
            path.write_text(json.dumps(decision))
            path2 = root / "semantic/result-projection.json"
            projection = replay.load(path2)
            projection["record"]["semantic_decision_sha256"] = decision[
                "decision_sha256"
            ]
            projection["record"]["decision_file_sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            path2.write_text(json.dumps(projection))

        self.with_extraction_tamper(change, "Recorded semantic vector")

    def test_closed_allowance_totals_must_reconcile(self):
        def change(root):
            path = root / "ordinary/terminal-check.json"
            value = replay.load(path)
            value["allowance"]["known_completion_tokens"] += 1
            path.write_text(json.dumps(value))

        self.with_extraction_tamper(change, "Terminal allowance does not conserve")

    def test_work_source_stays_read_only(self):
        replay_module = importlib.import_module("scripts.verify_grounded_comparison")
        path = REPOSITORY / "research/hybrid-agent/work-snapshot/registered/work.sqlite"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        import sqlite3

        with self.assertRaises(sqlite3.Error):
            replay_module.sql_result(path, "DELETE FROM task_outcomes")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
