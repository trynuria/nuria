"""Consistent private commerce ledger snapshots; wallet keys are excluded."""

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from commerce.ledger import Ledger


def snapshot(source, destination, *, progress=None):
    destination = Path(destination)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as reader:
        if reader.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise RuntimeError(
                "Online commerce backup requires WAL mode; source settings were not changed"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(destination)) as writer:
            reader.execute("BEGIN")
            try:
                # Keep the original WAL view while unrelated ledger writers commit.
                reader.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
                reader.backup(writer, pages=256, progress=progress)
            finally:
                reader.rollback()
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
