"""Real SQLite worker fixtures; no model, neural or financial execution."""

import hashlib
import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cognition import analytical_report_worker as report_worker
from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.analytical_report_worker import AnalyticalReportWorker
from cognition.analytical_skills import AnalyticalSkills
from cognition.procedural_memory import ProceduralMemory, digest
from scripts.run_analytical_report import main

SOURCE = "report-worker-fixture"
CHECKER = "raw-ledger-grouping-fixture"
REPORT = "provider-report-v1"
SQL = """SELECT provider,COUNT(*),
COUNT(CASE WHEN delivery_passed=1 THEN 1 END),
COUNT(CASE WHEN outcome_utility>0 THEN 1 END),
COUNT(CASE WHEN outcome_utility IS NULL THEN 1 END),
COUNT(CASE WHEN settlement_basis='included_capacity' THEN 1 END),
COUNT(CASE WHEN settled_micro_usd>0 THEN 1 END)
FROM task_outcomes GROUP BY provider ORDER BY provider"""


class AnalyticalReportWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("report-worker-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        self.a = self.snapshot("a", [("alpha", 1, 2, "included_capacity", 0)])
        self.b = self.snapshot(
            "b",
            [
                ("beta", 0, -1, "external", 5),
                ("alpha", 1, None, "included_capacity", 0),
            ],
        )
        self.bank = AnalyticalSkills(
            self.root / "skills.sqlite", source_id=SOURCE, checker_id=CHECKER
        )
        self.memory = ProceduralMemory(
            self.root / "memory.sqlite",
            source_id=SOURCE,
            model_id="declared-fixture-query",
            tool_version=SCHEMA,
        )
        self.addCleanup(self.bank.close)
        self.addCleanup(self.memory.close)
        workspace = AnalystWorkspace(self.a)
        receipt = workspace.call("work", "query", {"sql": SQL})
        episode = self.memory.begin(
            "origin",
            "Return the seven-column provider report",
            digest("origin-context"),
        )
        self.memory.record_action(
            episode,
            "query-1",
            "query",
            SQL,
            True,
            receipt["cost"]["vm_progress_callbacks"],
            receipt["result_sha256"],
        )
        self.memory.propose(episode, SQL)
        self.memory.settle(
            episode,
            verify_terminal=lambda value: {
                "accepted": receipt["rows"] == [["alpha", 1, 1, 1, 0, 1, 0]],
                "checker_id": CHECKER,
                "evidence_sha256": digest(value),
            },
        )
        self.skill = self.bank.promote(
            self.memory, episode, self.a, "work", REPORT, receipt
        )
        self.worker = self.open()
        self.addCleanup(lambda: self.worker.close())

    def snapshot(self, name, rows, *, changed_docs=False, changed_schema=False):
        root = self.root / name
        root.mkdir(mode=0o700)
        database = root / "work.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "CREATE TABLE task_outcomes(provider TEXT,delivery_passed INTEGER,outcome_utility REAL,settlement_basis TEXT,settled_micro_usd INTEGER)"
            )
            connection.executemany("INSERT INTO task_outcomes VALUES(?,?,?,?,?)", rows)
            if changed_schema:
                connection.execute(
                    "CREATE INDEX provider_index ON task_outcomes(provider)"
                )
        docs = {}
        for section in ("schema", "columns", "business"):
            file = root / (section + ".txt")
            file.write_text(
                "Rows are tasks; delivery exactly1, utility and settled amounts positive, NULL utility unknown."
                + (" New meaning." if changed_docs else "")
            )
            docs[section] = {
                "path": file.name,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        registry = root / "registry.json"
        registry.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "revision": "sha256:"
                    + digest([name, rows, changed_docs, changed_schema]),
                    "databases": {
                        "work": {
                            "database": {
                                "path": database.name,
                                "sha256": hashlib.sha256(
                                    database.read_bytes()
                                ).hexdigest(),
                            },
                            "documents": docs,
                        }
                    },
                }
            )
        )
        return registry

    def open(self, **changes):
        config = {
            "source_id": SOURCE,
            "checker_id": CHECKER,
            "report_id": REPORT,
            "db_id": "work",
            "skill_id": self.skill["skill_id"],
        }
        config.update(changes)
        return AnalyticalReportWorker(self.root / "worker", self.bank, **config)

    def test_fresh_rows_are_checked_and_no_neural_credit_is_invented(self):
        result = self.worker.run("fresh", self.b)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(
            result["artifact"]["execution"]["receipt"]["rows"],
            [["alpha", 1, 1, 0, 1, 1, 0], ["beta", 1, 0, 0, 0, 0, 1]],
        )
        self.assertIsNone(result["artifact"]["neural_decision"])
        self.assertIsNone(result["artifact"]["neural_credit"])
        self.assertEqual(
            result["invocation_work"],
            {
                "workspace_query_calls": 2,
                "independent_check_calls": 1,
                "model_calls": 0,
            },
        )

    def test_same_job_replays_without_query_or_checker(self):
        original = self.worker.run("same", self.b)
        with (
            patch.object(
                self.bank, "execute", side_effect=AssertionError("duplicate query")
            ),
            patch(
                "cognition.analytical_report_worker.check_recent_work",
                side_effect=AssertionError("duplicate check"),
            ),
        ):
            replay = self.worker.run("same", self.b)
        self.assertEqual(original["artifact"], replay["artifact"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            replay["invocation_work"],
            {
                "workspace_query_calls": 0,
                "independent_check_calls": 0,
                "model_calls": 0,
            },
        )
        with self.assertRaises(ValueError):
            self.worker.run("same", self.a)

    def test_pending_checker_is_retained_without_automatic_retry(self):
        with patch(
            "cognition.analytical_report_worker.check_recent_work",
            side_effect=ValueError("checker fixture unavailable"),
        ):
            pending = self.worker.run("pending", self.b)
        self.assertEqual(pending["status"], "pending_check")
        with patch.object(
            self.bank, "execute", side_effect=AssertionError("duplicate")
        ):
            self.assertEqual(
                self.worker.run("pending", self.b)["status"], "pending_check"
            )
        self.assertEqual(self.bank.state()["pending"], 1)

    def test_failed_check_revokes_the_skill_for_later_jobs(self):
        with patch(
            "cognition.analytical_report_worker.check_recent_work",
            return_value={
                "accepted": False,
                "evidence_sha256": digest("independent rejection fixture"),
            },
        ):
            self.assertEqual(self.worker.run("rejected", self.b)["status"], "rejected")
        with patch.object(self.bank, "execute", side_effect=AssertionError("revoked")):
            self.assertEqual(self.worker.run("later", self.b)["status"], "unavailable")

    def test_changed_documents_and_schema_refuse_reuse(self):
        docs = self.snapshot("docs", [], changed_docs=True)
        schema = self.snapshot("schema", [], changed_schema=True)
        self.assertEqual(self.worker.run("docs", docs)["status"], "incompatible")
        self.assertEqual(self.worker.run("schema", schema)["status"], "incompatible")
        self.assertEqual(self.bank.state()["pending"], 0)

    def test_absent_skill_has_no_model_fallback(self):
        self.worker.close()
        worker = AnalyticalReportWorker(
            self.root / "empty-worker",
            self.bank,
            source_id=SOURCE,
            checker_id=CHECKER,
            report_id=REPORT,
            db_id="work",
        )
        self.addCleanup(worker.close)
        self.assertEqual(worker.run("empty", self.b)["status"], "unavailable")

    def test_empty_and_null_provider_rows_preserve_report_semantics(self):
        empty = self.snapshot("empty", [])
        nulls = self.snapshot(
            "nulls",
            [(None, 2, None, "included_capacity", 0), (None, 1, 0, "external", 1)],
        )
        self.assertEqual(
            self.worker.run("empty", empty)["artifact"]["execution"]["receipt"]["rows"],
            [],
        )
        self.assertEqual(
            self.worker.run("nulls", nulls)["artifact"]["execution"]["receipt"]["rows"],
            [[None, 2, 1, 0, 1, 1, 1]],
        )

    def test_worker_identity_limits_and_retained_verdict_are_checked(self):
        result = self.worker.run("tamper", self.b)
        directory = self.root / "worker/jobs" / hashlib.sha256(b"tamper").hexdigest()
        value = json.loads((directory / "result.json").read_text())
        value["verdict"]["accepted"] = False
        (directory / "result.json").write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.worker.run("tamper", self.b)
        self.worker.close()
        with self.assertRaises(ValueError):
            self.open(report_id="different")
        self.assertEqual(result["status"], "accepted")

    def test_cli_reuses_the_host_enrolled_bank_without_model_startup(self):
        self.worker.close()
        self.bank.close()
        args = [
            "--registry",
            str(self.b),
            "--state",
            str(self.root / "cli-state"),
            "--skill-bank",
            str(self.root / "skills.sqlite"),
            "--source-id",
            SOURCE,
            "--checker-id",
            CHECKER,
            "--report-id",
            REPORT,
            "--db-id",
            "work",
            "--skill-id",
            self.skill["skill_id"],
            "--job-id",
            "cli",
        ]
        with patch("builtins.print"):
            result = main(args)
            replay = main(args)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["invocation_work"]["workspace_query_calls"], 0)

    def test_interrupted_artifact_write_does_not_repeat_execution(self):
        write = report_worker._write

        def interrupted(path, value):
            if path.name == "execution.json":
                raise OSError("retained artifact interruption fixture")
            return write(path, value)

        with patch(
            "cognition.analytical_report_worker._write", side_effect=interrupted
        ):
            with self.assertRaises(OSError):
                self.worker.run("interrupted", self.b)
        with patch.object(
            self.bank, "execute", side_effect=AssertionError("duplicate query")
        ):
            result = self.worker.run("interrupted", self.b)
        self.assertEqual(result["status"], "pending_execution")
        self.assertEqual(result["invocation_work"]["workspace_query_calls"], 0)
        self.assertEqual(self.bank.state()["pending"], 1)

    def test_job_cap_refuses_new_work_but_keeps_existing_replay(self):
        self.worker.close()
        worker = AnalyticalReportWorker(
            self.root / "finite-worker",
            self.bank,
            source_id=SOURCE,
            checker_id=CHECKER,
            report_id=REPORT,
            db_id="work",
            skill_id=self.skill["skill_id"],
            max_jobs=1,
        )
        self.addCleanup(worker.close)
        self.assertEqual(worker.run("one", self.b)["status"], "accepted")
        with self.assertRaises(ValueError):
            worker.run("two", self.b)
        self.assertTrue(worker.run("one", self.b)["replayed"])

    def test_existing_job_directory_cannot_redirect_through_a_symlink(self):
        target = self.root / "unrelated-retained-state"
        target.mkdir(mode=0o700)
        redirected = self.root / "worker/jobs" / hashlib.sha256(b"redirect").hexdigest()
        redirected.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "job directory"):
            self.worker.run("redirect", self.b)


if __name__ == "__main__":
    unittest.main()
