"""Online SQLite backup; checkpoint and cognitive records share one transaction."""

import json
import os
import sqlite3
import sys
from pathlib import Path


def snapshot(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=15) as reader:
        with sqlite3.connect(destination, timeout=15) as writer:
            reader.backup(writer, pages=256)
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
