"""Export a PostgreSQL snapshot and its matching neural checkpoint without pausing the model during the dump."""

import fcntl
import json
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg

root = Path("/var/lib/nuria/history/continuous")
stage = Path(sys.argv[1])
stage.mkdir(mode=0o700, parents=True, exist_ok=True)
with psycopg.connect("dbname=nuria user=nuria_engine") as db:
    db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    with (root / "checkpoint.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        snapshot = db.execute("SELECT pg_export_snapshot()").fetchone()[0]
        head = db.execute(
            "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        shutil.copyfile(root / "resume.zip", stage / "resume.zip")
        import zipfile

        with zipfile.ZipFile(stage / "resume.zip") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        if not head or manifest["tick"] != head[0]:
            raise RuntimeError("Checkpoint and database head differ")
        (stage / "manifest.json").write_text(
            json.dumps(
                {
                    "head": head,
                    "tick": manifest["tick"],
                    "genesis_utc": manifest["genesis_utc"],
                },
                indent=2,
            )
        )
        fcntl.flock(lock, fcntl.LOCK_UN)
    subprocess.run(
        [
            "pg_dump",
            "-U",
            "nuria_engine",
            "-d",
            "nuria",
            "-Fc",
            "--snapshot=" + snapshot,
            "-f",
            str(stage / "database.dump"),
        ],
        check=True,
        timeout=300,
    )
