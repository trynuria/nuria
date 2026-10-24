"""Pinned WAL copies stay finite while another connection commits real changes."""

import json
import sqlite3
import stat
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from cognition.backup import snapshot as cognitive_snapshot
from cognition.journal import Journal
from commerce.backup import snapshot as commerce_snapshot
from commerce.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]


class SQLiteBackupSnapshotTests(unittest.TestCase):
    def directory(self):
        directory = ROOT / ".test-state" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        return directory

    def pad(self, db):
        db.execute("CREATE TABLE sensory(value INTEGER NOT NULL)")
        db.execute("INSERT INTO sensory VALUES(0)")
        db.execute("CREATE TABLE padding(payload BLOB NOT NULL)")
        db.executemany(
            "INSERT INTO padding VALUES(?)", [(b"x" * 4096,) for _ in range(600)]
        )
        db.commit()
        self.assertGreater(db.execute("PRAGMA page_count").fetchone()[0], 512)

    def assert_progress(self, observed):
        self.assertGreaterEqual(len(observed), 3)
        self.assertEqual(observed[-1][0], sqlite3.SQLITE_DONE)
        self.assertEqual(observed[-1][1], 0)
        # Every step advances despite a separate committed write between steps.
        self.assertTrue(
            all(after[1] < before[1] for before, after in zip(observed, observed[1:]))
        )
        self.assertEqual(len({item[2] for item in observed}), 1)

    def test_cognitive_wal_copy_keeps_original_checkpoint_during_commits(self):
        directory = self.directory()
        journal = Journal(directory / "live")
        self.addCleanup(journal.db.close)
        self.pad(journal.db)

        def save(value):
            journal.db.execute("UPDATE sensory SET value=?", (value,))
            journal.append("observation", {"value": value})
            journal.save(
                {
                    "tick": value,
                    "cursor": value,
                    "record_seq": journal.seq,
                    "record_head": journal.head,
                },
                b"fixture network",
            )

        save(0)
        original_head = journal.head
        observed = []
        committed = 0

        def progress(status, remaining, total):
            nonlocal committed
            observed.append((status, remaining, total))
            if remaining:
                committed += 1
                save(committed)

        destination = directory / "snapshot.sqlite3"
        result = cognitive_snapshot(
            directory / "live" / "cognition.sqlite3", destination, progress=progress
        )
        self.assert_progress(observed)
        self.assertGreaterEqual(committed, 2)
        self.assertEqual(result["head"], original_head)
        self.assertEqual(result["tick"], 0)
        with closing(sqlite3.connect(destination)) as copied:
            metadata = json.loads(
                copied.execute("SELECT metadata FROM checkpoint WHERE id=1").fetchone()[
                    0
                ]
            )
            self.assertEqual(metadata["record_seq"], 1)
            self.assertEqual(metadata["record_head"], original_head)
            self.assertEqual(
                copied.execute("SELECT count(*) FROM records").fetchone()[0], 1
            )
            self.assertEqual(
                copied.execute("SELECT value FROM sensory").fetchone()[0], 0
            )
        self.assertEqual(journal.seq, committed + 1)
        self.assertEqual(
            journal.db.execute("SELECT value FROM sensory").fetchone()[0], committed
        )
        self.assertEqual(
            journal.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0
        )
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_commerce_wal_copy_keeps_original_ledger_during_commits(self):
        directory = self.directory()
        source = directory / "live.sqlite3"
        ledger = Ledger(source)
        self.addCleanup(ledger.db.close)
        self.pad(ledger.db)
        ledger.record({"value": 0})
        original_head = ledger.verify()["head"]
        observed = []
        committed = 0

        def progress(status, remaining, total):
            nonlocal committed
            observed.append((status, remaining, total))
            if remaining:
                committed += 1
                ledger.db.execute("BEGIN IMMEDIATE")
                ledger.db.execute("UPDATE sensory SET value=?", (committed,))
                ledger.event({"value": committed})
                ledger.db.execute("COMMIT")

        destination = directory / "snapshot.sqlite3"
        result = commerce_snapshot(source, destination, progress=progress)
        self.assert_progress(observed)
        self.assertGreaterEqual(committed, 2)
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["head"], original_head)
        self.assertEqual(ledger.verify()["events"], committed + 1)
        with closing(sqlite3.connect(destination)) as copied:
            self.assertEqual(
                copied.execute("SELECT value FROM sensory").fetchone()[0], 0
            )
        self.assertEqual(
            ledger.db.execute("SELECT value FROM sensory").fetchone()[0], committed
        )
        self.assertEqual(
            ledger.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0
        )
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_non_wal_sources_fail_without_changing_mode_or_creating_copy(self):
        for snapshot in (cognitive_snapshot, commerce_snapshot):
            with self.subTest(snapshot=snapshot.__module__):
                directory = self.directory()
                source = directory / "source.sqlite3"
                destination = directory / "snapshot.sqlite3"
                with closing(sqlite3.connect(source)) as writer:
                    writer.execute("CREATE TABLE value(item INTEGER)")
                    writer.execute("INSERT INTO value VALUES(1)")
                    writer.commit()
                    self.assertEqual(
                        writer.execute("PRAGMA journal_mode").fetchone()[0], "delete"
                    )
                    with self.assertRaisesRegex(RuntimeError, "requires WAL mode"):
                        snapshot(source, destination)
                    self.assertFalse(destination.exists())
                    self.assertEqual(
                        writer.execute("PRAGMA journal_mode").fetchone()[0], "delete"
                    )
                    # The rejected backup leaves no reader blocking this commit.
                    writer.execute("UPDATE value SET item=2")
                    writer.commit()
                    self.assertEqual(
                        writer.execute("SELECT item FROM value").fetchone()[0], 2
                    )

    def test_callback_failure_releases_pin_and_preserves_partial_copy(self):
        for snapshot in (cognitive_snapshot, commerce_snapshot):
            with self.subTest(snapshot=snapshot.__module__):
                directory = self.directory()
                source = directory / "source.sqlite3"
                destination = directory / "snapshot.sqlite3"
                with closing(sqlite3.connect(source)) as writer:
                    writer.execute("PRAGMA journal_mode=WAL")
                    self.pad(writer)

                    def fail(status, remaining, total):
                        writer.execute("UPDATE sensory SET value=1")
                        writer.commit()
                        raise RuntimeError("fixture callback failure")

                    with self.assertRaisesRegex(
                        RuntimeError, "fixture callback failure"
                    ):
                        snapshot(source, destination, progress=fail)
                    self.assertTrue(destination.exists())
                    self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                    self.assertEqual(
                        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0],
                        0,
                    )

    def test_existing_destinations_are_preserved(self):
        for snapshot in (cognitive_snapshot, commerce_snapshot):
            with self.subTest(snapshot=snapshot.__module__):
                directory = self.directory()
                source = directory / "source.sqlite3"
                destination = directory / "snapshot.sqlite3"
                destination.write_bytes(b"retained prior evidence")
                with closing(sqlite3.connect(source)) as writer:
                    writer.execute("PRAGMA journal_mode=WAL")
                    writer.execute("CREATE TABLE value(item INTEGER)")
                    writer.commit()
                    with self.assertRaises(FileExistsError):
                        snapshot(source, destination)
                self.assertEqual(destination.read_bytes(), b"retained prior evidence")


if __name__ == "__main__":
    unittest.main()
