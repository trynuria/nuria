"""Offline execution checks; scripted proposals are not model intelligence."""

from __future__ import annotations

import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from cognition.workflow_agent import classical_plan, run_goal
from cognition.workflow_catalog import build_catalog, fingerprint
from cognition.workflow_planner import VERSION, source_identity
from scripts.check_workflow_artifact import verify_artifact


class ScriptedPlanner:
    """An in-process wiring fixture, with no provider or network client."""

    def __init__(self, propose):
        self.propose = propose
        self.calls = []

    def complete(self, messages, *, max_tokens, seed):
        self.calls.append(
            {"messages": messages.copy(), "max_tokens": max_tokens, "seed": seed}
        )
        request = json.loads(messages[1]["content"])
        return {
            "content": json.dumps(self.propose(request, len(self.calls))),
            "usage": {"completion_tokens": 11},
            "model": "offline-scripted-fixture",
        }


class WorkflowAgentTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("workflow-agent-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        self.projection = {
            "scope": "offline fixture, not live activity",
            "action_tasks": [
                {
                    "id": "a",
                    "provider": "provider_one",
                    "acceptance": {"passed": True},
                    "settlement": {"basis": "included_capacity", "total_micro_usd": 0},
                    "outcome": {"utility": 1},
                },
                {
                    "id": "b",
                    "provider": "provider_one",
                    "acceptance": {"passed": False},
                    "settlement": {
                        "basis": "reported_external",
                        "total_micro_usd": 1250000,
                    },
                    "outcome": {"utility": -1},
                },
                {
                    "id": "c",
                    "provider": "provider_two",
                    "acceptance": {"passed": None},
                    "settlement": {
                        "basis": "reported_external",
                        "total_micro_usd": 2500000,
                    },
                    "outcome": {"utility": None},
                },
            ],
        }
        self.json_path = self.root / "snapshot.json"
        self.json_path.write_text(json.dumps(self.projection))
        self.sqlite_path = self.root / "mirror.sqlite"
        with closing(sqlite3.connect(self.sqlite_path)) as db, db:
            db.execute(
                "CREATE TABLE task_outcomes(task_id TEXT,provider TEXT,delivery_passed INTEGER,settlement_basis TEXT,settled_micro_usd INTEGER,outcome_utility REAL)"
            )
            db.executemany(
                "INSERT INTO task_outcomes VALUES(?,?,?,?,?,?)",
                [
                    (
                        row["id"],
                        row["provider"],
                        row["acceptance"]["passed"],
                        row["settlement"]["basis"],
                        row["settlement"]["total_micro_usd"],
                        row["outcome"]["utility"],
                    )
                    for row in self.projection["action_tasks"]
                ],
            )
        self.source_spec = {
            "snapshot_sha256": fingerprint(self.projection),
            "role": "enrolled-work-snapshot",
        }
        self.sources = {
            "projection": {"type": "source_spec", "value": self.source_spec},
            "audit_policy": {"type": "audit_policy", "value": {"maximum_tasks": 1}},
        }

    def catalog(self, name, *, missing_json=False):
        return build_catalog(
            projection=self.projection,
            json_path=self.root / "deliberately-absent.json"
            if missing_json
            else self.json_path,
            sqlite_path=self.sqlite_path,
            output_dir=self.root / name,
            source_spec=self.source_spec,
        )

    def checker(self, kind, directory):
        return lambda record: verify_artifact(
            self.projection,
            record["final_artifact"]["value"],
            kind,
            audit_policy={"maximum_tasks": 1},
            case_id=record["case_id"],
            artifact_root=directory,
        )

    def goal(
        self,
        catalog,
        kind="provider_brief",
        method="exact",
        reasoner=None,
        maximum_work=7,
        execution_log=None,
    ):
        # The trusted output root is the test's enrollment directory, never model data.
        directory = self.root
        return run_goal(
            goal="Save the requested local evidence brief while preserving unknowns.",
            catalog=catalog,
            case_id="fixture_case",
            sources=self.sources,
            goal_type=kind,
            maximum_work=maximum_work,
            deadline_units=maximum_work,
            checker=self.checker(kind, directory),
            execution_log={} if execution_log is None else execution_log,
            reasoner=reasoner,
            method=method,
        )

    @staticmethod
    def proposal(request, reader="read_json"):
        kind = request["goal_type"]
        operation, renderer = {
            "provider_brief": ("summarize_provider", "render_provider"),
            "payment_brief": ("separate_delivery_payment", "render_payment"),
            "audit_brief": ("plan_local_audits", "render_audits"),
        }[kind]
        middle_inputs = {"rows": {"node": "read", "port": "rows"}}
        if kind == "audit_brief":
            middle_inputs["policy"] = {"source": "audit_policy"}
        return {
            "version": VERSION,
            "case_id": request["case_id"],
            "source_sha256": request["source_sha256"],
            "nodes": [
                {
                    "id": "read",
                    "op": reader,
                    "arguments": {},
                    "inputs": {"source": {"source": "projection"}},
                },
                {
                    "id": "derive",
                    "op": operation,
                    "arguments": {},
                    "inputs": middle_inputs,
                },
                {
                    "id": "save",
                    "op": renderer,
                    "arguments": {},
                    "inputs": {"evidence": {"node": "derive", "port": "evidence"}},
                },
            ],
            "output": {"node": "save", "port": "artifact"},
        }

    def test_exact_and_greedy_deliver_actual_independently_checked_files(self):
        for method in ("exact", "greedy"):
            for kind in ("provider_brief", "payment_brief", "audit_brief"):
                with self.subTest(method=method, kind=kind):
                    catalog = self.catalog(method + "-" + kind)
                    result = self.goal(catalog, kind, method)
                    self.assertTrue(result["accepted"])
                    self.assertEqual(result["model_calls"], 0)
                    artifact = result["attempts"][0]["execution"]["final_artifact"][
                        "value"
                    ]
                    path = Path(artifact["path"])
                    self.assertTrue(path.is_file())
                    self.assertIn("no independent payment", path.read_text())
                    self.assertTrue(
                        verify_artifact(
                            self.projection,
                            artifact,
                            kind,
                            {"maximum_tasks": 1},
                            artifact_root=self.root,
                        )["accepted"]
                    )

    def test_scripted_planner_validates_before_execution_then_revises(self):
        def propose(request, call):
            plan = self.proposal(request)
            if call == 1:
                plan["nodes"][0]["op"] = "unregistered_shell"
            return plan

        fixture = ScriptedPlanner(propose)
        catalog = self.catalog("scripted-structural")
        result = self.goal(catalog, method="model", reasoner=fixture)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["completion_tokens"], 22)
        self.assertIsNone(result["attempts"][0]["execution"])
        self.assertIn("validation_error", result["attempts"][0])
        self.assertEqual(len(result["execution_log"]), 1)
        self.assertTrue(all(call["max_tokens"] == 1250 for call in fixture.calls))
        self.assertNotIn(str(self.root), json.dumps(fixture.calls))

    def test_real_missing_reader_fallback_charges_failure_and_checks_artifact(self):
        fixture = ScriptedPlanner(
            lambda request, call: self.proposal(
                request, "read_json" if call == 1 else "read_sqlite"
            )
        )
        catalog = self.catalog("scripted-fallback", missing_json=True)
        result = self.goal(catalog, method="model", reasoner=fixture)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["charged_work_units"], 7)
        failed, repaired = [row["execution"] for row in result["attempts"]]
        self.assertEqual(failed["status"], "tool_unavailable")
        self.assertEqual(failed["charged_work_units"], 1)
        self.assertEqual(repaired["charged_work_units"], 6)
        self.assertEqual(repaired["trace"][0]["op"], "read_sqlite")
        feedback = json.loads(fixture.calls[1]["messages"][-1]["content"])
        self.assertEqual(feedback["remaining_work_units"], 6)
        self.assertEqual(feedback["failed_operation"], "read_json")

    def test_control_fallback_has_same_observation_and_cost(self):
        for method in ("exact", "greedy"):
            catalog = self.catalog(method + "-fallback", missing_json=True)
            result = self.goal(catalog, method=method)
            self.assertTrue(result["accepted"])
            self.assertEqual(result["charged_work_units"], 7)
            self.assertEqual(
                result["attempts"][0]["execution"]["status"], "tool_unavailable"
            )
            self.assertEqual(
                result["attempts"][1]["execution"]["trace"][0]["op"], "read_sqlite"
            )

    def test_repair_cannot_renew_case_allowance(self):
        fixture = ScriptedPlanner(
            lambda request, call: self.proposal(
                request, "read_json" if call == 1 else "read_sqlite"
            )
        )
        catalog = self.catalog("over-budget", missing_json=True)
        result = self.goal(catalog, method="model", reasoner=fixture, maximum_work=6)
        self.assertFalse(result["accepted"])
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(len(result["execution_log"]), 1)
        self.assertEqual(result["charged_work_units"], 1)
        self.assertFalse((self.root / "over-budget" / "provider_brief.md").exists())

    def test_no_file_overwrite_and_no_implicit_retry(self):
        catalog = self.catalog("exclusive-output")
        first = self.goal(catalog)
        artifact = first["attempts"][0]["execution"]["final_artifact"]["value"]
        before = Path(artifact["path"]).read_bytes()
        second = self.goal(catalog)
        self.assertFalse(second["accepted"])
        self.assertEqual(len(second["attempts"]), 1)
        self.assertEqual(second["attempts"][0]["execution"]["status"], "handler_error")
        self.assertEqual(Path(artifact["path"]).read_bytes(), before)

    def test_control_plan_binds_same_enrolled_source(self):
        catalog = self.catalog("source-binding")
        plan = classical_plan(
            catalog=catalog,
            case_id="fixture_case",
            sources=self.sources,
            goal_type="provider_brief",
        )
        self.assertEqual(
            plan["source_sha256"], source_identity("fixture_case", self.sources)
        )
        self.assertEqual(plan["nodes"][0]["op"], "read_json")

    def assert_mirror_rejected(self, sql):
        with closing(sqlite3.connect(self.sqlite_path)) as db, db:
            db.execute(sql)
        catalog = self.catalog("corrupt-mirror", missing_json=True)
        result = self.goal(catalog)
        self.assertFalse(result["accepted"])
        self.assertEqual(len(result["attempts"]), 2)
        record = result["attempts"][1]["execution"]
        self.assertEqual(record["status"], "handler_error")
        self.assertEqual(record["trace"][0]["op"], "read_sqlite")
        self.assertIsNone(record["final_artifact"])
        self.assertFalse((self.root / "corrupt-mirror" / "provider_brief.md").exists())

    def test_sqlite_rejects_truthy_non_boolean_delivery(self):
        self.assert_mirror_rejected(
            "UPDATE task_outcomes SET delivery_passed=2 WHERE task_id='a'"
        )

    def test_sqlite_rejects_changed_utility(self):
        self.assert_mirror_rejected(
            "UPDATE task_outcomes SET outcome_utility=7 WHERE task_id='a'"
        )

    def test_sqlite_rejects_duplicate_id_even_when_mapping_would_hide_it(self):
        self.assert_mirror_rejected(
            "INSERT INTO task_outcomes SELECT * FROM task_outcomes WHERE task_id='a'"
        )


if __name__ == "__main__":
    unittest.main()
