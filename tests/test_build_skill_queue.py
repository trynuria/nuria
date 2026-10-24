"""Serving files bind to explicit learned formats, never shuffled positions."""

import copy
import unittest

from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG
from commerce.tasks import contractor_brief
from scripts.build_skill_queue import build_execution


def file_entry(index, phase, first_row):
    return {
        "phase": phase,
        "format_index": index,
        "recipe": {"field": index, "encoding": "fixture"},
        "row_ids": list(range(first_row, first_row + 24)),
        "inputs": [
            f"format-{index}:row-{row}" for row in range(first_row, first_row + 24)
        ],
        "outputs": [str(row) for row in range(first_row, first_row + 24)],
    }


class ExecutionQueueBuilderTests(unittest.TestCase):
    def setUp(self):
        self.learning = [file_entry(8, "unseen", 1), file_entry(3, "unseen", 25)]
        self.prior = [file_entry(3, "serve", 49), file_entry(8, "serve", 73)]
        self.fresh = [file_entry(3, "serve", 97), file_entry(8, "serve", 121)]
        self.corpus = {
            "config": copy.deepcopy(DEFAULT_EXPRESSION_CONFIG),
            "source": {"description": "Deterministic unit fixture"},
            "tasks": [
                {"phase": "acquisition"},
                *self.learning,
                *self.prior,
                *self.fresh,
            ],
        }
        self.learned = [
            {
                "title": "Public-record repair 002",
                "source_id": "unit-source",
                "accepted": True,
                "skill_id": "8" * 64,
            },
            {
                "title": "Public-record repair 003",
                "source_id": "unit-source",
                "accepted": True,
                "skill_id": "3" * 64,
            },
        ]

    def build(self, **changes):
        options = {"entries": self.fresh, "prior_entries": self.corpus["tasks"][:-2]}
        options.update(changes)
        return build_execution(
            self.corpus, self.learned, "included-skills", "unit-source", **options
        )

    def test_permuted_serves_select_their_explicit_learned_format(self):
        result = self.build()
        self.assertEqual(
            [task["acceptance"]["skill_id"] for task in result["tasks"]],
            ["3" * 64, "8" * 64],
        )
        self.assertEqual(
            [binding["learning_title"] for binding in result["source"]["bindings"]],
            ["Public-record repair 003", "Public-record repair 002"],
        )
        self.assertTrue(all(task["maximum_micro_usd"] == 0 for task in result["tasks"]))
        self.assertTrue(
            all(task["acceptance"]["maximum_work"] == 10000 for task in result["tasks"])
        )
        self.learned.reverse()
        self.assertEqual(
            [task["acceptance"]["skill_id"] for task in self.build()["tasks"]],
            ["3" * 64, "8" * 64],
        )

    def test_provider_receives_inputs_but_no_targets_or_training_examples(self):
        brief = contractor_brief(self.build()["tasks"][0], "unit-job")
        self.assertEqual(brief["acceptance"]["inputs"], self.fresh[0]["inputs"])
        self.assertNotIn("expected_outputs", brief["acceptance"])
        self.assertNotIn("training_inputs", brief["acceptance"])
        self.assertEqual(brief["acceptance"]["type"], "transform_execution")

    def test_missing_duplicate_or_rejected_learning_receipt_is_refused(self):
        self.learned.pop()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.build()
        self.setUp()
        self.learned.append(copy.deepcopy(self.learned[0]))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.build()
        self.setUp()
        self.learned[0]["accepted"] = False
        with self.assertRaisesRegex(ValueError, "unbound"):
            self.build()

    def test_duplicate_unseen_or_serving_format_is_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.build(entries=[self.fresh[0], self.fresh[0]])
        self.corpus["tasks"].append(copy.deepcopy(self.learning[0]))
        with self.assertRaisesRegex(ValueError, "duplicate unseen"):
            self.build()

    def test_recipe_mismatch_is_refused_even_if_declared_in_corpus(self):
        self.fresh[0]["recipe"]["encoding"] = "different"
        with self.assertRaisesRegex(ValueError, "recipe differs"):
            self.build()

    def test_same_format_source_row_or_input_overlap_is_refused(self):
        self.fresh[0]["row_ids"][0] = self.prior[0]["row_ids"][0]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.build()
        self.setUp()
        self.fresh[0]["inputs"][0] = self.learning[1]["inputs"][0]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.build()

    def test_unknown_source_or_undeclared_serving_file_is_refused(self):
        self.learned[0]["source_id"] = "another-source"
        with self.assertRaisesRegex(ValueError, "unbound"):
            self.build()
        self.setUp()
        with self.assertRaisesRegex(ValueError, "absent"):
            self.build(entries=[file_entry(3, "serve", 1000)])

    def test_invalid_or_insufficient_work_allowance_is_refused(self):
        for allowance in (True, 0, 10_000_001, 1):
            with self.assertRaises(ValueError):
                self.build(maximum_work=allowance)
