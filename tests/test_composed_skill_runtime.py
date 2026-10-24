"""The separate four-answer text route through real commerce reservations."""

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
from cognition.composed_skills import ComposedSkillSession
from cognition.text_composition import CompositionalTextLibrary
from cognition.text_skills import DEFAULT_TEXT_CONFIG
from commerce.action_loop import ActionLoop
from commerce.composed_skill_runtime import (
    COMPOSED_POLICY,
    COMPOSED_PROVIDER,
    COMPOSED_SOURCE,
)
from commerce.ledger import Ledger, canonical
from commerce.local_skills import LocalSkills
from commerce.task_runtime import run_actions
from commerce.tasks import contractor_brief, digest, validate_task, verify_delivery


def language():
    config = copy.deepcopy(DEFAULT_TEXT_CONFIG)
    config.update(max_rows=16, max_input_bytes=128, max_ops=12)
    return config


def provider(*, enabled=True):
    return {
        "type": "local_skills",
        "id": COMPOSED_PROVIDER,
        "source_id": COMPOSED_SOURCE,
        "enabled": enabled,
        "config": language(),
        "mode": "active_composed",
        "maximum_work_per_job": 150000,
        "maximum_work_per_day": 2000000,
        "maximum_jobs_per_day": 10,
        "cooldown_seconds": 0,
    }


def records(offset, count):
    return [
        canonical(["  Batch" + str(index) + "  ", "unused:" + str(index)])
        for index in range(offset, offset + count)
    ]


def targets(inputs):
    return [json.loads(value)[0].strip().upper() for value in inputs]


def task(*, ident="composed-records", offset=10):
    inputs, heldout = records(offset, 6), records(offset + 100, 8)
    return {
        "id": ident,
        "title": "Normalize a structured record batch",
        "purpose": "Compose bounded text operations and check the fixed result on unseen records",
        "brief": "Request four diagnostic answers and return one declarative text program. The buyer retains all final data.",
        "kind": "compute",
        "provider": COMPOSED_PROVIDER,
        "action": "experiment",
        "maximum_micro_usd": 0,
        "delivery_seconds": 3600,
        "evaluation_nonce": digest({"composed_fixture": ident, "offset": offset}),
        "acceptance": {
            "type": "transform_active",
            "policy": COMPOSED_POLICY,
            "source_id": COMPOSED_SOURCE,
            "config": language(),
            "diagnostic_inputs": inputs,
            "diagnostic_outputs": targets(inputs),
            "heldout_inputs": heldout,
            "heldout_outputs": targets(heldout),
            "maximum_work": 150000,
            "maximum_probes": 4,
            "synthesis_work": 50000,
            "minimum_evidence": 4,
        },
    }


class ComposedSkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(".test-state") / ("composed-runtime-" + uuid.uuid4().hex)
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

    def complete(self, contract):
        original = decision(NeuralTaskPolicy(), [contract], self.now)
        report = self.poll(contract, original)
        for _ in range(40):
            self.assertFalse(report["errors"], report["errors"])
            row = next(
                self.loop.row(value["id"])
                for value in report["tasks"]
                if value["contract_sha256"] == digest(contract)
            )
            if row["state"] == "evaluated":
                return row, report
            report = self.poll(contract)
        self.fail("Four-audit text episode did not finish")

    def delivery(self, ident):
        return json.loads(
            self.ledger.db.execute(
                "SELECT delivery FROM local_skill_jobs WHERE job_id=?", (ident,)
            ).fetchone()[0]
        )

    def test_composition_acquisition_and_reuse_have_four_recorded_audits_and_no_financial_authority(
        self,
    ):
        first, report = self.complete(task())
        self.assertTrue(first["acceptance"]["passed"])
        self.assertEqual(first["acceptance"]["selection"], "synthesized")
        self.assertEqual(first["acceptance"]["exact_rows"], 8)
        self.assertEqual(first["acceptance"]["probes_used"], 4)
        self.assertFalse(report["execution_enabled"])
        self.assertEqual(first["amount"], 0)
        self.assertEqual(first["settlement"]["basis"], "included_capacity")
        second, report = self.complete(task(ident="composed-reuse", offset=40))
        self.assertTrue(second["acceptance"]["passed"])
        self.assertEqual(second["acceptance"]["selection"], "retained")
        self.assertEqual(second["acceptance"]["probes_used"], 4)
        self.assertEqual(
            first["acceptance"]["program_sha256"],
            second["acceptance"]["program_sha256"],
        )
        self.assertEqual(report["local_skills"][0]["verified_programs"][0]["uses"], 2)
        self.assertEqual(len(report["feedback"]), 2)
        self.assertEqual(
            self.ledger.db.execute("SELECT count(*) FROM authorizations").fetchone()[0],
            0,
        )
        self.assertTrue(self.ledger.verify()["valid"])

    def test_only_four_returned_diagnostics_enter_search_and_final_data_stays_private(
        self,
    ):
        contract = task()
        observed = []
        actual_solve = CompositionalTextLibrary.solve

        def capture(library, inputs, outputs, **kwargs):
            observed.append((inputs, outputs, kwargs))
            return actual_solve(library, inputs, outputs, **kwargs)

        with patch.object(CompositionalTextLibrary, "solve", capture):
            row, report = self.complete(contract)
        self.assertEqual(len(observed), 1)
        self.assertEqual(
            observed[0][0], contract["acceptance"]["diagnostic_inputs"][:4]
        )
        self.assertEqual(
            observed[0][1], contract["acceptance"]["diagnostic_outputs"][:4]
        )
        brief = contractor_brief(contract, row["id"])
        for hidden in (
            "heldout_inputs",
            "heldout_outputs",
            "diagnostic_outputs",
            "evaluation_nonce",
        ):
            self.assertNotIn(hidden, canonical(brief))
            self.assertNotIn(hidden, canonical(report))
        state = json.loads(
            self.ledger.db.execute(
                "SELECT session FROM active_skill_episodes WHERE job_id=?", (row["id"],)
            ).fetchone()[0]
        )
        for heldout in contract["acceptance"]["heldout_inputs"]:
            self.assertNotIn(heldout, canonical(state))
        self.assertEqual(len(state["probe_receipts"]), 4)

    def test_text_authority_requires_exact_source_provider_policy_and_four_answers(
        self,
    ):
        validate_task(task())
        for change in (
            "policy",
            "source_id",
            "minimum_evidence",
            "maximum_probes",
            "provider",
            "paid",
        ):
            broken = task(ident="broken-" + change)
            if change == "provider":
                broken["provider"] = "other-local-provider"
            elif change == "paid":
                broken["maximum_micro_usd"] = 1
            elif change in ("policy", "source_id"):
                broken["acceptance"][change] = "other"
            else:
                broken["acceptance"][change] = 2
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_task(broken)
        unmarked = task()
        del unmarked["acceptance"]["policy"]
        del unmarked["acceptance"]["source_id"]
        with self.assertRaises(ValueError):
            validate_task(unmarked)
        for change in ("source_id", "id", "mode", "config"):
            config = provider()
            config[change] = "active_audited" if change == "mode" else "other"
            with self.subTest(change=change), self.assertRaises(ValueError):
                LocalSkills(self.ledger, config)

    def test_completed_actions_recover_with_the_same_checked_choice(self):
        contract = task()
        report = self.poll(contract, decision(NeuralTaskPolicy(), [contract], self.now))
        ident = report["tasks"][0]["id"]
        before = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions WHERE job_id=?", (ident,)
        ).fetchone()[0]
        self.assertLessEqual(before, 4)
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        for _ in range(40):
            report = self.poll(contract)
            self.assertFalse(report["errors"], report["errors"])
            row = self.loop.row(ident)
            if row["state"] == "evaluated":
                break
        self.assertTrue(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["probes_used"], 4)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions WHERE state!='completed'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(len(report["feedback"]), 1)
        self.assertTrue(self.ledger.verify()["valid"])

    def test_new_solver_source_changes_cannot_resume_a_reserved_episode(self):
        contract = task()
        report = self.poll(contract, decision(NeuralTaskPolicy(), [contract], self.now))
        ident = report["tasks"][0]["id"]
        adapter = LocalSkills(self.ledger, provider())
        actual_read = Path.read_bytes

        def changed(path):
            raw = actual_read(path)
            return (
                raw + b"\n# changed implementation\n"
                if path.name == "text_composition.py"
                else raw
            )

        before = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions"
        ).fetchone()[0]
        with patch("commerce.composed_skill_runtime.Path.read_bytes", changed):
            with self.assertRaisesRegex(ValueError, "committed checkpoint"):
                adapter.active.advance(self.loop.row(ident), self.now)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions"
            ).fetchone()[0],
            before,
        )

    def test_interrupted_probe_stays_uncertain_and_is_not_repeated(self):
        contract = task()
        self.poll(contract, decision(NeuralTaskPolicy(), [contract], self.now))
        with patch(
            "commerce.active_skill_runtime.active_probe_receipt",
            side_effect=ValueError("Interrupted disclosure"),
        ) as probe:
            for _ in range(4):
                self.poll(contract)
                if probe.call_count:
                    break
            self.assertEqual(probe.call_count, 1)
        with patch("commerce.active_skill_runtime.active_probe_receipt") as probe:
            report = self.poll(contract)
            self.assertEqual(probe.call_count, 0)
        self.assertEqual(report["feedback"], [])
        public = LocalSkills(self.ledger, provider()).public()
        self.assertEqual(public["active_episodes"][0]["state"], "uncertain")
        self.assertGreater(
            public["active_episodes"][0]["unresolved_operation_reservation"], 0
        )
        self.assertEqual(public["active_episodes"][0]["reserved_job_work"], 150000)

    def test_interrupted_synthesis_keeps_reservation_after_database_reopen(self):
        contract = task()
        report = self.poll(contract, decision(NeuralTaskPolicy(), [contract], self.now))
        ident = report["tasks"][0]["id"]
        adapter = LocalSkills(self.ledger, provider())
        for _ in range(30):
            pending = self.ledger.db.execute(
                "SELECT next_action FROM active_skill_episodes WHERE job_id=?", (ident,)
            ).fetchone()[0]
            if pending and json.loads(pending)["kind"] == "synthesize":
                break
            adapter.active.advance(self.loop.row(ident), self.now)
        else:
            self.fail("No synthesis reservation became ready")
        with patch.object(
            ComposedSkillSession,
            "perform_synthesis",
            side_effect=RuntimeError("Interrupted synthesis"),
        ) as solve:
            with self.assertRaisesRegex(RuntimeError, "Interrupted synthesis"):
                adapter.active.advance(self.loop.row(ident), self.now)
            self.assertEqual(solve.call_count, 1)
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.loop = ActionLoop(self.ledger)
        with patch.object(ComposedSkillSession, "perform_synthesis") as solve:
            report = self.poll(contract)
            self.assertEqual(solve.call_count, 0)
        self.assertEqual(report["feedback"], [])
        self.assertEqual(
            report["local_skills"][0]["active_episodes"][0]["state"], "uncertain"
        )

    def test_disabled_source_pauses_without_spending_or_new_tool_calls(self):
        contract = task()
        report = self.poll(contract, decision(NeuralTaskPolicy(), [contract], self.now))
        self.assertEqual(report["feedback"], [])
        before = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions"
        ).fetchone()[0]
        report = self.poll(contract, config=provider(enabled=False))
        self.assertFalse(report["included_compute_enabled"])
        self.assertEqual(report["feedback"], [])
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions"
            ).fetchone()[0],
            before,
        )

    def test_final_failure_never_enters_search_or_triggers_a_second_attempt(self):
        contract = task()
        contract["acceptance"]["heldout_outputs"] = ["buyer-final-only"] * 8
        row, _ = self.complete(contract)
        self.assertFalse(row["acceptance"]["passed"])
        self.assertEqual(row["acceptance"]["exact_rows"], 0)
        self.assertEqual(row["acceptance"]["probes_used"], 4)
        self.assertNotIn("buyer-final-only", canonical(self.delivery(row["id"])))
        self.assertFalse(
            LocalSkills(self.ledger, provider()).library.dump()["programs"]
        )
        before = self.ledger.db.execute(
            "SELECT count(*) FROM active_skill_actions"
        ).fetchone()[0]
        self.poll(contract)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT count(*) FROM active_skill_actions"
            ).fetchone()[0],
            before,
        )

    def test_forged_text_source_or_missing_probe_is_rejected_by_the_buyer(self):
        contract = task()
        row, _ = self.complete(contract)
        actual = self.delivery(row["id"])
        for change in ("source", "probe"):
            artifact = copy.deepcopy(actual)
            if change == "source":
                artifact["result"]["source_id"] = "other:source"
            else:
                artifact["result"]["probe_receipts"].pop()
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_delivery(contract, row["id"], canonical(artifact).encode())


if __name__ == "__main__":
    unittest.main()
