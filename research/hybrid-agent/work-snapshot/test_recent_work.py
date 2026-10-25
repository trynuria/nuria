"""Boundary fixtures and read-only checks; no model or network calls."""

import math
import unittest
from pathlib import Path

from check_recent_work import check, group_rows


class RecentWorkTests(unittest.TestCase):
    def test_original_projection_matches_predeclared_answer_and_database(self):
        result = check(Path(__file__).resolve().parent)
        self.assertTrue(result["passed"])
        self.assertEqual(result["task_rows"], 40)
        self.assertEqual(result["semantic_acceptance_unknown_rows"], 40)
        self.assertEqual(
            result["expected_rows"],
            [
                ["local-composed-records-v1", 25, 20, 20, 0, 25, 0],
                ["local-record-audit-v1", 15, 14, 14, 0, 15, 0],
            ],
        )

    def test_null_utility_is_unknown_and_zero_is_a_known_nonpositive_value(self):
        rows = [
            {"provider": "p", "outcome": {"utility": None}},
            {"provider": "p", "outcome": {"utility": 0}},
            {"provider": "p", "outcome": {"utility": -0.1}},
        ]
        self.assertEqual(group_rows(rows), [["p", 3, 0, 0, 1, 0, 0]])

    def test_missing_boolean_delivery_is_not_semantic_acceptance(self):
        rows = [
            {"provider": "p", "acceptance": {"passed": True}},
            {"provider": "p", "acceptance": {"passed": False}},
            {"provider": "p", "acceptance": {"passed": None}},
            {"provider": "p", "acceptance": {"passed": 1}},
        ]
        self.assertEqual(group_rows(rows), [["p", 4, 1, 0, 4, 0, 0]])

    def test_positive_utility_excludes_boolean_and_nonfinite_claims(self):
        rows = [
            {"provider": "p", "outcome": {"utility": value}}
            for value in (1, 0.1, True, math.nan, math.inf, None)
        ]
        self.assertEqual(group_rows(rows), [["p", 6, 0, 2, 1, 0, 0]])

    def test_included_capacity_and_positive_integer_amount_are_separate_fields(self):
        rows = [
            {
                "provider": "p",
                "settlement": {"basis": "included_capacity", "total_micro_usd": 0},
            },
            {
                "provider": "p",
                "settlement": {"basis": "reported_transfer", "total_micro_usd": 1},
            },
            {
                "provider": "p",
                "settlement": {"basis": "included_capacity", "total_micro_usd": None},
            },
            {
                "provider": "p",
                "settlement": {"basis": "unknown", "total_micro_usd": True},
            },
            {
                "provider": "p",
                "settlement": {"basis": "unknown", "total_micro_usd": 1.0},
            },
        ]
        self.assertEqual(group_rows(rows), [["p", 5, 0, 0, 5, 2, 1]])


if __name__ == "__main__":
    unittest.main()
