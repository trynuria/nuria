"""Retained historical origin and explicitly declared source-revision fixtures."""

import json
import unittest
import uuid
from pathlib import Path

from scripts.verify_analytical_report_lifecycle import extract_origin, truth, verify


class AnalyticalReportLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]
        self.root = (
            self.repository / ".test-state" / ("report-lifecycle-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700)

    def test_independent_truth_distinguishes_exact_flags_and_nulls(self):
        rows = [
            ["a", "provider", 2, None, "included_capacity", -1],
            ["b", "provider", 1, 0, "other", 1],
        ]
        self.assertEqual(truth(rows), [["provider", 2, 1, 0, 1, 1, 1]])
        self.assertEqual(truth([]), [])

    def test_existing_workspace_is_preserved(self):
        marker = self.root / "marker.txt"
        marker.write_text("retained")
        with self.assertRaisesRegex(ValueError, "new workspace"):
            verify(self.repository, self.root)
        self.assertEqual(marker.read_text(), "retained")

    def test_changed_historical_archive_is_refused(self):
        fake = self.root / "repository/research/hybrid-agent/work-trials"
        fake.mkdir(parents=True)
        (fake / "manifest.json").write_text("{}")
        (fake / "retained-work-trials.zip").write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "evidence identity changed"):
            extract_origin(self.root / "repository", self.root / "origin")
        self.assertFalse((self.root / "origin").exists())

    def test_actual_historical_origin_and_fresh_rows_replay(self):
        result = verify(self.repository, self.root / "actual")
        self.assertTrue(result["passed"])
        self.assertTrue(result["original_evidence_unchanged"])
        self.assertTrue(result["retained_artifact_replay_identical"])
        self.assertEqual(result["replay_workspace_queries"], 0)
        self.assertEqual(result["replay_independent_checks"], 0)
        self.assertEqual(result["model_calls"], 0)
        self.assertIsNone(result["neural_credit"])
        self.assertEqual(len(result["synthetic_cases"]), 2)
        self.assertEqual(result["synthetic_cases"][1]["rows"], [])
        plan = json.loads((self.root / "actual/prospective-plan.json").read_bytes())
        self.assertEqual(plan["model_calls"], 0)
        self.assertIn("deterministic", plan["scope"])


if __name__ == "__main__":
    unittest.main()
