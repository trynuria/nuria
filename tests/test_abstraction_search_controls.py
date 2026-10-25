"""Source isolation, shared preprocessing and finite search regressions."""

import copy
import dataclasses
import json
import unittest

from cognition.abstraction_search import prepare_grammar, solve_abstractions
from cognition.skills import fingerprint
from cognition.text_abstractions import extract_abstractions
from cognition.text_skills import DEFAULT_TEXT_CONFIG, VERSION, execute_checked


def history():
    records = []
    for kind in ("token", "slice"):
        for index in (0, 1):
            leaf = {"op": "field", "index": index}
            if kind == "token":
                expr = {
                    "op": "upper",
                    "arg": {
                        "op": "token",
                        "arg": leaf,
                        "pattern": "alpha",
                        "index": 0,
                    },
                }
            else:
                expr = {
                    "op": "slice",
                    "arg": {"op": "upper", "arg": leaf},
                    "start": 0,
                    "end": 3,
                }
            program = {"version": VERSION, "expr": expr}
            records.append(
                {
                    "program": program,
                    "program_sha256": fingerprint(program),
                    "origin_sha256": fingerprint(["control-origin", kind, index]),
                    "evidence_sha256": fingerprint(["control-receipt", kind, index]),
                }
            )
    return records


class AbstractionSearchControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        cls.library = extract_abstractions(
            history(), cls.config, source_id="nuria:search-controls"
        )
        cls.inputs = [
            json.dumps([v])
            for v in (
                "123 orchard pear",
                "9876 marigold stem",
                "55 cobalt azure",
                "2 lavender leaf",
            )
        ]
        cls.outputs = ["ORC", "MAR", "COB", "LAV"]

    def solve(self, library, **kwargs):
        return solve_abstractions(
            self.inputs,
            self.outputs,
            self.config,
            library,
            budget=200000,
            **kwargs,
        )

    def test_raw_library_rejects_mixed_source_and_changed_envelope(self):
        other = extract_abstractions(
            history(), self.config, source_id="nuria:foreign-controls"
        )
        mixed = copy.deepcopy(self.library)
        mixed["abstractions"][0] = copy.deepcopy(other["abstractions"][0])
        with self.assertRaises(ValueError):
            prepare_grammar(mixed, self.config)
        for key, value in (
            ("source_id", "nuria:wrong"),
            ("schema", "unrecognized"),
            ("config_sha256", "0" * 64),
        ):
            changed = copy.deepcopy(self.library)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                prepare_grammar(changed, self.config)

    def test_prepared_source_and_config_bindings_cannot_be_replaced(self):
        prepared = prepare_grammar(self.library, self.config)
        changed_config = {**self.config, "max_parts": 3}
        changes = (
            ({"source_id": "nuria:foreign-controls"}, self.config),
            ({"config_sha256": fingerprint(changed_config)}, changed_config),
            ({"_factory": object()}, self.config),
        )
        for change, config in changes:
            with (
                self.subTest(change=tuple(change)),
                self.assertRaises((TypeError, ValueError)),
            ):
                forged = dataclasses.replace(prepared, **change)
                solve_abstractions(
                    self.inputs, self.outputs, config, forged, budget=200000
                )

    def test_prepared_costs_cannot_be_replaced_with_negative_precharges(self):
        prepared = prepare_grammar(self.library, self.config)
        self.assertTrue(prepared.macros)
        with self.assertRaises((TypeError, ValueError)):
            forged = dataclasses.replace(
                prepared, base_costs=(-100000,) * len(prepared.macros)
            )
            self.solve(forged)

    def test_used_abstraction_ids_resolve_to_the_admitted_library(self):
        prepared = prepare_grammar(self.library, self.config)
        result = self.solve(prepared)
        self.assertEqual(result["status"], "proposed")
        self.assertGreaterEqual(len(result["abstractions_used"]), 2)
        known = {record["sha256"] for record in self.library["abstractions"]}
        self.assertTrue(set(result["abstractions_used"]) <= known)
        checked = execute_checked(
            result["program"], '["77 tangerine tree"]', self.config
        )
        self.assertIsNone(checked["error"])
        self.assertEqual(checked["output"], "TAN")

    def test_shared_preparation_is_declared_and_raw_preparation_is_debited(self):
        prepared = prepare_grammar(self.library, self.config)
        shared, raw = self.solve(prepared), self.solve(self.library)
        self.assertEqual(shared["program"], raw["program"])
        self.assertEqual(shared["status"], "proposed")
        self.assertEqual(shared["commitment"], raw["commitment"])
        self.assertTrue(shared["preparation"]["shared"])
        self.assertFalse(raw["preparation"]["shared"])
        self.assertEqual(shared["preparation"]["work"], prepared.preparation_work)
        self.assertEqual(raw["preparation"]["work"], prepared.preparation_work)
        self.assertGreater(prepared.preparation_work, 0)
        self.assertEqual(
            raw["work"]["total"] - shared["work"]["total"], prepared.preparation_work
        )
        for result in (shared, raw):
            self.assertLessEqual(result["work"]["total"], 200000)
            self.assertTrue(all(v >= 0 for v in result["work"].values()))

    def test_prepared_bodies_have_no_mutable_alias_to_the_source_library(self):
        original = copy.deepcopy(self.library)
        prepared = prepare_grammar(original, self.config)
        immutable_body = prepared.macros[0].body
        immutable_body["op"] = "not-a-native-operation"
        self.assertNotEqual(prepared.macros[0].body, immutable_body)
        with self.assertRaises((AttributeError, dataclasses.FrozenInstanceError)):
            prepared.source_id = "nuria:mutated"
        original["abstractions"].clear()
        original["source_id"] = "nuria:mutated"
        result = self.solve(prepared)
        self.assertEqual(result["status"], "proposed")

    def test_concat_frontier_cap_is_explicit_and_changes_no_authority(self):
        empty = {**copy.deepcopy(self.library), "abstractions": []}
        prepared = prepare_grammar(empty, self.config)
        inputs = ['["ab","cd"]', '["ef","gh"]']
        outputs = ["abXcd", "efXgh"]
        limited = solve_abstractions(
            inputs, outputs, self.config, prepared, budget=5000, maximum_frontier=1
        )
        complete = solve_abstractions(
            inputs, outputs, self.config, prepared, budget=5000, maximum_frontier=128
        )
        self.assertTrue(limited["frontier_limited"])
        self.assertIsNone(limited["program"])
        self.assertFalse(complete["frontier_limited"])
        self.assertEqual(complete["status"], "proposed")
        self.assertEqual(
            execute_checked(complete["program"], '["ij","kl"]', self.config)["output"],
            "ijXkl",
        )
        for result in (limited, complete):
            self.assertLessEqual(result["work"]["total"], 5000)

    def test_candidate_count_stops_at_its_declared_unique_bound(self):
        config = {**self.config, "max_candidates": 1}
        library = extract_abstractions(
            history(), config, source_id="nuria:small-candidate-cap"
        )
        result = solve_abstractions(
            ['["ab","cd"]', '["ef","gh"]'],
            ["AB", "EF"],
            config,
            prepare_grammar(library, config),
            budget=5000,
        )
        self.assertEqual(result["status"], "work_limit")
        self.assertIsNone(result["program"])
        self.assertLessEqual(result["attempted"], config["max_candidates"])


if __name__ == "__main__":
    unittest.main()
