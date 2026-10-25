import copy
import unittest

import numpy as np

from cognition.abstraction_search import (
    observations,
    prepare_grammar,
    solve_abstractions,
)
from cognition.skills import fingerprint
from cognition.text_abstractions import extract_abstractions
from cognition.text_skills import DEFAULT_TEXT_CONFIG, VERSION, execute_checked


class AbstractionSearchTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        records = []
        for kind in ("token", "slice"):
            for index in (0, 1):
                child = {"op": "field", "index": index}
                if kind == "token":
                    expr = {
                        "op": "upper",
                        "arg": {
                            "op": "token",
                            "arg": child,
                            "pattern": "alpha",
                            "index": 0,
                        },
                    }
                else:
                    expr = {
                        "op": "slice",
                        "arg": {"op": "upper", "arg": child},
                        "start": 0,
                        "end": 3,
                    }
                program = {"version": VERSION, "expr": expr}
                records.append(
                    {
                        "program": program,
                        "program_sha256": fingerprint(program),
                        "origin_sha256": fingerprint([kind, index]),
                        "evidence_sha256": fingerprint(["checked", kind, index]),
                    }
                )
        self.library = extract_abstractions(
            records, self.config, source_id="search-test"
        )

    def test_composes_verified_parts_into_new_solution(self):
        inputs = [
            '["123 orchard pear"]',
            '["9876 marigold stem"]',
            '["55 cobalt azure"]',
            '["2 lavender leaf"]',
        ]
        outputs = ["ORC", "MAR", "COB", "LAV"]
        result = solve_abstractions(
            inputs, outputs, self.config, self.library, budget=200000
        )
        self.assertEqual(result["status"], "proposed")
        self.assertGreaterEqual(len(result["abstractions_used"]), 2)
        self.assertEqual(
            execute_checked(result["program"], '["77 tangerine tree"]')["output"], "TAN"
        )
        self.assertLessEqual(result["work"]["total"], 200000)

    def test_budget_exhaustion_keeps_work_bounded(self):
        result = solve_abstractions(
            ['["abc def"]'], ["UNAVAILABLE"], self.config, self.library, budget=1
        )
        self.assertIsNone(result["program"])
        self.assertEqual(result["status"], "work_limit")
        self.assertLessEqual(result["work"]["total"], 1)

    def test_invalid_ranker_is_not_a_success_or_silent_fallback(self):
        with self.assertRaises(ValueError):
            solve_abstractions(
                ['["a b"]'],
                ["Z"],
                self.config,
                self.library,
                ordering="guided",
                scorer=lambda x: np.full(len(x), np.nan),
            )

    def test_feature_and_config_binding(self):
        features = observations(
            {"op": "field", "index": 0}, ["abc"], ["ABC"], self.config
        )
        self.assertEqual(len(features), 64)
        self.assertTrue(all(0 <= v <= 1 for v in features))
        changed = copy.deepcopy(self.library)
        changed["config_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            solve_abstractions(['["a"]'], ["A"], self.config, changed)

    def test_search_does_not_mutate_library(self):
        before = fingerprint(self.library)
        solve_abstractions(
            ['["a"]'], ["unavailable"], self.config, self.library, budget=500
        )
        self.assertEqual(fingerprint(self.library), before)

    def test_frontier_yields_between_primitive_invocations(self):
        prepared = prepare_grammar(self.library, self.config)
        result = solve_abstractions(
            [
                '["alpha beta","gamma delta","epsilon zeta"]',
                '["orchid rose","silver fern","cedar pine"]',
            ],
            ["IMPOSSIBLE-A", "IMPOSSIBLE-B"],
            self.config,
            prepared,
            budget=3000,
        )
        self.assertGreaterEqual(result["expanded_states"], 3)
        self.assertGreater(result["expansion_turns"], result["expanded_states"])


if __name__ == "__main__":
    unittest.main()
