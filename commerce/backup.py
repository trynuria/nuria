"""Consistent private commerce ledger snapshots; wallet keys are excluded."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from commerce.ledger import Ledger


def snapshot(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
            if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Commerce backup failed SQLite integrity check")
    destination.chmod(0o600)
    ledger = Ledger(destination)
    try:
        return ledger.verify()
    finally:
        ledger.db.close()


if __name__ == "__main__":
    print(snapshot(Path("/var/lib/nuria/commerce/commerce.sqlite3"), Path(sys.argv[1])))
