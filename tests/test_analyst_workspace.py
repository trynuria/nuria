"""Read-only execution, exact finite grading and explicit capacity failures."""

import hashlib
import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import (
    SCHEMA,
    AnalystWorkspace,
    grade_result,
    ordered_query,
    select_only,
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AnalystWorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Evidence is retained; the fixture does not remove files at teardown.
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("analyst-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        self.database = self.root / "sample.sqlite"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TABLE events(id INTEGER PRIMARY KEY,name TEXT,amount INTEGER)"
            )
            connection.executemany(
                "INSERT INTO events VALUES (?,?,?)",
                [
                    (1, "a", None),
                    (2, "a", 1),
                    (3, "b", 1),
                    (4, "b", 1),
                    (5, "empty", 0),
                ],
            )
        documents = {}
        for name in ("business", "columns", "schema"):
            path = self.root / (name + ".txt")
            path.write_text("Supplied " + name + " documentation")
            documents[name] = {"path": path.name, "sha256": sha(path)}
        self.manifest = {
            "schema": SCHEMA,
            "revision": "0" * 40,
            "databases": {
                "sample": {
                    "database": {
                        "path": self.database.name,
                        "sha256": sha(self.database),
                    },
                    "documents": documents,
                }
            },
        }
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps(self.manifest))

    def workspace(self, **limits):
        return AnalystWorkspace(self.registry, **limits)

    def test_schema_and_supplied_knowledge_tools(self):
        workspace = self.workspace()
        tables = workspace.call("sample", "list_tables")
        self.assertEqual(tables["rows"], [["events", "table"]])
        self.assertTrue(tables["complete"])
        description = workspace.call("sample", "describe_table", {"table": "events"})
        self.assertEqual(
            [row[1] for row in description["rows"]], ["id", "name", "amount"]
        )
        knowledge = workspace.call("sample", "read_knowledge", {"section": "business"})
        self.assertEqual(knowledge["content"], "Supplied business documentation")
        self.assertEqual(knowledge["source"]["db_sha256"], sha(self.database))
        self.assertEqual(
            {row["name"] for row in workspace.tool_schema},
            {"list_tables", "describe_table", "read_knowledge", "query"},
        )
        with self.assertRaises(ValueError):
            workspace.call(
                "sample", "read_knowledge", {"section": "evaluator/oracle.json"}
            )

    def test_null_duplicates_and_requested_order_are_not_erased(self):
        workspace = self.workspace()
        receipt = workspace.call(
            "sample", "query", {"sql": "SELECT amount FROM events ORDER BY id"}
        )
        expected = [[None], [1], [1], [1], [0]]
        self.assertTrue(
            grade_result(receipt, expected, ordered=True, expected_columns=1)
        )
        self.assertFalse(grade_result(receipt, [[None], [1], [0]], ordered=False))
        self.assertFalse(
            grade_result(receipt, [[0], [1], [1], [1], [None]], ordered=True)
        )
        self.assertTrue(
            grade_result(receipt, [[0], [1], [1], [1], [None]], ordered=False)
        )
        self.assertFalse(
            grade_result(receipt, [[None], [1.0], [1.0], [1.0], [0]], ordered=True)
        )

    def test_empty_answers_still_check_column_count(self):
        receipt = self.workspace().call(
            "sample", "query", {"sql": "SELECT id,name FROM events WHERE 0"}
        )
        self.assertTrue(grade_result(receipt, [], expected_columns=2))
        self.assertFalse(grade_result(receipt, [], expected_columns=1))

    def test_writes_attachment_pragma_extensions_and_multiple_statements_fail(self):
        workspace = self.workspace()
        original = sha(self.database)
        queries = [
            "DELETE FROM events",
            "WITH c AS (SELECT 1) DELETE FROM events",
            "ATTACH ':memory:' AS other",
            "PRAGMA writable_schema=ON",
            "SELECT * FROM pragma_database_list",
            "SELECT load_extension('unregistered')",
            "SELECT readfile('unregistered')",
            "SELECT 1; DELETE FROM events",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                receipt = workspace.call("sample", "query", {"sql": sql})
                self.assertFalse(receipt["complete"])
                self.assertNotEqual(receipt["status"], "ok")
                self.assertFalse(grade_result(receipt, []))
        self.assertEqual(sha(self.database), original)
        rows = workspace.call("sample", "query", {"sql": "SELECT COUNT(*) FROM events"})
        self.assertEqual(rows["rows"], [[5]])

    def test_vm_capacity_is_an_incomplete_result_not_a_zero_answer(self):
        workspace = self.workspace(vm_steps=100)
        result = workspace.call(
            "sample",
            "query",
            {
                "sql": "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x"
            },
        )
        self.assertEqual(result["status"], "vm_limit")
        self.assertEqual(result["cost"]["vm_progress_callbacks"], 100)
        self.assertFalse(result["complete"])
        self.assertFalse(grade_result(result, []))

    def test_hard_deadline_reports_unknown_or_completed_observed_cost(self):
        result = self.workspace(deadline_ms=25).call(
            "sample",
            "query",
            {
                "sql": "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x"
            },
        )
        self.assertEqual(result["status"], "deadline")
        self.assertFalse(result["complete"])
        steps = result["cost"]["vm_progress_callbacks"]
        self.assertTrue(steps is None or steps > 0)

    def test_row_and_byte_limits_prevent_qualified_partial_answers(self):
        row_limited = self.workspace(row_limit=2).call(
            "sample", "query", {"sql": "SELECT id FROM events ORDER BY id"}
        )
        self.assertEqual(row_limited["status"], "row_limit")
        self.assertFalse(grade_result(row_limited, [[1], [2]], ordered=True))
        large = "x" * 5000
        byte_limited = self.workspace(byte_limit=4096).call(
            "sample", "query", {"sql": "SELECT '" + large + "'"}
        )
        self.assertEqual(byte_limited["status"], "byte_limit")
        self.assertFalse(byte_limited["complete"])
        encoded = json.dumps(
            byte_limited,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        self.assertLessEqual(len(encoded), 4096)
        self.assertEqual(byte_limited["cost"]["output_bytes"], len(encoded))

    def test_registry_source_changes_or_paths_outside_registry_are_refused(self):
        workspace = self.workspace()
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO events VALUES (6,'changed',2)")
        with self.assertRaises(ValueError):
            workspace.call("sample", "list_tables")
        self.manifest["databases"]["sample"]["database"]["path"] = "../outside.sqlite"
        self.registry.write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            self.workspace()

    def test_model_controls_only_typed_tool_arguments(self):
        workspace = self.workspace()
        detached = workspace.limits
        detached["vm_steps"] = 1000000000000
        self.assertEqual(workspace.limits["vm_steps"], 1000000)
        for args in (
            {"sql": "SELECT 1", "path": "another.sqlite"},
            {"sql": 1},
            {"sql": ""},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                workspace.call("sample", "query", args)
        for db in ("missing", "../sample", []):
            with self.subTest(db=db), self.assertRaises(ValueError):
                workspace.call(db, "list_tables")
        with self.assertRaises(ValueError):
            self.workspace(vm_steps=True)

    def test_order_contract_ignores_comments_strings_and_nested_ordering(self):
        self.assertFalse(ordered_query("SELECT 'ORDER BY' /* ORDER BY */ FROM events"))
        self.assertFalse(
            ordered_query("SELECT * FROM (SELECT * FROM events ORDER BY id)")
        )
        self.assertTrue(
            ordered_query(
                "WITH c AS (SELECT * FROM events) SELECT * FROM c ORDER BY id DESC"
            )
        )
        self.assertTrue(select_only("-- SELECT comment\n SELECT 1"))
        self.assertFalse(select_only("/* SELECT */ DELETE FROM events"))


if __name__ == "__main__":
    unittest.main()
