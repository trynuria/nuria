"""Typed executable field composition and verified parameterized skill reuse."""

import copy
import json
import unittest
from unittest.mock import patch

from cognition.skill_expressions import (
    DEFAULT_EXPRESSION_CONFIG,
    VERSION,
    ExpressionLibrary,
)
from cognition.skills import (
    DEFAULT_CONFIG,
    SkillLibrary,
    execute,
    execute_checked,
    fingerprint,
    normalize_config,
    validate_program,
)


def record(*values):
    return json.dumps(list(values), separators=(",", ":"))


def field(index):
    return {"op": "field", "index": index}


def unary(name, child, **terms):
    return {"op": name, **terms, "arg": child}


def program(node):
    return {"version": VERSION, "expr": node}


class ExpressionSkillTests(unittest.TestCase):
    def library(self):
        return SkillLibrary("fixture:field-skills", DEFAULT_EXPRESSION_CONFIG)

    def train_normalizers(self):
        library = self.library()
        for index, (encoded, denominator) in enumerate(
            (
                (["value=120", "value=250", "value=320", "value=470"], 100),
                (["1200 units", "2500 units", "3200 units", "4700 units"], 1000),
            )
        ):
            inputs = [
                record(value, other, "0.2", "0.81")
                for value, other in zip(encoded, ["11", "17", "23", "36"], strict=True)
            ]
            result = library.solve(
                inputs, ["1.2", "2.5", "3.2", "4.7"], mode="macro", budget=20000
            )
            self.assertTrue(result["solved_training"])
            receipt = library.validate_and_admit(
                result["proposal_id"],
                inputs,
                ["1.2", "2.5", "3.2", "4.7"],
                fingerprint(["normalizer", index, denominator]),
            )
            self.assertTrue(receipt["accepted"])
        return library

    def test_versioned_dispatch_preserves_the_original_unary_language(self):
        self.assertIsInstance(self.library(), ExpressionLibrary)
        original = SkillLibrary("fixture:unary", DEFAULT_CONFIG)
        self.assertNotIsInstance(original, ExpressionLibrary)
        result = original.solve(["n=1", "n=2"], ["1", "2"])
        self.assertEqual(result["program"]["version"], "safe-transform-v1")
        self.assertEqual(
            normalize_config(DEFAULT_EXPRESSION_CONFIG), DEFAULT_EXPRESSION_CONFIG
        )

    def test_actual_expression_composes_two_fields_and_counts_every_operand(self):
        left = unary("decimal", field(0))
        right = unary("decimal", field(1))
        tree = program(unary("format", {"op": "add", "left": left, "right": right}))
        result = execute_checked(
            tree, record("12", "24", "0.2", "0.8"), DEFAULT_EXPRESSION_CONFIG
        )
        self.assertEqual(result["output"], "36")
        self.assertEqual(result["work"]["primitive_attempts"], 6)
        # Four one-operand nodes, one two-operand addition, one final format.
        self.assertEqual(result["work"]["total"], 13)
        self.assertEqual(result["error"], None)

    def test_synthesizes_a_sum_instead_of_selecting_a_predeclared_tool(self):
        library = self.library()
        inputs = [
            record(str(a), str(b), "20", "30")
            for a, b in ((12, 24), (15, 30), (17, 42), (23, 51))
        ]
        result = library.solve(inputs, ["36", "45", "59", "74"], budget=20000)
        self.assertTrue(result["solved_training"])
        self.assertIn('"op": "add"', json.dumps(result["program"]))
        self.assertEqual(
            execute(
                result["program"],
                record("13", "17", "21", "42"),
                DEFAULT_EXPRESSION_CONFIG,
            )[0],
            "30",
        )

    def test_parameterized_numeric_fragments_compose_at_previously_unseen_slots(self):
        library = self.train_normalizers()
        old_programs = set(library.dump()["programs"])
        inputs = [
            record(*row)
            for row in (
                ("1300 units", "value=250", "0.8", "0.21"),
                ("2800 units", "value=330", "0.7", "0.18"),
                ("3700 units", "value=470", "0.5", "0.66"),
                ("4100 units", "value=650", "0.2", "0.43"),
            )
        ]
        targets = ["3.8", "6.1", "8.4", "10.6"]
        result = library.solve(inputs, targets, mode="macro", budget=100000)
        self.assertTrue(result["solved_training"])
        self.assertNotIn(result["program_sha256"], old_programs)
        self.assertEqual(
            execute(
                result["program"],
                record("1700 units", "value=220", "0.3", "0.44"),
                DEFAULT_EXPRESSION_CONFIG,
            )[0],
            "3.9",
        )
        numeric_fragments = [
            value["ops"] for value in library.dump()["macros"].values()
        ]
        self.assertTrue(any(ops[-1]["op"] == "scale" for ops in numeric_fragments))
        self.assertTrue(
            all("field" not in [op["op"] for op in ops] for ops in numeric_fragments)
        )

    def test_fresh_search_and_retrieval_have_the_same_language_and_work_allowance(self):
        library = self.train_normalizers()
        inputs = [
            record(*row)
            for row in (
                ("1300 units", "value=250", "0.8", "0.21"),
                ("2800 units", "value=330", "0.7", "0.18"),
                ("3700 units", "value=470", "0.5", "0.66"),
                ("4100 units", "value=650", "0.2", "0.43"),
            )
        ]
        for mode in ("fresh", "retrieval", "macro", "contextual"):
            result = library.solve(
                inputs,
                ["3.8", "6.1", "8.4", "10.6"],
                mode=mode,
                budget=5000,
                features=[0.1, 0.2],
            )
            self.assertLessEqual(result["work"]["total"], 5000)
            self.assertEqual(
                result["config_sha256"], fingerprint(DEFAULT_EXPRESSION_CONFIG)
            )

    def test_arithmetic_is_exact_bounded_decimal_and_runtime_failure_is_charged(self):
        subtract = program(
            unary(
                "format",
                {
                    "op": "subtract",
                    "left": unary("decimal", field(0)),
                    "right": unary("decimal", field(1)),
                },
            )
        )
        multiply = program(
            unary(
                "format",
                {
                    "op": "multiply",
                    "left": unary("decimal", field(0)),
                    "right": unary("decimal", field(1)),
                },
            )
        )
        self.assertEqual(
            execute(
                subtract, record("0.3", "0.2", "0", "0"), DEFAULT_EXPRESSION_CONFIG
            )[0],
            "0.1",
        )
        self.assertEqual(
            execute(
                multiply, record("0.3", "0.2", "0", "0"), DEFAULT_EXPRESSION_CONFIG
            )[0],
            "0.06",
        )
        for text in ("NaN", "Infinity", "1e100", "1e-100", "1e" + "9" * 100):
            result = execute_checked(
                multiply, record(text, "2", "3", "4"), DEFAULT_EXPRESSION_CONFIG
            )
            self.assertEqual(result["error"], "operation_failed")
            self.assertGreater(result["work"]["total"], 0)
        result = execute_checked(
            multiply, record("1e20", "1e20", "3", "4"), DEFAULT_EXPRESSION_CONFIG
        )
        self.assertEqual(result["error"], "operation_failed")
        self.assertEqual(result["work"]["primitive_attempts"], 5)

    def test_source_parser_and_ast_refuse_arbitrary_data_and_code(self):
        valid = program(field(0))
        for text in ('{"0":"value"}', '["one"]', '[1,"2","3","4"]', "[" * 200):
            result = execute_checked(valid, text, DEFAULT_EXPRESSION_CONFIG)
            self.assertEqual(result["error"], "operation_failed")
            self.assertGreater(result["work"]["total"], 0)
        self.assertEqual(
            execute(
                valid,
                record("__import__('os')", "b", "c", "d"),
                DEFAULT_EXPRESSION_CONFIG,
            )[0],
            "__import__('os')",
        )
        invalid = [
            program({"op": "field", "index": True}),
            program({"op": "field", "index": 4}),
            program({"op": "field", "index": 0, "source": "private"}),
            program({"op": "exec", "source": "pass"}),
            program({"op": "add", "left": field(0), "right": field(1)}),
            program(unary("decimal", field(0))),
            {"version": VERSION, "expr": field(0), "extra": None},
        ]
        for tree in invalid:
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                validate_program(tree, DEFAULT_EXPRESSION_CONFIG)

    def test_depth_node_and_semantic_state_bounds_are_enforced(self):
        tree = field(0)
        for _ in range(8):
            tree = unary("strip", tree)
        with self.assertRaises(ValueError):
            validate_program(program(tree), DEFAULT_EXPRESSION_CONFIG)
        config = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
        config["max_ops"] = 1
        with self.assertRaises(ValueError):
            validate_program(program(unary("strip", field(0))), config)
        library = self.library()
        with patch("cognition.skill_expressions.MAX_STATES", 2):
            result = library.solve([record("2", "3", "4", "5")], ["9"], budget=100000)
        self.assertTrue(result["exhausted"])
        self.assertLessEqual(result["semantic_states"], 2)

    def test_hidden_failure_does_not_install_any_program_or_macro(self):
        library = self.library()
        report = library.solve([record("3", "3", "3", "3")], ["3"])
        receipt = library.validate_and_admit(
            report["proposal_id"],
            [record("4", "5", "6", "7")],
            ["9"],
            fingerprint("ambiguity"),
            reveal_counterexample=True,
        )
        self.assertFalse(receipt["accepted"])
        self.assertIsNotNone(receipt["counterexample"])
        self.assertEqual(library.dump()["programs"], {})
        self.assertEqual(library.dump()["macros"], {})

    def test_state_restores_program_fragments_pending_receipts_and_next_search(self):
        library = self.train_normalizers()
        pending = library.solve(
            [record("value=350", "17", "8", "9")], ["3.5"], mode="retrieval"
        )
        restored = SkillLibrary(
            library.source_id, DEFAULT_EXPRESSION_CONFIG, state=library.dump()
        )
        args = (
            pending["proposal_id"],
            [record("value=450", "19", "11", "12")],
            ["4.5"],
            fingerprint("restart"),
        )
        self.assertEqual(
            library.validate_and_admit(*args), restored.validate_and_admit(*args)
        )
        self.assertEqual(library.dump(), restored.dump())
        self.assertEqual(
            library.solve([record("value=550", "11", "1", "2")], ["5.5"], mode="macro"),
            restored.solve(
                [record("value=550", "11", "1", "2")], ["5.5"], mode="macro"
            ),
        )

    def test_trusted_receipts_are_exact_and_cannot_cross_sources_or_programs(self):
        library = self.library()
        proposal = library.solve([record("n=2", "3", "4", "5")], ["2"])
        receipt = library.validate_and_admit(
            proposal["proposal_id"],
            [record("n=7", "8", "9", "10")],
            ["7"],
            fingerprint("receipt"),
        )
        before = library.dump()
        self.assertEqual(
            library.admit_verified(proposal["proposal_id"], receipt), receipt
        )
        self.assertEqual(library.dump(), before)
        for field_name, value in (
            ("work", 0),
            ("source_id", "other:source"),
            ("program_sha256", "a" * 64),
        ):
            with self.assertRaises(ValueError):
                library.admit_verified(
                    proposal["proposal_id"], {**receipt, field_name: value}
                )
        damaged = self.train_normalizers().dump()
        key = next(iter(damaged["macros"]))
        damaged["macros"][key]["parent_program_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            SkillLibrary(library.source_id, DEFAULT_EXPRESSION_CONFIG, damaged)

    def test_frozen_buyer_validation_never_acquires_programs(self):
        library = self.library()
        proposal = library.solve([record("n=2", "3", "4", "5")], ["2"])
        receipt = library.validate_and_admit(
            proposal["proposal_id"],
            [record("n=7", "8", "9", "10")],
            ["7"],
            fingerprint("frozen"),
            retain=False,
        )
        self.assertTrue(receipt["accepted"])
        self.assertEqual(library.dump()["programs"], {})
        self.assertEqual(library.dump()["pending"], {})

    def test_factored_and_empty_retained_fallback_are_identical(self):
        inputs = [
            record(str(a), str(b), "20", "30")
            for a, b in ((12, 24), (15, 30), (17, 42), (23, 51))
        ]
        outputs = ["36", "45", "59", "74"]
        fresh = self.library().solve(inputs, outputs, mode="factored", budget=100000)
        retained = self.library().solve(
            inputs, outputs, mode="retained_factored", budget=100000
        )
        self.assertTrue(fresh["solved_training"])
        self.assertEqual(fresh["program"], retained["program"])
        self.assertEqual(fresh["work"], retained["work"])
        self.assertEqual(fresh["proposal_id"], retained["proposal_id"])
        self.assertGreater(fresh["work"]["constraint_operations"], 0)

    def test_retained_factored_reuses_only_buyer_verified_asts(self):
        library = self.library()
        inputs = [
            record(str(a), str(b), "20", "30")
            for a, b in ((12, 24), (15, 30), (17, 42), (23, 51))
        ]
        proposed = library.solve(
            inputs, ["36", "45", "59", "74"], mode="factored", budget=100000
        )
        self.assertEqual(library.dump()["programs"], {})
        library.validate_and_admit(
            proposed["proposal_id"],
            [record("13", "17", "11", "19")],
            ["30"],
            fingerprint("factored-buyer"),
        )
        reused = library.solve(
            [record("19", "29", "11", "7")],
            ["48"],
            mode="retained_factored",
            budget=100000,
        )
        fresh = self.library().solve(
            [record("19", "29", "11", "7")], ["48"], mode="factored", budget=100000
        )
        self.assertEqual(reused["program_sha256"], proposed["program_sha256"])
        self.assertLess(reused["work"]["total"], fresh["work"]["total"])
        self.assertEqual(reused["work"]["constraint_operations"], 0)

    def test_serving_retained_skill_requires_no_training_labels_or_search(self):
        library = self.train_normalizers()
        key = next(
            key
            for key, value in library.dump()["programs"].items()
            if '"value": "value="' in json.dumps(value["program"])
        )
        before = library.dump()
        with patch(
            "cognition.skill_factored.solve_factored",
            side_effect=AssertionError("Serving must never synthesize"),
        ):
            result = library.serve(
                key,
                [
                    record("value=350", "7", "8", "9"),
                    record("value=450", "11", "12", "13"),
                ],
                1000,
            )
        self.assertEqual(result["status"], "served")
        self.assertEqual(result["outputs"], ["3.5", "4.5"])
        self.assertEqual(result["source_id"], library.source_id)
        self.assertEqual(result["program_sha256"], key)
        self.assertEqual(result["work"]["candidates"], 1)
        self.assertEqual(library.dump()["programs"], before["programs"])
        self.assertEqual(library.dump()["pending"], before["pending"])
        self.assertEqual(
            library.dump()["total_work"], before["total_work"] + result["work"]["total"]
        )

    def test_serving_unknown_skill_bad_new_row_and_budget_failures_are_recorded(self):
        library = self.train_normalizers()
        missing = library.serve("a" * 64, [record("1", "2", "3", "4")])
        self.assertEqual(missing["status"], "skill_missing")
        self.assertEqual(missing["outputs"], [None])
        self.assertGreater(missing["work"]["total"], 0)
        key = next(
            key
            for key, value in library.dump()["programs"].items()
            if '"value": "value="' in json.dumps(value["program"])
        )
        wrong = library.serve(key, [record("different-format", "2", "3", "4")])
        self.assertEqual(wrong["status"], "failed")
        self.assertEqual(wrong["outputs"], [None])
        self.assertGreater(wrong["work"]["errors"], 0)
        capped = library.serve(key, [record("value=350", "7", "8", "9")], 5)
        self.assertEqual(capped["status"], "work_limit")
        self.assertLessEqual(capped["work"]["total"], 5)
        with self.assertRaises(ValueError):
            library.serve("not-a-hash", [record("1", "2", "3", "4")])
        other = SkillLibrary("fixture:other-source", DEFAULT_EXPRESSION_CONFIG)
        self.assertEqual(
            other.serve(key, [record("1", "2", "3", "4")])["status"], "skill_missing"
        )

    def test_factored_search_handles_zero_multiplication_and_nonnumeric_targets(self):
        config = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
        config["operations"] = [
            op for op in config["operations"] if op["op"] not in ("add", "subtract")
        ]
        library = SkillLibrary("fixture:zero-products", config)
        result = library.solve(
            [record("0", "3", "5", "8"), record("2", "4", "7", "9")],
            ["0", "8"],
            mode="factored",
            budget=100000,
        )
        self.assertTrue(result["solved_training"])
        self.assertIn('"op": "multiply"', json.dumps(result["program"]))
        impossible = library.solve(
            [record("0", "3", "5", "8")],
            ["not-numeric"],
            mode="factored",
            budget=100000,
        )
        self.assertFalse(impossible["solved_training"])
        self.assertGreater(impossible["work"]["total"], 0)
        bounded = library.solve(
            [record("0", "3", "5", "8")], ["1e99999999"], mode="factored", budget=100000
        )
        self.assertFalse(bounded["solved_training"])


if __name__ == "__main__":
    unittest.main()
