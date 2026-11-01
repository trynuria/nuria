"""Fixture controls for an explicit, limited native-replay qualification."""

import copy
import importlib.util
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


class FilesystemQualificationTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1]
        self.package = source / "research/outcome-learning/reasoning-continuity-09"
        spec = importlib.util.spec_from_file_location(
            "filesystem_qualification_fixture", self.package / "replay-cached.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.records = self.module.replay.read_rows(
            self.package / "executions.jsonl.gz"
        )
        self.workspace = source / ".test-state" / ("filesystem-" + uuid.uuid4().hex)
        self.workspace.mkdir(parents=True, mode=0o700)

    def run_fixture(self, *, label, mixed=False, extra=False):
        physical = []
        mismatches = []
        for item in self.records:
            record = item["record"]
            observed = (
                label if record["task_id"] == "BigCodeBench/832" else record["accepted"]
            )
            row = {
                "condition": item["condition"],
                "task_id": record["task_id"],
                "recorded": record["accepted"],
                "actual": observed,
            }
            physical.append(row)
            if row["actual"] != row["recorded"]:
                mismatches.append(row)
        if extra:
            mismatches.append({"task_id": "different-task", "actual": False})
        (self.workspace / "replay.json").write_text(json.dumps({"programs": physical}))

        class FixtureExecutor:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, code, checks, *, label):
                return {
                    "passed": True
                    if mixed and label.endswith("reference")
                    else expected
                }

        expected = label
        with patch.object(self.module.replay, "IsolatedPython", FixtureExecutor):
            return self.module.qualify_filesystem(
                self.package,
                self.workspace,
                (),
                {"mismatches": copy.deepcopy(mismatches)},
            )

    def test_shared_filesystem_failure_remains_visible_and_cannot_promote(self):
        report = self.run_fixture(label=False)
        qualified = report["filesystem_qualification"]
        self.assertEqual(len(report["mismatches"]), 2)
        self.assertEqual(
            qualified["native_accepted"], {"continuity": 49, "visible": 49}
        )
        self.assertEqual(len(qualified["controls"]), 9)
        self.assertFalse(qualified["exact_reproduction_supported"])
        self.assertFalse(qualified["original_grades_changed"])
        self.assertTrue(qualified["negative_conclusion_robust_to_all_case_labels"])
        self.assertEqual(len(qualified["sensitivity"]), 4)

    def test_reference_disagreement_is_not_waived(self):
        with self.assertRaisesRegex(ValueError, "both programs must agree"):
            self.run_fixture(label=False, mixed=True)

    def test_unregistered_mismatch_is_not_waived(self):
        with self.assertRaisesRegex(ValueError, "different native verdict"):
            self.run_fixture(label=False, extra=True)

    def test_matching_host_preserves_exact_native_agreement(self):
        report = self.run_fixture(label=True)
        self.assertEqual(report["mismatches"], [])
        self.assertTrue(
            report["filesystem_qualification"]["exact_reproduction_supported"]
        )


if __name__ == "__main__":
    unittest.main()
