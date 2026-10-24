"""A small unit fixture checks a wrapper; no reserved source is accessed here."""

import copy
import json
import unittest

from cognition.text_skills import DEFAULT_TEXT_CONFIG, TextSkillLibrary
from scripts.reanalyze_composed_external import (
    BUDGETS,
    METHODS,
    public_rows,
    run_job,
    summarize,
    verify_records,
)


class ComposedExternalReanalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        cls.initial = TextSkillLibrary("unit:external-reanalysis", cls.config).dump()
        inputs = [
            json.dumps([value])
            for value in ("  Alpha ", " Beta ", " Gamma ", " Delta ")
        ]
        cls.jobs = [
            {
                "id": "Unit.000001",
                "family": "Unit",
                "unsupported": None,
                "diagnostic_inputs": inputs[:2],
                "diagnostic_outputs": ["Alpha", "Beta"],
                "score_inputs": inputs[2:],
                "score_outputs": ["Gamma", "Delta"],
            },
            {
                "id": "Unit.000002",
                "family": "Unit",
                "unsupported": "unit_unsupported_source_type",
                "diagnostic_inputs": [],
                "diagnostic_outputs": [],
                "score_inputs": [],
                "score_outputs": [],
            },
        ]
        cls.records = []
        for budget in BUDGETS:
            states = {method: copy.deepcopy(cls.initial) for method in METHODS}
            for job in cls.jobs:
                for method in METHODS:
                    row, states[method] = run_job(
                        states[method],
                        job,
                        method,
                        budget,
                        cls.config,
                        "unit:external-reanalysis",
                        "unit-wrapper",
                    )
                    cls.records.append(row)

    def test_every_branch_keeps_original_supported_and_unsupported_denominators(self):
        result = verify_records(self.records, self.jobs, self.config, self.initial)
        self.assertTrue(result["verified"])
        self.assertEqual(result["records"], 8)
        totals = summarize(self.records, self.jobs)
        for branches in totals.values():
            for method in branches.values():
                self.assertEqual(method["jobs"], 2)
                self.assertEqual(method["unsupported"], 1)
                self.assertEqual(method["labels"], 2)
                self.assertEqual(method["exact_successes"], 1)

    def test_projection_never_publishes_programs_rows_or_labels(self):
        for row in public_rows(self.records):
            for name in (
                "program",
                "diagnostic_inputs",
                "score_inputs",
                "checked",
                "library_before",
                "library_after",
            ):
                self.assertNotIn(name, row)

    def test_changed_outputs_and_missing_unsupported_job_are_rejected(self):
        records = copy.deepcopy(self.records)
        records[0]["checked"]["outputs"][0] = "altered"
        with self.assertRaisesRegex(ValueError, "Independent external outputs"):
            verify_records(records, self.jobs, self.config, self.initial)
        with self.assertRaisesRegex(ValueError, "Missing failed, unsupported"):
            verify_records(self.records[:-1], self.jobs, self.config, self.initial)

    def test_buyer_acceptance_and_complete_failed_work_are_checked(self):
        records = copy.deepcopy(self.records)
        records[0]["admission"]["accepted"] = False
        records[0]["library_after"]["receipts"][records[0]["proposal_id"]][
            "accepted"
        ] = False
        with self.assertRaisesRegex(ValueError, "Buyer receipt misreports"):
            verify_records(records, self.jobs, self.config, self.initial)
        records = copy.deepcopy(self.records)
        records[0]["total_work"] -= 1
        with self.assertRaisesRegex(ValueError, "Complete job work"):
            verify_records(records, self.jobs, self.config, self.initial)


if __name__ == "__main__":
    unittest.main()
