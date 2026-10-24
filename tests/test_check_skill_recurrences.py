"""Recurrence plans bind declared semantics and fresh observations, not positions."""

import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from scripts.check_skill_recurrences import (
    MAX_BYTES,
    MAX_CHECKS,
    check_recurrences,
    load_corpus,
    parse_pairs,
)


def entry(start, phase, recipe=None, format_index=0):
    return {
        "phase": phase,
        "format_index": format_index,
        "recipe": {"operation": "add", "columns": [0, 1]}
        if recipe is None
        else copy.deepcopy(recipe),
        "row_ids": list(range(start, start + 3)),
        "inputs": [
            json.dumps([str(row), str(row + 2), "0.2", "0.8"])
            for row in range(start, start + 3)
        ],
        "outputs": ["buyer-private-output"] * 3,
    }


def corpus():
    return {
        "schema": "bounded-fixture",
        "source": {"description": "Unit fixture, not observed data"},
        "tasks": [
            entry(1, "unseen"),
            entry(10, "serve"),
            entry(20, "serve"),
        ],
    }


class RecurrencePlanTests(unittest.TestCase):
    def test_explicit_recurrences_are_fresh_and_projection_is_hashes_and_counts(self):
        source = corpus()
        before = copy.deepcopy(source)
        result = check_recurrences(source, [(0, 1), (0, 2)])
        self.assertEqual(source, before)
        self.assertTrue(result["passed"])
        self.assertEqual(result["counts"]["pairs"], 2)
        self.assertEqual(result["counts"]["acquisition_entries"], 1)
        self.assertEqual(result["counts"]["selected_entries"], 3)
        self.assertEqual(result["counts"]["selected_rows"], 9)
        self.assertEqual(result["counts"]["recipe_groups"], 1)
        self.assertEqual(result["pairs"][0]["recurrence_position"], 1)
        serialized = json.dumps(result)
        for forbidden in (
            "buyer-private-output",
            "columns",
            "operation",
            '"row_ids"',
            '"inputs"',
            '"outputs"',
            '"format_index"',
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(len(result["corpus_canonical_sha256"]), 64)

    def test_serve_phase_and_matching_format_id_do_not_prove_recipe_recurrence(self):
        source = corpus()
        source["tasks"][1]["recipe"]["operation"] = "multiply"
        with self.assertRaisesRegex(ValueError, "recipes differ"):
            check_recurrences(source, [(0, 1)])

    def test_equal_recipe_with_changed_format_id_is_valid(self):
        source = corpus()
        source["tasks"][1]["format_index"] = 917
        self.assertTrue(check_recurrences(source, [(0, 1)])["passed"])

    def test_acquisition_and_recurrence_roles_must_have_appropriate_phases(self):
        for position, phase in ((0, "serve"), (1, "unseen"), (1, None)):
            source = corpus()
            source["tasks"][position]["phase"] = phase
            with self.subTest(position=position, phase=phase):
                with self.assertRaises(ValueError):
                    check_recurrences(source, [(0, 1)])

    def test_matching_entries_cannot_reuse_source_row_even_with_new_inputs(self):
        source = corpus()
        source["tasks"][1]["row_ids"][0] = source["tasks"][0]["row_ids"][0]
        with self.assertRaisesRegex(ValueError, "overlap source row"):
            check_recurrences(source, [(0, 1)])

    def test_matching_entries_cannot_disguise_same_visible_input_with_json_spacing(
        self,
    ):
        source = corpus()
        source["tasks"][1]["inputs"][0] = json.dumps(
            json.loads(source["tasks"][0]["inputs"][0]), separators=(",", ":")
        )
        with self.assertRaisesRegex(ValueError, "overlap visible inputs"):
            check_recurrences(source, [(0, 1)])

    def test_freshness_applies_between_recurring_files_of_same_recipe(self):
        source = corpus()
        source["tasks"][2]["row_ids"][0] = source["tasks"][1]["row_ids"][0]
        with self.assertRaisesRegex(ValueError, "overlap source row"):
            check_recurrences(source, [(0, 1), (0, 2)])
        source = corpus()
        source["tasks"][2]["inputs"][0] = source["tasks"][1]["inputs"][0]
        with self.assertRaisesRegex(ValueError, "overlap visible inputs"):
            check_recurrences(source, [(0, 1), (0, 2)])

    def test_different_recipes_may_share_observations(self):
        source = corpus()
        different = {"operation": "multiply", "columns": [0, 1]}
        source["tasks"] += [
            entry(1, "unseen", different),
            entry(10, "serve", different),
        ]
        result = check_recurrences(source, [(0, 1), (3, 4)])
        self.assertEqual(result["counts"]["recipe_groups"], 2)

    def test_missing_recipe_and_malformed_observations_fail_closed(self):
        corruptions = (
            lambda task: task.pop("recipe"),
            lambda task: task.update(recipe={}),
            lambda task: task.update(recipe=[]),
            lambda task: task.update(row_ids=[True, 2, 3]),
            lambda task: task.update(row_ids=[1, 1, 3]),
            lambda task: task.update(inputs=["not-json"] * 3),
            lambda task: task.update(inputs=['["1", "2", 3, "4"]'] * 3),
            lambda task: task.update(inputs=task["inputs"][:2]),
        )
        for change in corruptions:
            source = corpus()
            change(source["tasks"][0])
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    check_recurrences(source, [(0, 1)])

    def test_bad_repeated_reversed_or_out_of_range_plan_is_rejected(self):
        for plan in (
            [],
            [(0, 1), (0, 1)],
            [(1, 0)],
            [(0, 0)],
            [(0, 3)],
            [(False, 1)],
            [(0, "1")],
        ):
            with self.subTest(plan=plan):
                with self.assertRaises(ValueError):
                    check_recurrences(corpus(), plan)
        self.assertEqual(parse_pairs("0:1,0:2"), [(0, 1), (0, 2)])
        for value in ("0", "0:1,", "-1:2", "1:2:3", "0:1 "):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_pairs(value)

    def test_logical_check_and_byte_budgets_fail_closed(self):
        for budget in (True, 0, MAX_CHECKS + 1, 1):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(ValueError, "budget"):
                    check_recurrences(corpus(), [(0, 1)], maximum_checks=budget)
        with patch(
            "pathlib.Path.open", return_value=io.BytesIO(b" " * (MAX_BYTES + 1))
        ):
            with self.assertRaisesRegex(ValueError, "byte budget"):
                load_corpus("unused")

    def test_raw_hash_pin_and_ambiguous_json_are_checked_before_planning(self):
        raw = json.dumps(corpus()).encode()
        actual = hashlib.sha256(raw).hexdigest()
        with patch("pathlib.Path.open", return_value=io.BytesIO(raw)):
            loaded, observed = load_corpus("unused", actual)
        self.assertEqual(observed, actual)
        self.assertEqual(loaded, corpus())
        with patch("pathlib.Path.open", return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(ValueError, "differs from its pinned"):
                load_corpus("unused", "0" * 64)
        with patch(
            "pathlib.Path.open", return_value=io.BytesIO(b'{"source":1,"source":2}')
        ):
            with self.assertRaisesRegex(ValueError, "unambiguous JSON"):
                load_corpus("unused")


if __name__ == "__main__":
    unittest.main()
