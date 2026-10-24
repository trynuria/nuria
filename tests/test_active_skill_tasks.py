"""Buyer-directed diagnostics through the actual local data-transform loop."""

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
from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG
from commerce.action_loop import ActionLoop
from commerce.ledger import Ledger, canonical
from commerce.local_skills import LocalSkills
from commerce.task_runtime import run_actions
from commerce.tasks import contractor_brief, digest, validate_task, verify_delivery


def language():
    config = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
    config.update(
        operations=[
            *[{"op": "field", "index": index} for index in range(4)],
            {"op": "decimal"},
            {"op": "format"},
            {"op": "add"},
            {"op": "subtract"},
            {"op": "multiply"},
        ],
        max_rows=16,
        max_input_bytes=128,
        max_macros=0,
    )
    return config


def provider(*, mode="active", enabled=True):
    return {
        "type": "local_skills",
        "id": "active-transform-fixture",
        "source_id": "nuria:active-transform-fixture",
        "enabled": enabled,
        "config": language(),
        "mode": mode,
        "maximum_work_per_job": 150000,
        "maximum_work_per_day": 1000000,
        "maximum_jobs_per_day": 10,
        "cooldown_seconds": 0,
    }


def records(offset, count):
    return [
        canonical([str(index + 2), str(3 * index + 1), str(index % 5), "7"])
        for index in range(offset, offset + count)
    ]


def targets(inputs, kind="add"):
    result = []
    for value in inputs:
        left, right, _, _ = map(int, json.loads(value))
        result.append(str(left + right if kind == "add" else left - right))
    return result


def task(*, ident="active-data-repair", offset=10, kind="add"):
    inputs, heldout = records(offset, 6), records(offset + 100, 8)
    return {
        "id": ident,
        "title": "Repair an imported record batch",
        "purpose": "Choose or acquire a checked numeric transform using bounded diagnostic requests",
        "brief": "Select useful diagnostic rows and return one declarative program. Final acceptance data stays with the buyer.",
        "kind": "compute",
        "provider": provider()["id"],
        "action": "experiment",
        "maximum_micro_usd": 0,
        "delivery_seconds": 3600,
        "evaluation_nonce": digest({"fixture": ident, "offset": offset, "kind": kind}),
        "acceptance": {
            "type": "transform_active",
            "config": language(),
            "diagnostic_inputs": inputs,
            "diagnostic_outputs": targets(inputs, kind),
            "heldout_inputs": heldout,
            "heldout_outputs": targets(heldout, kind),
            "maximum_work": 150000,
            "maximum_probes": 4,
            "synthesis_work": 100000,
            "minimum_evidence": 2,
        },
    }


class ActiveSkillTaskTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(".test-state") / ("active-tasks-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.path = self.directory / "commerce.sqlite3"
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        self.now = float(int(time.time()))
        self.policy = SimpleNamespace(enabled=False)

    def tearDown(self):
        self.ledger.db.close()

    def poll(self, contract, original=None, *, config=None):
        return run_actions(
            self.ledger,
            SimpleNamespace(),
            self.policy,
            None,
            "",
            {"planning_enabled": original is not None, "tasks": [contract]},
            {"enabled": False, "adapters": [config or provider()]},
            {
                "phase": "running",
                "committed_tick": original["tick"] if original else 10,
                "agency": {"feedback_cursor": 0},
            },
            [original] if original else [],
            self.now,
        )

    def complete(self, contract, *, config=None):
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        report = self.poll(contract, original, config=config)
        for _ in range(40):
            row = next(
                self.loop.row(value["id"])
                for value in report["tasks"]
                if value["contract_sha256"] == digest(contract)
            )
            if row["state"] == "evaluated":
                return row, report
            self.assertFalse(report["errors"], report["errors"])
            report = self.poll(contract, config=config)
        self.fail("The finite active episode did not complete")

    def seed_program(self, kind):
        adapter = LocalSkills(self.ledger, provider())
        inputs, checks = records(1, 4), records(50, 8)
        proposal = adapter.library.solve(
            inputs, targets(inputs, kind), mode="factored", budget=100000
        )
        self.assertIsNotNone(proposal["program"])
        receipt = adapter.library.validate_and_admit(
            proposal["proposal_id"],
            checks,
            targets(checks, kind),
            digest({"kind": kind}),
        )
        self.assertTrue(receipt["accepted"])
        adapter._transaction(adapter._save_library)
        return proposal["program_sha256"]

    def delivery(self, ident):
        raw = self.ledger.db.execute(
            "SELECT delivery FROM local_skill_jobs WHERE job_id=?", (ident,)
        ).fetchone()[0]
        return json.loads(raw)

    def test_acquires_from_chosen_labels_then_reuses_without_named_skill(self):
        first, report = self.complete(task())
        self.assertTrue(first["acceptance"]["passed"])
        self.assertEqual(first["acceptance"]["selection"], "synthesized")
        self.assertEqual(first["acceptance"]["probes_used"], 4)
        self.assertEqual(first["acceptance"]["exact_rows"], 8)
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(len(report["feedback"]), 1)
        second, report = self.complete(task(ident="new-import", offset=40))
        self.assertTrue(second["acceptance"]["passed"])
        self.assertEqual(second["acceptance"]["selection"], "retained")
        self.assertEqual(
            first["acceptance"]["program_sha256"],
            second["acceptance"]["program_sha256"],
        )
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE job_id=? AND kind='synthesize'",
                (second["id"],),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(len(report["feedback"]), 2)
        self.assertTrue(self.ledger.verify()["valid"])
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM authorizations").fetchone()[0],
            0,
        )

    def test_audited_policy_acquires_and_admits_retained_lookup_with_two_probes(self):
        config = provider(mode="active_audited")
        first, _ = self.complete(task(), config=config)
        second, report = self.complete(
            task(ident="audited-reuse", offset=40), config=config
        )
        self.assertTrue(
            first["acceptance"]["passed"] and second["acceptance"]["passed"]
        )
        self.assertEqual(first["acceptance"]["probes_used"], 2)
        self.assertEqual(second["acceptance"]["probes_used"], 2)
        self.assertEqual(second["acceptance"]["selection"], "retained")
        self.assertIsNotNone(second["acceptance"]["proposal_id"])
        self.assertEqual(report["local_skills"][0]["verified_programs"][0]["uses"], 2)

    def test_audited_poll_is_bounded_and_policy_change_cannot_resume(self):
        contract = task()
        config = provider(mode="active_audited")
        report = self.poll(
            contract, decision(NeuralTaskPolicy(), [contract], self.now), config=config
        )
        ident = report["tasks"][0]["id"]
        count = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
        ).fetchone()[0]
        self.assertLessEqual(count, 4)
        with self.assertRaises(ValueError):
            LocalSkills(self.ledger, provider()).active.advance(
                self.loop.row(ident), self.now
            )
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
            ).fetchone()[0],
            count,
        )
        self.poll(contract, config=config)
        after = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
        ).fetchone()[0]
        self.assertLessEqual(after - count, 4)

    def test_disabling_compute_enrollment_stops_unfinished_episode(self):
        contract = task()
        report = self.poll(contract, decision(NeuralTaskPolicy(), [contract], self.now))
        ident = report["tasks"][0]["id"]
        before = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
        ).fetchone()[0]
        report = self.poll(contract, config=provider(enabled=False))
        self.assertEqual(report["feedback"], [])
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
            ).fetchone()[0],
            before,
        )

    def test_late_exact_artifact_is_consumed_but_never_retained(self):
        for seeded in (False, True):
            if seeded:
                self.seed_program("add")
            contract = task(ident="late-" + str(seeded), offset=80 if seeded else 10)
            contract["delivery_seconds"] = 60
            config = provider(mode="active_audited")
            report = self.poll(
                contract,
                decision(NeuralTaskPolicy(), [contract], self.now),
                config=config,
            )
            ident = report["tasks"][0]["id"]
            self.now += 61
            for _ in range(10):
                report = self.poll(contract, config=config)
                row = self.loop.row(ident)
                if row["state"] == "evaluated":
                    break
            self.assertFalse(row["acceptance"]["passed"])
            self.assertTrue(row["acceptance"]["late"])
            self.assertEqual(row["acceptance"]["exact_rows"], 8)
            self.assertLess(row["outcome"]["reward"], 0)
            adapter = LocalSkills(self.ledger, config)
            self.assertEqual(len(adapter.library.dump()["programs"]), int(seeded))
            receipt = json.loads(
                self.ledger.db.execute(
                    "SELECT admission FROM local_skill_jobs WHERE job_id=?", (ident,)
                ).fetchone()[0]
            )
            self.assertFalse(receipt["accepted"])
            self.assertTrue(receipt["admission"]["accepted"])

    def test_diagnostic_outcome_excludes_wrong_retained_route(self):
        wrong = self.seed_program("add")
        correct = self.seed_program("subtract")
        row, _ = self.complete(task(kind="subtract"))
        self.assertTrue(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["selection"], "retained")
        self.assertEqual(row["acceptance"]["program_sha256"], correct)
        self.assertNotEqual(row["acceptance"]["program_sha256"], wrong)
        result = self.delivery(row["id"])["result"]
        self.assertNotIn(
            "skill_id", contractor_brief(row["contract"], row["id"])["acceptance"]
        )
        self.assertGreater(len(result["probe_receipts"]), 0)
        self.assertEqual(result["probes_used"], len(result["probe_receipts"]))

    def test_one_actual_neural_intent_receives_only_the_final_episode_outcome(self):
        from cognition.worker import Organism

        organism = Organism(self.directory / "cognition", "active-data-fixture")
        contract = task()
        organism.agency_tasks = [contract]
        try:
            for _ in range(5):
                organism.cycle([])
            original = organism.journal.recent("decision")[0]
            self.now = time.time()
            config = provider(mode="active_audited")
            report = self.poll(contract, original, config=config)
            self.assertEqual(report["feedback"], [])
            for _ in range(40):
                ident = report["tasks"][0]["id"]
                row = self.loop.row(ident)
                if row["state"] == "evaluated":
                    break
                self.assertEqual(report["feedback"], [])
                report = self.poll(contract, config=config)
            self.assertTrue(row["acceptance"]["passed"])
            self.assertEqual(len(report["feedback"]), 1)
            before = organism.brain.state_hash()
            organism.task_feedback(report["feedback"])
            self.assertEqual(organism.agency.completed, 1)
            self.assertNotEqual(organism.brain.state_hash(), before)
            credited = organism.brain.state_hash()
            organism.task_feedback(report["feedback"])
            self.assertEqual(organism.brain.state_hash(), credited)
            organism.save()
            self.assertTrue(organism.journal.verify(full=True)["valid"])
        finally:
            organism.journal.db.close()

    def test_final_rows_stay_with_buyer_and_failure_cannot_trigger_repair(self):
        self.seed_program("add")
        contract = task()
        rule = contract["acceptance"]
        rule["heldout_outputs"] = ["buyer-final-only"] * 8
        brief = contractor_brief(contract, "fixture")
        for key in ("heldout_inputs", "heldout_outputs", "diagnostic_outputs"):
            self.assertNotIn(key, brief["acceptance"])
        row, _ = self.complete(contract)
        self.assertFalse(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["selection"], "retained")
        delivery = self.delivery(row["id"])
        self.assertNotIn("buyer-final-only", canonical(delivery))
        state = json.loads(
            self.ledger.db.execute(
                "SELECT session FROM active_skill_episodes WHERE job_id=?", (row["id"],)
            ).fetchone()[0]
        )
        self.assertEqual(state["inputs"], rule["diagnostic_inputs"])
        self.assertNotIn("buyer-final-only", canonical(state))
        count = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (row["id"],)
        ).fetchone()[0]
        self.poll(contract)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (row["id"],)
            ).fetchone()[0],
            count,
        )

    def test_completed_operations_resume_with_exact_remaining_state(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        report = self.poll(contract, original)
        ident = report["tasks"][0]["id"]
        self.assertEqual(self.loop.row(ident)["state"], "submitted")
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        for _ in range(40):
            report = self.poll(contract)
            row = self.loop.row(ident)
            if row["state"] == "evaluated":
                break
        self.assertTrue(row["acceptance"]["passed"])
        self.assertTrue(self.ledger.verify()["valid"])
        self.assertEqual(len(report["feedback"]), 1)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE state!='completed'"
            ).fetchone()[0],
            0,
        )

    def test_underlying_language_identity_change_refuses_continuation(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        report = self.poll(contract, original)
        ident = report["tasks"][0]["id"]
        adapter = LocalSkills(self.ledger, provider())
        before = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
        ).fetchone()[0]
        original_read = Path.read_bytes

        def changed(path):
            raw = original_read(path)
            return (
                raw + b"\n# changed implementation\n"
                if path.name == "skill_factored.py"
                else raw
            )

        with patch("commerce.active_skill_runtime.Path.read_bytes", changed):
            with self.assertRaises(ValueError):
                adapter.active.advance(self.loop.row(ident), self.now)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
            ).fetchone()[0],
            before,
        )

    def test_dispatch_rechecks_single_episode_guard_after_two_quotes(self):
        adapter = LocalSkills(self.ledger, provider())
        first = {"id": "a" * 64, "contract": task()}
        second = {"id": "b" * 64, "contract": task(ident="another", offset=40)}
        adapter.quote(first, self.now)
        adapter.quote(second, self.now)
        adapter.dispatch(first, self.now, "c" * 64)
        with self.assertRaises(ValueError):
            adapter.dispatch(second, self.now, "d" * 64)
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM local_skill_jobs").fetchone()[
                0
            ],
            1,
        )

    def test_open_probe_is_never_replayed_and_keeps_reserved_liability(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        report = self.poll(contract, original)
        ident = report["tasks"][0]["id"]
        with patch(
            "commerce.active_skill_runtime.active_probe_receipt",
            side_effect=ValueError("Interrupted label disclosure"),
        ) as probe:
            report = self.poll(contract)
            self.assertEqual(probe.call_count, 1)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT state FROM local_skill_jobs WHERE job_id=?", (ident,)
            ).fetchone()[0],
            "active_uncertain",
        )
        with patch("commerce.active_skill_runtime.active_probe_receipt") as probe:
            self.poll(contract)
            self.assertEqual(probe.call_count, 0)
        self.assertIsNone(self.loop.row(ident)["outcome"])
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT budget FROM local_skill_jobs WHERE job_id=?", (ident,)
            ).fetchone()[0],
            contract["acceptance"]["maximum_work"],
        )
        public = LocalSkills(self.ledger, provider()).public()["jobs"][0]
        self.assertIsNone(public["work"])
        self.assertEqual(public["work_accounting"], "unknown_after_disclosure")

    def test_failed_search_keeps_measured_work_without_retaining_a_program(self):
        contract = task()
        contract["acceptance"]["diagnostic_outputs"] = ["not-a-number"] * 6
        row, report = self.complete(contract)
        self.assertFalse(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["execution_status"], "abstained")
        self.assertGreater(row["acceptance"]["solver_work"], 0)
        self.assertLess(row["outcome"]["reward"], 0)
        self.assertEqual(report["local_skills"][0]["verified_programs"], [])
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE state!='completed'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(len(report["feedback"]), 1)

    def test_interrupted_synthesis_is_not_retried_after_reopen(self):
        contract = task()
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        report = self.poll(contract, original)
        ident = report["tasks"][0]["id"]
        for _ in range(30):
            raw = self.ledger.db.execute(
                "SELECT next_action FROM active_skill_episodes WHERE job_id=?", (ident,)
            ).fetchone()[0]
            if raw and json.loads(raw)["kind"] == "synthesize":
                break
            self.poll(contract)
        else:
            self.fail("No synthesis operation was proposed")
        with patch(
            "cognition.active_skills.ActiveSkillSession.perform_synthesis",
            side_effect=ValueError("Interrupted local synthesis"),
        ) as synthesis:
            self.poll(contract)
            self.assertEqual(synthesis.call_count, 1)
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        with patch(
            "cognition.active_skills.ActiveSkillSession.perform_synthesis"
        ) as synthesis:
            report = self.poll(contract)
            self.assertEqual(synthesis.call_count, 0)
        self.assertEqual(report["feedback"], [])
        self.assertEqual(
            report["local_skills"][0]["active_episodes"][0]["state"], "uncertain"
        )
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE job_id=? AND state='disclosed' AND work IS NULL",
                (ident,),
            ).fetchone()[0],
            1,
        )

    def test_checkpoint_or_probe_receipt_tampering_is_rejected(self):
        row, _ = self.complete(task())
        delivery = self.delivery(row["id"])
        broken = copy.deepcopy(delivery)
        broken["result"]["probe_receipts"][0]["receipt"]["output"] = "changed"
        with self.assertRaises(ValueError):
            verify_delivery(row["contract"], row["id"], canonical(broken).encode())
        broken = copy.deepcopy(delivery)
        broken["result"]["work"]["total"] = 0
        with self.assertRaises(ValueError):
            verify_delivery(row["contract"], row["id"], canonical(broken).encode())
        changed = copy.deepcopy(delivery)
        changed["result"]["reason"] = "changed-after-selection"
        self.ledger.db.execute(
            "UPDATE local_skill_jobs SET delivery=? WHERE job_id=?",
            (canonical(changed), row["id"]),
        )
        adapter = LocalSkills(self.ledger, provider())
        with self.assertRaises(ValueError):
            adapter.observe(row, self.now)
        self.ledger.db.execute(
            "UPDATE local_skill_jobs SET delivery=? WHERE job_id=?",
            (canonical(delivery), row["id"]),
        )
        self.ledger.db.execute(
            "UPDATE active_skill_episodes SET session_sha256=? WHERE job_id=?",
            ("0" * 64, row["id"]),
        )
        adapter = LocalSkills(self.ledger, provider())
        with self.assertRaises(ValueError):
            adapter.active.advance(row, self.now)

    def test_explicit_activation_and_finite_work_cannot_authorize_payment(self):
        contract = task()
        validate_task(contract)
        for field, value in (("skill_id", "0" * 64), ("format_index", 2)):
            broken = copy.deepcopy(contract)
            broken["acceptance"][field] = value
            with self.assertRaises(ValueError):
                validate_task(broken)
        broken = copy.deepcopy(contract)
        broken["maximum_micro_usd"] = 1
        with self.assertRaises(ValueError):
            validate_task(broken)
        adapter = LocalSkills(self.ledger, provider(mode="factored"))
        with self.assertRaises(ValueError):
            adapter.quote({"id": "fixture", "contract": contract}, self.now)
        disabled = LocalSkills(self.ledger, provider(enabled=False))
        with self.assertRaises(ValueError):
            disabled.quote({"id": "fixture", "contract": contract}, self.now)
        from commerce.tasks import active_verifier_ceiling

        contract["acceptance"]["maximum_work"] = active_verifier_ceiling(language()) + 1
        row, _ = self.complete(contract)
        self.assertFalse(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["execution_status"], "abstained")
        self.assertLessEqual(
            row["acceptance"]["total_work"], contract["acceptance"]["maximum_work"]
        )

    def test_small_text_limit_still_reserves_large_decimal_intermediates(self):
        from cognition.skill_language import execute_checked, execution_work_ceiling

        config = language()
        config.update(
            max_input_bytes=32,
            max_ops=8,
            operations=[
                {"op": "field", "index": 0},
                {"op": "decimal"},
                {"op": "scale", "numerator": 1, "denominator": 3},
                {"op": "scale", "numerator": 2, "denominator": 1},
                {"op": "format"},
            ],
        )
        node = {
            "op": "scale",
            "numerator": 1,
            "denominator": 3,
            "arg": {"op": "decimal", "arg": {"op": "field", "index": 0}},
        }
        for _ in range(4):
            node = {"op": "scale", "numerator": 2, "denominator": 1, "arg": node}
        program = {
            "version": "safe-expression-v1",
            "expr": {"op": "format", "arg": node},
        }
        checked = execute_checked(program, '["1","0","0","0"]', config)
        self.assertEqual(checked["error"], "operation_failed")
        # Even a rejected final output consumes the prior numerical work.
        self.assertGreater(checked["work"]["total"], config["max_ops"] * 3)
        self.assertLessEqual(
            checked["work"]["total"], execution_work_ceiling(config, 1)
        )


if __name__ == "__main__":
    unittest.main()
