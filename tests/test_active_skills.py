"""Actual bounded probing, routing, acquisition and source-bound recovery."""

import copy
import json
import unittest
from unittest.mock import patch

from cognition.active_skills import ActiveSkillSession, probe_cost
from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG
from cognition.skills import SkillLibrary, execute_checked, fingerprint

SOURCE = "fixture:active-routing"


def record(*values):
    return json.dumps([str(value) for value in values], separators=(",", ":"))


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


def receipt(session, action, output):
    return {
        "source_id": SOURCE,
        "input_index": action["input_index"],
        "input_sha256": action["input_sha256"],
        "output": output,
        "receipt_id": fingerprint({"fixture_action": action["id"], "output": output}),
        "work": probe_cost(action["input"], output),
    }


def verified_library(config):
    library = SkillLibrary(SOURCE, config)
    training = [
        record(*row, 0) for row in ((11, 3, 2), (13, 5, 4), (17, 7, 8), (19, 9, 16))
    ]
    final = [record(*row, 0) for row in ((23, 12, 7), (31, 15, 11))]
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
            raise AssertionError("Fixture did not actually verify its field program")
    return library


def finish(session, outputs):
    for _ in range(12):
        action = session.next_action()
        if action["kind"] == "probe":
            session.observe_probe(
                action["id"], receipt(session, action, outputs[action["input_index"]])
            )
        elif action["kind"] == "synthesize":
            session.perform_synthesis(action["id"])
        else:
            return session.result()
    raise AssertionError("Finite routing session failed to terminate")


class ActiveSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configuration()
        cls.initial = verified_library(cls.config).dump()

    def make(self, inputs=None, **controls):
        return ActiveSkillSession(
            SOURCE,
            self.config,
            inputs or [record(1, 1, 2, 0), record(3, 4, 4, 0)],
            self.initial,
            **controls,
        )

    def test_informative_probe_routes_without_a_caller_skill_or_format(self):
        session = self.make()
        first = session.next_action()
        self.assertEqual(first["kind"], "probe")
        self.assertEqual(first["input_index"], 0)
        state = session.dump()
        self.assertEqual(session.next_action(), first)
        self.assertEqual(session.dump(), state)
        observed = receipt(session, first, "2")
        session.observe_probe(first["id"], observed)
        result = finish(session, ["2", "4"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "retained")
        self.assertIsNone(result["proposal_id"])
        self.assertEqual(result["probes_used"], 1)
        self.assertEqual(
            execute_checked(result["program"], record(19, 31, 43, 0), self.config)[
                "output"
            ],
            "43",
        )
        self.assertGreater(result["work"]["portfolio_work"], 0)
        self.assertGreater(result["work"]["routing_work"], 0)
        self.assertEqual(result["work"]["synthesis_work"], 0)
        before = session.dump()
        session.observe_probe(first["id"], observed)
        self.assertEqual(session.dump(), before)
        changed = {**observed, "output": "1"}
        with self.assertRaisesRegex(ValueError, "already returned"):
            session.observe_probe(first["id"], changed)

    def test_ambiguous_survivors_abstain_instead_of_using_hidden_target(self):
        session = self.make(maximum_probes=1)
        result = finish(session, ["1", "4"])
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason"], "unresolved_ambiguity")
        self.assertEqual(result["probes_used"], 1)
        self.assertIsNone(result["program"])
        self.assertGreater(result["work"]["total"], 0)
        self.assertEqual(
            finish(self.make(maximum_probes=0), ["2", "4"])["status"], "abstained"
        )

    def test_one_unique_probe_cannot_create_a_new_program_but_can_audit_reuse(self):
        duplicate = record(1, 1, 2, 0)
        session = self.make([duplicate, duplicate], maximum_probes=2)
        with patch.object(
            session.library,
            "solve",
            side_effect=AssertionError(
                "One distinct label cannot justify new synthesis"
            ),
        ) as solve:
            result = finish(session, ["99", "99"])
        solve.assert_not_called()
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason"], "insufficient_target_evidence")
        self.assertEqual(result["probes_used"], 1)
        self.assertGreater(result["work"]["probe_work"], 0)
        self.assertEqual(result["work"]["synthesis_work"], 0)
        self.assertFalse(result["accounting_uncertain"])
        reused = finish(self.make([duplicate, duplicate], maximum_probes=2), ["2", "2"])
        self.assertEqual(reused["status"], "proposed")
        self.assertEqual(reused["selection"], "retained")
        self.assertEqual(reused["probes_used"], 1)

    def test_new_program_acquisition_and_separate_final_buyer_admission(self):
        inputs = [
            record(*row, 0)
            for row in ((13, 17, 21), (19, 20, 33), (7, 11, 0), (23, 29, 3))
        ]
        session = self.make(inputs)
        result = finish(session, ["30", "39", "18", "52"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "synthesized")
        self.assertEqual(result["probes_used"], 4)
        self.assertGreater(result["work"]["synthesis_work"], 0)
        self.assertNotIn(result["program_sha256"], self.initial["programs"])
        self.assertNotIn(result["program_sha256"], session.library.dump()["programs"])
        final = [record(50, 70, 90, 0), record(3, 17, 5, 0)]
        admitted = session.library.validate_and_admit(
            result["proposal_id"],
            final,
            ["120", "20"],
            fingerprint({"fixture_final": 1}),
        )
        self.assertTrue(admitted["accepted"])
        self.assertIn(result["program_sha256"], session.library.dump()["programs"])
        self.assertEqual(session.result(), result)
        reused = ActiveSkillSession(
            SOURCE,
            self.config,
            [record(31, 41, 99, 0), record(47, 53, 0, 0)],
            session.library.dump(),
        )
        later = finish(reused, ["72", "100"])
        self.assertEqual(later["selection"], "retained")
        self.assertEqual(later["program_sha256"], result["program_sha256"])
        self.assertEqual(later["work"]["synthesis_work"], 0)

    def test_each_completed_action_recovers_to_identical_next_choice_and_cost(self):
        inputs = [
            record(*row, 0)
            for row in ((13, 17, 21), (19, 20, 33), (7, 11, 0), (23, 29, 3))
        ]
        oracle = ["30", "39", "18", "52"]
        ordinary = self.make(inputs)
        expected = finish(ordinary, oracle)
        recovered = self.make(inputs)
        for _ in range(12):
            action = recovered.next_action()
            saved = json.loads(json.dumps(recovered.dump(), sort_keys=True))
            restored = self.make(inputs, state=saved)
            self.assertEqual(restored.next_action(), action)
            self.assertEqual(restored.work, recovered.work)
            recovered = restored
            if action["kind"] == "probe":
                recovered.observe_probe(
                    action["id"],
                    receipt(recovered, action, oracle[action["input_index"]]),
                )
            elif action["kind"] == "synthesize":
                once = recovered.perform_synthesis(action["id"])
                state = recovered.dump()
                self.assertEqual(recovered.perform_synthesis(action["id"]), once)
                self.assertEqual(recovered.dump(), state)
            else:
                break
        self.assertEqual(recovered.result(), expected)
        self.assertEqual(recovered.dump(), ordinary.dump())

    def test_foreign_probe_bad_cost_and_mutated_checkpoint_are_rejected(self):
        session = self.make()
        action = session.next_action()
        correct = receipt(session, action, "2")
        before = session.dump()
        for changed in (
            {**correct, "source_id": "fixture:foreign"},
            {**correct, "input_sha256": "a" * 64},
            {**correct, "work": 0},
            {**correct, "input_index": True},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    session.observe_probe(action["id"], changed)
                self.assertEqual(session.dump(), before)
        saved = session.dump()
        saved["work"]["total"] += 1
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.make(state=saved)
        session.observe_probe(action["id"], correct)
        saved = session.dump()
        saved["labels"]["0"] = "relabelled"
        body = {key: value for key, value in saved.items() if key != "state_sha256"}
        saved["state_sha256"] = fingerprint(body)
        with self.assertRaisesRegex(ValueError, "outside their receipts"):
            self.make(state=saved)
        with self.assertRaisesRegex(ValueError, "source, inputs or terms"):
            self.make([record(999, 1, 2, 0)], state=session.dump())

    def test_finite_work_and_failed_search_are_retained(self):
        limited = self.make(maximum_work=10)
        result = finish(limited, ["2", "4"])
        self.assertEqual(result["status"], "abstained")
        self.assertLessEqual(result["work"]["total"], 10)
        self.assertGreater(result["work"]["total"], 0)
        self.assertEqual(
            self.make(maximum_work=10, state=limited.dump()).result(), result
        )
        inputs = [record(i, i + 1, i + 3, 0) for i in range(1, 5)]
        failed = self.make(inputs, synthesis_work=1000)
        failure = finish(failed, ["unsupported"] * 4)
        self.assertEqual(failure["status"], "abstained")
        self.assertEqual(failure["reason"], "no_program_found")
        self.assertGreater(failure["work"]["synthesis_work"], 0)
        self.assertEqual(failed.dump()["history"][-1]["status"], "failed")
        work = failure["work"]
        self.assertEqual(
            work["total"],
            sum(
                work[key]
                for key in (
                    "portfolio_work",
                    "routing_work",
                    "probe_work",
                    "synthesis_work",
                )
            ),
        )

    def test_audited_retention_uses_two_probes_and_lazy_verified_lookup(self):
        inputs = [record(1, 1, 2, 0), record(3, 4, 4, 0)]
        session = self.make(
            inputs,
            policy="audited_retention",
            executor=lambda *_args, **_kwargs: self.fail(
                "Audited policy must not run the full portfolio"
            ),
        )
        native = session.library.solve
        with patch.object(session.library, "solve", wraps=native) as solve:
            result = finish(session, ["2", "4"])
        self.assertEqual(solve.call_count, 1)
        self.assertEqual(solve.call_args.kwargs["mode"], "retained_factored")
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "retained")
        self.assertIsNotNone(result["proposal_id"])
        self.assertEqual(result["probes_used"], 2)
        self.assertEqual(result["work"]["portfolio_work"], 0)
        self.assertEqual(result["work"]["candidate_evaluations"], 0)
        baseline = SkillLibrary(SOURCE, self.config, self.initial).solve(
            inputs, ["2", "4"], mode="retained_factored", budget=10000
        )
        self.assertEqual(result["work"]["synthesis_work"], baseline["work"]["total"])
        accepted = session.library.validate_and_admit(
            result["proposal_id"],
            [record(19, 31, 43, 0)],
            ["43"],
            fingerprint({"audited_retained_final": 1}),
        )
        self.assertTrue(accepted["accepted"])
        restored = self.make(inputs, policy="audited_retention", state=session.dump())
        self.assertEqual(restored.result(), result)
        experimental = finish(self.make(inputs, minimum_evidence=2), ["2", "4"])
        self.assertLess(result["work"]["total"], experimental["work"]["total"])

    def test_audited_retention_acquires_missing_composition_and_recovers_policy(self):
        inputs = [
            record(*row, 0)
            for row in ((13, 17, 21), (19, 20, 33), (7, 11, 0), (23, 29, 3))
        ]
        outputs = ["30", "39", "18", "52"]
        expected = finish(self.make(inputs, policy="audited_retention"), outputs)
        session = self.make(inputs, policy="audited_retention")
        for _ in range(8):
            action = session.next_action()
            checkpoint = json.loads(json.dumps(session.dump(), sort_keys=True))
            with self.assertRaisesRegex(ValueError, "source, inputs or terms"):
                self.make(inputs, state=checkpoint)
            session = self.make(inputs, policy="audited_retention", state=checkpoint)
            self.assertEqual(session.next_action(), action)
            if action["kind"] == "probe":
                session.observe_probe(
                    action["id"],
                    receipt(session, action, outputs[action["input_index"]]),
                )
            elif action["kind"] == "synthesize":
                session.perform_synthesis(action["id"])
            else:
                break
        result = session.result()
        self.assertEqual(result, expected)
        self.assertEqual(result["selection"], "synthesized")
        self.assertEqual(result["probes_used"], 2)
        self.assertEqual(result["work"]["portfolio_work"], 0)
        self.assertNotIn(result["program_sha256"], self.initial["programs"])
        checked = execute_checked(result["program"], record(50, 70, 90, 0), self.config)
        self.assertEqual(checked["output"], "120")
        accepted = session.library.validate_and_admit(
            result["proposal_id"],
            [record(50, 70, 90, 0)],
            ["120"],
            fingerprint({"audited_new_final": 1}),
        )
        self.assertTrue(accepted["accepted"])
        self.assertIn(result["program_sha256"], session.library.dump()["programs"])
        duplicate = record(1, 1, 2, 0)
        insufficient = finish(
            self.make([duplicate, duplicate], policy="audited_retention"), ["99", "99"]
        )
        self.assertEqual(insufficient["status"], "abstained")
        self.assertEqual(insufficient["reason"], "insufficient_target_evidence")
        self.assertEqual(insufficient["probes_used"], 1)

    def test_known_synthesis_capacity_abstains_without_unknown_work_or_solve(self):
        inputs = [
            record(*row, 0)
            for row in ((13, 17, 21), (19, 20, 33), (7, 11, 0), (23, 29, 3))
        ]
        outputs = ["30", "39", "18", "52"]
        pending = SkillLibrary(SOURCE, self.config, self.initial)
        for _ in range(64):
            proposal = pending.solve(
                inputs,
                [json.loads(value)[0] for value in inputs],
                mode="factored",
                budget=10000,
            )
            self.assertIsNotNone(proposal["proposal_id"])
        states = {"pending_full": pending.dump()}
        for counter in ("sequence", "total_work"):
            altered = copy.deepcopy(self.initial)
            altered[counter] = 2**53 - 1
            states[counter] = altered
        for reason, initial in states.items():
            with self.subTest(capacity=reason):
                session = ActiveSkillSession(SOURCE, self.config, inputs, initial)
                with patch.object(
                    session.library,
                    "solve",
                    side_effect=AssertionError(
                        "Known capacity cannot disclose synthesis"
                    ),
                ) as solve:
                    result = finish(session, outputs)
                solve.assert_not_called()
                self.assertEqual(result["status"], "abstained")
                self.assertEqual(result["reason"], "library_capacity_unavailable")
                self.assertEqual(result["probes_used"], 4)
                self.assertGreater(result["work"]["portfolio_work"], 0)
                self.assertGreater(result["work"]["probe_work"], 0)
                self.assertEqual(result["work"]["synthesis_work"], 0)
                self.assertFalse(result["accounting_uncertain"])
                self.assertEqual(result["uncertain_reservation"], 0)
                self.assertEqual(len(session.dump()["history"]), 4)
                self.assertEqual(session.library.dump(), initial)
                restored = ActiveSkillSession(
                    SOURCE, self.config, inputs, initial, state=session.dump()
                )
                self.assertEqual(restored.result(), result)
                self.assertEqual(restored.next_action()["kind"], "abstain")

    def test_extensible_language_dispatch_acquires_and_retains_a_text_program(self):
        from cognition.skill_language import execute_checked as language_execute
        from cognition.text_skills import DEFAULT_TEXT_CONFIG

        config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
        inputs = [json.dumps([value]) for value in ("$12.00", "$3.50")]
        session = ActiveSkillSession(
            SOURCE, config, inputs, maximum_probes=2, synthesis_work=50000
        )
        result = finish(session, ["12.00", "3.50"])
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["selection"], "synthesized")
        self.assertEqual(result["program"]["version"], "safe-text-v1")
        final = language_execute(result["program"], json.dumps(["$19.99"]), config)
        self.assertEqual(final["output"], "19.99")
        admitted = session.library.admit_verified(
            result["proposal_id"],
            {
                "source_id": SOURCE,
                "proposal_id": result["proposal_id"],
                "program_sha256": result["program_sha256"],
                "evaluation_id": fingerprint({"text_final": 1}),
                "accepted": True,
                "correct_rows": 1,
                "total_rows": 1,
                "evidence_sha256": fingerprint(final),
                "work": final["work"]["total"],
            },
        )
        self.assertTrue(admitted["accepted"])
        recovered = ActiveSkillSession(
            SOURCE,
            config,
            inputs,
            maximum_probes=2,
            synthesis_work=50000,
            state=session.dump(),
        )
        self.assertEqual(recovered.result(), result)
        self.assertIn(result["program_sha256"], recovered.library.dump()["programs"])

    def test_interrupted_synthesis_remains_unknown_without_silent_retry(self):
        session = self.make([record(i, i + 7, i + 10, 0) for i in range(1, 5)])
        for value in ["9", "11", "13", "15"]:
            action = session.next_action()
            self.assertEqual(action["kind"], "probe")
            session.observe_probe(action["id"], receipt(session, action, value))
        action = session.next_action()
        self.assertEqual(action["kind"], "synthesize")
        original = session.library.dump()
        with patch.object(
            session.library,
            "solve",
            side_effect=RuntimeError("injected local tool interruption"),
        ):
            with self.assertRaises(RuntimeError):
                session.perform_synthesis(action["id"])
        self.assertEqual(session.library.dump(), original)
        self.assertEqual(session.next_action()["kind"], "abstain")
        self.assertTrue(session.result()["accounting_uncertain"])
        self.assertGreater(session.result()["uncertain_reservation"], 0)
        self.assertEqual(session.dump()["history"][-1]["status"], "accounting_unknown")
        with self.assertRaises(ValueError):
            session.perform_synthesis(action["id"])


if __name__ == "__main__":
    unittest.main()
