"""Bounded fixture storage/projection benchmark; never sends a job or payment."""

import argparse
import hashlib
import json
import platform
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commerce.commissioning import Commissioner  # noqa: E402
from commerce.ledger import Ledger, canonical  # noqa: E402


def run(rows):
    if not 40 <= rows <= 100000:
        raise ValueError("Benchmark size must be 40–100000 rows")
    directory = Path(".test-state") / ("commission-capacity-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    ledger = Ledger(directory / "journal.sqlite3")
    controller = Commissioner(ledger)
    now = time.time() - rows
    goal = {
        "title": "Fixture storage benchmark",
        "purpose": "Measure bounded projection only",
        "action": "experiment",
        "evaluation": {"dataset_sha256": "a" * 64},
    }
    started = time.perf_counter()
    ledger.db.execute("BEGIN IMMEDIATE")
    for index in range(rows):
        ident = hashlib.sha256(str(index).encode()).hexdigest()
        ledger.db.execute(
            "INSERT INTO commissions(id,goal,kind,decision_hash,created,updated,state,contract) VALUES(?,?,?,?,?,?,'proposed',?)",
            (
                ident,
                "fixture",
                "agent",
                "b" * 64,
                now + index,
                now + index,
                canonical(goal),
            ),
        )
        ledger.event(
            {
                "commission_id": ident,
                "commission_state": "proposed",
                "contract_sha256": hashlib.sha256(canonical(goal).encode()).hexdigest(),
                "scope": "fixture storage benchmark",
            }
        )
    ledger.db.execute("COMMIT")
    insertion = time.perf_counter() - started
    timings = []
    for _ in range(25):
        started = time.perf_counter()
        projection = controller.public()
        summary = controller.summary()
        timings.append((time.perf_counter() - started) * 1000)
    started = time.perf_counter()
    integrity = ledger.verify()
    audit = time.perf_counter() - started
    ledger.db.close()
    return {
        "rows": rows,
        "public_rows": len(projection),
        "total": summary["total"],
        "fixture_insertion_seconds": insertion,
        "projection_p50_ms": sorted(timings)[12],
        "projection_p95_ms": sorted(timings)[23],
        "full_audit_seconds": audit,
        "integrity": integrity["valid"],
        "projection_bytes": len(canonical(projection).encode()),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "scope": "Local fixture SQLite storage/projection, not marketplace, RPC or sustained trade-ingestion throughput",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    args = parser.parse_args()
    print(json.dumps(run(args.rows), indent=2))
