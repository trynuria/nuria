"""Observed ambiguity, bounded correction and source-bound durable recovery."""

import copy
import json
import unittest
from unittest.mock import patch

from cognition.active_skills import probe_cost
from cognition.guarded_skills import COSTS, GuardedSkillSession
from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG, execute_checked
from cognition.skill_language import library_factory
from cognition.skills import SkillLibrary, fingerprint
from cognition.text_skills import DEFAULT_TEXT_CONFIG, TextSkillLibrary

SOURCE = "fixture:guarded-repair"


def record(a, b, c=0, d=0):
    return json.dumps([str(a), str(b), str(c), str(d)], separators=(",", ":"))


def configuration():
    config = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
    config["operations"] = [{"op": "field", "index": index} for index in range(3)] + [
        {"op": "decimal"},
        {"op": "format"},
        {"op": "add"},
        {"op": "multiply"},
    ]
    config["max_macros"] = 0
    return config


def verified_library(config):
    library = SkillLibrary(SOURCE, config)
    training = [record(11, 3, 2), record(13, 5, 4)]
    final = [record(23, 12, 7), record(31, 15, 11)]
    for column in range(3):
        proposal = library.solve(
            training,
            [json.loads(value)[column] for value in training],
            mode="factored",
            budget=10000,
        )
        accepted = library.validate_and_admit(
            proposal["proposal_id"],
            final,
            [json.loads(value)[column] for value in final],
            fingerprint({"column": column}),
        )
        if not accepted["accepted"]:
            raise AssertionError("Fixture field program was not actually verified")
    return library


def receipt(session, action, output):
    return {
        "source_id": session.source_id,
        "input_index": action["input_index"],
        "input_sha256": action["input_sha256"],
        "output": output,
        "receipt_id": fingerprint({"action": action["id"], "output": output}),
        "work": probe_cost(action["input"], output),
    }


def finish(session, outputs):
    for _ in range(15):
        action = session.next_action()
        if action["kind"] == "probe":
            session.observe_probe(
                action["id"], receipt(session, action, outputs[action["input_index"]])
            )
        elif action["kind"] == "synthesize":
            session.perform_synthesis(action["id"])
        else:
            return session.result()
    raise AssertionError("Bounded guarded controller did not terminate")


def resign(state):
    saved = copy.deepcopy(state)
    saved.pop("state_sha256")
    return {**saved, "state_sha256": fingerprint(saved)}


class GuardedSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configuration()
        cls.initial = verified_library(cls.config).dump()
        cls.inputs = [
            record(1, 1, 100),
            record(3, 3, 200),
            record(7, 11, 300),
            record(13, 19, 400),
        ]

    def make(self, *, inputs=None, initial=None, **controls):
        return GuardedSkillSession(
            SOURCE,
            self.config,
            self.inputs if inputs is None else inputs,
            self.initial if initial is None else initial,
            **controls,
        )

    def test_disagreement_buys_distinguishing_label_instead_of_first_hash(self):
        session = self.make()
        result = finish(session, ["1", "3", "11", "19"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "retained")
        self.assertEqual(result["probes_used"], 4)
        self.assertEqual(result["work"]["synthesis_work"], 0)
        self.assertEqual(
            execute_checked(result["program"], record(31, 59, 97), self.config)[
                "output"
            ],
            "59",
        )
        self.assertGreater(result["work"]["portfolio_work"], 0)
        self.assertGreater(result["work"]["checkpoint_work"], 0)
        self.assertEqual(
            result["work"]["total"], sum(result["work"][key] for key in COSTS)
        )

    def test_two_ambiguous_answers_do_not_establish_a_unique_rule(self):
        result = finish(self.make(maximum_probes=2), ["1", "3", "11", "19"])
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason"], "unresolved_ambiguity")
        self.assertIsNone(result["program"])
        self.assertEqual(result["probes_used"], 2)

    def test_a_single_retained_match_needs_a_fresh_applicability_answer(self):
        initial = copy.deepcopy(self.initial)
        initial["programs"] = {
            ident: program
            for ident, program in initial["programs"].items()
            if execute_checked(program["program"], record(17, 23, 99), self.config)[
                "output"
            ]
            == "17"
        }
        self.assertEqual(len(initial["programs"]), 1)
        original_id = next(iter(initial["programs"]))
        inputs = [
            record(2, 0, 100),
            record(3, 0, 200),
            record(5, 7, 300),
            record(11, 13, 400),
        ]
        session = self.make(inputs=inputs, initial=initial)
        result = finish(session, ["2", "3", "12", "24"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "synthesized")
        self.assertEqual(result["retained_audit_index"], 2)
        self.assertEqual(result["probes_used"], 4)
        self.assertEqual(len(result["synthesis_attempts"]), 1)
        self.assertEqual(result["diagnostic_rejections"], [])
        self.assertNotEqual(result["program_sha256"], original_id)
        self.assertIn(original_id, session.library.dump()["programs"])
        self.assertEqual(
            execute_checked(result["program"], record(17, 23, 101), self.config)[
                "output"
            ],
            "40",
        )
        restored = self.make(inputs=inputs, initial=initial, state=session.dump())
        self.assertEqual(restored.result(), result)

    def test_a_disambiguating_answer_is_not_a_fresh_post_selection_audit(self):
        result = finish(self.make(maximum_probes=3), ["1", "3", "11", "19"])
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason"], "independent_retained_audit_unavailable")
        self.assertEqual(result["probes_used"], 3)

    def test_lookup_exhaustion_preserves_full_fresh_search_quota(self):
        session = self.make(lookup_work=1, maximum_work=100000, synthesis_work=10000)
        calls = []
        actual_solve = session.library.solve

        def checked_solve(*args, **kwargs):
            calls.append(kwargs["budget"])
            return actual_solve(*args, **kwargs)

        with patch.object(session.library, "solve", side_effect=checked_solve):
            result = finish(session, ["1", "3", "7", "13"])
        self.assertTrue(result["lookup_truncated"])
        self.assertEqual(calls, [10000])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "synthesized")
        self.assertLessEqual(result["work"]["portfolio_work"], 1)
        self.assertLessEqual(result["work"]["total"], 100000)

    def test_failed_diagnostic_proposal_is_rejected_then_corrected_within_one_quota(
        self,
    ):
        inputs = [
            record(2, 0, 100),
            record(3, 0, 200),
            record(5, 7, 300),
            record(11, 13, 400),
        ]
        initial = SkillLibrary(SOURCE, self.config).dump()
        session = self.make(inputs=inputs, initial=initial)
        result = finish(session, ["2", "3", "12", "24"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "synthesized")
        self.assertEqual(result["probes_used"], 4)
        self.assertEqual(len(result["synthesis_attempts"]), 2)
        self.assertEqual(len(result["diagnostic_rejections"]), 1)
        rejected = result["diagnostic_rejections"][0]
        self.assertFalse(rejected["receipt"]["accepted"])
        self.assertNotIn("evidence", rejected)
        private_rejection = session.dump()["diagnostic_rejection"]
        self.assertEqual(private_rejection["evidence"]["expected"], "12")
        self.assertEqual(private_rejection["evidence"]["observed"], "5")
        self.assertNotIn(rejected["proposal_id"], session.library.dump()["pending"])
        self.assertIn(rejected["proposal_id"], session.library.dump()["receipts"])
        self.assertNotIn(rejected["program_sha256"], session.library.dump()["programs"])
        self.assertIn(result["proposal_id"], session.library.dump()["pending"])
        self.assertLessEqual(result["work"]["synthesis_work"], 10000)
        self.assertEqual(
            execute_checked(result["program"], record(17, 23, 101), self.config)[
                "output"
            ],
            "40",
        )
        # Final admission remains a separate, genuinely untouched buyer check.
        accepted = session.library.validate_and_admit(
            result["proposal_id"],
            [record(19, 31, 89)],
            ["50"],
            fingerprint({"final": 1}),
        )
        self.assertTrue(accepted["accepted"])
        restored = self.make(inputs=inputs, initial=initial, state=session.dump())
        self.assertEqual(restored.result(), result)

    def test_every_action_recovers_identically_without_reexecuting_completed_tools(
        self,
    ):
        inputs = [
            record(2, 0, 100),
            record(3, 0, 200),
            record(5, 7, 300),
            record(11, 13, 400),
        ]
        initial = SkillLibrary(SOURCE, self.config).dump()
        outputs = ["2", "3", "12", "24"]
        ordinary = self.make(inputs=inputs, initial=initial)
        expected = finish(ordinary, outputs)
        recovered = self.make(inputs=inputs, initial=initial)
        for _ in range(15):
            action = recovered.next_action()
            saved = recovered.dump()
            self.assertEqual(recovered.dump(), saved)
            restored = self.make(inputs=inputs, initial=initial, state=saved)
            self.assertEqual(restored.next_action(), action)
            self.assertEqual(restored.dump(), saved)
            recovered = restored
            if action["kind"] == "probe":
                observed = receipt(recovered, action, outputs[action["input_index"]])
                recovered.observe_probe(action["id"], observed)
                once = recovered.dump()
                recovered.observe_probe(action["id"], observed)
                self.assertEqual(recovered.dump(), once)
            elif action["kind"] == "synthesize":
                once = recovered.perform_synthesis(action["id"])
                saved = recovered.dump()
                self.assertEqual(recovered.perform_synthesis(action["id"]), once)
                self.assertEqual(recovered.dump(), saved)
            else:
                break
        self.assertEqual(recovered.result(), expected)
        self.assertEqual(recovered.dump(), ordinary.dump())

    def test_foreign_receipts_wrong_costs_and_changed_returned_labels_are_refused(self):
        session = self.make()
        action = session.next_action()
        valid = receipt(session, action, "1")
        before = session.dump()
        for changed in (
            {**valid, "source_id": "other:source"},
            {**valid, "work": valid["work"] + 1},
        ):
            with self.assertRaises(ValueError):
                session.observe_probe(action["id"], changed)
            self.assertEqual(session.dump(), before)
        session.observe_probe(action["id"], valid)
        with self.assertRaisesRegex(ValueError, "already returned"):
            session.observe_probe(action["id"], {**valid, "output": "99"})

    def test_checkpoints_bind_source_input_solver_limits_and_cost_receipts(self):
        session = self.make()
        action = session.next_action()
        session.observe_probe(action["id"], receipt(session, action, "1"))
        state = session.dump()
        for controls in (
            {"maximum_probes": 3},
            {"solver_id": "different-solver"},
            {"inputs": [*self.inputs[:-1], record(13, 99, 400)]},
        ):
            with self.assertRaisesRegex(ValueError, "source, inputs, solver or limits"):
                self.make(state=state, **controls)
        changed = copy.deepcopy(state)
        changed["work"]["routing_work"] += 1
        changed["work"]["total"] += 1
        with self.assertRaisesRegex(ValueError, "work ledger"):
            self.make(state=resign(changed))
        changed = copy.deepcopy(state)
        changed["labels"]["0"] = "99"
        with self.assertRaisesRegex(ValueError, "labels changed"):
            self.make(state=resign(changed))
        changed = copy.deepcopy(state)
        changed["requirements"][next(iter(changed["requirements"]))] = 0
        with self.assertRaisesRegex(ValueError, "applicability index"):
            self.make(state=resign(changed))

    def test_custom_factory_is_trusted_at_construction_and_solver_identity_is_bound(
        self,
    ):
        calls = []

        def factory(source_id, config, state=None):
            calls.append(source_id)
            return library_factory(source_id, config, state)

        session = self.make(factory=factory, solver_id="fixture-reviewed-solver-v1")
        action = session.next_action()
        restored = self.make(
            factory=factory,
            solver_id="fixture-reviewed-solver-v1",
            state=session.dump(),
        )
        self.assertEqual(restored.next_action(), action)
        self.assertEqual(calls, [SOURCE, SOURCE, SOURCE])

    def test_interrupted_synthesis_preserves_unknown_liability_and_cannot_retry(self):
        session = self.make(initial=SkillLibrary(SOURCE, self.config).dump())
        while True:
            action = session.next_action()
            if action["kind"] != "probe":
                break
            session.observe_probe(
                action["id"],
                receipt(session, action, ["1", "3", "7", "13"][action["input_index"]]),
            )
        self.assertEqual(action["kind"], "synthesize")
        with patch.object(
            session.library, "solve", side_effect=RuntimeError("interrupted local tool")
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted local tool"):
                session.perform_synthesis(action["id"])
        result = session.result()
        self.assertEqual(result["status"], "abstained")
        self.assertTrue(result["accounting_uncertain"])
        self.assertEqual(result["uncertain_reservation"], action["maximum_work"])
        self.assertLessEqual(
            result["work"]["total"] + result["uncertain_reservation"], 100000
        )
        restored = self.make(
            initial=SkillLibrary(SOURCE, self.config).dump(), state=session.dump()
        )
        self.assertEqual(restored.next_action()["kind"], "abstain")
        with self.assertRaisesRegex(ValueError, "no matching pending"):
            restored.perform_synthesis(action["id"])

    def test_duplicate_only_inputs_cannot_supply_two_distinct_answers(self):
        value = record(1, 1, 100)
        result = finish(self.make(inputs=[value, value, value]), ["1", "1", "1"])
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason"], "insufficient_target_evidence")
        self.assertEqual(result["probes_used"], 1)
        self.assertEqual(result["work"]["synthesis_work"], 0)

    def test_small_whole_job_allowance_is_a_bound_not_free_search(self):
        session = self.make(maximum_work=1, synthesis_work=10000)
        result = session.result()
        self.assertEqual(result["status"], "abstained")
        self.assertLessEqual(result["work"]["total"], 1)
        restored = self.make(maximum_work=1, synthesis_work=10000, state=session.dump())
        self.assertEqual(restored.result(), result)

    def test_budget_abort_exposes_real_pending_proposal_for_buyer_disposition(self):
        initial = SkillLibrary(SOURCE, self.config).dump()
        inputs = [record(1, 10, 100), record(3, 30, 300), record(5, 50, 500)]
        session = self.make(
            inputs=inputs,
            initial=initial,
            maximum_work=1500,
            synthesis_work=100,
        )
        result = finish(session, ["1", "3", "5"])
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(len(result["unresolved_proposals"]), 1)
        abandoned = result["unresolved_proposals"][0]
        self.assertEqual(abandoned["status"], "requires_buyer_disposition")
        self.assertIn(abandoned["proposal_id"], session.library.dump()["pending"])
        self.assertLessEqual(result["work"]["total"], 1500)
        # A genuine final check can settle the abandoned candidate without
        # pretending that budget exhaustion demonstrated incorrect outputs.
        checked = session.library.validate_and_admit(
            abandoned["proposal_id"],
            [record(17, 29, 37)],
            ["17"],
            fingerprint({"abandoned_final_check": 1}),
            retain=False,
        )
        self.assertTrue(checked["accepted"])
        self.assertFalse(session.library.dump()["pending"])
        self.assertFalse(session.library.dump()["programs"])
        restored = self.make(
            inputs=inputs,
            initial=initial,
            maximum_work=1500,
            synthesis_work=100,
            state=session.dump(),
        )
        self.assertFalse(restored.result()["unresolved_proposals"])

    def test_text_language_uses_same_guarded_interface_and_untouched_buyer_admission(
        self,
    ):
        config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        inputs = [json.dumps([value]) for value in ("Alpha", "Beta", "Gamma", "Delta")]
        session = GuardedSkillSession(SOURCE, config, inputs)
        result = finish(session, ["ALPHA", "BETA", "GAMMA", "DELTA"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["probes_used"], 3)
        self.assertEqual(result["selection"], "synthesized")
        restored = GuardedSkillSession(SOURCE, config, inputs, state=session.dump())
        self.assertEqual(restored.result(), result)
        self.assertIsInstance(restored.library, TextSkillLibrary)

    def test_second_failed_diagnostic_ends_correction_without_retaining_either_program(
        self,
    ):
        initial = SkillLibrary(SOURCE, self.config).dump()
        inputs = [
            record(2, 0, 100),
            record(3, 0, 200),
            record(5, 7, 300),
            record(11, 13, 400),
        ]
        session = self.make(inputs=inputs, initial=initial)
        result = finish(session, ["2", "3", "12", "999"])
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(
            result["reason"], "synthesized_program_failed_diagnostic_audit"
        )
        self.assertEqual(len(result["synthesis_attempts"]), 2)
        self.assertEqual(len(result["diagnostic_rejections"]), 2)
        self.assertFalse(session.library.dump()["pending"])
        self.assertFalse(session.library.dump()["programs"])
        self.assertFalse(result["unresolved_proposals"])
        self.assertLessEqual(result["work"]["synthesis_work"], 10000)
        restored = self.make(inputs=inputs, initial=initial, state=session.dump())
        self.assertEqual(restored.result(), result)

    def test_interrupted_retained_execution_preserves_unknown_work_and_cannot_retry(
        self,
    ):
        session = self.make()
        for output in ("1", "3"):
            action = session.next_action()
            session.observe_probe(action["id"], receipt(session, action, output))
        with patch.object(
            session, "executor", side_effect=RuntimeError("local execution interrupted")
        ):
            with self.assertRaisesRegex(RuntimeError, "local execution interrupted"):
                session.next_action()
        result = session.result()
        self.assertTrue(result["accounting_uncertain"])
        self.assertEqual(result["reason"], "execution_accounting_unknown")
        self.assertGreater(result["uncertain_reservation"], 0)
        self.assertLessEqual(
            result["work"]["total"] + result["uncertain_reservation"], 100000
        )
        restored = self.make(state=session.dump())
        self.assertEqual(restored.next_action()["kind"], "abstain")


if __name__ == "__main__":
    unittest.main()
