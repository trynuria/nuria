"""Safe text acquisition, independent buyer admission and finite recovery."""

import copy
import json
import unittest

from cognition.skill_language import execution_work_ceiling
from cognition.skills import fingerprint
from cognition.text_skills import (
    DEFAULT_TEXT_CONFIG,
    TextSkillLibrary,
    execute_checked,
    validate_program,
)


def rows(values):
    return [json.dumps(value if type(value) is list else [value]) for value in values]


def admission(library, proposal, values, expected, identity="buyer-fixture"):
    checked = [
        execute_checked(proposal["program"], value, library.config) for value in values
    ]
    actual = [item["output"] for item in checked]
    correct = sum(a == b for a, b in zip(actual, expected, strict=True))
    receipt = {
        "source_id": library.source_id,
        "proposal_id": proposal["proposal_id"],
        "program_sha256": proposal["program_sha256"],
        "evaluation_id": fingerprint(identity),
        "accepted": correct == len(expected),
        "correct_rows": correct,
        "total_rows": len(expected),
        "evidence_sha256": fingerprint([values, expected, actual]),
        "work": sum(item["work"]["total"] for item in checked),
    }
    return library.admit_verified(proposal["proposal_id"], receipt)


class TextSkillTests(unittest.TestCase):
    def test_currency_precision_survives_learning_and_new_input_execution(self):
        library = TextSkillLibrary("text-fixture")
        proposal = library.solve(rows(["$45.20", "€73.10"]), ["45.20", "73.10"])
        self.assertIsNotNone(proposal["program"])
        receipt = admission(
            library, proposal, rows(["£0.50", "$123.00"]), ["0.50", "123.00"]
        )
        self.assertTrue(receipt["accepted"])
        restored = TextSkillLibrary("text-fixture", state=library.dump())
        result = restored.serve(proposal["program_sha256"], rows(["$19.99", "€81.40"]))
        self.assertEqual(result["outputs"], ["19.99", "81.40"])

    def test_multifield_concatenation_generalizes_beyond_examples(self):
        library = TextSkillLibrary("text-fixture")
        proposal = library.solve(
            rows([["Alice", "Smith"], ["Ben", "Jones"]]), ["Smith, Alice", "Jones, Ben"]
        )
        self.assertIsNotNone(proposal["program"])
        checked = execute_checked(proposal["program"], rows([["Cora", "Ng"]])[0])
        self.assertEqual(checked["output"], "Ng, Cora")

    def test_conditional_exception_is_executable_and_rejects_wrong_buyer_target(self):
        library = TextSkillLibrary("text-fixture")
        proposal = library.solve(
            rows(["ID", "Coln8", "Coln9", "Coln23"]), ["Col1", "Col8", "Col9", "Col23"]
        )
        self.assertIsNotNone(proposal["program"])
        self.assertEqual(
            execute_checked(proposal["program"], rows(["Coln105"])[0])["output"],
            "Col105",
        )
        receipt = admission(library, proposal, rows(["Coln42"]), ["wrong-target"])
        self.assertFalse(receipt["accepted"])
        self.assertEqual(library.dump()["programs"], {})
        self.assertGreater(receipt["work"], 0)

    def test_source_config_pending_program_and_cost_commitments_are_bound(self):
        library = TextSkillLibrary("text-fixture")
        proposal = library.solve(rows(["$1.20", "$2.30"]), ["1.20", "2.30"])
        state = library.dump()
        for mutation in ("program", "cost", "config"):
            altered = copy.deepcopy(state)
            if mutation == "program":
                altered["pending"][proposal["proposal_id"]]["program"]["expr"][
                    "start"
                ] = 2
            elif mutation == "cost":
                altered["total_work"] = 0
            else:
                altered["config_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                TextSkillLibrary("text-fixture", state=altered)
        changed = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        changed["max_parts"] = 3
        alternative = TextSkillLibrary("text-fixture", changed).solve(
            rows(["$1.20", "$2.30"]), ["1.20", "2.30"]
        )
        self.assertNotEqual(alternative["proposal_id"], proposal["proposal_id"])

    def test_receipts_are_immutable_and_evaluation_ids_cannot_be_reused(self):
        library = TextSkillLibrary("text-fixture")
        proposal = library.solve(rows(["A12", "B34"]), ["12", "34"])
        receipt = admission(library, proposal, rows(["C56"]), ["56"])
        self.assertEqual(
            library.admit_verified(proposal["proposal_id"], receipt), receipt
        )
        altered = {**receipt, "work": receipt["work"] + 1}
        with self.assertRaises(ValueError):
            library.admit_verified(proposal["proposal_id"], altered)
        next_proposal = library.solve(rows(["D78", "E90"]), ["78", "90"])
        with self.assertRaisesRegex(ValueError, "already consumed"):
            admission(library, next_proposal, rows(["F11"]), ["11"])

    def test_unknown_skill_still_validates_inputs_before_lookup(self):
        library = TextSkillLibrary("text-fixture")
        for malformed in (
            ["x" * 5000],
            ["[" * 1500 + "]" * 1500],
            [{"not": "a string"}],
        ):
            with self.assertRaises(ValueError):
                library.serve("0" * 64, malformed)

    def test_arbitrary_code_patterns_and_broad_rejected_trees_are_refused(self):
        field = {"op": "field", "index": 0}
        for node in (
            {"op": "eval", "arg": field},
            {"op": "token", "arg": field, "pattern": "(a+)+$", "index": 0},
        ):
            with self.assertRaises(ValueError):
                validate_program({"version": "safe-text-v1", "expr": node})
        node = field
        for _ in range(8):
            node = {"op": "concat", "parts": [node] * 4}
        with self.assertRaises(ValueError):
            validate_program({"version": "safe-text-v1", "expr": node})

    def test_tiny_and_huge_numeric_exponents_are_rejected_before_rendering(self):
        program = {
            "version": "safe-text-v1",
            "expr": {
                "op": "number",
                "arg": {"op": "field", "index": 0},
                "scale": "1",
                "places": None,
            },
        }
        for value in ("1e-100000000", "0e-100000000", "1e100000000", "NaN", "Infinity"):
            result = execute_checked(program, rows([value])[0])
            self.assertIsNone(result["output"])
            self.assertEqual(result["error"], "invalid_input")
            self.assertGreater(result["work"]["total"], 0)

    def test_search_and_execution_work_caps_preserve_failed_work(self):
        library = TextSkillLibrary("text-fixture")
        failed = library.solve(
            rows(["alpha", "beta"]), ["impossible-one", "impossible-two"], budget=10
        )
        self.assertIsNone(failed["program"])
        self.assertTrue(failed["exhausted"])
        self.assertGreater(failed["work"]["total"], 0)
        self.assertLessEqual(failed["work"]["total"], 10)
        self.assertEqual(library.dump()["total_work"], failed["work"]["total"])
        checked = execute_checked(
            {"version": "safe-text-v1", "expr": {"op": "field", "index": 0}},
            rows(["alpha"])[0],
            budget=1,
        )
        self.assertEqual(checked["error"], "work_limit")
        self.assertLessEqual(checked["work"]["total"], 1)

    def test_counter_capacity_is_checked_before_new_work_or_mutation(self):
        library = TextSkillLibrary("text-fixture")
        state = library.dump()
        state["sequence"] = 2**53 - 1
        restored = TextSkillLibrary("text-fixture", state=state)
        with self.assertRaises(ValueError):
            restored.solve(rows(["one"]), ["one"])
        self.assertEqual(restored.dump(), state)

    def test_conservative_interpreter_ceiling_covers_invalid_expansion(self):
        config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        config["max_input_bytes"] = 64
        program = {
            "version": "safe-text-v1",
            "expr": {
                "op": "replace",
                "arg": {"op": "field", "index": 0},
                "old": "a",
                "new": "x" * 16,
            },
        }
        result = execute_checked(program, rows(["a" * 50])[0], config, budget=100000)
        self.assertEqual(result["error"], "invalid_input")
        self.assertLessEqual(result["work"]["total"], execution_work_ceiling(config, 1))


if __name__ == "__main__":
    unittest.main()
