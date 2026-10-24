"""Online SQLite backup; checkpoint and cognitive records share one transaction."""

import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def snapshot(source: Path, destination: Path, *, progress=None) -> dict:
    with closing(
        sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=15)
    ) as reader:
        if reader.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise RuntimeError(
                "Online cognitive backup requires WAL mode; source settings were not changed"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(destination, timeout=15)) as writer:
            reader.execute("BEGIN")
            try:
                # Pin one WAL view before stepping: independent commits must not
                # repeatedly restart the copy or change its checkpoint halfway.
                reader.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
                reader.backup(writer, pages=256, progress=progress)
            finally:
                # Release the WAL pin before destination validation scans.
                reader.rollback()
            row = writer.execute(
                "SELECT metadata FROM checkpoint WHERE id=1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Cognitive backup has no checkpoint")
            metadata = json.loads(row[0])
            head = writer.execute(
                "SELECT seq,hash FROM records ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if (head or (0, "0" * 64)) != (
                metadata["record_seq"],
                metadata["record_head"],
            ):
                raise RuntimeError("Cognitive backup checkpoint and journal disagree")
            if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Cognitive backup failed SQLite integrity check")
    destination.chmod(0o600)
    return {
        "tick": metadata["tick"],
        "cursor": metadata["cursor"],
        "head": metadata["record_head"],
        "bytes": destination.stat().st_size,
    }


if __name__ == "__main__":
    source = (
        Path(os.environ.get("NURIA_COGNITION_DATA", "/var/lib/nuria/cognition"))
        / "cognition.sqlite3"
    )
    print(json.dumps(snapshot(source, Path(sys.argv[1]))))
