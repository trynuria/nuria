"""Evaluator wiring fixtures; these are not inference or benchmark results."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import ReasonerError, digest
from scripts import evaluate_grounded_agent as evaluation


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        # Preserve private fixtures for inspection; no cleanup deletes histories.
        self.directory = Path(tempfile.mkdtemp(prefix="nuria-comparison-test-"))
        self.repository = self.directory / "repository"
        self.repository.mkdir()
        self.events, self.messages = [], []
        for name in evaluation.REQUIRED_SOURCE:
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("immutable fixture source\n")
        self.courses = {}
        oracle = {"tasks": {}}
        for phase, count in (("teaching", 8), ("reserved", 12)):
            tasks = []
            for number in range(count):
                task = {
                    "id": phase + str(number),
                    "db_id": "fixture",
                    "question": phase + " fixture question " + str(number),
                }
                tasks.append(task)
                oracle["tasks"][task["id"]] = {
                    "gradeable": True,
                    "db_id": "fixture",
                    "split": phase,
                    "expected_rows": [[1]],
                    "ordered": False,
                    "column_count": 1,
                    "reference_sql": "SECRET_REFERENCE_MUST_NOT_REACH_THE_MODEL",
                }
            self.courses[phase] = tasks
            evaluation.write(self.directory / (phase + ".json"), {"tasks": tasks})
        evaluation.write(self.directory / "oracle.json", oracle)
        evaluation.write(self.directory / "registry.json", {"revision": "fixture-v1"})
        self.protocol = {
            "schema": evaluation.VERSION,
            "arms": list(evaluation.ARMS),
            "execution_schedule": evaluation.SCHEDULE,
            "seed": 20261025,
            "limits": evaluation.LIMITS,
            "global_completion_cap": 200000,
            "global_wall_seconds": 10800,
            "prior_known_completion_tokens": 0,
            "prior_unknown_completion_ceiling": 0,
            "prior_known_prompt_tokens": None,
            "prior_prompt_tokens_unknown": True,
            "prior_wall_seconds": 0,
            "prior_usage_unknown": False,
            "enable_thinking": False,
            "endpoint": "http://127.0.0.1:1/v1/chat/completions",
            "model_alias": "fixture",
            "model_identity": "fixture-model-v1",
            "selected_task_ids": {
                phase: [t["id"] for t in tasks] for phase, tasks in self.courses.items()
            },
            "artifacts": {
                name: {"path": file, "sha256": evaluation.sha(self.directory / file)}
                for name, file in (
                    ("registry", "registry.json"),
                    ("oracle", "oracle.json"),
                    ("teaching_course", "teaching.json"),
                    ("reserved_course", "reserved.json"),
                )
            },
        }
        self.protocol_path, self.freeze_path = (
            self.directory / "protocol.json",
            self.directory / "freeze.json",
        )
        self.refresh_freeze()

    def refresh_freeze(self):
        self.protocol_path.write_text(json.dumps(self.protocol))
        self.freeze_path.write_text(
            json.dumps(
                {
                    "schema": evaluation.VERSION,
                    "parent_commit": "a" * 40,
                    "protocol_sha256": evaluation.sha(self.protocol_path),
                    "files": {
                        name: evaluation.sha(self.repository / name)
                        for name in evaluation.REQUIRED_SOURCE
                    },
                }
            )
        )

    def dependencies(
        self,
        *,
        unknown=False,
        known_incomplete=False,
        mutate_source=False,
        final_credit_error=False,
    ):
        events, messages = self.events, self.messages
        source_path = self.repository / evaluation.REQUIRED_SOURCE[0]

        class Memory(ProceduralMemory):
            def __init__(self, path, **kwargs):
                self.arm = path.parent.name
                super().__init__(path, **kwargs)

            def settle(self, episode_id, **kwargs):
                events.append((self.arm, "settle", self._origin(episode_id)["job_id"]))
                return super().settle(episode_id, **kwargs)

            def verify(self):
                # Incidental host-only audit metadata must not become a sensory
                # perturbation between matched fixed and neural treatments.
                return super().verify() | {"fixture_host_wall_observation": self.arm}

        class Workspace:
            def __init__(self, path):
                self.registry_sha256 = evaluation.sha(path)

        class Reasoner:
            enable_thinking = False

            def __init__(self, *args, **kwargs):
                pass

            def complete(self, supplied, **kwargs):
                messages.append(supplied)
                if unknown:
                    raise ReasonerError("fixture_unknown_usage")
                if known_incomplete:
                    raise ReasonerError(
                        "model_output_incomplete",
                        usage={
                            "completion_tokens": 13,
                            "prompt_tokens": 23,
                            "total_tokens": 36,
                        },
                    )
                return {"usage": {"completion_tokens": 7, "prompt_tokens": 19}}

        class Agent:
            def __init__(self, reasoner, workspace, memory, **kwargs):
                self.reasoner, self.memory, self.config = reasoner, memory, kwargs

            def run(self, *, job_id, question, db_id, seed):
                episode = self.memory.begin_once(job_id, question, digest(question))
                try:
                    self.reasoner.complete(
                        [{"question": question, "configuration": self.config}],
                        max_tokens=768,
                    )
                    sql, status = "SELECT 1", "proposed"
                    self.memory.record_action(
                        episode,
                        "query-fixture",
                        "query",
                        sql,
                        True,
                        0,
                        digest("fixture receipt"),
                    )
                    proposal = self.memory.propose(
                        episode,
                        sql,
                        procedure_text="Count with explicit cardinality checks",
                    )
                    receipt = {
                        "status": "ok",
                        "complete": True,
                        "rows": [[1]],
                        "columns": ["one"],
                    }
                except ReasonerError:
                    sql, status, receipt = None, "fixture_unknown_usage", None
                    proposal = self.memory.abstain(episode, status)
                events.append((self.memory.arm, "propose", job_id))
                if mutate_source:
                    source_path.write_text("changed during inference")
                return {
                    "episode_id": episode,
                    "status": status,
                    "proposal_sha256": proposal,
                    "final_sql": sql,
                    "selected_receipt": receipt,
                }

        class Bridge:
            def __init__(self, directory, **kwargs):
                self.arm = directory.parent.name
                self.mode = kwargs["mode"]

            def choose(self, job, context, question, features):
                events.append((self.arm, "choose", job, context, features))
                return {
                    "first_tool": "list_tables",
                    "utilities": {"list_tables": 0.5, "read_knowledge": 0.5},
                    "action_spikes": {"list_tables": 1, "read_knowledge": 0},
                    "window_ms": 100,
                    "mode": self.mode,
                    "terms_sha256": "MUST_STAY_HOST_ONLY",
                }

            def settle(self, job, accepted, evidence):
                if final_credit_error and self.mode == "neural" and job == "reserved11":
                    raise RuntimeError("Fixture credit failure")
                events.append((self.arm, "brain_settle", job))
                return {"accepted": accepted}

            def close(self):
                pass

        return {
            "workspace": Workspace,
            "memory": Memory,
            "reasoner": Reasoner,
            "agent": Agent,
            "bridge": Bridge,
        }

    def run_fixture(self, **kwargs):
        with patch.object(evaluation, "current_commit", return_value="a" * 40):
            return evaluation.evaluate(
                self.protocol_path,
                self.freeze_path,
                evaluation.sha(self.freeze_path),
                self.directory / "output",
                repository=self.repository,
                dependencies=self.dependencies(**kwargs),
            )

    def test_six_arms_delayed_credit_and_no_oracle_prompt(self):
        result = self.run_fixture()
        self.assertTrue(result["complete"])
        self.assertEqual(result["allowance"]["known_completion_tokens"], 120 * 7)
        for arm in evaluation.ARMS:
            batch = [event for event in self.events if event[0] == arm]
            reserved_proposals = [
                i
                for i, event in enumerate(batch)
                if event[1] == "propose" and event[2].startswith("reserved")
            ]
            reserved_grades = [
                i
                for i, event in enumerate(batch)
                if event[1] == "settle" and event[2].startswith("reserved")
            ]
            self.assertEqual(len(reserved_proposals), 12)
            self.assertLess(max(reserved_proposals), min(reserved_grades))
            for index, event in enumerate(batch):
                if event[1] == "brain_settle":
                    self.assertEqual(batch[index - 1][1:3], ("settle", event[2]))
            self.assertEqual(result["arms"][arm]["reserved"]["selected"], 12)
            self.assertEqual(result["arms"][arm]["reserved"]["accepted"], 12)
        first = {
            event[0]: event[3]
            for event in self.events
            if event[1:3] == ("choose", "teaching0")
        }
        self.assertEqual(first["hybrid_fixed"], first["hybrid_neural"])
        prompt_text = json.dumps(self.messages)
        self.assertNotIn("SECRET_REFERENCE", prompt_text)
        self.assertNotIn("MUST_STAY_HOST_ONLY", prompt_text)
        self.assertNotIn('"mode": "neural"', prompt_text)
        self.assertEqual(
            result["arms"]["stateless"]["reserved"]["prompt_tokens"], 12 * 19
        )

    def test_balanced_schedule_rotates_and_defers_teaching_feedback(self):
        self.run_fixture()
        proposals = [event for event in self.events if event[1] == "propose"]
        self.assertEqual([event[0] for event in proposals[:6]], list(evaluation.ARMS))
        expected = list(evaluation.ARMS)[1:] + list(evaluation.ARMS)[:1]
        self.assertEqual([event[0] for event in proposals[6:12]], expected)
        last = max(
            i for i, e in enumerate(self.events) if e[1:] == ("propose", "teaching0")
        )
        first = min(
            i for i, e in enumerate(self.events) if e[1:] == ("settle", "teaching0")
        )
        self.assertLess(last, first)

    def test_unknown_usage_preserves_conservative_liability_and_stops(self):
        result = self.run_fixture(unknown=True)
        self.assertFalse(result["complete"])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(result["allowance"]["pending_completion_reservation"], 6000)
        self.assertEqual(result["allowance"]["stop_reason"], "unreconciled_model_usage")
        self.assertEqual(
            sum(
                phase["selected"]
                for arm in result["arms"].values()
                for phase in arm.values()
            ),
            120,
        )
        self.assertEqual(
            sum(
                phase["not_executed"]
                for arm in result["arms"].values()
                for phase in arm.values()
            ),
            119,
        )

    def test_full_job_reservation_prevents_first_call(self):
        self.protocol["prior_known_completion_tokens"] = 194001
        self.refresh_freeze()
        result = self.run_fixture()
        self.assertEqual(len(self.messages), 0)
        self.assertEqual(
            result["allowance"]["stop_reason"], "global_matched_block_reservation_limit"
        )
        self.assertFalse(result["complete"])

    def test_partial_heldout_batch_receives_no_terminal_credit(self):
        # Eight teaching blocks and one full heldout block fit. A second
        # six-job worst-case reservation does not. No subset earns credit.
        self.protocol["prior_known_completion_tokens"] = 163664
        self.refresh_freeze()
        result = self.run_fixture()
        self.assertEqual(len(self.messages), 54)
        for arm in evaluation.ARMS:
            reserved = result["arms"][arm]["reserved"]
            self.assertEqual(reserved["selected"], 12)
            self.assertEqual(reserved["executed"], 1)
            self.assertEqual(reserved["settled"], 0)
            self.assertEqual(reserved["descriptively_graded"], 1)
            self.assertEqual(reserved["descriptively_accepted"], 1)
        self.assertFalse(
            any(
                event[1] == "settle" and event[2].startswith("reserved")
                for event in self.events
            )
        )

    def test_checker_failure_is_recorded_and_stops_new_inference(self):
        path = self.directory / "oracle.json"
        oracle = evaluation.read(path)
        oracle["tasks"]["teaching0"]["column_count"] = 129
        path.write_text(json.dumps(oracle))
        self.protocol["artifacts"]["oracle"]["sha256"] = evaluation.sha(path)
        self.refresh_freeze()
        result = self.run_fixture()
        self.assertEqual(len(self.messages), 6)
        self.assertEqual(
            result["allowance"]["stop_reason"],
            "terminal_checker_failure_requires_review",
        )
        self.assertFalse(result["complete"])
        rows = evaluation.read(self.directory / "output" / "selected-results.json")
        self.assertEqual(rows[0]["settlement_error"], "ValueError")
        self.assertEqual(len(rows), 120)

    def test_freeze_rejection_precedes_reading_reserved_bodies(self):
        (self.repository / evaluation.REQUIRED_SOURCE[0]).write_text("changed")
        observed, original = [], evaluation.read

        def read(path):
            observed.append(Path(path).name)
            return original(path)

        with patch.object(evaluation, "read", side_effect=read):
            with self.assertRaisesRegex(ValueError, "Frozen source differs"):
                self.run_fixture()
        self.assertNotIn("reserved.json", observed)
        self.assertNotIn("oracle.json", observed)
        self.assertFalse((self.directory / "output").exists())

    def test_prior_ambiguous_usage_and_wall_expiry_are_not_renewed(self):
        self.protocol["prior_usage_unknown"] = True
        with self.assertRaises(ValueError):
            evaluation.Allowance(self.protocol)
        self.protocol["prior_usage_unknown"] = False
        self.protocol["prior_wall_seconds"] = 10800
        allowance = evaluation.Allowance(self.protocol, clock=lambda: 10)
        self.assertFalse(allowance.reserve())
        self.assertEqual(allowance.stopped, "global_wall_limit")

    def test_bounded_old_unknown_is_charged_without_inventing_exact_usage(self):
        self.protocol.update(
            {
                "prior_known_completion_tokens": 193233,
                "prior_unknown_completion_ceiling": 768,
                "prior_usage_unknown": True,
            }
        )
        self.refresh_freeze()
        result = self.run_fixture()
        self.assertEqual(len(self.messages), 0)
        state = result["allowance"]
        self.assertEqual(state["known_completion_tokens"], 193233)
        self.assertEqual(state["prior_unknown_completion_ceiling"], 768)
        self.assertEqual(state["conservative_completion_charge"], 194001)
        self.assertTrue(state["prior_usage_unknown"])
        self.assertTrue(state["unknown_usage"])
        self.assertFalse(state["current_unknown_usage"])
        self.assertIsNone(state["prior_known_prompt_tokens"])
        self.assertTrue(state["prior_prompt_tokens_unknown"])
        self.assertEqual(state["stop_reason"], "global_matched_block_reservation_limit")

        # A bounded historical liability permits a new study if its entire job
        # allowance fits, while retaining that liability after known settlement.
        self.protocol["prior_known_completion_tokens"] = 0
        allowance = evaluation.Allowance(self.protocol)
        self.assertTrue(allowance.reserve())
        allowance.finish(7, False)
        self.assertEqual(allowance.state()["conservative_completion_charge"], 775)
        self.assertTrue(allowance.reserve())

    def test_known_incomplete_error_usage_is_counted_without_unknown_stop(self):
        result = self.run_fixture(known_incomplete=True)
        self.assertEqual(len(self.messages), 120)
        self.assertTrue(result["complete"])
        state = result["allowance"]
        self.assertEqual(state["known_completion_tokens"], 120 * 13)
        self.assertEqual(state["pending_completion_reservation"], 0)
        self.assertFalse(state["current_unknown_usage"])
        self.assertIsNone(state["stop_reason"])
        for arm in result["arms"].values():
            self.assertEqual(arm["reserved"]["prompt_tokens"], 12 * 23)
            self.assertEqual(arm["reserved"]["accepted"], 0)

    def test_interrupted_inference_retains_its_full_pending_allowance(self):
        class Interrupted:
            enable_thinking = False

            def complete(self, *args, **kwargs):
                raise KeyboardInterrupt

        allowance = evaluation.Allowance(self.protocol)
        self.assertTrue(allowance.reserve())
        metered = evaluation.MeteredReasoner(Interrupted(), allowance)
        with self.assertRaises(KeyboardInterrupt):
            try:
                metered.complete([], max_tokens=100)
            finally:
                allowance.finish(metered.completion, metered.unknown)
        self.assertEqual(allowance.pending, 6000)
        self.assertEqual(allowance.stopped, "unreconciled_model_usage")
        self.assertFalse(allowance.reserve())

    def test_mid_job_source_change_invalidates_the_run(self):
        result = self.run_fixture(mutate_source=True)
        self.assertFalse(result["complete"])
        self.assertFalse(result["source_freeze_verified_at_end"])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(
            result["allowance"]["stop_reason"], "final_source_freeze_changed"
        )

    def test_complete_terminal_coverage_does_not_hide_final_credit_failure(self):
        result = self.run_fixture(final_credit_error=True)
        self.assertTrue(result["all_terminal_results_settled"])
        self.assertTrue(result["source_freeze_verified_at_end"])
        self.assertFalse(result["complete"])
        self.assertEqual(
            result["allowance"]["stop_reason"], "neural_credit_failure_requires_review"
        )


if __name__ == "__main__":
    unittest.main()
