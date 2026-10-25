"""Independent labels, structural holdouts and finite authored task bounds."""

import ast
import collections
import copy
import inspect
import json
import unittest

from cognition.text_skills import execute_checked, validate_program
from scripts import build_abstraction_tasks as tasks


class AbstractionTaskTests(unittest.TestCase):
    def test_default_course_has_eight_structural_clusters_and_64_tasks(self):
        course = tasks.generate(0)
        self.assertEqual(len(course["tasks"]), 64)
        self.assertEqual(len(course["clusters"]), 8)
        self.assertEqual(
            set(collections.Counter(t["cluster"] for t in course["tasks"]).values()),
            {8},
        )
        self.assertIn("synthetic", course["source"]["scope"])
        self.assertIn("not external", course["source"]["scope"])
        self.assertEqual(len({t["id"] for t in course["tasks"]}), 64)

    def test_targets_agree_with_the_separate_interpreter_across_entire_pool(self):
        for offset in (0, 8, 12):
            excluded = []
            for family in tasks.STRUCTURAL_POOL[:offset]:
                recipe = {
                    "family": family,
                    "fields": [0, 1],
                    "separator": ":",
                    "joiner": "~",
                }
                excluded.append(tasks.skeleton_hash(tasks.program(recipe)))
            course = tasks.generate(
                31 + offset, per_cluster=1, excluded_skeletons=excluded
            )
            for task in course["tasks"]:
                program = task["private_recipe"]["program"]
                self.assertEqual(validate_program(program, tasks.CONFIG), program)
                pairs = zip(
                    task["diagnostic_inputs"] + task["score_inputs"],
                    task["diagnostic_outputs"] + task["score_outputs"],
                    strict=True,
                )
                for value, expected in pairs:
                    actual = execute_checked(program, value, tasks.CONFIG)
                    self.assertIsNone(actual["error"], (task["family"], actual))
                    self.assertEqual(actual["output"], expected, task["family"])

    def test_oracle_does_not_import_or_execute_a_learner(self):
        tree = ast.parse(inspect.getsource(tasks))
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        names = [node.module for node in imports if isinstance(node, ast.ImportFrom)]
        names += [
            alias.name
            for node in imports
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        self.assertTrue(
            all(not name.startswith(("cognition", "commerce")) for name in names)
        )
        source = inspect.getsource(tasks.target)
        self.assertNotIn("program(", source)
        self.assertNotIn("execute", source)
        recipe = {
            "family": tasks.STRUCTURAL_POOL[1],
            "fields": [2, 5],
            "separator": ":",
            "joiner": "~",
        }
        self.assertEqual(
            tasks.target(
                recipe,
                [
                    "unused",
                    "unused",
                    "  Alpha Beta  ",
                    "unused",
                    "unused",
                    "Gamma DELTA",
                ],
            ),
            "ALPHA~del",
        )

    def test_diagnostic_and_buyer_rows_are_distinct_bounded_and_separately_seeded(self):
        course = tasks.generate(7, per_cluster=1)
        for task in course["tasks"]:
            self.assertEqual(len(task["diagnostic_inputs"]), 4)
            self.assertEqual(len(task["diagnostic_outputs"]), 4)
            self.assertEqual(len(task["score_inputs"]), 24)
            self.assertEqual(len(task["score_outputs"]), 24)
            all_inputs = task["diagnostic_inputs"] + task["score_inputs"]
            self.assertEqual(len(set(all_inputs)), 28)
            self.assertFalse(set(task["diagnostic_inputs"]) & set(task["score_inputs"]))
            for value in all_inputs:
                self.assertLessEqual(
                    len(value.encode()), tasks.CONFIG["max_input_bytes"]
                )
                self.assertEqual(len(json.loads(value)), 6)
            recipe = task["private_recipe"]
            direct = tasks._rows(7, recipe, recipe["instance"], "buyer", 24)
            self.assertEqual(direct, (task["score_inputs"], task["score_outputs"]))

    def test_determinism_seed_variation_and_no_instance_count_dependency(self):
        left, right = tasks.generate(19), tasks.generate(19)
        self.assertEqual(left, right)
        changed = tasks.generate(20)
        self.assertEqual(left["clusters"], changed["clusters"])
        self.assertNotEqual(
            left["tasks"][0]["diagnostic_inputs"],
            changed["tasks"][0]["diagnostic_inputs"],
        )
        small = tasks.generate(19, per_cluster=1)
        self.assertEqual([left["tasks"][i * 8] for i in range(8)], small["tasks"])
        left["config"]["max_ops"] = 1
        self.assertEqual(right["config"]["max_ops"], 24)

    def test_fixed_pool_selection_obeys_full_skeleton_exclusion(self):
        initial = tasks.generate(1, per_cluster=1)
        identities = [c["skeleton_sha256"] for c in initial["clusters"]]
        objects = [c["skeleton"] for c in initial["clusters"]]
        by_hash = tasks.generate(1, per_cluster=1, excluded_skeletons=identities)
        by_object = tasks.generate(1, per_cluster=1, excluded_skeletons=objects)
        self.assertEqual(by_hash, by_object)
        self.assertFalse(
            set(identities) & {c["skeleton_sha256"] for c in by_hash["clusters"]}
        )
        self.assertEqual(
            [c["family"] for c in by_hash["clusters"]],
            list(tasks.STRUCTURAL_POOL[8:16]),
        )
        self.assertEqual(
            [
                c["family"]
                for c in tasks.generate(
                    999, per_cluster=1, excluded_skeletons=identities
                )["clusters"]
            ],
            list(tasks.STRUCTURAL_POOL[8:16]),
        )

    def test_pool_contains_distinct_finite_structures_and_sufficient_composition(self):
        signatures = set()
        for family in tasks.STRUCTURAL_POOL:
            recipe = {
                "family": family,
                "fields": [0, 1],
                "separator": ":",
                "joiner": "~",
            }
            program = tasks.program(recipe)
            validate_program(program, tasks.CONFIG)
            signatures.add(tasks.skeleton_hash(program))

            def counts(node, depth=0):
                children = node.get("parts", []) + (
                    [node["arg"]] if "arg" in node else []
                )
                values = [counts(child, depth + 1) for child in children]
                return 1 + sum(v[0] for v in values), max(
                    [depth, *[v[1] for v in values]]
                )

            nodes, depth = counts(program["expr"])
            self.assertLessEqual(nodes, 24)
            self.assertLessEqual(depth, 8)
            self.assertGreaterEqual(depth, 3)
        self.assertGreaterEqual(len(signatures), 16)
        self.assertEqual(len(signatures), len(tasks.STRUCTURAL_POOL))

    def test_structural_aliases_preserve_field_and_literal_relationships(self):
        def example(a, b, x, y):
            return {
                "version": "safe-text-v1",
                "expr": {
                    "op": "concat",
                    "parts": [
                        {"op": "field", "index": a},
                        {"op": "field", "index": b},
                        {"op": "constant", "value": x},
                        {"op": "constant", "value": y},
                    ],
                },
            }

        source = example(0, 0, "a", "a")
        saved = copy.deepcopy(source)
        self.assertEqual(
            tasks.skeleton(source), tasks.skeleton(example(4, 4, "x", "x"))
        )
        self.assertNotEqual(
            tasks.skeleton(source), tasks.skeleton(example(4, 5, "x", "x"))
        )
        self.assertNotEqual(
            tasks.skeleton(source), tasks.skeleton(example(4, 4, "x", "y"))
        )
        self.assertEqual(source, saved)

    def test_learner_boundary_exposes_only_diagnostic_inputs(self):
        task = tasks.generate(4, per_cluster=1)["tasks"][0]
        brief = tasks.learner_request(task)
        self.assertEqual(set(brief), {"diagnostic_inputs"})
        self.assertEqual(brief["diagnostic_inputs"], task["diagnostic_inputs"])
        brief["diagnostic_inputs"].append("not part of source")
        self.assertEqual(len(task["diagnostic_inputs"]), 4)

    def test_invalid_bounds_and_exhausted_pool_fail_explicitly(self):
        for seed, count in (
            (True, 8),
            (-1, 8),
            (2**256, 8),
            (0, True),
            (0, 0),
            (0, 33),
        ):
            with self.assertRaises(ValueError):
                tasks.generate(seed, count)
        for value in (None, "0" * 64, [True], ["bad"], [0]):
            with self.assertRaises(ValueError):
                tasks.generate(0, excluded_skeletons=value)
        excluded = [
            tasks.skeleton_hash(
                tasks.program(
                    {"family": f, "fields": [0, 1], "separator": ":", "joiner": "~"}
                )
            )
            for f in tasks.STRUCTURAL_POOL
        ]
        with self.assertRaisesRegex(ValueError, "Fewer than eight"):
            tasks.generate(0, excluded_skeletons=excluded)
        invalid = {"version": "safe-text-v1", "expr": {"op": "field", "index": True}}
        with self.assertRaises(ValueError):
            tasks.skeleton(invalid)


if __name__ == "__main__":
    unittest.main()
