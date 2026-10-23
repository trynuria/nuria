"""Independent reconstruction and tamper refusal for the memory comparison."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verify_adaptive_memory import ROOT, verify

RESULTS = ROOT / "research/adaptive-memory/validation"


class AdaptiveResultsTests(unittest.TestCase):
    def altered_report(self, change):
        report = json.loads((RESULTS / "result.json").read_text())
        change(report)
        original = Path.read_text

        def read_text(path, *args, **kwargs):
            return (
                json.dumps(report)
                if path == RESULTS / "result.json"
                else original(path, *args, **kwargs)
            )

        return patch.object(Path, "read_text", read_text)

    def test_retained_prior_forecasts_reconstruct_the_complete_comparison(self):
        result = verify(RESULTS)
        self.assertEqual(result["scored_forecasts"], 1204224)
        self.assertEqual(result["trial_streams"], 96)
        self.assertTrue(result["all_predictive_gates_passed"])

    def test_inflated_virtual_reward_is_refused(self):
        def inflate(report):
            report["trials"][0]["scores"]["adaptive"]["net_reward"] += 0.01

        with (
            self.altered_report(inflate),
            self.assertRaisesRegex(ValueError, "Published score differs"),
        ):
            verify(RESULTS)

    def test_changed_promotion_gate_is_refused(self):
        def change(report):
            report["gates"]["control_nonregression_gate"] = False

        with (
            self.altered_report(change),
            self.assertRaisesRegex(ValueError, "Promotion gates disagree"),
        ):
            verify(RESULTS)

    def test_changed_trace_bytes_are_refused_before_scoring(self):
        original = Path.read_bytes

        def read_bytes(path):
            raw = original(path)
            return (
                raw + b"tampered" if path == RESULTS / "delayed_copy.jsonl.gz" else raw
            )

        with (
            patch.object(Path, "read_bytes", read_bytes),
            self.assertRaisesRegex(ValueError, "Compressed trace changed"),
        ):
            verify(RESULTS)


if __name__ == "__main__":
    unittest.main()
