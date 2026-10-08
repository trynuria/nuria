"""Bounded local-ledger capacity probe; never touches production balances."""

import argparse
import json
import platform
import resource
import time
from pathlib import Path

from commerce.evidence import export_pages, verify_pages
from commerce.ledger import Ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=100000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.events <= 1000000:
        raise ValueError("Capacity probe must be bounded")
    args.output.mkdir(parents=True, exist_ok=False)
    ledger = Ledger(args.output / "fixture.sqlite3")
    started = time.perf_counter()
    for n in range(args.events):
        ledger.record(
            {
                "state": "capacity_fixture",
                "transaction": str(n),
                "amount_micro_usdc": 1,
                "at": n,
                "scope": "Synthetic record; no transfer or signing",
            }
        )
    written = time.perf_counter()
    index = export_pages(ledger, args.output)
    published = time.perf_counter()
    verification = verify_pages(
        index,
        (
            json.loads((args.output / f"ledger-{n}.json").read_text())
            for n in range(index["pages"])
        ),
    )
    finished = time.perf_counter()
    result = {
        "schema": "nuria.finance.capacity.v1",
        "system": platform.system(),
        "events": args.events,
        "write_seconds": written - started,
        "events_per_second": args.events / (written - started),
        "export_seconds": published - written,
        "verify_seconds": finished - published,
        "pages": index["pages"],
        "database_bytes": (args.output / "fixture.sqlite3").stat().st_size,
        "peak_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "verification": verification,
        "scope": "Unpaced synthetic SQLite FULL/WAL event writes, cache export and hash verification. No RPC load, transactions, merchant calls, signatures or 24-hour soak.",
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    ledger.db.close()


if __name__ == "__main__":
    main()
