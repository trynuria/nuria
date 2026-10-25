"""Safe structural reuse, provenance and finite preprocessing boundaries."""

import copy
import json
import unittest

from cognition.skills import fingerprint
from cognition.text_abstractions import (
    PreparedAbstraction,
    extract_abstractions,
    instantiate,
    instantiate_with_report,
    prepare_abstraction,
)
from cognition.text_skills import DEFAULT_TEXT_CONFIG, execute_checked, validate_program


def field(index):
    return {"op": "field", "index": index}


def unary(name, node):
    return {"op": name, "arg": node}


def program(node):
    return {"version": "safe-text-v1", "expr": node}


def record(node, origin):
    value = program(node)
    return {
        "program": value,
        "program_sha256": fingerprint(value),
        "origin_sha256": fingerprint({"origin": origin}),
        "evidence_sha256": fingerprint({"receipt": origin}),
    }


def rehash(value):
    value["sha256"] = fingerprint(
        {key: item for key, item in value.items() if key != "sha256"}
    )
    return value


def alias_program(index):
    return {
        "op": "concat",
        "parts": [
            unary("lower", field(index)),
            {"op": "constant", "value": ":"},
            unary("upper", field(index)),
        ],
    }


class TextAbstractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        cls.records = [record(alias_program(0), 0), record(alias_program(2), 1)]
        cls.library = extract_abstractions(
            cls.records, cls.config, source_id="nuria:abstraction-test"
        )
        cls.abstraction = next(
            item
            for item in cls.library["abstractions"]
            if item["body"]["op"] == "concat"
            and item["arity"] == 1
            and item["hole_occurrences"] == [2]
            and item["body"]["parts"][0]["op"] == "lower"
        )

    def test_all_reported_support_reconstructs_exact_original_subtrees(self):
        self.assertTrue(self.library["abstractions"])
        for abstraction in self.library["abstractions"]:
            self.assertGreaterEqual(abstraction["fixed_nodes"], 2)
            self.assertGreaterEqual(len(abstraction["support"]), 2)
            for support in abstraction["support"]:
                with self.subTest(pattern=abstraction["pattern_sha256"]):
                    result = instantiate(abstraction, support["arguments"], self.config)
                    self.assertEqual(
                        fingerprint(result["expr"]), support["subtree_sha256"]
                    )
                    parent = next(
                        r["program"]["expr"]
                        for r in self.records
                        if r["program_sha256"] == support["program_sha256"]
                    )
                    for step in support["path"]:
                        parent = parent[step]
                    self.assertEqual(result["expr"], parent)

    def test_repeated_field_alias_becomes_one_repeated_argument(self):
        arg = {"op": "slice", "arg": field(4), "start": 1, "end": None}
        expected = alias_program(4)
        expected["parts"][0]["arg"] = copy.deepcopy(arg)
        expected["parts"][2]["arg"] = copy.deepcopy(arg)
        result = instantiate_with_report(self.abstraction, [arg], self.config)
        self.assertEqual(result["program"], program(expected))
        self.assertEqual(result["expanded_nodes"], 8)
        self.assertGreater(result["construction_work"], result["expanded_nodes"])
        checked = execute_checked(
            result["program"], json.dumps(["", "", "", "", "AbCd"]), self.config
        )
        self.assertEqual(checked["output"], "bcd:BCD")
        self.assertIsNone(checked["error"])

    def test_new_composition_need_not_be_a_retained_whole_program(self):
        arg = unary(
            "strip", {"op": "split", "arg": field(1), "separator": "|", "index": 0}
        )
        result = instantiate(self.abstraction, [arg], self.config)
        self.assertNotIn(
            fingerprint(result), {r["program_sha256"] for r in self.records}
        )
        output = execute_checked(
            result, json.dumps(["unused", " AbC |rest"]), self.config
        )
        self.assertEqual(output["output"], "abc:ABC")
        self.assertGreater(output["work"]["primitive_attempts"], 1)

    def test_pair_anti_unification_preserves_different_argument_aliases(self):
        def pair(a, b):
            return {
                "op": "concat",
                "parts": [unary("upper", field(a)), unary("lower", field(b))],
            }

        # First parent's repeated field must not collapse two distinct second-
        # parent fields into one hole during paired anti-unification.
        library = extract_abstractions(
            [record(pair(0, 0), 10), record(pair(1, 2), 11)],
            self.config,
            source_id="nuria:abstraction-test",
        )
        matches = [
            a
            for a in library["abstractions"]
            if a["body"]
            == {
                "op": "concat",
                "parts": [
                    unary("upper", {"op": "hole", "index": 0}),
                    unary("lower", {"op": "hole", "index": 1}),
                ],
            }
        ]
        self.assertTrue(matches)
        abstraction = matches[0]
        self.assertEqual(abstraction["arity"], 2)
        result = instantiate(abstraction, [field(3), field(4)], self.config)
        self.assertEqual(result, program(pair(3, 4)))

    def test_proper_subtree_holes_are_extracted_with_literal_parameters(self):
        def make(index, case):
            inner = {"op": "split", "arg": field(index), "separator": "|", "index": -1}
            return unary(
                "upper",
                {"op": "slice", "arg": unary(case, inner), "start": 1, "end": -1},
            )

        library = extract_abstractions(
            [record(make(0, "lower"), 20), record(make(2, "strip"), 21)],
            self.config,
            source_id="nuria:abstraction-test",
        )
        body = unary(
            "upper",
            {"op": "slice", "arg": {"op": "hole", "index": 0}, "start": 1, "end": -1},
        )
        matches = [a for a in library["abstractions"] if a["body"] == body]
        self.assertTrue(matches)
        self.assertTrue(
            any(s["arguments"][0]["op"] != "field" for s in matches[0]["support"])
        )
        replacement = {"op": "replace", "arg": field(3), "old": "-", "new": ""}
        expected = unary(
            "upper", {"op": "slice", "arg": replacement, "start": 1, "end": -1}
        )
        self.assertEqual(
            instantiate(matches[0], [replacement], self.config), program(expected)
        )

    def test_condition_selector_and_constant_parameters_remain_literal(self):
        def make(index):
            return {
                "op": "if_equal",
                "field": 0,
                "value": "yes",
                "then": unary("upper", field(index)),
                "else": {"op": "constant", "value": "no"},
            }

        library = extract_abstractions(
            [record(make(1), 30), record(make(2), 31)],
            self.config,
            source_id="nuria:abstraction-test",
        )
        abstraction = next(
            a
            for a in library["abstractions"]
            if a["body"]["op"] == "if_equal" and a["arity"] == 1
        )
        result = instantiate(abstraction, [field(3)], self.config)
        self.assertEqual(result, program(make(3)))
        self.assertEqual(result["expr"]["field"], 0)
        self.assertEqual(result["expr"]["value"], "yes")

    def test_deterministic_order_and_inputs_are_not_mutated(self):
        before = copy.deepcopy(self.records)
        reverse = extract_abstractions(
            list(reversed(self.records)),
            self.config,
            source_id="nuria:abstraction-test",
        )
        self.assertEqual(reverse, self.library)
        self.assertEqual(self.records, before)
        self.assertEqual(
            self.library["work"]["total"],
            sum(value for key, value in self.library["work"].items() if key != "total"),
        )

    def test_duplicates_do_not_create_distinct_parent_support(self):
        duplicate = record(alias_program(0), 100)
        library = extract_abstractions(
            [self.records[0], duplicate],
            self.config,
            source_id="nuria:abstraction-test",
        )
        self.assertEqual(library["abstractions"], [])
        wrong_origin = copy.deepcopy(self.records)
        wrong_origin[1]["origin_sha256"] = wrong_origin[0]["origin_sha256"]
        with self.assertRaises(ValueError):
            extract_abstractions(
                wrong_origin, self.config, source_id="nuria:abstraction-test"
            )

    def test_invalid_provenance_or_program_hash_is_refused(self):
        for key in ("program_sha256", "origin_sha256", "evidence_sha256"):
            altered = copy.deepcopy(self.records)
            altered[0][key] = "short"
            with self.subTest(key=key), self.assertRaises(ValueError):
                extract_abstractions(
                    altered, self.config, source_id="nuria:abstraction-test"
                )
        altered = copy.deepcopy(self.records)
        altered[0]["program"]["expr"]["parts"][1]["value"] = "changed"
        with self.assertRaises(ValueError):
            extract_abstractions(
                altered, self.config, source_id="nuria:abstraction-test"
            )

    def test_record_and_configuration_schemas_are_strict(self):
        extra = copy.deepcopy(self.records)
        extra[0]["accepted"] = True
        with self.assertRaises(ValueError):
            extract_abstractions(extra, self.config, source_id="nuria:abstraction-test")
        config = {**self.config, "max_macros": False}
        with self.assertRaises(ValueError):
            extract_abstractions(
                self.records, config, source_id="nuria:abstraction-test"
            )
        for source in ("", "x" * 129, True):
            with self.subTest(source=source), self.assertRaises(ValueError):
                extract_abstractions(self.records, self.config, source_id=source)

    def test_metadata_tampering_and_boolean_counts_fail_closed(self):
        for key in ("sha256", "pattern_sha256", "config_sha256"):
            value = copy.deepcopy(self.abstraction)
            value[key] = "a" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                instantiate(value, [field(0)], self.config)
        value = copy.deepcopy(self.abstraction)
        value["compression"]["node_gain"] = False
        rehash(value)
        with self.assertRaises(ValueError):
            instantiate(value, [field(0)], self.config)
        value = copy.deepcopy(self.abstraction)
        value["hole_occurrences"] = [True]
        rehash(value)
        with self.assertRaises(ValueError):
            instantiate(value, [field(0)], self.config)

    def test_arguments_cannot_add_macro_calls_or_unsafe_native_operations(self):
        for argument in (
            {"op": "hole", "index": 0},
            {"op": "macro", "id": self.abstraction["sha256"]},
            {"op": "eval", "arg": "__import__('os')"},
            field(8),
            {"op": "token", "arg": field(0), "pattern": "(a+)+", "index": 0},
        ):
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                instantiate(self.abstraction, [argument], self.config)
        with self.assertRaises(ValueError):
            instantiate(self.abstraction, [field(0), field(1)], self.config)
        with self.assertRaises(ValueError):
            instantiate(self.abstraction, (field(0),), self.config)

    def test_expanded_size_and_depth_are_not_discounted_to_macro_calls(self):
        argument = field(0)
        for _ in range(7):
            argument = unary("upper", argument)
        validate_program(program(argument), self.config)
        with self.assertRaises(ValueError):
            instantiate(self.abstraction, [argument], self.config)
        big = {
            "op": "concat",
            "parts": [unary("upper", unary("lower", field(0))) for _ in range(4)],
        }
        validate_program(program(big), self.config)
        with self.assertRaises(ValueError):
            instantiate(self.abstraction, [big], self.config)

    def test_cyclic_and_unbounded_body_structures_are_refused(self):
        for body in (
            {"op": "concat", "parts": [field(0)] * 10000},
            {"op": "upper", "arg": field(0), "surprise": [0] * 10000},
        ):
            value = copy.deepcopy(self.abstraction)
            value["body"] = body
            with self.subTest(op=body["op"]), self.assertRaises(ValueError):
                instantiate(value, [field(0)], self.config)
        cyclic = {"op": "upper"}
        cyclic["arg"] = cyclic
        value = copy.deepcopy(self.abstraction)
        value["body"] = cyclic
        with self.assertRaises(ValueError):
            instantiate(value, [field(0)], self.config)

    def test_changed_support_bindings_or_duplicate_origins_are_refused(self):
        value = copy.deepcopy(self.abstraction)
        value["support"][0]["arguments"] = [field(7)]
        rehash(value)
        with self.assertRaises(ValueError):
            instantiate(value, [field(0)], self.config)
        value = copy.deepcopy(self.abstraction)
        value["support"][0]["origin_sha256"] = value["support"][1]["origin_sha256"]
        rehash(value)
        with self.assertRaises(ValueError):
            instantiate(value, [field(0)], self.config)

    def test_rehashed_fake_parent_path_or_parent_program_is_refused(self):
        for key, replacement in (
            ("path", ["arg"]),
            ("parent_program", program(unary("upper", field(0)))),
        ):
            value = copy.deepcopy(self.abstraction)
            value["support"][0][key] = replacement
            rehash(value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                instantiate(value, [field(0)], self.config)

    def test_prepared_body_is_immutable_and_provenance_paid_once(self):
        source = copy.deepcopy(self.abstraction)
        prepared = prepare_abstraction(source, self.config)
        self.assertGreater(prepared.preparation_work, 0)
        first = prepared.apply([field(0)])
        second = prepared.apply([field(2)])
        self.assertEqual(first["program"], program(alias_program(0)))
        self.assertEqual(second["program"], program(alias_program(2)))
        self.assertEqual(
            first["construction_work"], sum(first["construction_counters"].values())
        )
        full = instantiate_with_report(source, [field(0)], self.config)
        self.assertEqual(full["program"], first["program"])
        self.assertEqual(
            full["construction_work"],
            first["construction_work"] + prepared.preparation_work,
        )
        with self.assertRaises(TypeError):
            PreparedAbstraction()
        with self.assertRaises(AttributeError):
            prepared.arity = 2
        # Neither a returned body projection nor later source corruption can
        # change the validated immutable prepared instance.
        projection = prepared.body
        projection["parts"][1]["value"] = "changed"
        source["body"]["parts"][1]["value"] = "also changed"
        source["support"][0]["parent_program"]["expr"] = field(7)
        self.assertEqual(prepared.apply([field(0)]), first)

    def test_prepared_application_keeps_expanded_authority_and_argument_checks(self):
        prepared = prepare_abstraction(self.abstraction, self.config)
        for args in (
            [field(8)],
            [],
            (field(0),),
            [{"op": "macro", "id": self.abstraction["sha256"]}],
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                prepared.apply(args)
        argument = field(0)
        for _ in range(7):
            argument = unary("upper", argument)
        with self.assertRaises(ValueError):
            prepared.apply([argument])

    def test_finite_limits_and_low_budget_report_exhaustion(self):
        for options in (
            {"max_programs": True},
            {"max_abstractions": 65},
            {"max_arity": 0},
            {"max_pairs": 4097},
            {"maximum_work": 0},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                extract_abstractions(
                    self.records,
                    self.config,
                    source_id="nuria:abstraction-test",
                    **options,
                )
        small = extract_abstractions(
            self.records,
            self.config,
            source_id="nuria:abstraction-test",
            maximum_work=1,
        )
        self.assertTrue(small["exhausted"])
        self.assertLessEqual(small["work"]["total"], 1)
        self.assertEqual(small["abstractions"], [])
        self.assertIsNone(small["records_sha256"])
        bounded = extract_abstractions(
            self.records,
            self.config,
            source_id="nuria:abstraction-test",
            max_abstractions=1,
            max_pairs=0,
        )
        self.assertLessEqual(len(bounded["abstractions"]), 1)
        self.assertTrue(bounded["pair_limit_reached"])
        self.assertLessEqual(bounded["work"]["total"], 2_500_000)

    def test_empty_history_is_explicit_and_expansion_returns_fresh_data(self):
        empty = extract_abstractions(
            [], self.config, source_id="nuria:abstraction-test"
        )
        self.assertEqual(empty["abstractions"], [])
        self.assertEqual(empty["records_sha256"], fingerprint([]))
        self.assertFalse(empty["exhausted"])
        arg = field(0)
        result = instantiate(self.abstraction, [arg], self.config)
        result["expr"]["parts"][0]["arg"]["index"] = 7
        self.assertEqual(arg, field(0))
        self.assertEqual(
            instantiate(self.abstraction, [arg], self.config), program(alias_program(0))
        )


if __name__ == "__main__":
    unittest.main()
