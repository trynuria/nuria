"""Exact offline SQLite to PostgreSQL history import; never resets nonempty data."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import psycopg

source = Path(sys.argv[1])
legacy = sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True)
with psycopg.connect("dbname=nuria user=nuria_engine") as dest:
    if dest.execute("SELECT count(*) FROM receipts").fetchone()[0]:
        raise SystemExit("Destination is nonempty; import refused")
    counts = {}
    tables = {
        "inputs": ["id", "kind", "payload", "created_utc", "receipt", "event_order"],
        "receipts": ["seq", "previous_hash", "hash", "payload"],
        "spike_windows": ["seq", "data"],
        "source_cursors": ["address", "signature"],
        "scan_progress": ["address", "newest", "before_signature"],
        "transactions": [
            "signature",
            "slot",
            "status",
            "detail",
            "attempts",
            "event_order",
        ],
    }
    for table, columns in tables.items():
        select = ",".join("rowid" if c == "event_order" else c for c in columns)
        query = "SELECT " + select + " FROM " + table
        count = 0
        with dest.cursor().copy(
            "COPY " + table + "(" + ",".join(columns) + ") FROM STDIN"
        ) as copy:
            for row in legacy.execute(query):
                copy.write_row(row)
                count += 1
        counts[table] = count
        if dest.execute("SELECT count(*) FROM " + table).fetchone()[0] != count:
            raise RuntimeError("Import count mismatch")
    for table in ("inputs", "transactions"):
        dest.execute(
            "SELECT setval(pg_get_serial_sequence(%s,'event_order'),coalesce((SELECT max(event_order) FROM "
            + table
            + "),1),EXISTS(SELECT 1 FROM "
            + table
            + "))",
            (table,),
        )
    head = dest.execute(
        "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
    ).fetchone()
print(
    json.dumps(
        {
            "counts": counts,
            "head": head,
            "sqlite_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
        indent=2,
    )
)
