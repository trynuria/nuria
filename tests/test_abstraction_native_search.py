import copy
import unittest

from cognition.abstraction_search import (
    _native_operations,
    prepare_grammar,
    solve_abstractions,
)
from cognition.text_abstractions import extract_abstractions
from cognition.text_skills import (
    DEFAULT_TEXT_CONFIG,
    VERSION,
    execute_checked,
    validate_program,
)


class NativeSearchTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        library = extract_abstractions([], self.config, source_id="native-search-test")
        self.grammar = prepare_grammar(library, self.config)

    def test_native_operations_work_without_learned_history(self):
        inputs = ['["  sunrise  "]', '["  moonrise  "]']
        result = solve_abstractions(
            inputs,
            ["SUNRISE", "MOONRISE"],
            self.config,
            self.grammar,
            use_abstractions=False,
            budget=50000,
        )
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["abstractions_used"], [])
        self.assertEqual(result["invocation_counts"]["macro"], 0)
        self.assertGreater(result["invocation_counts"]["native"], 0)
        self.assertEqual(
            execute_checked(result["program"], '["  starlight  "]')["output"],
            "STARLIGHT",
        )

    def test_native_switch_is_effective_and_bound_to_commitment(self):
        inputs = ['["sunrise"]', '["moonrise"]']
        on = solve_abstractions(
            inputs,
            ["SUNRISE", "MOONRISE"],
            self.config,
            self.grammar,
            use_native=True,
            budget=5000,
        )
        off = solve_abstractions(
            inputs,
            ["SUNRISE", "MOONRISE"],
            self.config,
            self.grammar,
            use_native=False,
            budget=5000,
        )
        self.assertIsNotNone(on["program"])
        self.assertIsNone(off["program"])
        self.assertNotEqual(on["commitment"], off["commitment"])
        for name in ("use_native", "use_abstractions"):
            with self.assertRaises(ValueError):
                solve_abstractions(
                    inputs,
                    ["SUNRISE", "MOONRISE"],
                    self.config,
                    self.grammar,
                    **{name: 1},
                )

    def test_parameter_families_stay_inside_original_program_language(self):
        nodes = list(
            _native_operations(
                {"op": "field", "index": 0}, ["a|b,c", "d|e,f"], ["b", "e"]
            )
        )
        self.assertLess(len(nodes), 1200)
        self.assertEqual(
            {n["op"] for n in nodes},
            {
                "strip",
                "lower",
                "upper",
                "title",
                "capitalize",
                "compact",
                "token",
                "split",
                "slice",
                "replace",
                "number",
            },
        )
        for node in nodes:
            validate_program({"version": VERSION, "expr": node}, self.config)

    def test_native_work_limit_has_no_wrong_success(self):
        result = solve_abstractions(
            ['["a"]', '["b"]'],
            ["A", "B"],
            self.config,
            self.grammar,
            budget=40,
            use_abstractions=False,
        )
        self.assertIsNone(result["program"])
        self.assertLessEqual(result["work"]["total"], 40)


if __name__ == "__main__":
    unittest.main()
