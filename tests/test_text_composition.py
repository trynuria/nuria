"""Composition is executable skill acquisition, not a claim of neural advantage."""

import copy
import json
import unittest

from cognition.skills import WorkLimit, _Meter
from cognition.text_composition import CompositionalTextLibrary, infer_composed
from cognition.text_skills import DEFAULT_TEXT_CONFIG, TextSkillLibrary, execute_checked
from tests.test_text_skills import admission


def rows(values):
    return [
        json.dumps(value if isinstance(value, list) else [value]) for value in values
    ]


class TextCompositionTests(unittest.TestCase):
    def test_later_field_shallow_case_is_not_starved_by_earlier_composition(self):
        values = rows(
            [
                [
                    "wrong-a",
                    "wrong-b",
                    "wrong-c",
                    "wrong-d",
                    "wrong-e",
                    "  Alpha one  ",
                ],
                ["other-a", "other-b", "other-c", "other-d", "other-e", "  Beta two  "],
                [
                    "third-a",
                    "third-b",
                    "third-c",
                    "third-d",
                    "third-e",
                    "  Gamma three  ",
                ],
                ["four-a", "four-b", "four-c", "four-d", "four-e", "  Delta four  "],
            ]
        )
        for mode in ("deduplicated", "enumerative"):
            proposal = CompositionalTextLibrary(
                "composition-fixture", search_mode=mode
            ).solve(
                values,
                ["Alpha one", "Beta two", "Gamma three", "Delta four"],
                budget=50_000,
            )
            self.assertEqual(
                proposal["program"]["expr"],
                {"op": "strip", "arg": {"op": "field", "index": 5}},
            )
            self.assertLess(proposal["work"]["total"], 5_000)

    def test_case_after_token_uses_existing_interpreter_and_new_rows(self):
        values, expected = rows(["red fox", "blue albatross"]), ["RED", "BLUE"]
        old = TextSkillLibrary("composition-fixture").solve(values, expected)
        proposal = CompositionalTextLibrary("composition-fixture").solve(
            values, expected
        )
        self.assertIsNone(old["program"])
        self.assertIsNotNone(proposal["program"])
        self.assertEqual(proposal["program"]["expr"]["op"], "upper")
        self.assertEqual(proposal["program"]["expr"]["arg"]["op"], "token")
        self.assertEqual(
            execute_checked(proposal["program"], rows(["green eel"])[0])["output"],
            "GREEN",
        )

    def test_slice_after_token_does_not_depend_on_fixed_field_offsets(self):
        library = CompositionalTextLibrary("composition-fixture")
        proposal = library.solve(rows(["cat-12345", "ox-678901"]), ["12", "67"])
        self.assertIsNotNone(proposal["program"])
        self.assertEqual(proposal["program"]["expr"]["op"], "slice")
        self.assertEqual(proposal["program"]["expr"]["arg"]["op"], "token")
        self.assertEqual(
            execute_checked(proposal["program"], rows(["elephant-98765"])[0])["output"],
            "98",
        )

    def test_whitespace_normalization_after_split_keeps_multiword_values(self):
        library = CompositionalTextLibrary(
            "composition-fixture", {**DEFAULT_TEXT_CONFIG, "max_parts": 1}
        )
        proposal = library.solve(
            rows(
                [
                    "x|  alpha beta gamma |unused",
                    "long-prefix| delta epsilon |suffix",
                ]
            ),
            ["alpha beta gamma", "delta epsilon"],
            budget=1_000_000,
        )
        self.assertIsNotNone(proposal["program"])
        self.assertEqual(proposal["program"]["expr"]["op"], "strip")
        self.assertEqual(proposal["program"]["expr"]["arg"]["op"], "split")
        self.assertEqual(
            execute_checked(proposal["program"], rows(["q|  eta theta |long-tail"])[0])[
                "output"
            ],
            "eta theta",
        )

    def test_composed_pieces_can_be_joined_and_independently_accepted(self):
        library = CompositionalTextLibrary("composition-fixture")
        proposal = library.solve(
            rows(
                [
                    ["red fox", "ivory ibis"],
                    ["green albatross", "black cat"],
                    ["ochre emu", "cyan jay"],
                ]
            ),
            ["RED:IVORY", "GREEN:BLACK", "OCHRE:CYAN"],
            budget=1_000_000,
        )
        self.assertIsNotNone(proposal["program"])
        self.assertEqual(proposal["program"]["expr"]["op"], "concat")
        receipt = admission(
            library, proposal, rows([["white owl", "brown bear"]]), ["WHITE:BROWN"]
        )
        self.assertTrue(receipt["accepted"])
        restored = CompositionalTextLibrary("composition-fixture", state=library.dump())
        self.assertEqual(
            restored.serve(
                proposal["program_sha256"], rows([["pink wren", "gold eagle"]])
            )["outputs"],
            ["PINK:GOLD"],
        )

    def test_no_hidden_targets_are_needed_and_wrong_buyer_answer_prevents_admission(
        self,
    ):
        library = CompositionalTextLibrary("composition-fixture")
        proposal = library.solve(rows(["red fox", "blue albatross"]), ["RED", "BLUE"])
        receipt = admission(library, proposal, rows(["green eel"]), ["not-the-target"])
        self.assertFalse(receipt["accepted"])
        self.assertEqual(library.dump()["programs"], {})
        self.assertGreater(library.dump()["total_work"], proposal["work"]["total"])

    def test_deduplication_and_enumeration_share_solution_but_charge_duplicate_work(
        self,
    ):
        proposals = [
            CompositionalTextLibrary("composition-fixture", search_mode=mode).solve(
                rows(["red fox", "blue albatross"]), ["RED", "BLUE"]
            )
            for mode in ("deduplicated", "enumerative")
        ]
        self.assertEqual(proposals[0]["program"], proposals[1]["program"])
        self.assertLess(proposals[0]["work"]["total"], proposals[1]["work"]["total"])
        self.assertGreater(proposals[0]["work"]["semantic_duplicates"], 0)
        self.assertGreater(
            proposals[1]["work"]["primitive_attempts"],
            proposals[0]["work"]["primitive_attempts"],
        )

    def test_intermediate_substring_pruning_would_be_unsound(self):
        # Lowercase extracted text is not a substring of an uppercase target.
        # It must survive until the later case operation executes.
        proposal = CompositionalTextLibrary("composition-fixture").solve(
            rows(["red fox", "blue albatross"]), ["RED", "BLUE"]
        )
        self.assertIsNotNone(proposal["program"])
        self.assertEqual(proposal["program"]["expr"]["op"], "upper")

    def test_candidate_node_and_work_caps_keep_all_failed_work(self):
        config = {**copy.deepcopy(DEFAULT_TEXT_CONFIG), "max_candidates": 2}
        library = CompositionalTextLibrary("composition-fixture", config)
        proposal = library.solve(rows(["red fox", "blue albatross"]), ["RED", "BLUE"])
        self.assertIsNone(proposal["program"])
        self.assertTrue(proposal["exhausted"])
        self.assertEqual(proposal["work"]["candidates"], 2)
        self.assertEqual(library.dump()["total_work"], proposal["work"]["total"])
        tiny = CompositionalTextLibrary("composition-fixture").solve(
            rows(["red fox", "blue albatross"]), ["RED", "BLUE"], budget=10
        )
        self.assertTrue(tiny["exhausted"])
        self.assertLessEqual(tiny["work"]["total"], 10)
        node_cap = CompositionalTextLibrary(
            "composition-fixture", {**DEFAULT_TEXT_CONFIG, "max_ops": 2}
        ).solve(rows(["red fox", "blue albatross"]), ["RED", "BLUE"], budget=1_000_000)
        self.assertIsNone(node_cap["program"])
        self.assertGreater(node_cap["work"]["errors"], 0)

    def test_infer_validates_bounds_modes_and_propagates_exhaustion(self):
        for values, expected in (([], []), (["not-json"], ["x"]), (rows(["x"]), [])):
            with self.assertRaises(ValueError):
                infer_composed(values, expected, DEFAULT_TEXT_CONFIG, _Meter(1000))
        with self.assertRaises(ValueError):
            infer_composed(
                rows(["x"]), ["x"], DEFAULT_TEXT_CONFIG, _Meter(1000), mode="unknown"
            )
        with self.assertRaises(WorkLimit):
            infer_composed(
                rows(["red fox", "blue albatross"]),
                ["RED", "BLUE"],
                DEFAULT_TEXT_CONFIG,
                _Meter(1),
            )


if __name__ == "__main__":
    unittest.main()
