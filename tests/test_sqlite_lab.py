"""Real SQLite result equivalence, measured work and intervention boundaries."""

import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from cognition.sqlite_lab import MAX_QUERIES, SQLiteLab


def fixture(rows=120):
    lines = ["hr,weekday,mnth,workingday,weathersit,season,cnt"]
    for index in range(rows):
        lines.append(
            f"{index % 24},{index % 7},{index % 12 + 1},{index % 2},"
            f"{index % 4 + 1},{index % 4 + 1},{index * 2}"
        )
    return ("\n".join(lines) + "\n").encode()


class SQLiteLabTests(unittest.TestCase):
    def setUp(self):
        self.csv = fixture()
        self.lab = SQLiteLab(self.csv)
        self.addCleanup(self.lab.close)
        self.queries = [
            {"family": 0, "a": 17, "b": 2},
            {"family": 1, "a": 4, "b": 1},
            {"family": 2, "a": 3, "b": 3},
        ]

    def test_every_index_retains_exact_aggregate_answers(self):
        baseline = self.lab.run(0, self.queries)
        for arm in range(1, 4):
            indexed = self.lab.run(arm, self.queries)
            self.assertEqual(indexed["results"], baseline["results"])
            self.assertEqual(indexed["results_sha256"], baseline["results_sha256"])
            self.assertGreater(indexed["index_build_steps"], 0)
            self.assertGreater(indexed["index_bytes"], 0)
            self.assertEqual(
                indexed["total_steps"],
                indexed["index_build_steps"] + indexed["query_steps"],
            )
            self.assertIn(
                f"USING INDEX candidate_{arm}", indexed["query_plans"][arm - 1][0]
            )
        self.assertEqual(baseline["index_build_steps"], 0)
        self.assertEqual(baseline["index_bytes"], 0)
        self.assertTrue(
            all("SCAN rental" in plan[0] for plan in baseline["query_plans"])
        )

    def test_query_cost_repeats_exactly_without_statement_cache_or_arm_contamination(
        self,
    ):
        first = self.lab.run(0, self.queries)
        self.lab.run(1, self.queries)
        self.lab.run(3, self.queries)
        again = self.lab.run(0, self.queries)
        for key in (
            "results_sha256",
            "index_build_steps",
            "query_steps",
            "total_steps",
            "index_bytes",
            "query_plans",
        ):
            self.assertEqual(first[key], again[key])
        self.assertEqual(first["rows"], 120)
        self.assertEqual(first["dataset_sha256"], hashlib.sha256(self.csv).hexdigest())
        self.assertEqual(first["sqlite_version"], self.lab._sqlite.version)
        self.assertEqual(first["sqlite_source_id"], self.lab._sqlite.source_id)
        self.assertEqual(first["sqlite_compile_options"], self.lab.compile_options)
        self.assertTrue(first["sqlite_compile_options"])
        self.assertGreater(first["wall_seconds"], 0)
        self.assertGreater(first["cpu_seconds"], 0)
        self.assertFalse(first["automatic_indexes"])

    def test_instrumentation_can_show_a_useful_index_but_charges_construction(self):
        queries = [{"family": 0, "a": 17, "b": 2}] * MAX_QUERIES
        unindexed = self.lab.run(0, queries)
        indexed = self.lab.run(1, queries)
        self.assertEqual(unindexed["results_sha256"], indexed["results_sha256"])
        self.assertLess(indexed["query_steps"], unindexed["query_steps"])
        self.assertGreater(indexed["index_build_steps"], 0)
        self.assertLess(indexed["total_steps"], unindexed["total_steps"])

    def test_sql_and_index_surfaces_are_closed(self):
        invalid_arms = [True, -1, 4, 1.0, "CREATE INDEX other ON rental(cnt)", None]
        for arm in invalid_arms:
            with self.subTest(arm=arm), self.assertRaises(ValueError):
                self.lab.run(arm, self.queries)
        invalid_queries = [
            [],
            tuple(self.queries),
            self.queries * 11,
            [{"family": "hr; DROP TABLE rental", "a": 1, "b": 2}],
            [{"family": 3, "a": 1, "b": 2}],
            [{"family": True, "a": 1, "b": 0}],
            [{"family": 0, "a": 24, "b": 0}],
            [{"family": 0, "a": 1, "b": -1}],
            [{"family": 1, "a": 0, "b": 0}],
            [{"family": 1, "a": 1, "b": 2}],
            [{"family": 2, "a": 5, "b": 1}],
            [{"family": 2, "a": 1, "b": 0}],
            [{"family": 0, "a": 1, "b": 0, "sql": "SELECT 1"}],
            [{"family": 0, "a": "1 OR 1=1", "b": 0}],
            [{"family": 0, "a": 1, "b": float("nan")}],
        ]
        for queries in invalid_queries:
            with self.subTest(queries=queries), self.assertRaises(ValueError):
                self.lab.run(0, queries)

    def test_empty_matches_keep_sql_null_sum_and_zero_count(self):
        with SQLiteLab(fixture(1)) as lab:
            result = lab.run(0, [{"family": 0, "a": 23, "b": 6}])
        self.assertEqual(result["results"], [[None, 0]])

    def test_input_csv_is_bounded_and_rejects_bad_records(self):
        good_header = b"hr,weekday,mnth,workingday,weathersit,season,cnt\n"
        invalid = [
            b"",
            "not bytes",
            good_header,
            b"hr,hr,cnt\n1,1,1\n",
            b"hr,cnt\n1,1\n",
            good_header + b"24,0,1,0,1,1,1\n",
            good_header + b"1,0,1,0,1,1,-1\n",
            good_header + b"1,0,1,0,1,1,1.0\n",
            good_header + b"1,0,1,0,1,1,1,EXTRA\n",
            good_header + b"1,0,1,0,1,1\n",
            good_header + b"1,0,1,0,1,1,\xff\n",
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                SQLiteLab(data)
        with patch("cognition.sqlite_lab.MAX_ROWS", 2), self.assertRaises(ValueError):
            SQLiteLab(fixture(3))
        with (
            patch("cognition.sqlite_lab.MAX_CSV_BYTES", 2),
            self.assertRaises(ValueError),
        ):
            SQLiteLab(fixture(1))

    def test_statement_is_finalized_even_when_work_bound_fails(self):
        connection = self.lab._sqlite.open()
        self.addCleanup(self.lab._sqlite.close, connection)
        with patch("cognition.sqlite_lab.MAX_STEPS", 1):
            with self.assertRaises(RuntimeError):
                self.lab._sqlite.run(connection, "SELECT 1 UNION ALL SELECT 2")
        self.assertEqual(self.lab._sqlite.run(connection, "SELECT 3")[0], [[3]])

    def test_fresh_database_is_closed_if_execution_fails(self):
        before = self.lab.run(0, self.queries)
        with patch("cognition.sqlite_lab.MAX_STEPS", 1):
            with self.assertRaises(RuntimeError):
                self.lab.run(1, self.queries)
        after = self.lab.run(0, self.queries)
        self.assertEqual(before["total_steps"], after["total_steps"])
        self.assertEqual(before["results_sha256"], after["results_sha256"])

    def test_native_planner_reference_has_explicit_extra_capacity_and_full_build_cost(
        self,
    ):
        result = self.lab.run_all_indexes_reference(self.queries)
        baseline = self.lab.run(0, self.queries)
        individual = [self.lab.run(arm, self.queries) for arm in range(1, 4)]
        self.assertEqual(result["results_sha256"], baseline["results_sha256"])
        self.assertEqual(result["index_count"], 3)
        self.assertEqual(result["arm"], "all-indexes-reference")
        self.assertIn("capacity exception", result["capacity"])
        self.assertGreaterEqual(
            result["index_build_steps"],
            sum(item["index_build_steps"] for item in individual),
        )
        self.assertGreater(
            result["index_bytes"], max(item["index_bytes"] for item in individual)
        )
        for index, plans in enumerate(result["query_plans"], 1):
            self.assertIn(f"USING INDEX candidate_{index}", plans[0])

    def test_c_api_reports_real_statement_counts_and_finalizes_failed_statements(self):
        connection = self.lab._sqlite.open()
        self.addCleanup(self.lab._sqlite.close, connection)
        first_rows, first_steps = self.lab._sqlite.run(connection, "SELECT 1")
        second_rows, second_steps = self.lab._sqlite.run(connection, "SELECT 1")
        self.assertEqual(first_rows, [[1]])
        self.assertEqual(second_rows, first_rows)
        self.assertEqual(first_steps, second_steps)
        self.assertGreater(first_steps, 0)
        with self.assertRaises(sqlite3.OperationalError):
            self.lab._sqlite.run(connection, "SELECT missing FROM absent")
        self.assertEqual(self.lab._sqlite.run(connection, "SELECT 2")[0], [[2]])
