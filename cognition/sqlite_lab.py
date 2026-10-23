"""Isolated, metered SQLite interventions over validated public CSV records.

The learner controls one index slot, not arbitrary SQL. Costs are observed
SQLite statement VM steps during index construction and query execution;
they are neither dollar prices nor a wall-clock performance guarantee. Dataset
loading, database cloning, validation and query-plan inspection are excluded
from that primary work count. Reported run timings include database cloning
and plan inspection; initial CSV parsing and base loading happen separately.
"""

from __future__ import annotations

import csv
import ctypes
import ctypes.util
import hashlib
import io
import json
import sqlite3
import time

ARCHIVE_SHA256 = "b70182d0d0508e9abbb79306ce5c0cec34869000f8220175ac83d11dbe845401"
CSV_SHA256 = "e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f"
SOURCE_URL = "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip"
SOURCE_DOI = "10.24432/C5W894"
FAMILIES = (("hr", "weekday"), ("mnth", "workingday"), ("weathersit", "season"))
RANGES = {
    "hr": (0, 23),
    "weekday": (0, 6),
    "mnth": (1, 12),
    "workingday": (0, 1),
    "weathersit": (1, 4),
    "season": (1, 4),
    "cnt": (0, 10_000_000),
}
MAX_QUERIES = 32
MAX_ROWS = 25_000
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_STEPS = 20_000_000


class _SQLite:
    """Own C connections; never inspect a Python connection's private pointer."""

    def __init__(self):
        path = ctypes.util.find_library("sqlite3")
        if not path:
            raise RuntimeError("A local SQLite C library is required for VM metering")
        self.lib = ctypes.CDLL(path)
        signatures = {
            "sqlite3_libversion": (ctypes.c_char_p, []),
            "sqlite3_sourceid": (ctypes.c_char_p, []),
            "sqlite3_open_v2": (
                ctypes.c_int,
                [
                    ctypes.c_char_p,
                    ctypes.POINTER(ctypes.c_void_p),
                    ctypes.c_int,
                    ctypes.c_char_p,
                ],
            ),
            "sqlite3_close": (ctypes.c_int, [ctypes.c_void_p]),
            "sqlite3_errmsg": (ctypes.c_char_p, [ctypes.c_void_p]),
            "sqlite3_prepare_v2": (
                ctypes.c_int,
                [
                    ctypes.c_void_p,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_void_p),
                    ctypes.POINTER(ctypes.c_char_p),
                ],
            ),
            "sqlite3_bind_int64": (
                ctypes.c_int,
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_int64],
            ),
            "sqlite3_step": (ctypes.c_int, [ctypes.c_void_p]),
            "sqlite3_column_count": (ctypes.c_int, [ctypes.c_void_p]),
            "sqlite3_column_type": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "sqlite3_column_int64": (ctypes.c_int64, [ctypes.c_void_p, ctypes.c_int]),
            "sqlite3_column_text": (ctypes.c_char_p, [ctypes.c_void_p, ctypes.c_int]),
            "sqlite3_stmt_status": (
                ctypes.c_int,
                [ctypes.c_void_p, ctypes.c_int, ctypes.c_int],
            ),
            "sqlite3_finalize": (ctypes.c_int, [ctypes.c_void_p]),
            "sqlite3_reset": (ctypes.c_int, [ctypes.c_void_p]),
            "sqlite3_backup_init": (
                ctypes.c_void_p,
                [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_char_p],
            ),
            "sqlite3_backup_step": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            "sqlite3_backup_finish": (ctypes.c_int, [ctypes.c_void_p]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = restype, argtypes
        self.version = self.lib.sqlite3_libversion().decode("ascii")
        self.source_id = self.lib.sqlite3_sourceid().decode("ascii")

    def open(self):
        connection = ctypes.c_void_p()
        code = self.lib.sqlite3_open_v2(b":memory:", ctypes.byref(connection), 6, None)
        if code:
            message = self.lib.sqlite3_errmsg(connection).decode("utf-8", "replace")
            self.lib.sqlite3_close(connection)
            raise sqlite3.OperationalError(message)
        return connection

    def close(self, connection):
        if connection.value:
            code = self.lib.sqlite3_close(connection)
            if code:
                raise sqlite3.OperationalError(
                    "SQLite lab connection retained a statement"
                )
            connection.value = None

    def check(self, connection, code):
        if code:
            raise sqlite3.OperationalError(
                self.lib.sqlite3_errmsg(connection).decode("utf-8", "replace")
            )

    def prepare(self, connection, sql):
        statement = ctypes.c_void_p()
        code = self.lib.sqlite3_prepare_v2(
            connection, sql.encode("ascii"), -1, ctypes.byref(statement), None
        )
        if code:
            if statement.value:
                self.lib.sqlite3_finalize(statement)
            self.check(connection, code)
        return statement

    def run(self, connection, sql, parameters=()):
        statement = self.prepare(connection, sql)
        try:
            for index, value in enumerate(parameters, 1):
                self.check(
                    connection, self.lib.sqlite3_bind_int64(statement, index, value)
                )
            rows = []
            while True:
                code = self.lib.sqlite3_step(statement)
                if code == 101:
                    break
                if code != 100:
                    self.check(connection, code)
                row = []
                for column in range(self.lib.sqlite3_column_count(statement)):
                    kind = self.lib.sqlite3_column_type(statement, column)
                    if kind == 5:
                        row.append(None)
                    elif kind == 1:
                        row.append(self.lib.sqlite3_column_int64(statement, column))
                    elif kind == 3:
                        row.append(
                            self.lib.sqlite3_column_text(statement, column).decode(
                                "utf-8"
                            )
                        )
                    else:
                        raise RuntimeError("Unexpected SQLite lab result type")
                rows.append(row)
            steps = self.lib.sqlite3_stmt_status(statement, 4, 0)
            if not 0 <= steps <= MAX_STEPS:
                raise RuntimeError("SQLite statement exceeded its VM work bound")
            return rows, steps
        finally:
            self.lib.sqlite3_finalize(statement)

    def insert(self, connection, rows):
        self.run(connection, "BEGIN")
        statement = self.prepare(
            connection, "INSERT INTO rental VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            for row in rows:
                for index, value in enumerate(row, 1):
                    self.check(
                        connection, self.lib.sqlite3_bind_int64(statement, index, value)
                    )
                code = self.lib.sqlite3_step(statement)
                if code != 101:
                    self.check(connection, code)
                self.check(connection, self.lib.sqlite3_reset(statement))
        finally:
            self.lib.sqlite3_finalize(statement)
        self.run(connection, "COMMIT")

    def copy(self, source, destination):
        backup = self.lib.sqlite3_backup_init(destination, b"main", source, b"main")
        if not backup:
            self.check(destination, 1)
        try:
            code = self.lib.sqlite3_backup_step(backup, -1)
            if code != 101:
                self.check(destination, code)
        finally:
            self.check(destination, self.lib.sqlite3_backup_finish(backup))


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("SQLite lab integer is outside its registered range")
    return value


def _queries(queries):
    if type(queries) is not list or not 1 <= len(queries) <= MAX_QUERIES:
        raise ValueError("SQLite lab requires one to 32 registered queries")
    result = []
    for query in queries:
        if type(query) is not dict or set(query) != {"family", "a", "b"}:
            raise ValueError("SQLite lab query fields are not registered")
        family = _integer(query["family"], 0, len(FAMILIES) - 1)
        first, second = FAMILIES[family]
        a = _integer(query["a"], *RANGES[first])
        b = _integer(query["b"], *RANGES[second])
        result.append({"family": family, "a": a, "b": b})
    return result


class SQLiteLab:
    """Fresh in-memory, one-index experiments; no provider or production access.

    Arm zero creates no index. Arms one through three create exactly one index
    corresponding to the registered predicate families. Each call clones the
    same unindexed dataset and cannot retain another arm's index or outcomes.
    Dataset bytes are hashed, allowing a caller to pin the exact public corpus.
    Small validated CSV fixtures are accepted for offline unit checks.
    """

    def __init__(self, csv_bytes: bytes):
        if type(csv_bytes) is not bytes or not 0 < len(csv_bytes) <= MAX_CSV_BYTES:
            raise ValueError("SQLite lab CSV must be bounded UTF-8 bytes")
        self.dataset_sha256 = hashlib.sha256(csv_bytes).hexdigest()
        try:
            reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
            fields = reader.fieldnames
            if (
                not fields
                or len(fields) != len(set(fields))
                or not set(RANGES).issubset(fields)
            ):
                raise ValueError("SQLite lab CSV columns are missing or duplicated")
            rows = []
            for record in reader:
                if None in record or any(value is None for value in record.values()):
                    raise ValueError("SQLite lab CSV record has inconsistent columns")
                values = []
                for field, (low, high) in RANGES.items():
                    raw = record[field]
                    if not raw or not raw.isascii() or not raw.isdecimal():
                        raise ValueError(
                            "SQLite lab CSV values must be decimal integers"
                        )
                    values.append(_integer(int(raw), low, high))
                rows.append(tuple(values))
                if len(rows) > MAX_ROWS:
                    raise ValueError("SQLite lab CSV exceeds the row bound")
        except (UnicodeDecodeError, csv.Error) as error:
            raise ValueError("SQLite lab CSV is malformed") from error
        if not rows:
            raise ValueError("SQLite lab CSV contains no observations")
        self.rows = len(rows)
        self._sqlite = _SQLite()
        self._base = self._sqlite.open()
        try:
            self._sqlite.run(self._base, "PRAGMA automatic_index=OFF")
            self._sqlite.run(
                self._base,
                "CREATE TABLE rental (hr INTEGER, weekday INTEGER, mnth INTEGER, "
                "workingday INTEGER, weathersit INTEGER, season INTEGER, cnt INTEGER)",
            )
            self._sqlite.insert(self._base, rows)
            compile_rows, _ = self._sqlite.run(self._base, "PRAGMA compile_options")
            self.compile_options = [row[0] for row in compile_rows]
        except BaseException:
            self._sqlite.close(self._base)
            raise

    def close(self):
        self._sqlite.close(self._base)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def run(self, arm: int, queries: list[dict]) -> dict:
        """Return only the requested arm's measured work and exact answer digest.

        Query specifications expose family and current predicates, but no future
        workload or unchosen-arm measurements. SQL identifiers originate only
        from the fixed family registry; predicate values are bound parameters.
        """
        arm = _integer(arm, 0, len(FAMILIES))
        return self._run((arm,) if arm else (), arm, queries)

    def run_all_indexes_reference(self, queries: list[dict]) -> dict:
        """Explicit capacity exception for the native planner reference only.

        This baseline owns all three indexes, and pays their complete observed
        construction cost and allocated pages. It is not a selectable learner
        action and must not be described as meeting the one-index constraint.
        """
        return self._run((1, 2, 3), "all-indexes-reference", queries)

    def _run(self, arms, label, queries):
        queries = _queries(queries)
        started, cpu_started = time.perf_counter(), time.process_time()
        connection = self._sqlite.open()
        try:
            self._sqlite.copy(self._base, connection)
            self._sqlite.run(connection, "PRAGMA automatic_index=OFF")
            page_rows, _ = self._sqlite.run(connection, "PRAGMA page_size")
            page_size = page_rows[0][0]
            before_rows, _ = self._sqlite.run(connection, "PRAGMA page_count")
            before_pages = before_rows[0][0]
            build_steps = 0
            for arm in arms:
                first, second = FAMILIES[arm - 1]
                _, index_steps = self._sqlite.run(
                    connection,
                    f"CREATE INDEX candidate_{arm} ON rental({first}, {second})",
                )
                build_steps += index_steps
            after_rows, _ = self._sqlite.run(connection, "PRAGMA page_count")
            after_pages = after_rows[0][0]
            results, plans, query_steps = [], [], 0
            for query in queries:
                first, second = FAMILIES[query["family"]]
                sql = (
                    f"SELECT SUM(cnt), COUNT(*) FROM rental "
                    f"WHERE {first}=? AND {second}=?"
                )
                parameters = (query["a"], query["b"])
                values, steps = self._sqlite.run(connection, sql, parameters)
                results.append(list(values[0]))
                query_steps += steps
                plan_rows, _ = self._sqlite.run(
                    connection, "EXPLAIN QUERY PLAN " + sql, parameters
                )
                plans.append([row[3] for row in plan_rows])
            return {
                "arm": label,
                "rows": self.rows,
                "queries": queries,
                "results": results,
                "results_sha256": _digest(results),
                "index_build_steps": build_steps,
                "query_steps": query_steps,
                "total_steps": build_steps + query_steps,
                "index_bytes": (after_pages - before_pages) * page_size,
                "index_bytes_definition": "allocated SQLite page-count delta",
                "wall_seconds": time.perf_counter() - started,
                "cpu_seconds": time.process_time() - cpu_started,
                "query_plans": plans,
                "sqlite_version": self._sqlite.version,
                "sqlite_source_id": self._sqlite.source_id,
                "sqlite_compile_options": list(self.compile_options),
                "dataset_sha256": self.dataset_sha256,
                "cost_scope": "SQLITE_STMTSTATUS_VM_STEP for CREATE INDEX and SELECT",
                "capacity": (
                    "three-index native-planner reference; one-index capacity exception"
                    if len(arms) > 1
                    else "one registered index or none on a fresh in-memory copy"
                ),
                "index_count": len(arms),
                "automatic_indexes": False,
            }
        finally:
            self._sqlite.close(connection)
