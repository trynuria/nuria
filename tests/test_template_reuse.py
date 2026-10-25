"""Whole-program reuse, alias authority and bounded serving accounting."""

import copy
import dataclasses
import json
import unittest
from unittest.mock import patch

from cognition.skills import WorkLimit, _Meter, fingerprint
from cognition.template_reuse import (
    PreparedTemplates,
    prepare_templates,
    solve_templates,
)
from cognition.text_composition import infer_composed
from cognition.text_skills import DEFAULT_TEXT_CONFIG, execute_checked


def program(node):
    return {"version": "safe-text-v1", "expr": node}


def field(index):
    return {"op": "field", "index": index}


def rows(*values):
    return [json.dumps(list(value)) for value in values]


class TemplateReuseTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        self.inputs = rows(
            ("Alpha", "one"), ("Birch", "two"), ("Cedar", "three"), ("Dune", "four")
        )

    def solve(self, programs, inputs, outputs, **kwargs):
        cache = prepare_templates(programs, self.config)
        return solve_templates(inputs, outputs, self.config, cache, **kwargs)

    def assert_fits(self, result, inputs, outputs):
        self.assertIsNotNone(result["program"])
        for value, expected in zip(inputs, outputs, strict=True):
            observed = execute_checked(result["program"], value, self.config)
            self.assertIsNone(observed["error"])
            self.assertEqual(observed["output"], expected)

    def test_preparation_canonicalizes_fields_and_deduplicates_stably(self):
        source = [
            program({"op": "upper", "arg": field(7)}),
            program({"op": "upper", "arg": field(1)}),
        ]
        prepared = prepare_templates(source, self.config)
        self.assertEqual(prepared.report["history_programs"], 2)
        self.assertEqual(prepared.report["templates"], 1)
        self.assertEqual(prepared.report["template_duplicates"], 1)
        self.assertEqual(prepared.report["preparation_work"]["candidates"], 2)
        self.assertGreater(prepared.report["stored_bytes"], 0)
        result = solve_templates(
            self.inputs,
            ["ONE", "TWO", "THREE", "FOUR"],
            self.config,
            prepared,
            fallback=False,
        )
        self.assertEqual(result["program"], program({"op": "upper", "arg": field(1)}))
        self.assertEqual(
            result["selection_metadata"]["source_program_sha256"],
            fingerprint(source[0]),
        )

    def test_template_and_fallback_share_one_candidate_allowance(self):
        self.config["max_candidates"] = 1
        inputs, targets = rows(("Alpha",), ("Beta",)), ["Alpha", "Beta"]
        source = [program({"op": "constant", "value": "wrong"})]
        limited = self.solve(source, inputs, targets, budget=5000)
        self.assertIsNone(limited["program"])
        self.assertEqual(limited["work"]["candidates"], 1)
        self.assertIsNone(limited["fallback_work"])
        self.assertEqual(limited["status"], "work_or_capacity_exhausted")
        self.config["max_candidates"] = 2
        enough = self.solve(source, inputs, targets, budget=5000)
        self.assert_fits(enough, inputs, targets)
        self.assertEqual(enough["work"]["candidates"], 2)
        self.assertEqual(enough["fallback_work"]["candidates"], 1)
        self.assertLessEqual(enough["work"]["total"], 5000)

    def test_repeated_field_aliases_remain_one_binding(self):
        original = program(
            {
                "op": "concat",
                "parts": [field(5), {"op": "constant", "value": ":"}, field(5)],
            }
        )
        output = ["one:one", "two:two", "three:three", "four:four"]
        result = self.solve([original], self.inputs, output, fallback=False)
        self.assert_fits(result, self.inputs, output)
        self.assertEqual(result["selection_metadata"]["binding"], [1])
        self.assertEqual(result["program"]["expr"]["parts"][0], field(1))
        self.assertEqual(result["program"]["expr"]["parts"][2], field(1))

    def test_distinct_slots_may_collapse_to_one_field(self):
        original = program({"op": "concat", "parts": [field(4), field(7)]})
        inputs = rows(("a",), ("bc",), ("DEF",), ("ghij",))
        output = ["aa", "bcbc", "DEFDEF", "ghijghij"]
        result = self.solve([original], inputs, output, fallback=False)
        self.assert_fits(result, inputs, output)
        self.assertEqual(result["selection_metadata"]["binding"], [0, 0])

    def test_condition_and_branch_share_their_original_field_alias(self):
        original = program(
            {
                "op": "if_equal",
                "field": 6,
                "value": "T",
                "then": field(6),
                "else": field(2),
            }
        )
        inputs = rows(("left1", "T"), ("left2", "x"), ("left3", "T"), ("left4", "y"))
        output = ["T", "left2", "T", "left4"]
        result = self.solve([original], inputs, output, fallback=False)
        self.assert_fits(result, inputs, output)
        self.assertEqual(result["selection_metadata"]["binding"], [1, 0])
        self.assertEqual(result["program"]["expr"]["field"], 1)
        self.assertEqual(result["program"]["expr"]["then"], field(1))

    def test_only_common_fields_are_bound(self):
        inputs = rows(("a", "extra"), ("b",), ("c", "extra"), ("d",))
        result = self.solve(
            [program(field(7))], inputs, ["a", "b", "c", "d"], fallback=False
        )
        self.assertEqual(result["program"], program(field(0)))
        self.assertEqual(result["statistics"]["common_disclosed_field_count"], 1)
        self.assertEqual(result["statistics"]["bindings_attempted"], 1)

    def test_zero_slot_constant_is_considered_once(self):
        result = self.solve(
            [program({"op": "constant", "value": "fixed"})],
            self.inputs,
            ["fixed"] * 4,
            fallback=False,
        )
        self.assertEqual(result["selection_metadata"]["binding"], [])
        self.assertEqual(result["statistics"]["candidate_attempts"], 1)

    def test_complete_ast_duplicates_are_not_executed_again(self):
        source = [
            program({"op": "concat", "parts": [field(0), field(1)]}),
            program({"op": "concat", "parts": [field(0), field(0)]}),
        ]
        inputs = rows(("a",), ("b",), ("c",), ("d",))
        result = self.solve(source, inputs, ["never"] * 4, fallback=False)
        self.assertIsNone(result["program"])
        self.assertEqual(result["statistics"]["candidate_attempts"], 2)
        self.assertEqual(result["statistics"]["complete_ast_duplicates"], 1)
        self.assertEqual(result["statistics"]["disclosed_rows_checked"], 1)

    def test_scan_short_circuits_on_first_mismatch(self):
        result = self.solve(
            [program(field(0))], self.inputs, ["no"] * 4, fallback=False
        )
        self.assertIsNone(result["program"])
        self.assertEqual(result["statistics"]["candidate_attempts"], 2)
        self.assertEqual(result["statistics"]["disclosed_rows_checked"], 2)

    def test_disclosed_fit_is_not_buyer_acceptance_or_a_retry(self):
        source = [program({"op": "constant", "value": "same"}), program(field(0))]
        inputs = rows(("same",), ("same",), ("same",), ("same",))
        result = self.solve(source, inputs, ["same"] * 4, fallback=False)
        self.assertEqual(result["program"], source[0])
        self.assertIsNone(result["buyer_accepted"])
        self.assertEqual(result["statistics"]["disclosed_rows_checked"], 4)
        self.assertEqual(
            result["commitment"],
            fingerprint({k: v for k, v in result.items() if k != "commitment"}),
        )

    def test_empty_history_fallback_has_exact_unchanged_composition_parity(self):
        inputs = rows((" A ",), (" B ",), (" C ",), (" D ",))
        outputs = ["A", "B", "C", "D"]
        expected_meter = _Meter(5000)
        node = infer_composed(inputs, outputs, self.config, expected_meter)
        result = self.solve([], inputs, outputs, budget=5000)
        self.assertEqual(result["program"], program(node))
        self.assertEqual(result["work"], expected_meter.values)
        self.assertEqual(result["template_work"]["total"], 0)
        self.assertEqual(result["selection_metadata"]["source"], "composed_fallback")

    def test_fallback_uses_only_remaining_shared_allowance(self):
        original = program({"op": "constant", "value": "wrong"})
        inputs = rows((" A ",), (" B ",), (" C ",), (" D ",))
        output = ["A", "B", "C", "D"]
        result = self.solve([original], inputs, output, budget=1000)
        self.assert_fits(result, inputs, output)
        self.assertGreater(result["template_work"]["total"], 0)
        expected_meter = _Meter(1000 - result["template_work"]["total"])
        infer_composed(inputs, output, self.config, expected_meter)
        self.assertEqual(result["fallback_work"], expected_meter.values)
        for key, count in result["work"].items():
            self.assertEqual(
                count, result["template_work"][key] + result["fallback_work"][key]
            )
        self.assertLessEqual(result["work"]["total"], 1000)

    def test_all_small_budgets_stop_without_overspending(self):
        original = program({"op": "concat", "parts": [field(0), field(1)]})
        cache = prepare_templates([original], self.config)
        for limit in (1, 2, 3, 7, 16, 31, 64, 127, 255):
            for fallback in (False, True):
                with self.subTest(limit=limit, fallback=fallback):
                    result = solve_templates(
                        self.inputs,
                        ["unmatched"] * 4,
                        self.config,
                        cache,
                        budget=limit,
                        fallback=fallback,
                    )
                    self.assertLessEqual(result["work"]["total"], limit)
                    if result["program"] is not None:
                        self.assert_fits(result, self.inputs, ["unmatched"] * 4)

    def test_inputs_cache_report_and_results_are_detached(self):
        original = program({"op": "upper", "arg": field(1)})
        source = [original]
        before = copy.deepcopy(source)
        config_before = copy.deepcopy(self.config)
        cache = prepare_templates(source, self.config)
        report = cache.report
        report["preparation_work"]["total"] = -1
        self.assertGreater(cache.report["preparation_work"]["total"], 0)
        original["expr"]["arg"]["index"] = 0
        result = solve_templates(
            self.inputs,
            ["ONE", "TWO", "THREE", "FOUR"],
            self.config,
            cache,
            fallback=False,
        )
        identity = cache.preparation_sha256
        result["program"]["expr"]["arg"]["index"] = 0
        second = solve_templates(
            self.inputs,
            ["ONE", "TWO", "THREE", "FOUR"],
            self.config,
            cache,
            fallback=False,
        )
        self.assertEqual(second["program"]["expr"]["arg"]["index"], 1)
        self.assertEqual(cache.preparation_sha256, identity)
        self.assertEqual(self.config, config_before)
        self.assertEqual(before[0]["expr"]["arg"]["index"], 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cache._payload = "changed"

    def test_configuration_implementation_and_cache_changes_are_refused(self):
        cache = prepare_templates([program(field(0))], self.config)
        changed = {**self.config, "max_ops": 23}
        with self.assertRaises(ValueError):
            solve_templates(self.inputs, ["x"] * 4, changed, cache)
        with (
            patch(
                "cognition.template_reuse.implementation_hash", return_value="0" * 64
            ),
            self.assertRaises(ValueError),
        ):
            solve_templates(self.inputs, ["x"] * 4, self.config, cache)
        object.__setattr__(cache, "_payload", cache._payload + " ")
        with self.assertRaises(ValueError):
            solve_templates(self.inputs, ["x"] * 4, self.config, cache)
        with self.assertRaises(ValueError):
            solve_templates(self.inputs, ["x"] * 4, self.config, {})
        with self.assertRaises(ValueError):
            solve_templates(self.inputs, ["x"] * 4, self.config, PreparedTemplates())

    def test_unique_template_and_history_capacity_never_silently_truncate(self):
        source = [program(field(0)), program({"op": "upper", "arg": field(0)})]
        with self.assertRaises(WorkLimit) as caught:
            prepare_templates(source, self.config, max_templates=1)
        self.assertEqual(caught.exception.report["status"], "failed")
        self.assertGreater(caught.exception.report["preparation_work"]["total"], 0)
        with self.assertRaises(ValueError):
            prepare_templates([program(field(0))] * 4097, self.config)
        for value in (True, False, 0, 4097, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare_templates([], self.config, max_templates=value)

    def test_malformed_programs_and_new_language_are_refused(self):
        bad = [
            program(field(True)),
            program(field(8)),
            program({"op": "exec", "arg": "ignored"}),
            {"version": "safe-text-v2", "expr": field(0)},
            program({"op": "field", "index": 0, "code": "ignored"}),
            program({"op": "constant", "value": False}),
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                prepare_templates([item], self.config)
        with self.assertRaises(ValueError):
            prepare_templates([], {**self.config, "version": "safe-text-v2"})
        with self.assertRaises(ValueError):
            prepare_templates([], {**self.config, "max_macros": False})
        recursive = {"op": "upper"}
        recursive["arg"] = recursive
        with self.assertRaises(ValueError):
            prepare_templates([program(recursive)], self.config)

    def test_invalid_inputs_and_boolean_authority_are_refused(self):
        cache = prepare_templates([], self.config)
        for inputs, outputs in (
            ([], []),
            (self.inputs, ["x"]),
            (["not-json"], ["x"]),
            ([json.dumps([True])], ["x"]),
            (self.inputs, [True] * 4),
        ):
            with (
                self.subTest(inputs=inputs, outputs=outputs),
                self.assertRaises(ValueError),
            ):
                solve_templates(inputs, outputs, self.config, cache)
        for value in (True, False, 0, 1.5, 10_000_001):
            with self.subTest(budget=value), self.assertRaises(ValueError):
                solve_templates(
                    self.inputs, ["x"] * 4, self.config, cache, budget=value
                )
        for value in (0, 1, "yes", None):
            with self.subTest(fallback=value), self.assertRaises(ValueError):
                solve_templates(
                    self.inputs, ["x"] * 4, self.config, cache, fallback=value
                )

    def test_candidate_capacity_and_interpreter_errors_remain_measured(self):
        config = {**self.config, "max_candidates": 1}
        cache = prepare_templates(
            [
                program(
                    {"op": "token", "arg": field(0), "pattern": "digits", "index": 0}
                )
            ],
            config,
        )
        result = solve_templates(self.inputs, ["x"] * 4, config, cache, fallback=False)
        self.assertIsNone(result["program"])
        self.assertEqual(result["statistics"]["candidate_attempts"], 1)
        self.assertEqual(result["work"]["errors"], 1)
        self.assertLessEqual(result["work"]["total"], 50000)


if __name__ == "__main__":
    unittest.main()
