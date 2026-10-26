"""Independently replay a retained finite analytical course without inference.

No Nuria execution/grading implementation is imported. Inputs retain their
original private paths inside the explicitly supplied protocol; the emitted
verdict contains hashes and counts only. Only attempted selected row spans are
decoded. SQL results are compared with explicit SQLite/JSON types, multiplicity
and an independently lexed outer ORDER BY requirement. These local hashes prove
integrity and execution agreement, not provider billing, model origin or learning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

VERSION = "nuria.independent-analytical-primary-replay.v1"
MAX_INPUT_BYTES = 32 * 1024 * 1024
SQL_LIMITS = {
    "sql_bytes": 16384,
    "rows": 200,
    "result_bytes": 65536,
    "wall_seconds": 2,
    "vm_instructions": 200000,
}


class ReplayError(ValueError):
    pass


def require(value, code):
    if not value:
        raise ReplayError(code)


def raw(path, maximum=MAX_INPUT_BYTES):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), "input_not_regular")
    require(path.stat().st_size <= maximum, "input_byte_limit")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    require(len(data) <= maximum, "input_byte_limit")
    return data


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value, *, ascii=True):
    return json.dumps(
        value,
        ensure_ascii=ascii,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(value, *, ascii=True):
    return sha(canonical(value, ascii=ascii))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def decode(data):
    return json.loads(
        data,
        object_pairs_hook=unique_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ReplayError("nonfinite_json")
        ),
    )


def read(path, maximum=8 * 1024 * 1024):
    return decode(raw(path, maximum))


def words(sql):
    require(
        type(sql) is str and 0 < len(sql.encode()) <= SQL_LIMITS["sql_bytes"],
        "sql_byte_limit",
    )
    require("\0" not in sql, "sql_nul")
    index, depth = 0, 0
    tokens = []
    while index < len(sql):
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end + 1
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            require(end >= 0, "sql_unclosed_comment")
            index = end + 2
        elif sql[index] in "'\"`[":
            closing = "]" if sql[index] == "[" else sql[index]
            index += 1
            closed = False
            while index < len(sql):
                if sql[index] == closing:
                    index += 1
                    if closing != "]" and index < len(sql) and sql[index] == closing:
                        index += 1
                        continue
                    closed = True
                    break
                index += 1
            require(closed, "sql_unclosed_quote")
        elif sql[index] == "(":
            depth += 1
            index += 1
        elif sql[index] == ")":
            depth -= 1
            require(depth >= 0, "sql_unbalanced_nesting")
            index += 1
        elif sql[index].isalpha() or sql[index] == "_":
            end = index + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            tokens.append((sql[index:end].upper(), depth))
            index = end
        else:
            index += 1
    require(depth == 0, "sql_unbalanced_nesting")
    return tokens


def ordered(sql):
    outer = [token for token, depth in words(sql) if depth == 0]
    return any(
        first == "ORDER" and second == "BY" for first, second in zip(outer, outer[1:])
    )


def cell(value):
    if value is None:
        return ["null", None]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float and math.isfinite(value):
        return ["real", repr(value)]
    if type(value) is str:
        return ["text", value]
    if type(value) is bytes:
        return ["blob", value.hex()]
    if type(value) is dict and set(value) == {"blob_hex"}:
        require(
            type(value["blob_hex"]) is str
            and re.fullmatch(r"(?:[0-9a-f]{2})*", value["blob_hex"]) is not None,
            "blob_shape",
        )
        return ["blob", value["blob_hex"]]
    raise ReplayError("unsupported_sql_cell")


def typed_rows(rows):
    require(type(rows) is list, "row_collection_shape")
    result = []
    for row in rows:
        require(type(row) in (list, tuple), "row_shape")
        result.append(canonical([cell(value) for value in row]).decode())
    return result


def equal_rows(left, right, *, preserve_order):
    left, right = typed_rows(left), typed_rows(right)
    return left == right if preserve_order else Counter(left) == Counter(right)


def execute(database, sql):
    tokens = words(sql)
    require(tokens and tokens[0][0] in ("SELECT", "WITH"), "select_only")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        require(
            not sidecar.exists() or sidecar.stat().st_size == 0, "active_sqlite_sidecar"
        )
    started, callbacks = time.monotonic(), 0

    def progress():
        nonlocal callbacks
        callbacks += 1
        return int(
            callbacks * 100 >= SQL_LIMITS["vm_instructions"]
            or time.monotonic() - started >= SQL_LIMITS["wall_seconds"]
        )

    def authorizer(action, first, second, name, trigger):
        del trigger
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and name in (None, "main"):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (
            second or first or ""
        ).lower() not in ("load_extension", "readfile", "writefile", "fts3_tokenizer"):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    connection = sqlite3.connect(
        Path(database).resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1
    )
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        for category, value in (
            (sqlite3.SQLITE_LIMIT_LENGTH, 65536),
            (sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQL_LIMITS["sql_bytes"]),
            (sqlite3.SQLITE_LIMIT_COLUMN, 128),
            (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64),
            (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 16),
            (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
            (sqlite3.SQLITE_LIMIT_VDBE_OP, 200000),
            (sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 0),
        ):
            connection.setlimit(category, value)
        connection.set_authorizer(authorizer)
        connection.set_progress_handler(progress, 100)
        cursor = connection.execute(sql)
        columns = [part[0] for part in cursor.description]
        rows, size = [], 0
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            require(len(rows) < SQL_LIMITS["rows"], "query_row_limit")
            require(
                time.monotonic() - started < SQL_LIMITS["wall_seconds"],
                "query_wall_limit",
            )
            size += len(canonical([cell(value) for value in row]))
            require(size <= SQL_LIMITS["result_bytes"], "query_result_byte_limit")
            rows.append(list(row))
        return {"columns": columns, "rows": rows, "vm_callbacks": callbacks}
    finally:
        connection.close()


def artifact_id(row):
    return digest({key: row[key] for key in ("phase", "method", "task_id")})


def check_receipt(receipt, source, implementation):
    require(receipt["source"] == source, "receipt_source_binding")
    require(
        receipt["runtime"]["implementation_sha256"] == implementation,
        "receipt_implementation_binding",
    )
    body = {key: value for key, value in receipt.items() if key != "result_sha256"}
    require(
        receipt["result_sha256"] == digest(body, ascii=False), "receipt_result_hash"
    )


def replay(run, protocol_path, protocol_sha256, frozen_sources):
    run, frozen_sources = Path(run), Path(frozen_sources)
    private_bytes = raw(protocol_path)
    require(sha(private_bytes) == protocol_sha256, "private_protocol_hash")
    private = decode(private_bytes)
    public_bytes = raw(private["public_protocol"])
    require(
        sha(public_bytes) == private["public_protocol_sha256"], "public_protocol_hash"
    )
    public = decode(public_bytes)
    source_checks = []
    require(len(private["source_files"]) == 11, "frozen_source_count")
    for relative, expected in private["source_files"].items():
        path = (frozen_sources / relative).resolve()
        require(path.is_relative_to(frozen_sources.resolve()), "frozen_source_escape")
        require(sha(raw(path, 2 * 1024 * 1024)) == expected, "frozen_source_hash")
        source_checks.append({"path": relative, "sha256": expected})
    manifest = read(frozen_sources / "manifest.json")
    require(
        {item["path"]: item["sha256"] for item in manifest} == private["source_files"],
        "frozen_source_manifest",
    )
    require(public["source_files"] == private["source_files"], "public_source_bindings")
    registry_path = Path(private["registry"])
    registry_bytes = raw(registry_path)
    require(
        sha(registry_bytes) == private["registry_sha256"] == public["registry_sha256"],
        "registry_raw_hash",
    )
    registry = decode(registry_bytes)
    registry_canonical = digest(registry, ascii=False)
    database_paths, registered_checks = {}, []
    for name, metadata in registry["databases"].items():
        for kind, file_record in [
            ("database", metadata["database"]),
            *[("document", entry) for entry in metadata["documents"].values()],
        ]:
            path = (registry_path.parent / file_record["path"]).resolve()
            require(
                path.is_relative_to(registry_path.parent.resolve()),
                "registered_source_escape",
            )
            require(
                sha(raw(path, 5 * 1024 * 1024)) == file_record["sha256"],
                "registered_source_hash",
            )
            registered_checks.append(
                {"db_id": name, "kind": kind, "sha256": file_record["sha256"]}
            )
            if kind == "database":
                database_paths[name] = path
    dataset_bytes = raw(private["dataset"])
    require(
        sha(dataset_bytes) == private["dataset_sha256"] == public["dataset_sha256"],
        "dataset_raw_hash",
    )
    report_bytes = raw(run / "report.json")
    report = decode(report_bytes)
    require(report["protocol_sha256"] == protocol_sha256, "report_protocol_binding")
    require(
        report["model_identity"] == public["model_identity"], "report_model_binding"
    )
    rows = report["rows"]
    expected = {
        (phase, record["method"], record["task_id"])
        for phase, records in public["expected_jobs"].items()
        for record in records
    }
    observed = [
        (record["phase"], record["method"], record["task_id"]) for record in rows
    ]
    require(
        len(rows) == len(expected) == public["expected_job_count"] == 54
        and len(set(observed)) == 54
        and set(observed) == expected,
        "complete_selected_denominator",
    )
    selection = read(run / "selection.json")
    require(
        selection["count"] == 54
        and {
            (record["phase"], record["method"], record["task_id"])
            for record in selection["selected"]
        }
        == expected,
        "selection_artifact_binding",
    )
    decoded, results = {}, []
    attempted_ids, final_count, accepted_count, abstentions = set(), 0, 0, 0
    implementation = private["source_files"]["cognition/analyst_workspace.py"]
    for row in rows:
        identity = artifact_id(row)
        require(row["artifact_id"] == identity, "report_artifact_identity")
        if not row["attempted"]:
            require(
                row["agent"] is None and not row.get("verdict", {}).get("complete"),
                "unexecuted_not_ungraded",
            )
            require(
                not (run / (identity + ".committed.json")).exists(),
                "unexpected_unexecuted_commit",
            )
            require(
                not (run / (identity + ".verdict.json")).exists(),
                "unexpected_unexecuted_grade",
            )
            continue
        attempted_ids.add(identity)
        full_bytes = raw(run / (identity + ".committed.json"))
        full = decode(full_bytes)
        require(
            full["attempted"] is True
            and all(
                full[key] == row[key]
                for key in ("phase", "method", "task_id", "usage", "policy_id")
            ),
            "committed_row_binding",
        )
        agent = full["agent"]
        require(
            agent is not None
            and all(row["agent"][key] == agent[key] for key in row["agent"]),
            "compact_agent_binding",
        )
        require(
            agent["result_sha256"]
            == digest(
                {key: value for key, value in agent.items() if key != "result_sha256"}
            ),
            "agent_result_hash",
        )
        require(agent["job_id"] == identity, "agent_job_binding")
        task_id, phase = row["task_id"], row["phase"]
        if task_id not in decoded:
            selected = [
                item
                for item in private["selected"][phase]
                if item["task_id"] == task_id
            ]
            require(len(selected) == 1, "attempted_selected_row")
            selected = selected[0]
            task_bytes = dataset_bytes[selected["start"] : selected["end"]]
            require(
                sha(task_bytes) == selected["row_sha256"], "attempted_task_row_hash"
            )
            task = decode(task_bytes)
            require(task["db_id"] == selected["db_id"], "attempted_database_binding")
            decoded[task_id] = task
        task = decoded[task_id]
        db_id = task["db_id"]
        source = {
            "dataset_revision": registry["revision"],
            "db_id": db_id,
            "db_sha256": registry["databases"][db_id]["database"]["sha256"],
            "registry_sha256": registry_canonical,
        }
        require(agent["source"] == source, "agent_source_binding")
        require(
            agent["configuration"]["registry_sha256"] == registry_canonical,
            "agent_registry_binding",
        )
        binding = agent["configuration"]["model_binding"]
        require(
            binding["source_id"] == registry["revision"]
            and binding["model_id"] == public["model_identity"],
            "agent_model_binding",
        )
        verdict_bytes = raw(run / (identity + ".verdict.json"))
        evidence = decode(verdict_bytes)
        require(evidence["row_sha256"] == digest(full), "verdict_committed_binding")
        require(
            row["verdict"]["evidence_sha256"] == digest(evidence),
            "verdict_evidence_hash",
        )
        require(
            row["verdict"]["complete"] is True
            and evidence["accepted"] == row["verdict"]["accepted"],
            "verdict_report_binding",
        )
        expected_query = task["query"]
        reference = execute(database_paths[db_id], expected_query)
        reference_ordered = ordered(expected_query)
        retained_reference = evidence["reference"]
        check_receipt(retained_reference, source, implementation)
        require(
            retained_reference["status"] == "ok"
            and retained_reference["complete"] is True,
            "reference_incomplete",
        )
        reference_request = {
            "db_id": db_id,
            "tool": "query",
            "arguments": {"sql": expected_query},
            "limits": agent["configuration"]["tool_limits"],
        }
        require(
            retained_reference["request_sha256"]
            == digest(reference_request, ascii=False),
            "reference_request_binding",
        )
        require(
            retained_reference["columns"] == reference["columns"]
            and equal_rows(
                retained_reference["rows"],
                reference["rows"],
                preserve_order=reference_ordered,
            ),
            "fresh_reference_mismatch",
        )
        submitted = agent["final_sql"]
        fresh = None
        if submitted is not None:
            final_count += 1
            receipt = agent["selected_receipt"]
            check_receipt(receipt, source, implementation)
            require(
                receipt["status"] == "ok" and receipt["complete"] is True,
                "own_query_incomplete",
            )
            request = {
                "db_id": db_id,
                "tool": "query",
                "arguments": {"sql": submitted},
                "limits": agent["configuration"]["tool_limits"],
            }
            require(
                receipt["request_sha256"] == digest(request, ascii=False),
                "own_query_request_binding",
            )
            require(
                any(
                    entry.get("kind") == "action"
                    and entry["action"]["tool"] == "query"
                    and entry["action"]["arguments"] == {"sql": submitted}
                    for entry in agent["trace"]
                ),
                "own_query_action_missing",
            )
            require(
                any(
                    entry.get("kind") == "tool_receipt" and entry["receipt"] == receipt
                    for entry in agent["trace"]
                ),
                "own_query_receipt_missing",
            )
            fresh = execute(database_paths[db_id], submitted)
            require(
                receipt["columns"] == fresh["columns"]
                and equal_rows(
                    receipt["rows"], fresh["rows"], preserve_order=ordered(submitted)
                ),
                "fresh_own_query_mismatch",
            )
            accepted = len(fresh["columns"]) == len(
                reference["columns"]
            ) and equal_rows(
                fresh["rows"], reference["rows"], preserve_order=reference_ordered
            )
        else:
            abstentions += 1
            require(agent["selected_receipt"] is None, "abstention_selected_receipt")
            accepted = False
        require(
            type(accepted) is bool and accepted == row["verdict"]["accepted"],
            "independent_acceptance_mismatch",
        )
        accepted_count += accepted
        results.append(
            {
                "phase": phase,
                "method": row["method"],
                "task_id": task_id,
                "policy_id": row["policy_id"],
                "artifact_id": identity,
                "committed_artifact_sha256": sha(full_bytes),
                "verdict_artifact_sha256": sha(verdict_bytes),
                "ordered_reference": reference_ordered,
                "reference_sql_sha256": sha(expected_query.encode()),
                "reference_typed_rows_sha256": digest(typed_rows(reference["rows"])),
                "submitted_sql_sha256": None
                if submitted is None
                else sha(submitted.encode()),
                "submitted_typed_rows_sha256": None
                if fresh is None
                else digest(typed_rows(fresh["rows"])),
                "reference_rows": len(reference["rows"]),
                "reference_columns": len(reference["columns"]),
                "submitted_rows": None if fresh is None else len(fresh["rows"]),
                "independent_accepted": accepted,
            }
        )
    require(
        {
            path.name.removesuffix(".committed.json")
            for path in run.glob("*.committed.json")
        }
        == attempted_ids,
        "complete_commit_inventory",
    )
    require(
        {path.name.removesuffix(".verdict.json") for path in run.glob("*.verdict.json")}
        == attempted_ids,
        "complete_verdict_inventory",
    )
    require(
        report["all_selected"]["attempted"] == len(results)
        and report["all_selected"]["accepted"] == accepted_count
        and report["all_selected"]["abstentions"] == abstentions
        and report["all_selected"]["unexecuted"] == 54 - len(results),
        "reported_totals_mismatch",
    )
    for name, metadata in registry["databases"].items():
        require(
            sha(raw(database_paths[name], 5 * 1024 * 1024))
            == metadata["database"]["sha256"],
            "database_changed_during_replay",
        )
    policy = read(run / "selected-policy.json")
    require(
        policy["artifact_sha256"]
        == digest(
            {key: value for key, value in policy.items() if key != "artifact_sha256"}
        ),
        "selected_policy_hash",
    )
    validation_scores = {}
    policy_scores, validation_pairs = {}, {}
    for row in results:
        if row["phase"] == "validation":
            validation_scores[row["method"]] = (
                validation_scores.get(row["method"], 0) + row["independent_accepted"]
            )
            policy_scores[row["policy_id"]] = (
                policy_scores.get(row["policy_id"], 0) + row["independent_accepted"]
            )
            validation_pairs[(row["policy_id"], row["task_id"])] = row[
                "independent_accepted"
            ]
    require(
        policy["validation_scores"] == policy_scores, "selected_policy_score_binding"
    )
    require(
        {
            (item["policy_id"], item["task_id"]): item["accepted"]
            for item in policy["validation"]
        }
        == validation_pairs,
        "selected_policy_verdict_binding",
    )
    if policy_scores["baseline"] == max(policy_scores.values()):
        require(
            policy["selected_policy"] == {"policy_id": "baseline", "suffix": ""}
            and policy["candidate_promoted"] is False,
            "tied_validation_must_keep_baseline",
        )
    return {
        "schema": VERSION,
        "verified": True,
        "verifier_sha256": sha(raw(__file__)),
        "report_sha256": sha(report_bytes),
        "private_protocol_sha256": protocol_sha256,
        "public_protocol_sha256": sha(public_bytes),
        "dataset_sha256": sha(dataset_bytes),
        "registry_file_sha256": sha(registry_bytes),
        "registry_canonical_sha256": registry_canonical,
        "frozen_sources": source_checks,
        "registered_sources": registered_checks,
        "sqlite_version": sqlite3.sqlite_version,
        "query_limits": SQL_LIMITS,
        "selected_jobs": 54,
        "attempted_jobs": len(results),
        "independent_accepted": accepted_count,
        "submitted_sql_reexecuted": final_count,
        "reference_sql_reexecuted": len(results),
        "abstentions": abstentions,
        "unexecuted_ungraded": 54 - len(results),
        "attempted_task_bodies_decoded": len(decoded),
        "unexecuted_task_bodies_decoded": 0,
        "validation_scores": validation_scores,
        "selected_policy_id": policy["selected_policy"]["policy_id"],
        "candidate_promoted": policy["candidate_promoted"],
        "outcomes": results,
        "model_calls": 0,
        "provider_billing_verified": False,
        "scope": "Independent finite source execution and artifact integrity corroboration. Partial primary course; no demonstrated learning, neural or general-intelligence advantage. No provider billing or usage reconciliation.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--protocol-sha256", required=True)
    parser.add_argument("--frozen-sources", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = replay(
            args.run, args.protocol, args.protocol_sha256, args.frozen_sources
        )
    except Exception as error:
        result = {
            "schema": VERSION,
            "verified": False,
            "failure_type": type(error).__name__,
            "failure_code": str(error)
            if isinstance(error, ReplayError)
            else "unexpected_exception",
            "model_calls": 0,
        }
    with args.output.open("xb") as stream:
        os.chmod(args.output, 0o600)
        stream.write(
            json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2).encode()
        )
        stream.flush()
        os.fsync(stream.fileno())
    print(
        json.dumps(
            {key: result[key] for key in ("verified", "model_calls")}, sort_keys=True
        )
    )
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
