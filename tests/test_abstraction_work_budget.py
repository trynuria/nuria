import copy
import unittest

from cognition.abstraction_search import prepare_grammar, solve_abstractions
from cognition.text_abstractions import extract_abstractions
from cognition.text_skills import DEFAULT_TEXT_CONFIG, execute_checked


class WorkBudgetTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        self.grammar = prepare_grammar(
            extract_abstractions([], self.config, source_id="work-budget-test"),
            self.config,
        )
        self.inputs = ['["upper_alpha"]', '["upper_beta"]']
        self.outputs = ["UPPER_ALPHA", "UPPER_BETA"]

    def test_an_exhausted_assembly_slice_yields_to_other_work(self):
        result = solve_abstractions(
            self.inputs,
            self.outputs,
            self.config,
            self.grammar,
            assembly_slice=1,
            budget=10000,
        )
        self.assertEqual(result["status"], "proposed")
        self.assertGreater(result["assembly_yields"], 0)
        self.assertGreater(result["expanded_states"], 0)
        self.assertEqual(
            execute_checked(result["program"], '["upper_gamma"]')["output"],
            "UPPER_GAMMA",
        )

    def test_all_state_and_assembly_costs_remain_inside_the_job_allowance(self):
        result = solve_abstractions(
            self.inputs,
            ["unavailable-A", "unavailable-B"],
            self.config,
            self.grammar,
            budget=3000,
            assembly_slice=20,
        )
        self.assertLessEqual(result["work"]["total"], 3000)
        self.assertLessEqual(
            sum(result["state_work"].values()), result["work"]["total"]
        )
        self.assertLessEqual(result["assembly_work"], result["work"]["total"])
        self.assertTrue(all(type(k) is str for k in result["state_work"]))
        self.assertEqual(
            result["expansion_turns"], sum(result["invocation_counts"].values())
        )

    def test_legacy_ignores_the_new_assembly_slice(self):
        one = solve_abstractions(
            self.inputs,
            self.outputs,
            self.config,
            self.grammar,
            scheduling="legacy",
            assembly_slice=1,
        )
        many = solve_abstractions(
            self.inputs,
            self.outputs,
            self.config,
            self.grammar,
            scheduling="legacy",
            assembly_slice=10000,
        )
        self.assertEqual(one["program"], many["program"])
        self.assertEqual(one["work"], many["work"])

    def test_scheduling_is_committed_even_when_no_search_is_needed(self):
        one = solve_abstractions(
            ['["a"]', '["b"]'],
            ["a", "b"],
            self.config,
            self.grammar,
            scheduling="legacy",
        )
        two = solve_abstractions(
            ['["a"]', '["b"]'],
            ["a", "b"],
            self.config,
            self.grammar,
            scheduling="work_fair",
        )
        self.assertEqual(one["program"], two["program"])
        self.assertNotEqual(one["commitment"], two["commitment"])
        with self.assertRaises(ValueError):
            solve_abstractions(
                self.inputs,
                self.outputs,
                self.config,
                self.grammar,
                scheduling="unspecified",
            )
        with self.assertRaises(ValueError):
            solve_abstractions(
                self.inputs,
                self.outputs,
                self.config,
                self.grammar,
                assembly_slice=True,
            )


if __name__ == "__main__":
    unittest.main()
