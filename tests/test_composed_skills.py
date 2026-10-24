"""The production wrapper enforces the studied four-audit information boundary."""

import copy
import json
import unittest
from unittest.mock import patch

from cognition.active_skills import probe_cost
from cognition.composed_skills import ComposedSkillSession
from cognition.skills import fingerprint
from cognition.text_composition import CompositionalTextLibrary
from cognition.text_skills import DEFAULT_TEXT_CONFIG, execute_checked

SOURCE = "fixture:composed-four-audits"


def rows(values):
    return [json.dumps([value], separators=(",", ":")) for value in values]


INPUTS = rows(
    ["red fox", "blue albatross", "green eagle", "amber crocodile", "ivory ibis"]
)
OUTPUTS = ["RED", "BLUE", "GREEN", "AMBER", "IVORY"]


def receipt(action, output, source=SOURCE):
    return {
        "source_id": source,
        "input_index": action["input_index"],
        "input_sha256": action["input_sha256"],
        "output": output,
        "receipt_id": fingerprint(["four-audit-fixture", action["id"], output]),
        "work": probe_cost(action["input"], output),
    }


def complete(session, outputs=OUTPUTS):
    for _ in range(12):
        action = session.next_action()
        if action["kind"] == "probe":
            session.observe_probe(
                action["id"], receipt(action, outputs[action["input_index"]])
            )
        elif action["kind"] == "synthesize":
            session.perform_synthesis(action["id"])
        else:
            return session.result()
    raise AssertionError("Four-audit session failed to terminate")


class ComposedSkillTests(unittest.TestCase):
    def make(self, inputs=INPUTS, **kwargs):
        return ComposedSkillSession(
            SOURCE, copy.deepcopy(DEFAULT_TEXT_CONFIG), inputs, **kwargs
        )

    def test_exactly_four_distinct_labels_precede_any_solver_execution(self):
        session = self.make()
        with patch.object(
            session.library, "solve", wraps=session.library.solve
        ) as solver:
            for index in range(4):
                action = session.next_action()
                self.assertEqual(action["kind"], "probe")
                self.assertEqual(action["input_index"], index)
                solver.assert_not_called()
                session.observe_probe(action["id"], receipt(action, OUTPUTS[index]))
                solver.assert_not_called()
            action = session.next_action()
            self.assertEqual(action["kind"], "synthesize")
            solver.assert_not_called()
            session.perform_synthesis(action["id"])
            solver.assert_called_once()
            self.assertEqual(solver.call_args.args, (INPUTS[:4], OUTPUTS[:4]))
            self.assertEqual(
                solver.call_args.kwargs, {"mode": "retained_factored", "budget": 50_000}
            )
        self.assertEqual(session.result()["probes_used"], 4)

    def test_nested_capability_matches_direct_four_example_study_solver(self):
        session = self.make()
        result = complete(session)
        direct = CompositionalTextLibrary(SOURCE, DEFAULT_TEXT_CONFIG).solve(
            INPUTS[:4], OUTPUTS[:4], mode="retained_factored", budget=50_000
        )
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["program"], direct["program"])
        self.assertEqual(result["program_sha256"], direct["program_sha256"])
        self.assertEqual(result["work"]["synthesis_work"], direct["work"]["total"])
        self.assertEqual(result["work"]["portfolio_work"], 0)
        self.assertGreater(result["work"]["routing_work"], 0)
        self.assertEqual(
            execute_checked(result["program"], rows(["violet woodpecker"])[0])[
                "output"
            ],
            "VIOLET",
        )
        # Merely fitting diagnostics is not buyer admission.
        self.assertEqual(session.library.dump()["programs"], {})
        self.assertIn(result["proposal_id"], session.library.dump()["pending"])

    def test_pending_and_completed_actions_recover_identically_without_reexecution(
        self,
    ):
        ordinary = self.make()
        expected = complete(ordinary)
        recovered = self.make()
        for _ in range(12):
            action = recovered.next_action()
            checkpoint = json.loads(json.dumps(recovered.dump()))
            recovered = self.make(state=checkpoint)
            self.assertEqual(recovered.next_action(), action)
            if action["kind"] == "probe":
                observed = receipt(action, OUTPUTS[action["input_index"]])
                recovered.observe_probe(action["id"], observed)
                before = recovered.dump()
                recovered.observe_probe(action["id"], observed)
                self.assertEqual(recovered.dump(), before)
            elif action["kind"] == "synthesize":
                proposal = recovered.perform_synthesis(action["id"])
                after = recovered.dump()
                self.assertEqual(recovered.perform_synthesis(action["id"]), proposal)
                self.assertEqual(recovered.dump(), after)
            else:
                break
        self.assertEqual(recovered.result(), expected)
        self.assertEqual(recovered.dump(), ordinary.dump())

    def test_duplicate_inputs_do_not_count_as_independent_audits(self):
        session = self.make([INPUTS[0], INPUTS[0], *INPUTS[1:4]])
        outputs = [OUTPUTS[0], OUTPUTS[0], *OUTPUTS[1:4]]
        requested = []
        for _ in range(4):
            action = session.next_action()
            requested.append(action["input_index"])
            session.observe_probe(
                action["id"], receipt(action, outputs[action["input_index"]])
            )
        self.assertEqual(requested, [0, 2, 3, 4])
        self.assertEqual(session.next_action()["kind"], "synthesize")
        insufficient = self.make([INPUTS[0], INPUTS[0], INPUTS[1], INPUTS[2]])
        with patch.object(
            insufficient.library,
            "solve",
            side_effect=AssertionError("Three distinct labels are insufficient"),
        ) as solver:
            result = complete(
                insufficient, [OUTPUTS[0], OUTPUTS[0], OUTPUTS[1], OUTPUTS[2]]
            )
        solver.assert_not_called()
        self.assertEqual(result["status"], "abstained")
        self.assertEqual(result["reason"], "insufficient_target_evidence")
        self.assertEqual(result["probes_used"], 3)
        self.assertEqual(result["work"]["synthesis_work"], 0)

    def test_weaker_policies_and_foreign_language_configuration_are_rejected(self):
        for controls in (
            {"maximum_probes": 2},
            {"maximum_probes": 5},
            {"minimum_evidence": 2},
            {"minimum_evidence": 3},
            {"policy": "disagreement"},
        ):
            with self.subTest(controls=controls):
                with self.assertRaisesRegex(ValueError, "exact four-audit policy"):
                    self.make(**controls)
        for config in (
            {**DEFAULT_TEXT_CONFIG, "version": "safe-expression-v1"},
            {**DEFAULT_TEXT_CONFIG, "max_fields": 0},
            {**DEFAULT_TEXT_CONFIG, "unexpected": True},
        ):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    ComposedSkillSession(SOURCE, config, INPUTS)

    def test_checkpoint_cannot_change_source_inputs_or_four_audit_terms(self):
        session = self.make()
        action = session.next_action()
        session.observe_probe(action["id"], receipt(action, OUTPUTS[0]))
        checkpoint = session.dump()
        with self.assertRaisesRegex(ValueError, "source, inputs or terms changed"):
            ComposedSkillSession(
                "fixture:foreign", DEFAULT_TEXT_CONFIG, INPUTS, state=checkpoint
            )
        with self.assertRaisesRegex(ValueError, "source, inputs or terms changed"):
            self.make(
                inputs=[*INPUTS[:4], rows(["changed fifth input"])[0]], state=checkpoint
            )
        changed = copy.deepcopy(checkpoint)
        changed["terms"]["limits"]["minimum_evidence"] = 2
        changed["state_sha256"] = fingerprint(
            {key: value for key, value in changed.items() if key != "state_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "source, inputs or terms changed"):
            self.make(state=changed)


if __name__ == "__main__":
    unittest.main()
