"""The timing exception cannot approve a model or hide unrelated differences."""

import copy
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts.characterize_timing_contract import qualify_negative


class NegativeTimingQualificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/tmp") / ("nuria-timing-check-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700)
        self.records = [
            {
                "condition": name,
                "record": {"task_id": "BigCodeBench/823", "accepted": False},
            }
            for name in ("astra-medium", "sol-low")
        ]
        self.original = copy.deepcopy(self.records)
        self.physical = {
            "result": {"superiority_supported": False},
            "mismatches": [
                {
                    "condition": "astra-medium",
                    "task_id": "BigCodeBench/823",
                    "recorded": False,
                    "actual": True,
                }
            ],
        }
        self.controls = {
            "rows": [
                {
                    "program": name,
                    "kind": "scripted_clock_calculation",
                    "checker": {"passed": name != "constant-control"},
                }
                for name in ("astra-medium", "sol-low", "reference", "constant-control")
            ]
        }

    def invoke(self, *, possible_winner=False):
        with ExitStack() as stack:
            base = "scripts.characterize_timing_contract."
            stack.enter_context(
                patch(
                    base + "inputs",
                    return_value=(
                        {"task_id": "BigCodeBench/823"},
                        {"task_id": "BigCodeBench/823"},
                        {row["program"]: "fixed" for row in self.controls["rows"]},
                    ),
                )
            )
            stack.enter_context(patch(base + "material", return_value=({}, [], [])))
            stack.enter_context(patch(base + "read_rows", return_value=self.records))
            stack.enter_context(patch(base + "verify", return_value=self.physical))
            stack.enter_context(
                patch(base + "characterize", return_value=self.controls)
            )
            summary = stack.enter_context(
                patch(
                    base + "summarize",
                    return_value=({"superiority_supported": possible_winner}, {}),
                )
            )
            result = qualify_negative(self.root, self.root / "result")
            return result, summary.call_count

    def test_discrepancy_is_exposed_and_all_label_assignments_are_checked(self):
        (self.root / "result").mkdir()
        report, count = self.invoke()
        self.assertEqual(count, 4)
        self.assertEqual(
            report["complete_native_replay"]["mismatches"], self.physical["mismatches"]
        )
        self.assertFalse(report["exact_reproduction_supported"])
        self.assertFalse(report["runtime_promoted"])
        self.assertFalse(report["original_grades_changed"])
        self.assertEqual(self.records, self.original)

    def test_other_native_difference_cannot_be_waived(self):
        self.physical["mismatches"][0]["task_id"] = "BigCodeBench/145"
        with self.assertRaisesRegex(ValueError, "non-timing"):
            self.invoke()

    def test_semantic_control_failure_or_constant_success_refuses(self):
        self.controls["rows"][-1]["checker"]["passed"] = True
        with self.assertRaisesRegex(ValueError, "independent controls"):
            self.invoke()

    def test_uncertain_case_cannot_be_used_to_claim_superiority(self):
        with self.assertRaisesRegex(ValueError, "not robust"):
            self.invoke(possible_winner=True)


if __name__ == "__main__":
    unittest.main()
