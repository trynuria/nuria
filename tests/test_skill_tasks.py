"""Actual safe programs through retained neural intent and commerce journals."""

import copy
import json
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_action_loop import decision

from cognition.agency import NeuralTaskPolicy
from cognition.skills import DEFAULT_CONFIG
from cognition.worker import Organism
from commerce.action_loop import ActionLoop
from commerce.ledger import Ledger, canonical
from commerce.local_skills import LocalSkills
from commerce.task_runtime import run_actions
from commerce.tasks import contractor_brief, digest, validate_task, verify_delivery


def config():
    value = copy.deepcopy(DEFAULT_CONFIG)
    value.update(
        operations=[
            {"op": "strip"},
            {"op": "prefix", "value": "value="},
            {"op": "suffix", "value": " units"},
        ],
        max_ops=4,
        max_input_bytes=64,
        max_rows=16,
    )
    return value


def provider(*, enabled=True, maximum_jobs=10):
    return {
        "type": "local_skills",
        "id": "included-skills",
        "source_id": "nuria:public-repair-test",
        "enabled": enabled,
        "config": config(),
        "mode": "retrieval",
        "maximum_work_per_job": 10000,
        "maximum_work_per_day": 100000,
        "maximum_jobs_per_day": maximum_jobs,
        "cooldown_seconds": 0,
    }


def task(ident="normalize-observations", offset=10, *, impossible=False):
    training = [" value=" + str(index) + " units " for index in range(3)]
    heldout = [
        " value=" + str(index) + " units " for index in range(offset, offset + 8)
    ]
    return {
        "id": ident,
        "title": "Normalize public observation fields",
        "purpose": "Learn a reusable bounded transform and check unseen observation rows",
        "brief": "Infer a declarative transform from the training examples. Buyer evaluation rows remain withheld.",
        "kind": "compute",
        "provider": "included-skills",
        "action": "experiment",
        "maximum_micro_usd": 0,
        "delivery_seconds": 3600,
        "evaluation_nonce": digest({"test_task": ident, "offset": offset}),
        "acceptance": {
            "type": "transform_program",
            "config": config(),
            "training_inputs": training,
            "training_outputs": [
                "unavailable:" + str(index) if impossible else str(index)
                for index in range(3)
            ],
            "validation_inputs": [
                " value=" + str(index) + " units " for index in range(100, 108)
            ],
            "validation_outputs": [str(index) for index in range(100, 108)],
            "heldout_inputs": heldout,
            "heldout_outputs": [str(index) for index in range(offset, offset + 8)],
            "maximum_work": 10000,
        },
    }


def execution_task(skill_id, *, ident="serve-normalized-observations", offset=60):
    return {
        "id": ident,
        "title": "Apply a retained normalization skill",
        "purpose": "Produce checked canonical fields from previously unseen unlabelled observations",
        "brief": "Apply the specified retained skill to the unlabelled input records. Do not synthesize a replacement.",
        "kind": "compute",
        "provider": "included-skills",
        "action": "predict",
        "maximum_micro_usd": 0,
        "delivery_seconds": 3600,
        "evaluation_nonce": digest({"execution": ident, "offset": offset}),
        "acceptance": {
            "type": "transform_execution",
            "config": config(),
            "source_id": "nuria:public-repair-test",
            "skill_id": skill_id,
            "inputs": [
                " value=" + str(i) + " units " for i in range(offset, offset + 6)
            ],
            "expected_outputs": [str(i) for i in range(offset, offset + 6)],
            "maximum_work": 10000,
        },
    }


class SkillTaskTests(unittest.TestCase):
    def setUp(self):
        directory = Path(".test-state") / ("skill-tasks-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        self.directory = directory
        self.path = directory / "commerce.sqlite3"
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        self.now = float(int(time.time()))
        self.policy = SimpleNamespace(enabled=False)

    def tearDown(self):
        self.ledger.db.close()

    def run_task(self, contract, original, *, provider_config=None):
        return run_actions(
            self.ledger,
            SimpleNamespace(),
            self.policy,
            None,
            "",
            {"planning_enabled": True, "tasks": [contract]},
            {"enabled": False, "adapters": [provider_config or provider()]},
            {
                "phase": "running",
                "committed_tick": original["tick"],
                "agency": {"feedback_cursor": 0},
            },
            [original],
            self.now,
        )

    def test_actual_program_delivery_retention_reuse_and_neural_feedback(self):
        organism = Organism(self.directory / "cognition", "skill-task-fixture")
        contract = task()
        organism.agency_tasks = [contract]
        try:
            for _ in range(5):
                organism.cycle([])
            original = organism.journal.recent("decision")[0]
            self.now = time.time()
            result = self.run_task(contract, original)
            self.assertFalse(result["execution_enabled"])
            self.assertTrue(result["included_compute_enabled"])
            self.assertEqual(len(result["tasks"]), 1)
            row = self.loop.row(result["tasks"][0]["id"])
            self.assertEqual(row["state"], "evaluated")
            self.assertEqual(row["amount"], 0)
            self.assertTrue(row["acceptance"]["passed"])
            self.assertEqual(row["acceptance"]["exact_rows"], 8)
            self.assertEqual(row["settlement"]["basis"], "included_capacity")
            self.assertGreater(row["outcome"]["reward"], 0.9)
            self.assertTrue(
                result["local_skills"][0]["jobs"][0]["admission"]["accepted"]
            )
            before = organism.brain.state_hash()
            organism.task_feedback(result["feedback"])
            self.assertEqual(organism.agency.completed, 1)
            self.assertNotEqual(organism.brain.state_hash(), before)
            credited = organism.brain.state_hash()
            organism.task_feedback(result["feedback"])
            self.assertEqual(organism.brain.state_hash(), credited)
            organism.save()
            self.assertTrue(organism.journal.verify(full=True)["valid"])
            self.ledger.db.close()
            self.ledger = Ledger(self.path)
            self.loop = ActionLoop(self.ledger)
            recovered = LocalSkills(self.ledger, provider())
            self.assertEqual(len(recovered.library.dump()["programs"]), 1)
            first_work = row["acceptance"]["solver_work"]
            second = task("normalize-observations-next", 30)
            second["acceptance"]["training_inputs"] = [
                " value=" + str(i) + " units " for i in range(21, 24)
            ]
            second["acceptance"]["training_outputs"] = [str(i) for i in range(21, 24)]
            next_policy = NeuralTaskPolicy()
            # The helper serializes microsecond timestamps. Use an exact test
            # clock so rounding cannot turn the helper's origin into the future.
            self.now = float(int(time.time()) + 1)
            original = decision(next_policy, [second], self.now, tick=10)
            second_result = self.run_task(second, original)
            second_row = next(
                self.loop.row(item["id"])
                for item in second_result["tasks"]
                if item["contract_sha256"] == digest(second)
            )
            self.assertTrue(second_row["acceptance"]["passed"])
            self.assertLess(second_row["acceptance"]["solver_work"], first_work)
            self.assertEqual(
                second_row["acceptance"]["program_sha256"],
                row["acceptance"]["program_sha256"],
            )
            self.assertTrue(self.ledger.verify()["valid"])
            self.assertEqual(
                self.ledger.db.execute(
                    "SELECT count(*) FROM authorizations"
                ).fetchone()[0],
                0,
            )
        finally:
            organism.journal.db.close()

    def test_buyer_rows_never_enter_provider_search_or_public_projection(self):
        contract = task()
        agent = NeuralTaskPolicy()
        original = decision(agent, [contract], self.now)
        from cognition.skills import SkillLibrary

        real = SkillLibrary.solve
        seen = []

        def capture(library, inputs, outputs, **kwargs):
            seen.append((inputs, outputs, kwargs))
            return real(library, inputs, outputs, **kwargs)

        with patch.object(SkillLibrary, "solve", capture):
            result = self.run_task(contract, original)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], contract["acceptance"]["training_inputs"])
        brief = contractor_brief(contract, "a" * 64)
        for private in (
            "heldout_inputs",
            "heldout_outputs",
            "validation_inputs",
            "validation_outputs",
            "evaluation_nonce",
        ):
            self.assertNotIn(private, canonical(brief))
            self.assertNotIn(private, canonical(result))
        for heldout in contract["acceptance"]["heldout_inputs"]:
            self.assertNotIn(heldout, canonical(result))

    def test_nonfinancial_authority_disabled_and_paid_contract_not_bypassed(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        result = self.run_task(
            contract, original, provider_config=provider(enabled=False)
        )
        self.assertFalse(result["included_compute_enabled"])
        self.assertEqual(result["tasks"][0]["state"], "proposed")
        paid = task("paid-contract")
        paid["maximum_micro_usd"] = 1
        original = decision(NeuralTaskPolicy(), [paid], self.now, tick=10)
        result = self.run_task(paid, original)
        paid_row = next(
            row for row in result["tasks"] if row["contract_sha256"] == digest(paid)
        )
        self.assertEqual(paid_row["state"], "proposed")
        self.assertTrue(result["errors"])
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM local_skill_jobs").fetchone()[
                0
            ],
            1,
        )

    def test_failed_search_remains_recorded_with_work_and_no_installed_skill(self):
        contract = task(impossible=True)
        result = self.run_task(
            contract, decision(NeuralTaskPolicy(), [contract], self.now)
        )
        row = self.loop.row(result["tasks"][0]["id"])
        self.assertEqual(row["state"], "evaluated")
        self.assertFalse(row["acceptance"]["passed"])
        self.assertLess(row["outcome"]["reward"], 0)
        self.assertGreater(row["acceptance"]["solver_work"], 0)
        self.assertFalse(
            LocalSkills(self.ledger, provider()).library.dump()["programs"]
        )

    def test_compute_budget_counts_failed_jobs_and_never_repeats_disclosed_job(self):
        first = task(impossible=True)
        result = self.run_task(
            first,
            decision(NeuralTaskPolicy(), [first], self.now),
            provider_config=provider(maximum_jobs=1),
        )
        self.assertEqual(result["tasks"][0]["state"], "evaluated")
        before = self.ledger.db.execute(
            "SELECT count(*) FROM local_skill_jobs"
        ).fetchone()[0]
        second = task("next-budget-job")
        result = self.run_task(
            second,
            decision(NeuralTaskPolicy(), [second], self.now, tick=10),
            provider_config=provider(maximum_jobs=1),
        )
        self.assertTrue(result["errors"])
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM local_skill_jobs").fetchone()[
                0
            ],
            before,
        )

    def test_wrong_program_heldout_failure_and_invalid_ast_are_not_admitted(self):
        contract = task()
        result = {
            "program": {"version": "safe-transform-v1", "ops": []},
            "proposal_id": "a" * 64,
            "work": {"total": 1},
        }
        envelope = {
            "schema": "nuria.task-result.v1",
            "job_id": "b" * 64,
            "contract_sha256": digest(contract),
            "result": result,
        }
        checked = verify_delivery(contract, "b" * 64, canonical(envelope).encode())
        self.assertFalse(checked["passed"])
        self.assertEqual(checked["exact_rows"], 0)
        envelope["result"]["program"]["ops"] = [{"op": "open", "path": "private"}]
        with self.assertRaises(ValueError):
            verify_delivery(contract, "b" * 64, canonical(envelope).encode())
        changed = copy.deepcopy(contract)
        changed["acceptance"]["heldout_inputs"][0] = changed["acceptance"][
            "training_inputs"
        ][0]
        with self.assertRaises(ValueError):
            validate_task(changed)

    def test_counterexample_refines_without_ever_exposing_final_rows(self):
        contract = task()
        extended = config()
        extended["operations"] += [{"op": "decimal"}, {"op": "format"}]
        extended["max_ops"] = 6
        contract["acceptance"]["config"] = extended
        contract["acceptance"]["validation_inputs"] = [
            " value=" + str(i).zfill(3) + " units " for i in range(100, 108)
        ]
        contract["acceptance"]["validation_outputs"] = [str(i) for i in range(100, 108)]
        # Ensure the first CE row reveals a distinct generalization failure.
        contract["acceptance"]["validation_inputs"][0] = " value=001 units "
        contract["acceptance"]["validation_outputs"][0] = "1"
        contract["acceptance"]["heldout_inputs"] = [
            " value=" + str(i).zfill(3) + " units " for i in range(10, 18)
        ]
        controls = provider()
        controls["config"] = extended
        from cognition.skills import SkillLibrary

        real = SkillLibrary.solve
        inputs_seen = []

        def observe(library, inputs, outputs, **kwargs):
            inputs_seen.append(list(inputs))
            return real(library, inputs, outputs, **kwargs)

        with patch.object(SkillLibrary, "solve", observe):
            result = self.run_task(
                contract,
                decision(NeuralTaskPolicy(), [contract], self.now),
                provider_config=controls,
            )
        row = self.loop.row(result["tasks"][0]["id"])
        self.assertTrue(row["acceptance"]["passed"])
        self.assertEqual(len(inputs_seen), 2)
        self.assertEqual(inputs_seen[0], contract["acceptance"]["training_inputs"])
        self.assertEqual(
            inputs_seen[1],
            inputs_seen[0] + [contract["acceptance"]["validation_inputs"][0]],
        )
        self.assertFalse(
            set(contract["acceptance"]["heldout_inputs"]) & set(sum(inputs_seen, []))
        )
        attempts = result["local_skills"][0]["jobs"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertLess(
            attempts[0]["validation_correct"], attempts[0]["validation_rows"]
        )
        self.assertEqual(
            attempts[1]["validation_correct"], attempts[1]["validation_rows"]
        )
        restored = LocalSkills(self.ledger, controls)
        self.assertEqual(
            restored.library.dump()["total_work"], row["acceptance"]["total_work"]
        )

    def test_completed_local_dispatch_reconciles_after_lost_ack_without_repeat(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        dispatch = LocalSkills.dispatch
        observe = LocalSkills.observe

        def lost_ack(adapter, *args):
            dispatch(adapter, *args)
            raise TimeoutError("Disconnected after durable local delivery")

        with (
            patch.object(LocalSkills, "dispatch", lost_ack),
            patch.object(LocalSkills, "observe", return_value={}),
        ):
            first = self.run_task(contract, original)
        self.assertEqual(first["tasks"][0]["state"], "uncertain")
        before = self.ledger.db.execute(
            "SELECT count(*),sum(work) FROM local_skill_jobs"
        ).fetchone()
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        with (
            patch.object(
                LocalSkills,
                "dispatch",
                side_effect=AssertionError("must not dispatch twice"),
            ),
            patch.object(LocalSkills, "observe", observe),
        ):
            recovered = self.run_task(contract, original)
        self.assertEqual(recovered["tasks"][0]["state"], "evaluated")
        after = self.ledger.db.execute(
            "SELECT count(*),sum(work) FROM local_skill_jobs"
        ).fetchone()
        self.assertEqual(after[0], before[0])
        self.assertEqual(
            after[1], before[1] + recovered["tasks"][0]["acceptance"]["verifier_work"]
        )

    def test_failed_persist_discards_speculative_library_and_retains_unknown_work(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        ident = self.loop.propose(contract, original, self.now)
        adapter = LocalSkills(self.ledger, provider())
        before = adapter.library.dump()
        quote = adapter.quote(self.loop.row(ident), self.now)
        self.loop.reserve(
            ident,
            quote,
            adapter.balance(self.now),
            self.policy,
            {"enabled": True, "providers": [adapter.id]},
            self.now,
        )
        with patch.object(
            adapter,
            "_save_library",
            side_effect=RuntimeError("injected persist failure"),
        ):
            self.assertFalse(
                self.loop.operate(
                    ident,
                    "dispatch",
                    {},
                    lambda _, op: adapter.dispatch(self.loop.row(ident), self.now, op),
                    self.now,
                )
            )
        self.assertEqual(adapter.library.dump(), before)
        self.assertEqual(self.loop.row(ident)["state"], "uncertain")
        self.assertEqual(adapter.public()["jobs"][0]["work"], None)
        self.assertEqual(
            adapter.public()["jobs"][0]["work_accounting"], "unknown_after_disclosure"
        )
        self.assertEqual(adapter.observe(self.loop.row(ident), self.now), {})
        with patch.object(
            adapter.library,
            "solve",
            side_effect=AssertionError("must not repeat disclosed compute"),
        ):
            adapter.dispatch(self.loop.row(ident), self.now, "retry")
        self.assertEqual(adapter.library.dump(), before)

    def test_failed_admission_persist_restores_pending_and_retries_once(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        ident = self.loop.propose(contract, original, self.now)
        adapter = LocalSkills(self.ledger, provider())
        self.loop.reserve(
            ident,
            adapter.quote(self.loop.row(ident), self.now),
            adapter.balance(self.now),
            self.policy,
            {"enabled": True, "providers": [adapter.id]},
            self.now,
        )
        self.loop.operate(
            ident,
            "dispatch",
            {},
            lambda _, op: adapter.dispatch(self.loop.row(ident), self.now, op),
            self.now,
        )
        observed = adapter.observe(self.loop.row(ident), self.now)
        self.loop.receive(ident, observed["delivery"], self.now)
        before = adapter.library.dump()
        with patch.object(
            adapter,
            "_save_library",
            side_effect=RuntimeError("injected admission failure"),
        ):
            with self.assertRaises(RuntimeError):
                adapter.admit(self.loop.row(ident))
        self.assertEqual(adapter.library.dump(), before)
        self.assertFalse(adapter.library.dump()["programs"])
        admitted = adapter.admit(self.loop.row(ident))
        self.assertTrue(admitted["accepted"])
        once = adapter.library.dump()
        self.assertEqual(adapter.admit(self.loop.row(ident)), admitted)
        self.assertEqual(adapter.library.dump(), once)

    def test_feedback_uses_durable_cursor_not_speculative_public_cursor(self):
        with patch.object(ActionLoop, "feedback", return_value=[]) as feedback:
            run_actions(
                self.ledger,
                SimpleNamespace(),
                self.policy,
                None,
                "",
                {"planning_enabled": False, "tasks": []},
                {"enabled": False, "adapters": []},
                {"agency": {"feedback_cursor": 40, "committed_feedback_cursor": 12}},
                [],
                self.now,
            )
        feedback.assert_called_once_with(12)

    def test_older_committed_intent_dispatches_despite_newest_speculative_tick(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now, tick=5)
        speculative = decision(NeuralTaskPolicy(), [contract], self.now, tick=10)
        arguments = (
            self.ledger,
            SimpleNamespace(),
            self.policy,
            None,
            "",
            {"planning_enabled": True, "tasks": [contract]},
            {"enabled": False, "adapters": [provider()]},
            {"phase": "running", "committed_tick": 5, "agency": {"feedback_cursor": 0}},
            [speculative, {"tick": 8}, original],
            self.now,
        )
        first = run_actions(*arguments)
        self.assertEqual(len(first["tasks"]), 1)
        self.assertEqual(first["tasks"][0]["intent_id"], original["intent"]["id"])
        self.assertEqual(first["tasks"][0]["state"], "evaluated")
        with patch.object(
            LocalSkills,
            "dispatch",
            side_effect=AssertionError("duplicate polling cannot compute twice"),
        ):
            again = run_actions(*arguments)
        self.assertEqual(again["tasks"], first["tasks"])
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM local_skill_jobs").fetchone()[
                0
            ],
            1,
        )

    def test_invalid_claimed_intent_reports_error_without_hiding_valid_work(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        malformed = copy.deepcopy(original)
        malformed["hash"] = "bad"
        result = run_actions(
            self.ledger,
            SimpleNamespace(),
            self.policy,
            None,
            "",
            {"planning_enabled": True, "tasks": [contract]},
            {"enabled": False, "adapters": [provider()]},
            {"phase": "running", "committed_tick": 5, "agency": {"feedback_cursor": 0}},
            [{"tick": 2, "intent": {"goal_id": "missing-utc"}}, malformed, original],
            self.now,
        )
        self.assertEqual(result["tasks"][0]["state"], "evaluated")
        self.assertTrue(
            any(item["phase"] == "invalid_neural_origin" for item in result["errors"])
        )

    def test_expression_sum_program_is_checked_retained_and_reused(self):
        from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG

        expression = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
        expression["operations"] = [
            {"op": "field", "index": 0},
            {"op": "field", "index": 1},
            {"op": "decimal"},
            {"op": "add"},
            {"op": "format"},
        ]
        expression["max_rows"] = 16
        controls = provider()
        controls.update(
            config=expression, maximum_work_per_job=100000, maximum_work_per_day=1000000
        )

        def sum_task(ident, offset):
            contract = task(ident, offset)
            contract["acceptance"].update(config=expression, maximum_work=100000)
            for phase, start, count in (
                ("training", offset, 4),
                ("validation", offset + 100, 8),
                ("heldout", offset + 200, 12),
            ):
                pairs = [(i, (i * 7) % 19 + 1) for i in range(start, start + count)]
                contract["acceptance"][phase + "_inputs"] = [
                    json.dumps([str(a), str(b), "unused", "unused"]) for a, b in pairs
                ]
                contract["acceptance"][phase + "_outputs"] = [
                    str(a + b) for a, b in pairs
                ]
            return contract

        first = sum_task("sum-fields-first", 10)
        first_result = self.run_task(
            first,
            decision(NeuralTaskPolicy(), [first], self.now),
            provider_config=controls,
        )
        first_row = self.loop.row(first_result["tasks"][0]["id"])
        self.assertTrue(first_row["acceptance"]["passed"])
        self.assertEqual(first_row["acceptance"]["exact_rows"], 12)
        installed = first_result["local_skills"][0]["verified_programs"]
        self.assertEqual(installed[0]["program"]["version"], "safe-expression-v1")
        second = sum_task("sum-fields-next", 1000)
        second_result = self.run_task(
            second,
            decision(NeuralTaskPolicy(), [second], self.now, tick=10),
            provider_config=controls,
        )
        second_row = next(
            self.loop.row(item["id"])
            for item in second_result["tasks"]
            if item["contract_sha256"] == digest(second)
        )
        self.assertTrue(second_row["acceptance"]["passed"])
        self.assertLess(
            second_row["acceptance"]["solver_work"],
            first_row["acceptance"]["solver_work"],
        )
        self.assertEqual(
            second_row["acceptance"]["program_sha256"],
            first_row["acceptance"]["program_sha256"],
        )

    def test_neural_goal_uses_retained_skill_without_training_labels_or_search(self):
        organism = Organism(
            self.directory / "serving-cognition", "skill-serving-fixture"
        )
        contract = task()
        organism.agency_tasks = [contract]
        try:
            for _ in range(5):
                organism.cycle([])
            self.now = time.time()
            learned = self.run_task(contract, organism.journal.recent("decision")[0])
            organism.task_feedback(learned["feedback"])
            organism.save()
            skill_id = learned["tasks"][0]["acceptance"]["program_sha256"]
            use = execution_task(skill_id)
            brief = contractor_brief(use, "a" * 64)
            self.assertNotIn("expected_outputs", brief["acceptance"])
            self.assertNotIn("training_inputs", brief["acceptance"])
            self.assertNotIn("program", brief["acceptance"])
            organism.agency_tasks = [use]
            for _ in range(5):
                organism.cycle([])
            self.now = time.time()
            from cognition.skills import SkillLibrary

            with patch.object(
                SkillLibrary,
                "solve",
                side_effect=AssertionError("Operational execution must not synthesize"),
            ):
                served = self.run_task(use, organism.journal.recent("decision")[0])
            row = next(
                self.loop.row(item["id"])
                for item in served["tasks"]
                if item["contract_sha256"] == digest(use)
            )
            self.assertEqual(row["state"], "evaluated")
            self.assertTrue(row["acceptance"]["passed"])
            self.assertEqual(row["acceptance"]["execution_status"], "served")
            self.assertEqual(row["acceptance"]["exact_rows"], 6)
            self.assertEqual(row["acceptance"]["solver_work"], 0)
            self.assertGreater(row["acceptance"]["execution_work"], 0)
            self.assertEqual(row["settlement"]["total_micro_usd"], 0)
            self.assertEqual(
                served["local_skills"][0]["verified_programs"][0][
                    "verified_serving_uses"
                ],
                1,
            )
            before = organism.brain.state_hash()
            organism.task_feedback(served["feedback"])
            self.assertEqual(organism.agency.completed, 2)
            self.assertNotEqual(organism.brain.state_hash(), before)
            organism.save()
            self.assertTrue(organism.journal.verify(full=True)["valid"])
            self.assertEqual(
                self.ledger.db.execute(
                    "SELECT count(*) FROM authorizations"
                ).fetchone()[0],
                0,
            )
        finally:
            organism.journal.db.close()

    def test_unknown_retained_skill_records_failed_work_without_fallback(self):
        use = execution_task("f" * 64)
        from cognition.skills import SkillLibrary

        with patch.object(
            SkillLibrary, "solve", side_effect=AssertionError("No synthesis fallback")
        ):
            served = self.run_task(use, decision(NeuralTaskPolicy(), [use], self.now))
        row = self.loop.row(served["tasks"][0]["id"])
        self.assertEqual(row["state"], "evaluated")
        self.assertFalse(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["execution_status"], "skill_missing")
        self.assertGreater(row["acceptance"]["execution_work"], 0)
        self.assertEqual(row["outcome"]["reward"], -1)
        self.assertFalse(
            LocalSkills(self.ledger, provider()).library.dump()["programs"]
        )

    def test_execution_contract_cannot_request_money_or_forge_source(self):
        use = execution_task("f" * 64)
        paid = copy.deepcopy(use)
        paid["maximum_micro_usd"] = 1
        with self.assertRaises(ValueError):
            validate_task(paid)
        use["acceptance"]["source_id"] = "another-project"
        result = self.run_task(use, decision(NeuralTaskPolicy(), [use], self.now))
        self.assertEqual(result["tasks"][0]["state"], "proposed")
        self.assertTrue(result["errors"])
        self.assertFalse(result["local_skills"][0]["jobs"])
        with patch.object(ActionLoop, "feedback", return_value=[]) as feedback:
            run_actions(
                self.ledger,
                SimpleNamespace(),
                self.policy,
                None,
                "",
                {"planning_enabled": False, "tasks": []},
                {"enabled": False, "adapters": []},
                {"agency": {"feedback_cursor": 12}},
                [],
                self.now,
            )
        feedback.assert_called_once_with(12)


if __name__ == "__main__":
    unittest.main()
