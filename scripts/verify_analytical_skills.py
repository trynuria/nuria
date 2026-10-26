"""Independently replay finite generated-ledger report outputs.

The checker imports neither the skill executor nor the course generator. It
verifies retained input identities, executes each committed SQL using SQLite,
and computes the requested report independently in Python.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_file(root, name):
    file = (root / name).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ValueError("Artifact path leaves the retained course")
    return file


def receipt_identity(receipt):
    body = {key: value for key, value in receipt.items() if key != "result_sha256"}
    canonical = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def record_identity(record):
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()


def read_only_authorizer(action, first, second, database, trigger):
    del first, second, database, trigger
    return (
        sqlite3.SQLITE_OK
        if action
        in {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE,
        }
        else sqlite3.SQLITE_DENY
    )


def bounded_query(connection, sql, *, max_rows=200, max_bytes=65536):
    """Bound retained-query replay; incomplete work is never a valid answer."""
    started, steps = time.monotonic(), 0

    def progress():
        nonlocal steps
        steps += 100
        return int(steps >= 1000000 or time.monotonic() - started >= 2)

    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16384)
    connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
    connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    connection.set_progress_handler(progress, 100)
    rows, size = [], 0
    try:
        cursor = connection.execute(sql)
        while True:
            row = cursor.fetchone()
            if row is None:
                return rows
            size += len(
                json.dumps(list(row), ensure_ascii=False, allow_nan=False).encode()
            )
            if (
                len(rows) >= max_rows
                or size > max_bytes
                or time.monotonic() - started >= 2
            ):
                raise ValueError("Replay query exceeds its finite output or time bound")
            rows.append(list(row))
    except sqlite3.Error as error:
        raise ValueError("Replay query failed or exhausted its finite bound") from error
    finally:
        connection.set_progress_handler(None, 0)


def journal(directory, prefix):
    """Reconstruct a retained hash chain without using the runtime ledger code."""
    if prefix not in {"memory", "skill"}:
        raise ValueError("Unknown retained journal")
    file = local_file(
        directory, "memory.sqlite3" if prefix == "memory" else "skills.sqlite3"
    )
    connection = sqlite3.connect(
        "file:" + quote(str(file), safe="/") + "?mode=ro&immutable=1", uri=True
    )
    connection.enable_load_extension(False)
    connection.set_authorizer(read_only_authorizer)
    try:
        identity_rows = bounded_query(
            connection, f"SELECT id,payload FROM {prefix}_identity", max_rows=2
        )
        if len(identity_rows) != 1 or identity_rows[0][0] != 1:
            raise ValueError("Journal has no singular identity")
        raw_binding = identity_rows[0][1]
        binding = json.loads(raw_binding)
        if (
            json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False)
            != raw_binding
        ):
            raise ValueError("Journal binding is not canonical")
        binding_hash = record_identity(binding)
        columns = (
            "seq,previous_hash,hash,kind,"
            + ("episode_id," if prefix == "memory" else "")
            + "item_id,payload"
        )
        rows = bounded_query(
            connection,
            f"SELECT {columns} FROM {prefix}_events ORDER BY seq",
            max_rows=1024,
            max_bytes=2 * 1024 * 1024,
        )
    finally:
        connection.close()
    previous, exported, events = "0" * 64, [], []
    for index, row in enumerate(rows, 1):
        seq, parent, head, kind = row[:4]
        episode, item, raw = (
            (row[4], row[5], row[6]) if prefix == "memory" else (None, row[4], row[5])
        )
        body = json.loads(raw)
        canonical = json.dumps(
            body, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if (
            seq != index
            or parent != previous
            or canonical != raw
            or hashlib.sha256((parent + raw).encode()).hexdigest() != head
        ):
            raise ValueError("Retained journal chain changed")
        if (
            body["schema"] != binding["schema"]
            or body["binding_sha256"] != binding_hash
            or body["kind"] != kind
            or body["item_id"] != item
            or prefix == "memory"
            and body["episode_id"] != episode
        ):
            raise ValueError("Retained journal binding changed")
        events.append(body)
        exported.append({**body, "seq": seq, "previous_hash": parent, "hash": head})
        previous = head
    return binding, binding_hash, events, exported, previous


def verify_journal_links(directory, results):
    binding, binding_hash, memory, exported, head = journal(directory, "memory")
    retained = json.loads(local_file(directory, "memory-events.json").read_text())
    if (
        retained["records"] != exported
        or retained["events"] != len(memory)
        or retained["head"] != head
        or retained["binding"] != binding
        or retained["binding_sha256"] != binding_hash
        or retained["truncated"]
    ):
        raise ValueError("Memory export differs from its retained journal")
    if [event["kind"] for event in memory] != [
        "begin",
        "action",
        "proposal",
        "settlement",
    ]:
        raise ValueError("Origin memory course coverage changed")
    origin, promoted = results["origin"], results["origin"]["promoted"]
    begin, action, proposal, settlement = [event["record"] for event in memory]
    if any(begin.get(key) != value for key, value in binding.items()):
        raise ValueError("Origin record differs from its memory binding")
    episode = record_identity(begin)
    if (
        any(event["episode_id"] != episode for event in memory)
        or origin["episode_id"] != episode
    ):
        raise ValueError("Promoted episode differs from the retained origin")
    if record_identity(
        {k: v for k, v in proposal.items() if k != "proposal_sha256"}
    ) != proposal["proposal_sha256"] or proposal["trace_sha256"] != record_identity(
        [action]
    ):
        raise ValueError("Origin proposal differs from its own action trace")
    if (
        action["tool_name"] != "query"
        or action["ok"] is not True
        or action["sql"] != proposal["final_sql"]
        or action["result_sha256"] != origin["receipt"]["result_sha256"]
    ):
        raise ValueError("Origin SQL lacks its own successful execution")
    if (
        settlement != origin["outcome"]
        or settlement["accepted"] is not True
        or settlement["proposal_sha256"] != proposal["proposal_sha256"]
        or settlement["episode_id"] != episode
        or settlement["origin_sha256"] != episode
    ):
        raise ValueError("Origin lacks its retained accepted terminal")
    if (
        record_identity({k: v for k, v in settlement.items() if k != "receipt_sha256"})
        != settlement["receipt_sha256"]
    ):
        raise ValueError("Origin terminal identity changed")
    expected_origin = {
        "episode_id": episode,
        "memory_binding_sha256": binding_hash,
        "proposal_sha256": proposal["proposal_sha256"],
        "accepted_settlement": settlement,
        "execution_receipt": origin["receipt"],
    }
    if (
        promoted["origin"] != expected_origin
        or promoted["final_sql"] != proposal["final_sql"]
    ):
        raise ValueError("Promoted origin differs from retained accepted memory")
    if receipt_identity(origin["receipt"]) != origin["receipt"]["result_sha256"]:
        raise ValueError("Origin execution receipt identity changed")
    registry_path = local_file(directory, "origin/registry.json")
    registry = json.loads(registry_path.read_text())
    dataset = registry["databases"]["ledger"]["database"]
    database = local_file(directory, "origin/" + dataset["path"])
    if (
        sha(database) != dataset["sha256"]
        or promoted["source"]["registry_file_sha256"] != sha(registry_path)
        or origin["receipt"]["source"]["db_sha256"] != dataset["sha256"]
    ):
        raise ValueError("Origin data identity changed")
    rows = json.loads(local_file(directory, "origin/input.json").read_text())["rows"]
    connection = sqlite3.connect(
        "file:" + quote(str(database), safe="/") + "?mode=ro&immutable=1", uri=True
    )
    connection.enable_load_extension(False)
    connection.set_authorizer(read_only_authorizer)
    try:
        actual_rows = bounded_query(
            connection,
            "SELECT id,provider,delivered,utility,included,paid_micro_usd,semantic_accepted FROM task_outcomes",
        )
        output = bounded_query(connection, proposal["final_sql"])
        if (
            actual_rows != rows
            or output != truth(rows)
            or origin["receipt"]["rows"] != output
        ):
            raise ValueError("Accepted origin differs from independent SQL/data truth")
    finally:
        connection.close()
    skill_id = record_identity(
        {
            k: v
            for k, v in promoted.items()
            if k not in {"skill_id", "schema_inspection_receipt"}
        }
    )
    if promoted["skill_id"] != skill_id:
        raise ValueError("Promoted skill identity changed")
    skill_binding, _, skills, _, _ = journal(directory, "skill")
    if (
        skill_binding["source_id"] != binding["source_id"]
        or skill_binding["checker_id"] != settlement["checker_id"]
    ):
        raise ValueError("Skill and memory source/checker bindings differ")
    expected = [("skill", skill_id, promoted)]
    for record in results["records"]:
        execution, outcome = record["skill_execution"], record["skill_qualification"]
        reserve = {
            key: execution[key]
            for key in (
                "job_id",
                "skill_id",
                "report_id",
                "source",
                "query_sha256",
                "contract_sha256",
            )
        }
        expected.extend(
            [
                ("reserve", execution["job_id"], reserve),
                ("execution", execution["job_id"], execution),
                ("result", execution["job_id"], outcome),
            ]
        )
    if [
        (event["kind"], event["item_id"], event["record"]) for event in skills
    ] != expected:
        raise ValueError("Skill journal and course executions differ or omit events")
    return {"memory_events": len(memory), "skill_events": len(skills)}


def truth(rows):
    providers = set(row[1] for row in rows)
    answer = []
    for provider in sorted(providers, key=lambda x: (x is not None, x or "")):
        group = [row for row in rows if row[1] == provider]
        answer.append(
            [
                provider,
                len(group),
                len([row for row in group if row[2] == 1]),
                len([row for row in group if row[3] is not None and row[3] > 0]),
                len([row for row in group if row[3] is None]),
                len([row for row in group if row[4] == 1]),
                len([row for row in group if row[5] is not None and row[5] > 0]),
            ]
        )
    return answer


def verify(directory, *, expected_protocol_sha256=None):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema") != "nuria.analytical-skill-course.v1":
        raise ValueError("Unknown course schema")
    for name, expected in manifest["files"].items():
        file = local_file(directory, name)
        if sha(file) != expected:
            raise ValueError("Retained artifact identity changed")
    if (directory / "failure.json").exists():
        failure = json.loads(local_file(directory, "failure.json").read_text())
        selection = json.loads(local_file(directory, "selection.json").read_text())
        if (
            failure["selected_method_cases"] != 3 * len(selection["cases"])
            or failure["completed_method_cases"] + failure["uncompleted_method_cases"]
            != failure["selected_method_cases"]
        ):
            raise ValueError("Incomplete course accounting changed")
        raise ValueError(
            f"Incomplete course cannot pass: {failure['completed_method_cases']}/{failure['selected_method_cases']} method/case records completed"
        )
    results = json.loads((directory / "results.json").read_text())
    if (directory / "selection.json").exists():
        selection = json.loads(local_file(directory, "selection.json").read_text())
        if (
            selection["cases"] != [record["case"] for record in results["records"]]
            or selection["selected_method_cases"] != 3 * len(results["records"])
            or selection["methods"]
            != ["qualified-skill", "exact-query-cache", "conventional-template"]
        ):
            raise ValueError("Selected course coverage changed")
        for index, record in enumerate(results["records"]):
            if (
                json.loads(
                    local_file(
                        directory, f"completed-case-{index:02d}.json"
                    ).read_text()
                )
                != record
            ):
                raise ValueError("Durable completed-case evidence differs")
    elif results["phase"] == "challenge":
        raise ValueError("Challenge course has no durable pre-execution selection")
    if results["model_calls"] != 0 or results["model_completion_tokens"] != 0:
        raise ValueError("This finite fixture course does not execute a model")
    if (
        results["phase"] != manifest["phase"]
        or results["protocol_sha256"] != manifest["protocol_sha256"]
    ):
        raise ValueError("Course phase or prospective identity differs")
    if results["phase"] == "challenge":
        if (
            expected_protocol_sha256 is None
            or results["protocol_sha256"] != expected_protocol_sha256
        ):
            raise ValueError(
                "Challenge replay requires the external prospective protocol identity"
            )
        protocol = local_file(directory, "prospective-protocol.json")
        if sha(protocol) != results["protocol_sha256"]:
            raise ValueError("Prospective protocol bytes changed")
        if [record["case"] for record in results["records"]] != json.loads(
            protocol.read_text()
        )["cases"]:
            raise ValueError("Challenge course coverage changed")
    journal_counts = verify_journal_links(directory, results)
    counts = {
        "qualified-skill": {"accepted": 0, "wrong": 0},
        "exact-query-cache": {"accepted": 0, "wrong": 0},
        "conventional-template": {"accepted": 0, "wrong": 0},
    }
    queries = 0
    for record in results["records"]:
        registry_path = local_file(directory, record["registry"])
        registry = json.loads(registry_path.read_text())
        dataset = registry["databases"]["ledger"]["database"]
        database = (registry_path.parent / dataset["path"]).resolve()
        if not database.is_relative_to(directory) or sha(database) != dataset["sha256"]:
            raise ValueError("Fresh database identity changed")
        input_record = json.loads(local_file(directory, record["input"]).read_text())
        if {key: input_record[key] for key in ("seed", "variant")} != record["case"]:
            raise ValueError("Declared challenge identity differs from input")
        rows = input_record["rows"]
        expected = truth(rows)
        if expected != record["expected"]:
            raise ValueError("Published expected report differs from independent truth")
        execution, qualification = (
            record["skill_execution"],
            record["skill_qualification"],
        )
        if (
            record_identity(
                {k: v for k, v in execution.items() if k != "execution_sha256"}
            )
            != execution["execution_sha256"]
        ):
            raise ValueError("Skill execution content identity changed")
        if (
            record_identity(
                {k: v for k, v in qualification.items() if k != "result_sha256"}
            )
            != qualification["result_sha256"]
        ):
            raise ValueError("Qualification content identity changed")
        if any(
            qualification.get(k) != execution.get(k)
            for k in ("job_id", "skill_id", "source", "execution_sha256")
        ):
            raise ValueError("Qualification is not bound to its originating execution")
        if (
            execution["skill_id"] != results["origin"]["promoted"]["skill_id"]
            or qualification["checker_id"] != "independent-ledger-grouping-v1"
        ):
            raise ValueError("Skill origin or checker identity changed")
        if execution["source"]["registry_file_sha256"] != sha(registry_path):
            raise ValueError("Execution registry byte identity changed")
        connection = sqlite3.connect(
            "file:" + quote(str(database), safe="/") + "?mode=ro&immutable=1", uri=True
        )
        connection.enable_load_extension(False)
        connection.set_authorizer(read_only_authorizer)
        try:
            source_rows = bounded_query(
                connection,
                "SELECT id,provider,delivered,utility,included,paid_micro_usd,semantic_accepted FROM task_outcomes",
            )
            if source_rows != rows:
                raise ValueError("SQLite rows differ from retained generator input")
            executions = [
                (
                    "qualified-skill",
                    results["origin"]["promoted"]["final_sql"],
                    record["skill_execution"]["receipt"],
                    record["skill_qualification"]["accepted"],
                )
            ]
            executions.extend(
                (name, item["sql"], item["receipt"], item["accepted"])
                for name, item in record["controls"].items()
            )
            if {x[0] for x in executions} != set(counts):
                raise ValueError("A comparison arm is missing or duplicated")
            for name, sql, receipt, accepted in executions:
                if receipt_identity(receipt) != receipt["result_sha256"]:
                    raise ValueError("Execution receipt content hash changed")
                if not sql.lstrip().upper().startswith("SELECT "):
                    raise ValueError(
                        "Course replay supports only committed SELECT reports"
                    )
                request = {
                    "db_id": "ledger",
                    "tool": "query",
                    "arguments": {"sql": sql},
                    "limits": {
                        "vm_steps": 1000000,
                        "row_limit": 200,
                        "byte_limit": 65536,
                        "deadline_ms": 2000,
                    },
                }
                request_text = json.dumps(
                    request,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if (
                    hashlib.sha256(request_text.encode()).hexdigest()
                    != receipt["request_sha256"]
                ):
                    raise ValueError("Query request identity changed")
                observed = bounded_query(connection, sql)
                actual = (
                    receipt.get("complete") is True
                    and receipt.get("status") == "ok"
                    and len(receipt.get("columns", [])) == 7
                    and observed == expected
                    and receipt.get("rows") == observed
                )
                if type(accepted) is not bool or accepted != actual:
                    raise ValueError(
                        "Retained verdict differs from independent SQL and truth"
                    )
                if (
                    receipt["source"]["db_sha256"] != dataset["sha256"]
                    or receipt["source"]["dataset_revision"] != registry["revision"]
                ):
                    raise ValueError(
                        "Execution receipt is not bound to its fresh source"
                    )
                counts[name]["accepted" if actual else "wrong"] += 1
                queries += 1
        finally:
            connection.close()
    return {
        "schema": "nuria.analytical-skill-course.verification.v1",
        "passed": True,
        "phase": results["phase"],
        "cases": len(results["records"]),
        "queries_replayed": queries,
        "origin_queries_replayed": 1,
        **journal_counts,
        "counts": counts,
        "model_calls": 0,
        "scope": "Retained input/hash, direct SQLite and independent Python report checks; no model intelligence or neural-gain test.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--protocol-sha256")
    args = parser.parse_args()
    result = verify(args.directory, expected_protocol_sha256=args.protocol_sha256)
    if args.output:
        if args.output.exists():
            raise ValueError("Verification output already exists")
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
