"""Create an immutable analytical projection of a retained public work response.

This command opens no network connection. Hashes identify the supplied snapshot;
they do not authenticate the server or establish payment settlement independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import SCHEMA

MAX_INPUT_BYTES = 1024 * 1024


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(value, maximum=500):
    if type(value) is not str or not value or len(value.encode()) > maximum:
        raise ValueError("Work snapshot text is missing or oversized")
    return value


def nullable_bool(value):
    if value is not None and type(value) is not bool:
        raise ValueError("Work snapshot booleans must preserve unknown values")
    return None if value is None else int(value)


def number(value, *, integer=False):
    allowed = (int,) if integer else (int, float)
    if value is not None and (type(value) not in allowed or not math.isfinite(value)):
        raise ValueError("Work snapshot numbers must be finite")
    return value


def mapping(value):
    if value is None:
        return {}
    if type(value) is not dict:
        raise ValueError("Work snapshot evidence must be an object or unknown")
    return value


def optional_text(value):
    return None if value is None else text(value, 2000)


def register(snapshot, output):
    snapshot, output = Path(snapshot), Path(output)
    if not snapshot.is_file() or snapshot.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Retained public work snapshot is missing or oversized")
    raw = snapshot.read_bytes()
    value = json.loads(raw)
    if type(value) is not dict or value.get("schema") != "nuria.work.v1":
        raise ValueError("Expected a retained Nuria work response")
    rows = value.get("action_tasks")
    if type(rows) is not list or len(rows) > 80:
        raise ValueError("Recent task coverage must be bounded")
    records, seen = [], set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError("Work task is malformed")
        ident = text(row.get("id"), 128)
        if ident in seen:
            raise ValueError("Duplicate task identity in retained snapshot")
        seen.add(ident)
        acceptance = mapping(row.get("acceptance"))
        settlement = mapping(row.get("settlement"))
        outcome = mapping(row.get("outcome"))
        records.append(
            (
                ident,
                text(row.get("provider"), 128),
                text(row.get("state"), 64),
                text(row.get("kind"), 64),
                nullable_bool(acceptance.get("passed")),
                nullable_bool(acceptance.get("semantic_accepted")),
                optional_text(acceptance.get("metric")),
                number(acceptance.get("utility")),
                nullable_bool(settlement.get("verified")),
                optional_text(settlement.get("basis")),
                number(settlement.get("total_micro_usd"), integer=True),
                optional_text(outcome.get("evidence_level")),
                optional_text(outcome.get("metric")),
                number(outcome.get("utility")),
                number(outcome.get("reward")),
            )
        )
    updated = optional_text(value.get("updated_utc"))
    source_scope = optional_text(value.get("scope"))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    retained = output / "work-response.json"
    with retained.open("xb") as stream:
        os.chmod(retained, 0o600)
        stream.write(raw)
    database = output / "work.sqlite"
    with closing(sqlite3.connect(database)) as connection, connection:
        os.chmod(database, 0o600)
        connection.execute("""CREATE TABLE task_outcomes(
            task_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
            state TEXT NOT NULL, kind TEXT NOT NULL,
            delivery_passed INTEGER, semantic_accepted INTEGER,
            delivery_metric TEXT, delivery_utility REAL,
            settlement_verified INTEGER, settlement_basis TEXT,
            settled_micro_usd INTEGER, outcome_evidence_level TEXT,
            outcome_metric TEXT, outcome_utility REAL, neural_reward REAL)""")
        connection.executemany(
            "INSERT INTO task_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", records
        )
        connection.execute(
            "CREATE TABLE snapshot_context(key TEXT PRIMARY KEY,value TEXT)"
        )
        context = {
            "snapshot_sha256": sha(retained),
            "source_updated_utc": updated,
            "source_scope": source_scope,
            "rows_in_projection": str(len(records)),
            "coverage": "Only action_tasks present in this retained recent response; "
            "not lifetime provider performance, complete payments or total market activity",
            "unknowns": "NULL is unknown or absent; never replace it with zero or failure",
            "payment": "included_capacity means hosting computation and is not an external purchase. "
            "A server-reported verified flag is not independent chain verification.",
        }
        connection.executemany(
            "INSERT INTO snapshot_context VALUES(?,?)", context.items()
        )
        schema = "\n".join(
            sql
            for (sql,) in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        )
    documents = {}
    supplied = {
        "schema": schema,
        "business": "Analyze only the recent task rows in this immutable snapshot. "
        "Delivery, semantic acceptance, settlement and measured outcomes are distinct. "
        "Missing flags remain NULL. Read snapshot_context before making coverage or payment claims.",
        "columns": "task_outcomes stores one row per task. delivery_passed is the existing "
        "acceptance.passed flag; it is not inferred semantic correctness. semantic_accepted "
        "is populated only when explicitly supplied. settled_micro_usd is an integer amount "
        "in millionths of USD. Zero included_capacity is not a paid purchase. "
        "outcome_utility and neural_reward describe recorded scoped feedback, not general intelligence.",
    }
    for section, content in supplied.items():
        path = output / (section + ".txt")
        path.write_text(content + "\n")
        os.chmod(path, 0o600)
        documents[section] = {"path": path.name, "sha256": sha(path)}
    manifest = {
        "schema": SCHEMA,
        "revision": "sha256:" + sha(retained),
        "databases": {
            "work": {
                "database": {"path": database.name, "sha256": sha(database)},
                "documents": documents,
            }
        },
    }
    registry = output / "registry.json"
    registry.write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(registry, 0o600)
    return {
        "registry_sha256": sha(registry),
        "source_sha256": sha(retained),
        "database_sha256": sha(database),
        "rows": len(records),
        "coverage": context["coverage"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(register(args.snapshot, args.output)))


if __name__ == "__main__":
    main()
