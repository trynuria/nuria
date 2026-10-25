import copy
import json
import unittest
from dataclasses import FrozenInstanceError

from cognition.skill_qualification import (
    prepare_qualification,
    qualify_text_skill,
)
from cognition.text_skills import DEFAULT_TEXT_CONFIG, VERSION, execute_checked


class SkillQualificationTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        self.inputs = [json.dumps([v]) for v in ("Alpha Beta-rest", "Gamma Delta-tail")]
        segment = {
            "op": "split",
            "arg": {"op": "field", "index": 0},
            "separator": "-",
            "index": 0,
        }
        self.correct = {"version": VERSION, "expr": {"op": "upper", "arg": segment}}
        # This shortcut agrees with all disclosed rows but truncates a longer
        # first segment. It reproduces the failure found in the study.
        self.shortcut = {
            "version": VERSION,
            "expr": {
                "op": "upper",
                "arg": {
                    "op": "concat",
                    "parts": [
                        {"op": "token", "arg": segment, "pattern": "alpha", "index": 0},
                        {"op": "constant", "value": " "},
                        {"op": "token", "arg": segment, "pattern": "alpha", "index": 1},
                    ],
                },
            },
        }
        self.cases = [
            {
                "input": '["Birch Cedar Elm-next"]',
                "expected": "BIRCH CEDAR ELM",
                "coverage": ["longer_segment"],
            },
            {
                "input": '["fir Ginkgo HAZEL-tail"]',
                "expected": "FIR GINKGO HAZEL",
                "coverage": ["longer_segment", "mixed_case"],
            },
            {
                "input": '["Iris juniper KELP Larch-end"]',
                "expected": "IRIS JUNIPER KELP LARCH",
                "coverage": ["longer_segment", "mixed_case"],
            },
        ]

    def prepare(self, program=None, **changes):
        values = {
            "source_id": "qualification-test-source",
            "domain": "uppercase-first-hyphen-segment",
            "cases": copy.deepcopy(self.cases),
            "minimum_coverage": {"longer_segment": 3, "mixed_case": 2},
        }
        values.update(changes)
        return prepare_qualification(
            self.correct if program is None else program,
            self.inputs,
            self.config,
            **values,
        )

    def test_original_example_fit_does_not_admit_a_shortcut(self):
        for value in self.inputs:
            self.assertEqual(
                execute_checked(self.shortcut, value)["output"],
                execute_checked(self.correct, value)["output"],
            )
        rejected = qualify_text_skill(self.prepare(self.shortcut))
        self.assertFalse(rejected["qualified_for_cases"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["passed_cases"], 0)
        self.assertEqual(rejected["completed_cases"], 3)
        self.assertEqual(rejected["rows"][0]["output"], "BIRCH CEDAR")
        accepted = qualify_text_skill(self.prepare())
        self.assertTrue(accepted["qualified_for_cases"])
        self.assertEqual(
            accepted["passed_coverage"], {"longer_segment": 3, "mixed_case": 2}
        )

    def test_a_late_success_cannot_erase_an_earlier_failure(self):
        cases = copy.deepcopy(self.cases)
        cases[-1]["input"], cases[-1]["expected"] = '["Oak Pine-end"]', "OAK PINE"
        result = qualify_text_skill(self.prepare(self.shortcut, cases=cases))
        self.assertEqual([r["passed"] for r in result["rows"]], [False, False, True])
        self.assertFalse(result["qualified_for_cases"])
        self.assertEqual(result["status"], "rejected")

    def test_partial_budget_cannot_qualify_even_after_correct_rows(self):
        prepared = self.prepare()
        complete = qualify_text_skill(prepared)
        result = qualify_text_skill(prepared, budget=complete["work"]["total"] - 1)
        self.assertFalse(result["qualified_for_cases"])
        self.assertEqual(result["status"], "work_limit")
        self.assertEqual(result["completed_cases"], 2)
        self.assertEqual(result["passed_cases"], 2)
        self.assertLessEqual(result["work"]["total"], complete["work"]["total"] - 1)

    def test_plan_is_bound_to_program_configuration_source_scope_and_cases(self):
        original = self.prepare()
        variants = [
            self.prepare(self.shortcut),
            self.prepare(source_id="other-source"),
            self.prepare(domain="another-contract"),
        ]
        cases = copy.deepcopy(self.cases)
        cases[-1]["expected"] = "ANOTHER OUTPUT"
        variants.append(self.prepare(cases=cases))
        self.config["max_ops"] -= 1
        variants.append(self.prepare())
        identities = {v.plan_sha256 for v in [original, *variants]}
        self.assertEqual(len(identities), 6)
        self.assertEqual(
            qualify_text_skill(original)["plan_sha256"], original.plan_sha256
        )
        with self.assertRaises(FrozenInstanceError):
            original.payload = "{}"

    def test_caller_mutation_cannot_change_a_prepared_plan(self):
        cases = copy.deepcopy(self.cases)
        prepared = self.prepare(cases=cases)
        cases[0]["expected"] = "WRONG"
        self.correct["expr"]["op"] = "lower"
        self.config["max_ops"] = 1
        self.inputs.append('["already disclosed"]')
        self.assertTrue(qualify_text_skill(prepared)["qualified_for_cases"])

    def test_duplicates_overlap_and_incomplete_coverage_are_rejected(self):
        changes = []
        repeated = copy.deepcopy(self.cases)
        repeated[-1]["input"] = ' [ "Birch Cedar Elm-next" ] '
        changes.append({"cases": repeated})
        overlap = copy.deepcopy(self.cases)
        overlap[0]["input"] = ' [ "Alpha Beta-rest" ] '
        changes.append({"cases": overlap})
        changes.append({"minimum_coverage": {"longer_segment": 4, "mixed_case": 2}})
        malformed = copy.deepcopy(self.cases)
        malformed[0]["coverage"] = ["longer_segment", "longer_segment"]
        changes.append({"cases": malformed})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(**change)

    def test_interpreter_error_remains_a_rejected_case(self):
        cases = copy.deepcopy(self.cases)
        cases[-1]["input"], cases[-1]["expected"] = '["single"]', "SINGLE"
        result = qualify_text_skill(self.prepare(self.shortcut, cases=cases))
        self.assertEqual(result["rows"][-1]["error"], "invalid_input")
        self.assertFalse(result["qualified_for_cases"])
        self.assertGreater(result["work"]["errors"], 0)

    def test_unicode_byte_bounds_and_escaped_duplicates(self):
        self.config["max_input_bytes"] = 64
        identity = {"version": VERSION, "expr": {"op": "field", "index": 0}}
        cases = [
            {
                "input": json.dumps(["ä" * n], ensure_ascii=False),
                "expected": "ä" * n,
                "coverage": ["unicode"],
            }
            for n in (11, 12)
        ]
        prepared = prepare_qualification(
            identity,
            ['["x"]'],
            self.config,
            source_id="unicode-test",
            domain="unicode-identity",
            cases=cases,
            minimum_coverage={"unicode": 2},
        )
        self.assertTrue(qualify_text_skill(prepared)["qualified_for_cases"])
        cases[-1]["input"] = json.dumps(["ä" * 11])
        # The default escaped representation exceeds this small byte limit;
        # use a larger limit to test semantic duplication independently.
        self.config["max_input_bytes"] = 256
        with self.assertRaises(ValueError):
            prepare_qualification(
                identity,
                ['["x"]'],
                self.config,
                source_id="unicode-test",
                domain="unicode-identity",
                cases=cases,
                minimum_coverage={"unicode": 2},
            )


if __name__ == "__main__":
    unittest.main()
