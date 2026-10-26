"""Read-only, bounded SQLite tools over a caller-selected dataset registry.

The model supplies tool arguments, never filesystem paths. Reference queries and
expected answers are deliberately absent from this interface. Receipts describe
finite execution, not universal query equivalence or authenticated attestations.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

SCHEMA = "nuria.analyst-workspace.v1"
MAX_DATABASE_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_BYTES = 128 * 1024
FUNCTIONS = frozenset(
    "abs avg ceil ceiling char coalesce concat concat_ws count cume_dist date datetime "
    "dense_rank exp first_value floor format glob group_concat hex ifnull iif instr "
    "json json_array json_array_length json_error_position json_extract json_group_array "
    "json_group_object json_object json_patch json_quote json_remove json_replace "
    "json_set json_type json_valid julianday lag last_value lead length like likelihood "
    "likely ln log log10 log2 lower ltrim max min mod nth_value ntile nullif octet_length "
    "percent_rank pi pow power rank replace round row_number rtrim sign sin cos sqrt "
    "strftime string_agg substr substring sum tan time timediff total trim trunc typeof "
    "unicode unixepoch unlikely unhex upper -> ->>".split()
) - {"format"}


def _serial(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value):
    return hashlib.sha256(_serial(value).encode()).hexdigest()


def _bounded_int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Analyst resource limit is invalid")
    return value


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cell(value):
    if value is None or type(value) in (str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is bytes:
        return {"blob_hex": value.hex()}
    raise ValueError("SQLite returned a value outside the receipt format")


def sql_tokens(sql):
    """Lexical tokens with nesting depth; quoted text/comments are not keywords."""
    if type(sql) is not str or not 1 <= len(sql.encode()) <= 16384 or "\x00" in sql:
        raise ValueError("SQL needs a bounded nonempty string")
    index, depth = 0, 0
    while index < len(sql):
        char = sql[index]
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end + 1
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise ValueError("SQL comment is unfinished")
            index = end + 2
        elif char in "'\"`[":
            close = "]" if char == "[" else char
            index += 1
            while index < len(sql):
                if sql[index] == close:
                    index += 1
                    if close != "]" and index < len(sql) and sql[index] == close:
                        index += 1
                        continue
                    break
                index += 1
        elif char == "(":
            depth += 1
            index += 1
        elif char == ")":
            depth -= 1
            index += 1
        elif char.isalpha() or char == "_":
            end = index + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            yield sql[index:end].upper(), depth
            index = end
        else:
            index += 1


def select_only(sql):
    """Syntactic eligibility only; the runtime authorizer remains mandatory."""
    tokens = iter(sql_tokens(sql))
    return next(tokens, (None, 0))[0] in ("SELECT", "WITH")


def ordered_query(sql):
    tokens = [token for token, depth in sql_tokens(sql) if depth == 0]
    return any(a == "ORDER" and b == "BY" for a, b in zip(tokens, tokens[1:]))


def _query_worker(sender, database, tool, arguments, limits):
    """One isolated process; the parent also enforces a wall-clock deadline."""
    started = time.monotonic()
    steps, stop = 0, None
    connection = None
    result = {
        "status": "query_error",
        "complete": False,
        "rows": [],
        "columns": [],
        "error": None,
    }

    def progress():
        nonlocal steps, stop
        steps += 1
        if steps >= limits["vm_steps"]:
            stop = "vm_limit"
        elif (time.monotonic() - started) * 1000 >= limits["deadline_ms"]:
            stop = "deadline"
        return int(stop is not None)

    def authorizer(action, first, second, database_name, trigger):
        del trigger
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and database_name in (None, "main"):
            return sqlite3.SQLITE_OK
        if (
            action == sqlite3.SQLITE_FUNCTION
            and (second or first or "").lower() in FUNCTIONS
        ):
            return sqlite3.SQLITE_OK
        if (
            tool == "describe_table"
            and action == sqlite3.SQLITE_PRAGMA
            and first == "table_xinfo"
            and second == arguments["table"]
        ):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    try:
        connection = sqlite3.connect(
            "file:" + quote(database, safe="/") + "?mode=ro&immutable=1", uri=True
        )
        if hasattr(connection, "enable_load_extension"):
            connection.enable_load_extension(False)
        connection.execute("PRAGMA temp_store=MEMORY")
        for category, value in (
            (sqlite3.SQLITE_LIMIT_LENGTH, 65536),
            (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16384),
            (sqlite3.SQLITE_LIMIT_COLUMN, 128),
            (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64),
            (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 16),
            (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
            (sqlite3.SQLITE_LIMIT_VDBE_OP, 200000),
            (sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 32),
        ):
            connection.setlimit(category, value)
        connection.set_authorizer(authorizer)
        connection.set_progress_handler(progress, 1)
        if tool == "list_tables":
            sql = "SELECT name,type FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        elif tool == "describe_table":
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                )
            }
            if arguments["table"] not in names:
                raise ValueError("Unknown table")
            sql = 'PRAGMA table_xinfo("' + arguments["table"].replace('"', '""') + '")'
        else:
            sql = arguments["sql"]
            if not select_only(sql):
                raise ValueError(
                    "Only a single SELECT or read-only WITH query is accepted"
                )
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description or ()]
        size = len(_serial(columns).encode())
        result["columns"] = columns
        while True:
            row = cursor.fetchone()
            if row is None:
                result.update(status="ok", complete=True)
                break
            if len(result["rows"]) >= limits["row_limit"]:
                result["status"] = "row_limit"
                break
            row = [_cell(value) for value in row]
            size += len(_serial(row).encode()) + 1
            if size > limits["byte_limit"]:
                result["status"] = "byte_limit"
                break
            result["rows"].append(row)
        result["output_bytes"] = size
    except sqlite3.Error as error:
        result["status"] = stop or "query_error"
        result["error"] = str(error)[:256]
    except (ValueError, MemoryError, OverflowError) as error:
        result["status"] = stop or "query_rejected"
        result["error"] = str(error)[:256]
    finally:
        if connection is not None:
            connection.close()
        result["cost"] = {
            "vm_progress_callbacks": steps,
            "worker_wall_ms": (time.monotonic() - started) * 1000,
            "output_bytes": result.pop(
                "output_bytes", len(_serial(result["rows"]).encode())
            ),
        }
        try:
            sender.send_bytes(_serial(result).encode())
        finally:
            sender.close()


class AnalystWorkspace:
    def __init__(
        self,
        registry_manifest,
        *,
        vm_steps=1000000,
        row_limit=200,
        byte_limit=65536,
        deadline_ms=2000,
    ):
        path = Path(registry_manifest).resolve()
        if path.stat().st_size > 65536:
            raise ValueError("Dataset registry exceeds its bound")
        manifest = json.loads(path.read_text())
        if (
            manifest.get("schema") != SCHEMA
            or not 1 <= len(manifest.get("databases", {})) <= 32
        ):
            raise ValueError("Dataset registry is malformed")
        self.root = path.parent
        self._manifest = json.loads(_serial(manifest))
        if (
            type(manifest.get("revision")) is not str
            or re.fullmatch(
                r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})", manifest["revision"]
            )
            is None
        ):
            raise ValueError(
                "Dataset revision must be a pinned commit or content identity"
            )
        self._limits = {
            "vm_steps": _bounded_int(vm_steps, 1, 10000000),
            "row_limit": _bounded_int(row_limit, 1, 10000),
            "byte_limit": _bounded_int(byte_limit, 4096, 1024 * 1024),
            "deadline_ms": _bounded_int(deadline_ms, 25, 30000),
        }
        self.registry_sha256 = _digest(manifest)
        for db_id, record in self._manifest["databases"].items():
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", db_id) is None:
                raise ValueError("Database registry identity is malformed")
            self._registered_file(record["database"], MAX_DATABASE_BYTES)
            for document in record["documents"].values():
                self._registered_file(document, MAX_DOCUMENT_BYTES)

    @property
    def limits(self):
        return dict(self._limits)

    def _registered_file(self, record, maximum):
        path = (self.root / record["path"]).resolve()
        if (
            not path.is_relative_to(self.root)
            or not path.is_file()
            or path.stat().st_size > maximum
            or re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")) is None
            or _sha(path) != record["sha256"]
        ):
            raise ValueError("Registered dataset file identity changed")
        return path

    @property
    def tool_schema(self):
        return [
            {
                "name": "list_tables",
                "description": "List tables and views in the active database",
                "arguments": {},
            },
            {
                "name": "describe_table",
                "description": "Read column names, declared types, nullability and primary-key metadata",
                "arguments": {"table": "string"},
            },
            {
                "name": "read_knowledge",
                "description": "Read supplied business knowledge, column descriptions or schema documentation",
                "arguments": {"section": ["business", "columns", "schema"]},
            },
            {
                "name": "query",
                "description": "Execute one read-only SELECT/WITH query. Incomplete results are not accepted answers.",
                "arguments": {"sql": "string"},
            },
        ]

    def call(self, db_id, tool, arguments=None):
        started = time.monotonic()
        arguments = {} if arguments is None else arguments
        if (
            type(db_id) is not str
            or type(tool) is not str
            or db_id not in self._manifest["databases"]
            or type(arguments) is not dict
            or tool not in {"list_tables", "describe_table", "read_knowledge", "query"}
        ):
            raise ValueError("Unknown database, tool or argument envelope")
        required = {
            "list_tables": set(),
            "describe_table": {"table"},
            "read_knowledge": {"section"},
            "query": {"sql"},
        }[tool]
        if set(arguments) != required or any(
            type(v) is not str for v in arguments.values()
        ):
            raise ValueError("Analyst tool arguments are malformed")
        if any(not 1 <= len(v.encode()) <= 16384 for v in arguments.values()):
            raise ValueError("Analyst tool argument exceeds its bound")
        record = self._manifest["databases"][db_id]
        database = self._registered_file(record["database"], MAX_DATABASE_BYTES)
        receipt = {
            "schema": SCHEMA,
            "tool": tool,
            "runtime": {
                "sqlite_version": sqlite3.sqlite_version,
                "implementation_sha256": _sha(Path(__file__).resolve()),
            },
            "request_sha256": _digest(
                {
                    "db_id": db_id,
                    "tool": tool,
                    "arguments": arguments,
                    "limits": self.limits,
                }
            ),
            "source": {
                "dataset_revision": self._manifest["revision"],
                "db_id": db_id,
                "db_sha256": record["database"]["sha256"],
                "registry_sha256": self.registry_sha256,
            },
        }
        if tool == "read_knowledge":
            section = arguments["section"]
            if section not in record["documents"]:
                raise ValueError("Unknown supplied knowledge section")
            path = self._registered_file(
                record["documents"][section], MAX_DOCUMENT_BYTES
            )
            value = path.read_text()
            size = len(value.encode())
            receipt.update(
                status="ok" if size <= self.limits["byte_limit"] else "byte_limit",
                complete=size <= self.limits["byte_limit"],
                content=value if size <= self.limits["byte_limit"] else None,
                document_sha256=_sha(path),
                cost={
                    "vm_progress_callbacks": 0,
                    "output_bytes": min(size, self.limits["byte_limit"]),
                },
            )
        else:
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(
                target=_query_worker,
                args=(sender, str(database), tool, arguments, dict(self.limits)),
            )
            process.start()
            sender.close()
            try:
                remaining = max(
                    0, self.limits["deadline_ms"] / 1000 - (time.monotonic() - started)
                )
                if receiver.poll(remaining):
                    try:
                        result = json.loads(receiver.recv_bytes(2 * 1024 * 1024))
                        receipt.update(result)
                    except (EOFError, OSError, ValueError):
                        receipt.update(
                            status="worker_failed",
                            complete=False,
                            rows=[],
                            columns=[],
                            cost={"vm_progress_callbacks": None, "output_bytes": None},
                        )
                else:
                    receipt.update(
                        status="deadline",
                        complete=False,
                        rows=[],
                        columns=[],
                        cost={"vm_progress_callbacks": None, "output_bytes": None},
                    )
            finally:
                receiver.close()
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=0.5)
                process.close()
            # Data replacement during execution invalidates the receipt.
            self._registered_file(record["database"], MAX_DATABASE_BYTES)
        receipt["cost"]["wall_ms"] = (time.monotonic() - started) * 1000
        receipt["cost"]["payload_bytes_observed"] = receipt["cost"]["output_bytes"]
        receipt["cost"]["output_bytes"] = 0
        for _ in range(4):
            receipt.pop("result_sha256", None)
            receipt["result_sha256"] = _digest(receipt)
            size = len(_serial(receipt).encode())
            if size == receipt["cost"]["output_bytes"]:
                break
            receipt["cost"]["output_bytes"] = size
        if size > self.limits["byte_limit"]:
            receipt.update(status="byte_limit", complete=False, rows=[], columns=[])
            if "content" in receipt:
                receipt["content"] = None
            for _ in range(4):
                receipt.pop("result_sha256", None)
                receipt["result_sha256"] = _digest(receipt)
                size = len(_serial(receipt).encode())
                if size == receipt["cost"]["output_bytes"]:
                    break
                receipt["cost"]["output_bytes"] = size
        if size > self.limits["byte_limit"]:
            raise ValueError("Receipt metadata exceeds the configured byte limit")
        return receipt


def grade_result(receipt, expected_rows, *, ordered=False, expected_columns=None):
    """Exact finite result comparison; the caller supplies the authentic receipt."""
    if type(ordered) is not bool or type(expected_rows) is not list:
        raise ValueError("Expected result contract is malformed")
    if expected_columns is not None:
        _bounded_int(expected_columns, 0, 128)
    if (
        type(receipt) is not dict
        or receipt.get("status") != "ok"
        or receipt.get("complete") is not True
        or type(receipt.get("rows")) is not list
    ):
        return False
    if (
        expected_columns is not None
        and len(receipt.get("columns", [])) != expected_columns
    ):
        return False
    observed = [_serial(row) for row in receipt["rows"]]
    expected = [_serial(row) for row in expected_rows]
    return observed == expected if ordered else Counter(observed) == Counter(expected)
