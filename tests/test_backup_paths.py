"""Backup runs retain prior snapshots and fail before copying unreadable state."""

import fcntl
import hashlib
import io
import json
import sqlite3
import types
import unittest
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import backup
import backup_snapshot

ROOT = Path(__file__).resolve().parents[1]


class BackupPathsTests(unittest.TestCase):
    def directory(self):
        directory = ROOT / ".test-state" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        return directory

    def test_same_clock_produces_distinct_paths_for_every_snapshot(self):
        directory = self.directory()
        now = datetime(2026, 10, 24, 1, 2, 3, tzinfo=timezone.utc)
        first = backup.new_backup_paths(directory, now)
        second = backup.new_backup_paths(directory, now)
        self.assertTrue(first.run_id.startswith("20261024T010203000000Z-"))
        for attribute in (
            "stage",
            "cognition",
            "discovery",
            "commerce",
            "sealed",
            "receipt",
        ):
            self.assertNotEqual(getattr(first, attribute), getattr(second, attribute))

    def test_existing_snapshot_path_is_rejected_without_mutation(self):
        paths = backup.new_backup_paths(self.directory())
        paths.commerce.parent.mkdir(parents=True)
        paths.commerce.write_bytes(b"previous snapshot")
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            paths.require_unused()
        self.assertEqual(paths.commerce.read_bytes(), b"previous snapshot")

    def test_capacity_limit_stops_before_overwriting_retained_archives(self):
        directory = self.directory()
        retained = directory / "prior.nuria.enc"
        retained.write_bytes(b"retained archive")
        with (
            patch.dict(
                "os.environ",
                {
                    "NURIA_BACKUP_MAX_LOCAL_BYTES": "10",
                    "NURIA_BACKUP_MIN_FREE_BYTES": "10",
                },
            ),
            patch.object(backup.shutil, "disk_usage", return_value=Mock(free=100)),
        ):
            with self.assertRaisesRegex(SystemExit, "retained-local capacity"):
                backup.require_capacity(directory)
        self.assertEqual(retained.read_bytes(), b"retained archive")

    def test_free_space_reserve_and_invalid_limits_fail_closed(self):
        directory = self.directory()
        with (
            patch.dict(
                "os.environ",
                {
                    "NURIA_BACKUP_MAX_LOCAL_BYTES": "100",
                    "NURIA_BACKUP_MIN_FREE_BYTES": "10",
                },
            ),
            patch.object(backup.shutil, "disk_usage", return_value=Mock(free=9)),
        ):
            with self.assertRaisesRegex(SystemExit, "free-space reserve"):
                backup.require_capacity(directory)
        for value in ("0", "-1", "unlimited"):
            with (
                self.subTest(value=value),
                patch.dict("os.environ", {"NURIA_BACKUP_MAX_LOCAL_BYTES": value}),
            ):
                with self.assertRaisesRegex(SystemExit, "positive byte count"):
                    backup.require_capacity(directory)

    def test_default_capacity_records_finite_limits_and_initial_usage(self):
        directory = self.directory()
        (directory / "prior.nuria.enc").write_bytes(b"prior archive")
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                backup.shutil, "disk_usage", return_value=Mock(free=64 * 1024**3)
            ),
        ):
            observed = backup.require_capacity(directory)
        self.assertEqual(observed["max_retained_local_bytes"], 32 * 1024**3)
        self.assertEqual(observed["minimum_free_bytes"], 16 * 1024**3)
        self.assertEqual(
            observed["retained_local_bytes_at_start"], len(b"prior archive")
        )
        self.assertEqual(observed["free_bytes_at_start"], 64 * 1024**3)

    def test_successive_runs_archive_fresh_paths_and_keep_prior_snapshots(self):
        directory = self.directory()
        first = backup.new_backup_paths(directory)
        second = backup.new_backup_paths(directory)
        legacy = [directory / "history" / "backup-staging" / "current" / "saved"]
        legacy += [
            directory / component / "backup" / "current.sqlite3"
            for component in ("cognition", "discovery", "commerce")
        ]
        for path in legacy:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"retained earlier evidence")

        archived = []
        objects = {}
        client = Mock()
        client.list_objects_v2.return_value = {}
        client.upload_file.side_effect = lambda source, bucket, key, **kwargs: (
            objects.__setitem__(key, Path(source).read_bytes())
        )
        client.get_object.side_effect = lambda **kwargs: {
            "Body": io.BytesIO(objects[kwargs["Key"]])
        }

        def seal(paths, recipient, destination):
            archived.append(paths)
            for path, _ in paths:
                if path.is_file() and path.suffix == ".sqlite3":
                    self.assertEqual(path.read_bytes()[:16], b"SQLite format 3\0")
            destination.write_bytes(b"encrypted fixture")
            return {
                "encrypted_bytes": destination.stat().st_size,
                "cipher_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }

        def run(command, **kwargs):
            destination = Path(command[-1])
            if command[-2].endswith("backup_snapshot.py"):
                self.assertEqual(command[-2], str(ROOT / "backup_snapshot.py"))
                destination.mkdir(parents=True)
                (destination / "manifest.json").write_text(
                    json.dumps({"head": [1, "database-head"]})
                )
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(destination)) as db:
                    db.execute("CREATE TABLE checkpoint(id INTEGER, metadata TEXT)")
                    db.execute(
                        "INSERT INTO checkpoint VALUES(1,?)",
                        (json.dumps({"record_head": "cognitive-head", "tick": 7}),),
                    )
                    db.commit()

        read_text = Path.read_text
        read_bytes = Path.read_bytes
        exists = Path.exists

        def text(path, *args, **kwargs):
            if path == Path("/etc/nuria/spaces-credentials.json"):
                return json.dumps({"access_key": "fixture", "secret_key": "fixture"})
            return read_text(path, *args, **kwargs)

        def binary(path, *args, **kwargs):
            if path == Path("/etc/nuria/backup-recipient.pem"):
                return b"fixture recipient"
            return read_bytes(path, *args, **kwargs)

        def present(path):
            if path in (
                Path("/var/lib/nuria/discovery/cognition.sqlite3"),
                Path("/var/lib/nuria/commerce/commerce.sqlite3"),
            ):
                return True
            return exists(path)

        with (
            patch.dict(
                "sys.modules",
                {"boto3": types.SimpleNamespace(client=lambda *a, **k: client)},
            ),
            patch.dict("os.environ", {"NURIA_BACKUP_BUCKET": "fixture"}),
            patch.object(backup, "new_backup_paths", side_effect=[first, second]),
            patch.object(
                backup.shutil, "disk_usage", return_value=Mock(free=16 * 1024**3)
            ),
            patch.object(backup.subprocess, "run", side_effect=run),
            patch.object(
                backup.serialization, "load_pem_public_key", return_value=object()
            ),
            patch.object(backup, "seal_archive", side_effect=seal),
            patch.object(Path, "read_text", text),
            patch.object(Path, "read_bytes", binary),
            patch.object(Path, "exists", present),
            patch("builtins.print"),
        ):
            backup.main()
            retained = {
                path: path.read_bytes()
                for path in (
                    first.cognition,
                    first.discovery,
                    first.commerce,
                    first.receipt,
                )
            }
            backup.main()

        for path in legacy:
            self.assertEqual(path.read_bytes(), b"retained earlier evidence")
        for path, original in retained.items():
            self.assertEqual(path.read_bytes(), original)
        for run_paths, archive in zip((first, second), archived, strict=True):
            self.assertIn((run_paths.stage, "snapshot"), archive)
            self.assertIn((run_paths.cognition, "cognition/cognition.sqlite3"), archive)
            self.assertIn((run_paths.discovery, "discovery/cognition.sqlite3"), archive)
            self.assertIn((run_paths.commerce, "commerce/commerce.sqlite3"), archive)
            self.assertTrue(run_paths.receipt.exists())
        latest = json.loads((directory / "backups" / "latest.json").read_text())
        self.assertEqual(latest["object_key"], "application/" + second.sealed.name)

    def test_snapshot_rejects_old_stage_before_opening_database(self):
        stage = self.directory()
        evidence = stage / "manifest.json"
        evidence.write_text("retained checkpoint")
        with patch.object(backup_snapshot.psycopg, "connect") as connect:
            with self.assertRaises(FileExistsError):
                backup_snapshot.snapshot(stage)
        connect.assert_not_called()
        self.assertEqual(evidence.read_text(), "retained checkpoint")

    def test_unreadable_table_fails_before_checkpoint_lock_or_dump(self):
        directory = self.directory()
        db = Mock()
        db.execute.return_value.fetchall.return_value = [
            ("public.token_totals",),
            ("public.token_transactions",),
        ]
        connection = Mock()
        connection.__enter__ = Mock(return_value=db)
        connection.__exit__ = Mock(return_value=False)
        with (
            patch.object(backup_snapshot.psycopg, "connect", return_value=connection),
            patch.object(backup_snapshot.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "cannot read: public.token_totals, public.token_transactions",
            ):
                backup_snapshot.snapshot(directory / "new-stage", directory / "absent")
        run.assert_not_called()
        db.commit.assert_not_called()
        self.assertFalse((directory / "absent").exists())

    def test_dump_uses_exported_transaction_after_releasing_checkpoint_lock(self):
        directory = self.directory()
        root = directory / "continuous"
        root.mkdir()
        stage = directory / "new-stage"
        with zipfile.ZipFile(root / "resume.zip", "w") as archive:
            archive.writestr(
                "manifest.json", json.dumps({"tick": 8, "genesis_utc": "fixture"})
            )
        active = False
        db = Mock()
        transaction = "new"
        selects = []

        def commit():
            nonlocal transaction
            self.assertEqual(transaction, "preflight")
            self.assertEqual(selects, ["preflight"])
            transaction = "committed"

        db.commit.side_effect = commit

        def execute(sql):
            nonlocal transaction
            cursor = Mock()
            if sql == "SET TRANSACTION READ ONLY":
                self.assertEqual(transaction, "new")
                transaction = "preflight"
            elif sql.startswith("SELECT format("):
                self.assertEqual(transaction, "preflight")
                with (root / "checkpoint.lock").open("a") as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(contender, fcntl.LOCK_UN)
                selects.append("preflight")
                cursor.fetchall.return_value = []
            elif sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
                self.assertEqual(transaction, "committed")
                transaction = "snapshot"
            elif sql.startswith("SELECT "):
                self.assertEqual(transaction, "snapshot")
                # A catalog or data SELECT here would freeze the dump snapshot.
                # Every SELECT in this transaction must occur under checkpoint.lock.
                with (root / "checkpoint.lock").open("a") as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                selects.append(sql)
            if sql == "SELECT pg_export_snapshot()":
                cursor.fetchone.return_value = ("fixture-exported-snapshot",)
            elif sql.startswith("SELECT seq,hash"):
                cursor.fetchone.return_value = (8, "fixture-head")
            return cursor

        db.execute.side_effect = execute

        class Connection:
            def __enter__(self):
                nonlocal active
                active = True
                return db

            def __exit__(self, *args):
                nonlocal active
                active = False

        copyfile = backup_snapshot.shutil.copyfile

        def copy_locked(source, destination):
            with (root / "checkpoint.lock").open("a") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return copyfile(source, destination)

        def dump(command, **kwargs):
            self.assertTrue(active)
            self.assertIn("--snapshot=fixture-exported-snapshot", command)
            with (root / "checkpoint.lock").open("a") as contender:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(contender, fcntl.LOCK_UN)
            Path(command[-1]).write_bytes(b"fixture database dump")

        with (
            patch.object(backup_snapshot.psycopg, "connect", return_value=Connection()),
            patch.object(backup_snapshot.shutil, "copyfile", side_effect=copy_locked),
            patch.object(backup_snapshot.subprocess, "run", side_effect=dump),
        ):
            backup_snapshot.snapshot(stage, root)
        self.assertFalse(active)
        self.assertEqual(selects[1], "SELECT pg_export_snapshot()")
        db.commit.assert_called_once_with()
        self.assertEqual(
            (stage / "resume.zip").read_bytes(), (root / "resume.zip").read_bytes()
        )
        manifest = json.loads((stage / "manifest.json").read_text())
        self.assertEqual(manifest["head"], [8, "fixture-head"])
        self.assertEqual(
            (stage / "database.dump").read_bytes(), b"fixture database dump"
        )


if __name__ == "__main__":
    unittest.main()
