"""Learning changes search vocabulary without changing execution authority."""

import copy
import importlib.util
import json
import unittest

from cognition.concept_learning import LIBRARY_VERSION, ConceptLibrary, digest, encode
from cognition.text_skills import execute_checked


def field(index=0):
    return {"op": "field", "index": index}


def upper_first(arg):
    return {
        "op": "upper",
        "arg": {"op": "token", "pattern": "word", "index": 0, "arg": arg},
    }


class ConceptTests(unittest.TestCase):
    def library(self):
        body = encode(upper_first(field())).replace(encode(field()), "#0")
        return ConceptLibrary(
            {
                "version": LIBRARY_VERSION,
                "programs": [],
                "training_sha256": digest([]),
                "concepts": [{"name": "fn_0", "arity": 1, "body": body}],
            }
        )

    def test_reloaded_concept_uses_new_field_and_native_execution(self):
        library = self.library()
        inputs = [
            json.dumps(["irrelevant", value]) for value in ("red fox", "blue whale")
        ]
        result = library.solve(inputs, ["RED", "BLUE"])
        self.assertEqual(result["source"], "concepts")
        self.assertEqual(
            execute_checked(result["program"], '["other", "green eel"]')["output"],
            "GREEN",
        )
        self.assertGreater(result["work"]["primitive_attempts"], 2)
        restored = ConceptLibrary(json.loads(json.dumps(library.state)))
        self.assertEqual(
            restored.solve(inputs, ["RED", "BLUE"])["program"], result["program"]
        )

    def test_cyclic_and_unknown_primitives_are_rejected(self):
        for body in ("(fn_0 #0)", "(shell #0)", "(fn_1 #0)"):
            state = copy.deepcopy(self.library().state)
            state["concepts"][0]["body"] = body
            with self.assertRaises(ValueError):
                ConceptLibrary(state)

    def test_total_budget_includes_failed_concept_search_and_fallback(self):
        for budget in (1, 10, 100, 1000):
            result = self.library().solve(
                ['["alpha one"]', '["beta two"]'],
                ["impossible-A", "impossible-B"],
                budget=budget,
            )
            self.assertLessEqual(result["work"]["total"], budget)
            self.assertIsNone(result["program"])

    def test_bad_feedback_cannot_be_learned(self):
        with self.assertRaisesRegex(ValueError, "failed its supplied checks"):
            ConceptLibrary.learn_checked(
                [
                    {
                        "program": {"version": "safe-text-v1", "expr": field()},
                        "inputs": ['["real"]'],
                        "outputs": ["false"],
                    }
                ]
            )

    @unittest.skipUnless(
        importlib.util.find_spec("stitch_core"), "Optional Stitch dependency"
    )
    def test_actual_stitch_learning_and_checked_incremental_retention(self):
        experiences = []
        for i in range(3):
            experiences.append(
                {
                    "program": {
                        "version": "safe-text-v1",
                        "expr": upper_first(field(i)),
                    },
                    "inputs": ['["red fox", "blue whale", "green eel"]'],
                    "outputs": [("RED", "BLUE", "GREEN")[i]],
                }
            )
        first = ConceptLibrary.learn_checked(experiences[:2])
        learned = ConceptLibrary.learn_checked(experiences[2:], previous=first)
        self.assertEqual(len(learned.programs), 3)
        self.assertGreater(len(learned.definitions), 0)
        self.assertEqual(len(first.programs), 2)
        changed = copy.deepcopy(learned.state)
        changed["programs"].pop()
        with self.assertRaisesRegex(ValueError, "commitment changed"):
            ConceptLibrary(changed)


if __name__ == "__main__":
    unittest.main()
