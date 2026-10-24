"""Executable skill synthesis, exact buyer admission and safe retained state."""

import copy
import unittest
from unittest.mock import patch

from cognition.skills import (
    DEFAULT_CONFIG,
    STATE_VERSION,
    VERSION,
    SkillLibrary,
    execute,
    execute_checked,
    fingerprint,
    validate_config,
    validate_program,
)


def program(*ops):
    return {"version": VERSION, "ops": list(ops)}


class SkillTests(unittest.TestCase):
    def library(self):
        return SkillLibrary("fixture:verified-skills")

    def test_synthesizes_actual_program_and_generalizes_to_new_values(self):
        library = self.library()
        report = library.solve(
            [" value=120 units ", "  value=250 units "], ["1.2", "2.5"], budget=2400
        )
        self.assertTrue(report["solved_training"])
        self.assertEqual(execute(report["program"], " value=735 units ")[0], "7.35")
        receipt = library.validate_and_admit(
            report["proposal_id"],
            [" value=375 units "],
            ["3.75"],
            fingerprint("buyer-evaluation-one"),
        )
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["program_sha256"], report["program_sha256"])
        self.assertEqual(receipt["correct_rows"], 1)
        self.assertEqual(len(library.dump()["programs"]), 1)
        self.assertGreater(len(library.dump()["macros"]), 0)
        self.assertEqual(
            library.dump()["total_work"], report["work"]["total"] + receipt["work"]
        )

    def test_verified_whole_program_retrieval_reduces_repeated_search_work(self):
        library = self.library()
        first = library.solve(["value=120 units", "value=240 units"], ["1.2", "2.4"])
        library.validate_and_admit(
            first["proposal_id"], ["value=350 units"], ["3.5"], fingerprint("receipt-1")
        )
        fresh = self.library().solve(
            ["value=450 units", "value=550 units"], ["4.5", "5.5"]
        )
        reused = library.solve(
            ["value=450 units", "value=550 units"], ["4.5", "5.5"], mode="retrieval"
        )
        self.assertEqual(reused["program_sha256"], fresh["program_sha256"])
        self.assertLess(reused["work"]["total"], fresh["work"]["total"])
        self.assertGreater(reused["work"]["primitive_attempts"], 0)

    def test_macro_search_composes_a_new_executable_program(self):
        library = self.library()
        first = library.solve(["value=120 units", "value=240 units"], ["1.2", "2.4"])
        library.validate_and_admit(
            first["proposal_id"], ["value=350 units"], ["3.5"], fingerprint("receipt-1")
        )
        learned = library.dump()["programs"]
        report = library.solve(
            ['"value=450 units"', '"value=550 units"'],
            ["4.5", "5.5"],
            mode="macro",
            budget=10000,
        )
        self.assertTrue(report["solved_training"])
        self.assertNotIn(report["program_sha256"], learned)
        self.assertEqual(execute(report["program"], '"value=735 units"')[0], "7.35")
        self.assertEqual(report["program"]["ops"][0], {"op": "json"})

    def test_training_fit_does_not_admit_a_program_that_fails_hidden_rows(self):
        library = self.library()
        report = library.solve(["0"], ["0"])
        self.assertEqual(report["program"]["ops"], [])
        before = library.dump()["total_work"]
        receipt = library.validate_and_admit(
            report["proposal_id"],
            ["100"],
            ["1"],
            fingerprint("hidden-ambiguity"),
            reveal_counterexample=True,
        )
        self.assertFalse(receipt["accepted"])
        self.assertEqual(
            receipt["counterexample"],
            {"input": "100", "expected": "1", "observed": "100"},
        )
        self.assertEqual(library.dump()["programs"], {})
        self.assertEqual(library.dump()["macros"], {})
        self.assertEqual(library.dump()["total_work"], before + receipt["work"])

    def test_frozen_validation_consumes_real_outcome_without_acquiring_skills(self):
        library = self.library()
        report = library.solve(["n=2", "n=5"], ["2", "5"])
        receipt = library.validate_and_admit(
            report["proposal_id"],
            ["n=7"],
            ["7"],
            fingerprint("frozen-check"),
            retain=False,
        )
        self.assertTrue(receipt["accepted"])
        self.assertEqual(library.dump()["programs"], {})
        self.assertEqual(library.dump()["pending"], {})
        self.assertGreater(receipt["work"], 0)

    def test_primitive_failures_and_search_exhaustion_are_charged(self):
        result = execute_checked(program({"op": "prefix", "value": "n="}), "wrong")
        self.assertEqual(result["error"], "operation_failed")
        self.assertEqual(result["work"]["primitive_attempts"], 1)
        self.assertEqual(result["work"]["errors"], 1)
        self.assertEqual(result["work"]["total"], 2)
        with self.assertRaises(ValueError) as caught:
            execute(program({"op": "prefix", "value": "n="}), "wrong")
        self.assertEqual(caught.exception.work["total"], 2)
        library = self.library()
        report = library.solve(["unknown-format"], ["7"], budget=17)
        self.assertFalse(report["solved_training"])
        self.assertTrue(report["exhausted"])
        self.assertLessEqual(report["work"]["total"], 17)
        self.assertGreater(report["work"]["errors"], 0)
        self.assertEqual(library.dump()["total_work"], report["work"]["total"])

    def test_macro_execution_charges_each_underlying_primitive(self):
        pipeline = program(
            {"op": "prefix", "value": "n="},
            {"op": "decimal"},
            {"op": "scale", "numerator": 1, "denominator": 100},
            {"op": "format"},
        )
        result = execute_checked(pipeline, "n=125")
        self.assertEqual(result["output"], "1.25")
        self.assertEqual(result["work"]["primitive_attempts"], 4)
        self.assertGreaterEqual(result["work"]["total"], 8)

    def test_every_mode_has_same_primitive_language_and_strict_work_budget(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["operations"] = [{"op": "strip"}]
        for mode in ("fresh", "retrieval", "macro", "contextual"):
            library = SkillLibrary("fixture:restricted", config)
            report = library.solve(
                ["n=4"], ["4"], mode=mode, budget=30, features=[0.1, 0.2]
            )
            self.assertFalse(report["solved_training"])
            self.assertLessEqual(report["work"]["total"], 30)
            self.assertEqual(report["config_sha256"], fingerprint(config))

    def test_receipts_are_source_program_and_evaluation_bound_once_only(self):
        library = self.library()
        report = library.solve(["n=2", "n=4"], ["2", "4"])
        receipt = {
            "source_id": library.source_id,
            "proposal_id": report["proposal_id"],
            "program_sha256": report["program_sha256"],
            "evaluation_id": fingerprint("heldout-eval"),
            "accepted": True,
            "correct_rows": 2,
            "total_rows": 2,
            "evidence_sha256": fingerprint("exact-buyer-observations"),
            "work": 4,
        }
        for field, wrong in (
            ("source_id", "fixture:other"),
            ("proposal_id", "a" * 64),
            ("program_sha256", "b" * 64),
            ("correct_rows", 1),
            ("accepted", "true"),
            ("work", -1),
        ):
            changed = {**receipt, field: wrong}
            with self.subTest(field=field), self.assertRaises(ValueError):
                library.admit_verified(report["proposal_id"], changed)
        self.assertEqual(
            library.admit_verified(report["proposal_id"], receipt), receipt
        )
        before = library.dump()
        self.assertEqual(
            library.admit_verified(report["proposal_id"], receipt), receipt
        )
        self.assertEqual(library.dump(), before)
        with self.assertRaises(ValueError):
            library.admit_verified(report["proposal_id"], {**receipt, "work": 5})
        other = library.solve(["n=3"], ["3"])
        with self.assertRaises(ValueError):
            library.admit_verified(
                other["proposal_id"],
                {
                    **receipt,
                    "proposal_id": other["proposal_id"],
                    "program_sha256": other["program_sha256"],
                },
            )

    def test_state_roundtrip_retains_programs_and_pending_exactly(self):
        library = self.library()
        report = library.solve(
            ["value=25%", "value=50%"], ["0.25", "0.5"], features=[0.2, 0.3]
        )
        state = library.dump()
        restored = SkillLibrary(library.source_id, state=state)
        args = (
            report["proposal_id"],
            ["value=75%"],
            ["0.75"],
            fingerprint("restart-eval"),
        )
        self.assertEqual(
            library.validate_and_admit(*args), restored.validate_and_admit(*args)
        )
        self.assertEqual(library.dump(), restored.dump())
        for mode in ("fresh", "retrieval", "macro", "contextual"):
            left = library.solve(["value=30%"], ["0.3"], mode=mode, features=[0.3, 0.4])
            right = restored.solve(
                ["value=30%"], ["0.3"], mode=mode, features=[0.3, 0.4]
            )
            self.assertEqual(left, right)

    def test_ast_registry_types_and_input_bounds_refuse_unsafe_programs(self):
        invalid = [
            {"version": VERSION, "ops": [{"op": "exec", "source": "pass"}]},
            program({"op": "scale", "numerator": 1, "denominator": 100}),
            program({"op": "decimal"}),
            program({"op": "prefix", "value": "unenrolled="}),
            program({"op": "strip", "extra": "ignored"}),
            {"version": "other", "ops": []},
            {"version": VERSION, "ops": [], "code": "unsafe"},
            program(*([{"op": "strip"}] * 9)),
        ]
        for value in invalid:
            with self.subTest(program=value), self.assertRaises(ValueError):
                validate_program(value)
        for value in (None, 2, True, "x" * 257):
            with self.subTest(value=value), self.assertRaises(ValueError):
                execute(program(), value)
        self.assertEqual(
            execute(program(), "__import__('os').system('x')")[0],
            "__import__('os').system('x')",
        )

    def test_numeric_edge_cases_are_canonical_and_bounded(self):
        numeric = program({"op": "decimal"}, {"op": "format"})
        for value, expected in (("001.200", "1.2"), ("-0.0", "0"), ("1e-3", "0.001")):
            self.assertEqual(execute(numeric, value)[0], expected)
        for value in ("NaN", "Infinity", "1e100", "1e-100"):
            self.assertEqual(
                execute_checked(numeric, value)["error"], "operation_failed"
            )
        for value in (
            "1e" + "9" * 200,
            "0e-9999999999999999999999999999999999999",
            "9" * 200,
        ):
            result = execute_checked(numeric, value)
            self.assertEqual(result["error"], "operation_failed")
            self.assertGreaterEqual(result["work"]["primitive_attempts"], 1)
            self.assertGreaterEqual(result["work"]["byte_blocks"], 1)

    def test_semantic_state_capacity_and_candidate_limits_are_enforced(self):
        library = self.library()
        with patch("cognition.skills.MAX_SEMANTIC_STATES", 2):
            report = library.solve([" n=2 "], ["0.02"], budget=100000)
        self.assertTrue(report["exhausted"])
        self.assertLessEqual(report["semantic_states"], 2)
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["max_candidates"] = 2
        report = SkillLibrary("fixture:small", config).solve(["n=2"], ["0.02"])
        self.assertLessEqual(report["work"]["candidates"], 2)

    def test_retained_fragments_require_their_verified_executable_parent(self):
        library = self.library()
        report = library.solve(["n=120%", "n=250%"], ["1.2", "2.5"])
        library.validate_and_admit(
            report["proposal_id"], ["n=350%"], ["3.5"], fingerprint("fragments")
        )
        state = library.dump()
        self.assertGreater(len(state["macros"]), 0)
        restored = SkillLibrary(library.source_id, state=state)
        self.assertEqual(restored.dump(), state)
        damaged = copy.deepcopy(state)
        fragment = next(iter(damaged["macros"].values()))
        fragment["parent_program_sha256"] = "a" * 64
        with self.assertRaises(ValueError):
            SkillLibrary(library.source_id, state=damaged)
        pending = library.solve(["n=4"], ["4"])
        damaged = library.dump()
        damaged["pending"][pending["proposal_id"]]["training_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            SkillLibrary(library.source_id, state=damaged)

    def test_checkpoint_and_configuration_reject_incompatible_terms(self):
        state = self.library().dump()
        self.assertEqual(state["version"], STATE_VERSION)
        for key, value in (
            ("version", "wrong"),
            ("source_id", "fixture:other"),
            ("config_sha256", "a" * 64),
            ("sequence", True),
            ("total_work", -1),
            ("programs", []),
            ("unknown", 1),
        ):
            changed = {**state, key: value}
            with self.subTest(field=key), self.assertRaises(ValueError):
                SkillLibrary("fixture:verified-skills", state=changed)
        for changed in (
            {**DEFAULT_CONFIG, "unknown": 1},
            {**DEFAULT_CONFIG, "max_ops": True},
            {**DEFAULT_CONFIG, "operations": [{"op": "open"}]},
        ):
            with self.assertRaises(ValueError):
                validate_config(changed)

    def test_unverifiable_or_unsolved_tasks_do_not_create_skills(self):
        library = self.library()
        report = library.solve(["a"], ["b"])
        self.assertFalse(report["solved_training"])
        self.assertIsNone(report["proposal_id"])
        self.assertEqual(library.dump()["pending"], {})
        self.assertEqual(library.dump()["programs"], {})
        with self.assertRaises(ValueError):
            library.validate_and_admit("a" * 64, ["a"], ["b"], fingerprint("invalid"))


if __name__ == "__main__":
    unittest.main()
