"""Independent text execution and large buyer checks keep finite search bounds."""

import json
import unittest

from cognition.skills import fingerprint
from cognition.text_skills import DEFAULT_TEXT_CONFIG, TextSkillLibrary, execute_checked
from scripts.evaluate_active_skills import run_job
from scripts.verify_active_skill_results import outcome, text_run


def program(node):
    return {"version": "safe-text-v1", "expr": node}


def field():
    return {"op": "field", "index": 0}


class ActiveSkillResultTests(unittest.TestCase):
    def test_warm_start_recheck_uses_a_new_source_bound_evaluation_namespace(self):
        library = TextSkillLibrary("study-namespace-fixture")
        job = {
            "id": "same-original-task",
            "unsupported": None,
            "diagnostic_inputs": ['["alpha"]', '["beta"]'],
            "diagnostic_outputs": ["alpha", "beta"],
            "score_inputs": ['["gamma"]'],
            "score_outputs": ["gamma"],
        }
        limits = {
            "maximum_work": 100000,
            "synthesis_work": 10000,
            "maximum_probes": 2,
            "minimum_evidence": 2,
        }
        first, state = run_job(
            library.dump(),
            job,
            "fixed2",
            library.config,
            library.source_id,
            limits,
            True,
            "study-one",
        )
        second, restored = run_job(
            state,
            job,
            "fixed2",
            library.config,
            library.source_id,
            limits,
            True,
            "study-two",
        )
        self.assertTrue(first["checked"]["passed"])
        self.assertTrue(second["checked"]["passed"])
        self.assertNotEqual(
            first["admission"]["evaluation_id"], second["admission"]["evaluation_id"]
        )
        self.assertEqual(set(state["programs"]), set(restored["programs"]))

    def test_independent_execution_matches_real_outputs_and_scalar_work(self):
        cases = [
            (field(), ["Example"]),
            ({"op": "slice", "arg": field(), "start": 1, "end": None}, ["$45.20"]),
            (
                {"op": "token", "arg": field(), "pattern": "digits", "index": -1},
                ["id-52-ref-97"],
            ),
            (
                {"op": "split", "arg": field(), "separator": "-", "index": 1},
                ["left-right"],
            ),
            ({"op": "replace", "arg": field(), "old": "ab", "new": "Z"}, ["ab-cab"]),
            ({"op": "compact", "arg": field()}, [" a\t b "]),
            ({"op": "lower", "arg": field()}, ["MIXED"]),
            (
                {
                    "op": "concat",
                    "parts": [field(), {"op": "constant", "value": "-suffix"}],
                },
                ["prefix"],
            ),
            (
                {
                    "op": "if_equal",
                    "field": 0,
                    "value": "special",
                    "then": {"op": "constant", "value": "mapped"},
                    "else": field(),
                },
                ["special"],
            ),
            ({"op": "number", "arg": field(), "scale": "0.01", "places": 2}, ["1,250"]),
            (
                {"op": "number", "arg": field(), "scale": "100", "places": None},
                ["0.125"],
            ),
        ]
        for node, values in cases:
            with self.subTest(operation=node["op"]):
                source = json.dumps(values)
                checked = execute_checked(program(node), source)
                actual, work = text_run(program(node), source, DEFAULT_TEXT_CONFIG)
                self.assertEqual(actual, checked["output"])
                self.assertEqual(work, checked["work"]["total"])

    def test_failed_execution_keeps_attempted_work_and_rejects_unsafe_syntax(self):
        for node, values in (
            (
                {"op": "token", "arg": field(), "pattern": "digits", "index": 0},
                ["none"],
            ),
            (
                {"op": "number", "arg": field(), "scale": "1", "places": None},
                ["1e1000000"],
            ),
            ({"op": "field", "index": 7}, ["one-field"]),
        ):
            source = json.dumps(values)
            actual, work = text_run(program(node), source, DEFAULT_TEXT_CONFIG)
            checked = execute_checked(program(node), source)
            self.assertIsNone(actual)
            self.assertEqual(work, checked["work"]["total"])
            self.assertGreater(work, 0)
        with self.assertRaisesRegex(ValueError, "registered"):
            text_run(
                program(
                    {"op": "token", "arg": field(), "pattern": "(a+)+", "index": 0}
                ),
                '["a"]',
                DEFAULT_TEXT_CONFIG,
            )

    def test_many_original_buyer_rows_do_not_widen_search_or_serve(self):
        library = TextSkillLibrary("large-buyer-fixture")
        proposal = library.solve(['["alpha"]', '["beta"]'], ["alpha", "beta"])
        self.assertIsNotNone(proposal["program"])
        inputs = [json.dumps([f"row-{index}"]) for index in range(65)]
        expected = [f"row-{index}" for index in range(65)]
        checked = outcome(proposal["program"], inputs, expected, library.config)
        receipt = {
            "source_id": library.source_id,
            "proposal_id": proposal["proposal_id"],
            "program_sha256": proposal["program_sha256"],
            "evaluation_id": fingerprint("large-buyer-check"),
            "accepted": True,
            "correct_rows": 65,
            "total_rows": 65,
            "evidence_sha256": fingerprint([inputs, expected, checked["outputs"]]),
            "work": checked["total_work"],
        }
        self.assertTrue(
            library.admit_verified(proposal["proposal_id"], receipt)["accepted"]
        )
        restored = TextSkillLibrary("large-buyer-fixture", state=library.dump())
        self.assertIn(proposal["program_sha256"], restored.dump()["programs"])
        self.assertEqual(restored.config["max_rows"], 32)
        with self.assertRaises(ValueError):
            restored.solve(inputs[:33], expected[:33])
        with self.assertRaises(ValueError):
            restored.serve(proposal["program_sha256"], inputs[:33])
        for total in (True, 0, 2049):
            with self.assertRaises(ValueError):
                restored._receipt(
                    {**receipt, "correct_rows": total, "total_rows": total}
                )
        with self.assertRaises(ValueError):
            restored._receipt({**receipt, "accepted": False})
