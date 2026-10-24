"""Export a PostgreSQL snapshot and its matching neural checkpoint without pausing the model during the dump."""

import fcntl
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import psycopg


def require_readable_relations(db) -> None:
    unreadable = db.execute(
        """SELECT format('%I.%I', n.nspname, c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','m','S')
          AND (NOT has_schema_privilege(n.oid, 'USAGE')
               OR CASE WHEN c.relkind = 'S'
                    THEN NOT has_sequence_privilege(c.oid, 'SELECT')
                    ELSE NOT has_table_privilege(c.oid, 'SELECT') END)
        ORDER BY n.nspname,c.relname"""
    ).fetchall()
    if unreadable:
        names = ", ".join(row[0] for row in unreadable)
        raise RuntimeError(
            f"PostgreSQL backup role cannot read: {names}. "
            "Restore the required read privileges before retrying the backup."
        )


def snapshot(
    stage: Path, root: Path = Path("/var/lib/nuria/history/continuous")
) -> None:
    # A failed or previous run remains intact instead of becoming the next run's stage.
    stage.mkdir(mode=0o700, parents=True, exist_ok=False)
    with psycopg.connect("dbname=nuria user=nuria_engine") as db:
        db.execute("SET TRANSACTION READ ONLY")
        require_readable_relations(db)
        # Catalog reads must not establish the dump's repeatable-read snapshot.
        # Start that transaction afresh, and make its first SELECT under the lock.
        db.commit()
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        with (root / "checkpoint.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            exported = db.execute("SELECT pg_export_snapshot()").fetchone()[0]
            head = db.execute(
                "SELECT seq,hash FROM receipts ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            shutil.copyfile(root / "resume.zip", stage / "resume.zip")
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
                "--snapshot=" + exported,
                "-f",
                str(stage / "database.dump"),
            ],
            check=True,
            timeout=600,
        )


if __name__ == "__main__":
    snapshot(Path(sys.argv[1]))
