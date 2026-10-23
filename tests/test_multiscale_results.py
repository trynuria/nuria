"""Check the retained experiment and refusal of inflated or altered evidence."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verify_multiscale_results import ROOT, verify

RESULTS = ROOT / "research/multiscale/results"


@unittest.skipUnless((RESULTS / "result.json").exists(), "Recorded trial not present")
class PublishedMultiscaleResultsTests(unittest.TestCase):
    def altered_report(self, change):
        report = json.loads((RESULTS / "result.json").read_text())
        change(report)
        original = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == RESULTS / "result.json":
                return json.dumps(report)
            return original(path, *args, **kwargs)

        return patch.object(Path, "read_text", read_text)

    def test_recorded_predictions_reconstruct_all_primary_scores(self):
        result = verify(RESULTS)
        self.assertEqual(result["windows"], 9600)
        self.assertEqual(result["scored_forecasts"], 5760)
        self.assertTrue(result["zero_return_matches_current"])

    def test_inflated_score_is_refused(self):
        def inflate(report):
            report["trials"][0]["branches"]["coupled"]["council_brier"] -= 0.01

        with (
            self.altered_report(inflate),
            self.assertRaisesRegex(ValueError, "Published value differs"),
        ):
            verify(RESULTS)

    def test_changed_promotion_decision_is_refused(self):
        def flip(report):
            report["predictive_promotion_gate_passed"] = not report[
                "predictive_promotion_gate_passed"
            ]

        with (
            self.altered_report(flip),
            self.assertRaisesRegex(ValueError, "Promotion decision differs"),
        ):
            verify(RESULTS)

    def test_duplicate_summary_is_refused(self):
        def duplicate(report):
            report["trials"].append(copy.deepcopy(report["trials"][0]))

        with (
            self.altered_report(duplicate),
            self.assertRaisesRegex(ValueError, "Incomplete published summary"),
        ):
            verify(RESULTS)

    def test_changed_trace_is_refused(self):
        original = Path.read_bytes

        def read_bytes(path):
            raw = original(path)
            if path == RESULTS / "traces.jsonl.gz":
                return raw + b"altered"
            return raw

        with (
            patch.object(Path, "read_bytes", read_bytes),
            self.assertRaisesRegex(ValueError, "Compressed trace changed"),
        ):
            verify(RESULTS)
