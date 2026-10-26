"""Finite protocol tests over real SQLite; no model or neural inference."""

import copy
import hashlib
import os
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.analytical_skills import AnalyticalSkills
from cognition.procedural_memory import ProceduralMemory, canonical, digest

CHECKER = "independent-integer-grouping-v1"
SOURCE = "fixture-report-history"
REPORT = "east-total-v1"
SQL = "SELECT coalesce(sum(amount),0) FROM entries WHERE region='east'"


class AnalyticalSkillTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("analytical-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        self.a = self.snapshot("a", [("east", 7), ("west", 13)])
        self.b = self.snapshot("b", [("east", 5), ("east", 11), ("west", -3)])
        self.memory = ProceduralMemory(
            self.root / "memory.sqlite",
            source_id=SOURCE,
            model_id="scripted-protocol-fixture",
            tool_version=SCHEMA,
        )
        self.bank = self.open()
        self.addCleanup(lambda: self.bank.close())
        self.addCleanup(self.memory.close)

    def open(self, **changes):
        return AnalyticalSkills(
            self.root / "skills.sqlite",
            **{"source_id": SOURCE, "checker_id": CHECKER, **changes},
        )

    def snapshot(self, name, rows, *, changed_schema=False, changed_docs=False):
        root = self.root / name
        root.mkdir(mode=0o700)
        database = root / "rows.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE entries(region TEXT,amount INTEGER)")
            connection.executemany("INSERT INTO entries VALUES(?,?)", rows)
            if changed_schema:
                connection.execute("CREATE INDEX entry_amount ON entries(amount)")
        documents = {}
        for section in ("schema", "columns", "business"):
            text = "Amounts are additive integers; NULL amounts remain unknown."
            if changed_docs:
                text += " Amounts now denote cents, not units."
            document = root / (section + ".txt")
            document.write_text(text)
            documents[section] = {
                "path": document.name,
                "sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
            }
        manifest = {
            "schema": SCHEMA,
            "revision": "sha256:" + digest([name, rows, changed_schema, changed_docs]),
            "databases": {
                "ledger": {
                    "database": {
                        "path": database.name,
                        "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                    },
                    "documents": documents,
                }
            },
        }
        registry = root / "registry.json"
        registry.write_text(canonical(manifest))
        return registry

    def origin(self, *, accepted=True, settle=True, sql=SQL, receipt_changes=None):
        workspace = AnalystWorkspace(self.a)
        receipt = workspace.call("ledger", "query", {"sql": sql})
        episode = self.memory.begin(
            "origin-" + uuid.uuid4().hex,
            "Report the total east amount",
            digest({"registry": workspace.registry_sha256}),
        )
        self.memory.record_action(
            episode, "query", "query", sql, True, 1, receipt["result_sha256"]
        )
        self.memory.propose(episode, sql)
        if settle:
            self.memory.settle(
                episode,
                verify_terminal=lambda request: {
                    "accepted": accepted,
                    "evidence_sha256": digest([request, receipt["rows"]]),
                    "checker_id": CHECKER,
                },
            )
        if receipt_changes:
            receipt.update(receipt_changes)
        return episode, receipt

    def promote(self, **origin_kwargs):
        episode, receipt = self.origin(**origin_kwargs)
        return self.bank.promote(
            self.memory, episode, self.a, "ledger", REPORT, receipt
        )

    @staticmethod
    def checker(expected):
        def verify(record):
            return {
                "accepted": record["receipt"]["rows"] == expected,
                "evidence_sha256": digest([record["execution_sha256"], expected]),
                "checker_id": CHECKER,
            }

        return verify

    def execute(self, skill, job="next", registry=None):
        return self.bank.execute(
            skill["skill_id"],
            job_id=job,
            registry=self.b if registry is None else registry,
            db_id="ledger",
            report_id=REPORT,
        )

    def test_accepted_sql_becomes_fresh_executable_skill_without_annotations(self):
        skill = self.promote()
        self.assertEqual(skill["origin"]["accepted_settlement"]["accepted"], True)
        self.assertNotIn("procedure_text", skill)
        self.assertEqual(skill["origin"]["execution_receipt"]["rows"], [[7]])
        execution = self.execute(skill)
        self.assertEqual(execution["receipt"]["rows"], [[16]])
        self.assertEqual(execution["status"], "pending_check")
        self.assertEqual(self.bank.state()["pending"], 1)
        result = self.bank.settle("next", verify_result=self.checker([[16]]))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["source"], execution["source"])
        self.assertEqual(result["execution_sha256"], execution["execution_sha256"])
        self.assertEqual(self.bank.state()["pending"], 0)

    def test_unsettled_or_rejected_origin_cannot_be_promoted(self):
        for options in ({"accepted": False}, {"settle": False}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.promote(**options)
        self.assertEqual(self.bank.state()["skills"], 0)

    def test_changed_receipt_or_wrong_original_source_is_rejected(self):
        episode, receipt = self.origin()
        changed = copy.deepcopy(receipt)
        changed["rows"] = [[700]]
        for source, evidence in ((self.a, changed), (self.b, receipt)):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.bank.promote(
                    self.memory, episode, source, "ledger", REPORT, evidence
                )

    def test_model_text_cannot_substitute_for_host_acceptance(self):
        episode, receipt = self.origin(settle=False)
        self.assertIsNone(self.memory.feedback(episode))
        with self.assertRaises(ValueError):
            self.bank.promote(self.memory, episode, self.a, "ledger", REPORT, receipt)

    def test_schema_and_business_semantics_are_compatible_only_when_exact(self):
        skill = self.promote()
        for name, options in (
            ("schema-changed", {"changed_schema": True}),
            ("meaning-changed", {"changed_docs": True}),
        ):
            source = self.snapshot(name, [("east", 9)], **options)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.execute(skill, job=name, registry=source)
        self.assertEqual(self.bank.state()["pending"], 0)

    def test_origin_workspace_implementation_must_still_match(self):
        episode, receipt = self.origin()
        original = AnalystWorkspace.call

        def changed_implementation(workspace, db_id, tool, arguments):
            result = original(workspace, db_id, tool, arguments)
            result["runtime"]["implementation_sha256"] = "f" * 64
            return result

        with patch.object(AnalystWorkspace, "call", changed_implementation):
            with self.assertRaisesRegex(ValueError, "exact source and query"):
                self.bank.promote(
                    self.memory, episode, self.a, "ledger", REPORT, receipt
                )

    def test_explicit_report_identity_cannot_be_swapped(self):
        skill = self.promote()
        with self.assertRaises(ValueError):
            self.bank.execute(
                skill["skill_id"],
                job_id="other-question",
                registry=self.b,
                db_id="ledger",
                report_id="west-total-v1",
            )

    def test_failed_check_revokes_reuse_and_is_durable(self):
        skill = self.promote()
        self.execute(skill)
        result = self.bank.settle("next", verify_result=self.checker([[100]]))
        self.assertFalse(result["accepted"])
        self.assertFalse(self.bank.get(skill["skill_id"])["active"])
        self.bank.close()
        self.bank = self.open()
        with self.assertRaises(ValueError):
            self.execute(skill, job="again")
        self.assertEqual(
            result,
            self.bank.settle(
                "next", verify_result=lambda value: self.fail("Repeated checker")
            ),
        )

    def test_same_execution_and_verdict_are_idempotent(self):
        episode, receipt = self.origin()
        skill = self.bank.promote(
            self.memory, episode, self.a, "ledger", REPORT, receipt
        )
        self.assertEqual(
            skill,
            self.bank.promote(self.memory, episode, self.a, "ledger", REPORT, receipt),
        )
        first = self.execute(skill)
        self.assertEqual(first, self.execute(skill))
        result = self.bank.settle("next", verify_result=self.checker([[16]]))
        self.assertEqual(
            result,
            self.bank.settle(
                "next", verify_result=lambda value: self.fail("Repeated checker")
            ),
        )
        with self.assertRaises(ValueError):
            self.execute(skill, registry=self.a)

    def test_checker_error_or_identity_change_leaves_pending(self):
        skill = self.promote()
        self.execute(skill)

        def broken(record):
            raise RuntimeError("Checker unavailable")

        with self.assertRaises(RuntimeError):
            self.bank.settle("next", verify_result=broken)
        with self.assertRaises(ValueError):
            self.bank.settle(
                "next",
                verify_result=lambda record: {
                    "accepted": True,
                    "evidence_sha256": digest(record),
                    "checker_id": "another-checker",
                },
            )
        self.assertEqual(self.bank.state()["pending"], 1)
        self.assertTrue(self.bank.get(skill["skill_id"])["active"])
        self.assertTrue(
            self.bank.settle("next", verify_result=self.checker([[16]]))["accepted"]
        )

    def test_interrupted_query_reservation_refuses_automatic_retry(self):
        skill = self.promote()
        original = AnalystWorkspace.call

        def fail_report(workspace, db_id, tool, arguments):
            if arguments == {"sql": SQL}:
                raise RuntimeError("Interrupted report execution")
            return original(workspace, db_id, tool, arguments)

        with patch.object(AnalystWorkspace, "call", fail_report):
            with self.assertRaises(RuntimeError):
                self.execute(skill)
        self.assertEqual(self.bank.state()["pending"], 1)
        with self.assertRaisesRegex(ValueError, "Interrupted reserved"):
            self.execute(skill)
        with self.assertRaises(ValueError):
            self.bank.settle("next", verify_result=self.checker([[16]]))

    def test_multiple_pending_jobs_keep_room_for_all_verdicts(self):
        self.bank.close()
        self.bank = AnalyticalSkills(
            self.root / "capped.sqlite",
            source_id=SOURCE,
            checker_id=CHECKER,
            max_events=8,
        )
        skill = self.promote()
        self.execute(skill, job="one")
        self.execute(skill, job="two")
        with self.assertRaises(ValueError):
            self.execute(skill, job="three")
        for job in ("one", "two"):
            self.assertTrue(
                self.bank.settle(job, verify_result=self.checker([[16]]))["accepted"]
            )
        self.assertEqual(self.bank.state()["events"], 7)

    def test_single_writer_private_permissions_and_binding_are_enforced(self):
        with self.assertRaises(BlockingIOError):
            self.open()
        self.bank.close()
        with self.assertRaises(ValueError):
            self.open(source_id="different-history")
        os.chmod(self.root / "skills.sqlite", 0o644)
        with self.assertRaises(ValueError):
            self.open()
        os.chmod(self.root / "skills.sqlite", 0o600)
        self.bank = self.open()

    def test_changed_journal_and_source_file_are_detected(self):
        skill = self.promote()
        data_path = self.b.parent / "rows.sqlite"
        with closing(sqlite3.connect(data_path)) as connection, connection:
            connection.execute("INSERT INTO entries VALUES('east',100)")
        with self.assertRaises(ValueError):
            self.execute(skill)
        self.bank.db.execute("UPDATE skill_events SET previous_hash=?", ("f" * 64,))
        with self.assertRaises(ValueError):
            self.bank.verify()

    def test_unicode_query_and_rows_keep_authentic_workspace_identity(self):
        sql = "SELECT 'é',coalesce(sum(amount),0) FROM entries WHERE region='east'"
        skill = self.promote(sql=sql)
        self.assertEqual(self.execute(skill)["receipt"]["rows"], [["é", 16]])


if __name__ == "__main__":
    unittest.main()
