"""Generator partitions and independent oracle are explicit, finite and stable."""

import copy
import json
import unittest

from cognition.text_skills import execute_checked, validate_program
from scripts.build_guarded_skill_tasks import (
    CONFIG,
    FAMILIES,
    generate,
    learner_request,
    program,
    target,
)


class GuardedSkillTaskBuilderTests(unittest.TestCase):
    def recipe(self, family):
        return {
            "family": family,
            "fields": [0, 1],
            "token_index": 0,
            "separator": ":",
        }

    def test_oracle_has_literal_expected_answers_for_every_template(self):
        values = ["  Acacia BIRCH:delta/path-123.csv ", "  Onyx PINE:reed/end-456.csv "]
        values += ["unused"] * 4
        expected = {
            "preserve-field": "  Acacia BIRCH:delta/path-123.csv ",
            "trim-field": "Acacia BIRCH:delta/path-123.csv",
            "uppercase-field": "  ACACIA BIRCH:DELTA/PATH-123.CSV ",
            "lowercase-field": "  acacia birch:delta/path-123.csv ",
            "uppercase-token": "ACACIA",
            "lowercase-token": "acacia",
            "trim-delimited-field": "Acacia BIRCH",
            "short-token": "Aca",
            "uppercase-prefix": "ACA",
            "joined-fields": "Acacia BIRCH:delta/path-123.csv | Onyx PINE:reed/end-456.csv",
            "joined-normalized-tokens": "ACACIA.csv",
            "joined-code-and-segment": "Aca:REED/END-456.CSV ",
        }
        self.assertEqual(set(expected), set(FAMILIES))
        for family, output in expected.items():
            with self.subTest(family=family):
                self.assertEqual(target(self.recipe(family), values), output)

    def test_programs_match_separate_python_oracle_on_development_fixtures(self):
        for seed in range(4):
            corpus = generate(seed, count=32)
            for task in corpus["tasks"]:
                compiled = task["private_recipe"]["program"]
                validate_program(compiled, corpus["config"])
                for section in ("diagnostic", "score"):
                    for value, expected in zip(
                        task[section + "_inputs"],
                        task[section + "_outputs"],
                        strict=True,
                    ):
                        result = execute_checked(compiled, value, corpus["config"])
                        self.assertIsNone(result["error"])
                        self.assertEqual(result["output"], expected)

    def test_every_original_row_is_distinct_and_partitions_never_overlap(self):
        corpus = generate(0, count=32)
        task_ids, row_ids = set(), set()
        for task in corpus["tasks"]:
            self.assertNotIn(task["id"], task_ids)
            task_ids.add(task["id"])
            self.assertEqual(len(task["diagnostic_inputs"]), 6)
            self.assertEqual(len(task["score_inputs"]), 24)
            self.assertEqual(len(task["diagnostic_outputs"]), 6)
            self.assertEqual(len(task["score_outputs"]), 24)
            self.assertEqual(
                len(set(task["diagnostic_inputs"] + task["score_inputs"])), 30
            )
            self.assertTrue(
                set(task["row_ids"]["diagnostic"]).isdisjoint(task["row_ids"]["score"])
            )
            for identity in task["row_ids"]["diagnostic"] + task["row_ids"]["score"]:
                self.assertNotIn(identity, row_ids)
                row_ids.add(identity)
            self.assertTrue(
                all(
                    len(value.encode()) <= CONFIG["max_input_bytes"]
                    for value in task["diagnostic_inputs"]
                    + task["score_inputs"]
                    + task["diagnostic_outputs"]
                    + task["score_outputs"]
                )
            )

    def test_repeatability_and_seed_separation_are_exact(self):
        self.assertEqual(generate(0, 16), generate(0, 16))
        self.assertNotEqual(generate(0, 16), generate(1, 16))
        self.assertTrue(
            {job["id"] for job in generate(0, 16)["tasks"]}.isdisjoint(
                {job["id"] for job in generate(1, 16)["tasks"]}
            )
        )

    def test_recurrences_and_library_growth_are_declared_without_recipe_hints(self):
        corpus = generate(0, 32)
        known = set()
        actual = 0
        for task in corpus["tasks"]:
            recipe = task["private_recipe"]
            self.assertEqual(recipe["recurrence"], recipe["program_sha256"] in known)
            if recipe["recurrence"]:
                actual += 1
            known.add(recipe["program_sha256"])
        self.assertGreaterEqual(actual, 6)
        self.assertGreaterEqual(len(known), 20)
        for task in corpus["tasks"]:
            visible = learner_request(task)
            self.assertEqual(set(visible), {"diagnostic_inputs"})
            self.assertNotIn("outputs", json.dumps(visible))
            visible["diagnostic_inputs"].clear()
            self.assertEqual(len(task["diagnostic_inputs"]), 6)

    def test_declared_prefix_collision_is_removed_in_later_diagnostics(self):
        corpus = generate(0, 16)
        for task in corpus["tasks"]:
            if task["private_recipe"]["ambiguous_prefix_stress"]:
                for encoded in task["diagnostic_inputs"][:2]:
                    self.assertEqual(len(set(json.loads(encoded))), 1)
                for encoded in task["diagnostic_inputs"][2:] + task["score_inputs"]:
                    self.assertGreater(len(set(json.loads(encoded))), 1)

    def test_invalid_seed_count_family_and_rows_are_refused(self):
        for seed in (True, -1, 2**256, "0"):
            with self.subTest(seed=seed):
                with self.assertRaises(ValueError):
                    generate(seed, 1)
        for count in (False, 0, 129, "1"):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    generate(0, count)
        recipe = self.recipe("unregistered")
        with self.assertRaises(ValueError):
            program(recipe)
        with self.assertRaises(ValueError):
            target(recipe, ["unused"] * 6)
        with self.assertRaises(ValueError):
            target(self.recipe(FAMILIES[0]), ["too", "few"])

    def test_configuration_is_not_shared_mutable_authority(self):
        config = copy.deepcopy(CONFIG)
        generated = generate(0, 1)
        generated["config"]["max_ops"] = 1
        self.assertEqual(CONFIG, config)


if __name__ == "__main__":
    unittest.main()
