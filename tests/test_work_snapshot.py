"""Snapshot projection preserves unknowns and the boundary around public claims."""

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import AnalystWorkspace
from scripts.register_work_snapshot import register


class WorkSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nuria-work-snapshot-"))
        self.source = self.root / "response.json"
        self.value = {
            "schema": "nuria.work.v1",
            "updated_utc": "2026-10-26T00:00:00Z",
            "scope": "Recent reported work only",
            "action_tasks": [
                {
                    "id": "known-zero",
                    "provider": "included-compute",
                    "state": "evaluated",
                    "kind": "compute",
                    "acceptance": {"passed": False, "utility": -0.1},
                    "settlement": {
                        "verified": True,
                        "basis": "included_capacity",
                        "total_micro_usd": 0,
                    },
                    "outcome": {"utility": -0.1, "reward": -0.1},
                },
                {
                    "id": "unknown",
                    "provider": "unverified-provider",
                    "state": "proposed",
                    "kind": "data",
                    "acceptance": None,
                    "settlement": None,
                    "outcome": None,
                },
            ],
        }

    def write(self):
        self.source.write_text(json.dumps(self.value))

    def test_exact_source_nulls_and_included_capacity_are_distinct(self):
        self.write()
        original = self.source.read_bytes()
        out = self.root / "registered"
        receipt = register(self.source, out)
        self.assertEqual(receipt["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual((out / "work-response.json").read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        with closing(sqlite3.connect(out / "work.sqlite")) as connection:
            rows = connection.execute(
                "SELECT task_id,delivery_passed,semantic_accepted,settled_micro_usd "
                "FROM task_outcomes ORDER BY task_id"
            ).fetchall()
            self.assertEqual(
                rows, [("known-zero", 0, None, 0), ("unknown", None, None, None)]
            )
        workspace = AnalystWorkspace(out / "registry.json")
        query = workspace.call(
            "work", "query", {"sql": "SELECT COUNT(*) FROM task_outcomes"}
        )
        self.assertEqual(query["rows"], [[2]])
        self.assertEqual(query["status"], "ok")
        self.assertEqual((out.stat().st_mode & 0o077), 0)
        for path in out.iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        with self.assertRaises(FileExistsError):
            register(self.source, out)

    def test_duplicate_or_malformed_flags_refuse_before_output_creation(self):
        for malformed in (
            self.value["action_tasks"] * 2,
            [dict(self.value["action_tasks"][0], acceptance={"passed": "false"})],
            [dict(self.value["action_tasks"][0], settlement={"total_micro_usd": True})],
        ):
            self.value["action_tasks"] = malformed
            self.write()
            out = self.root / "refused"
            with self.assertRaises(ValueError):
                register(self.source, out)
            self.assertFalse(out.exists())

    def test_native_amount_is_not_guessed_from_an_unrelated_currency_field(self):
        row = copy.deepcopy(self.value["action_tasks"][1])
        row["settlement"] = {"amount": 123, "currency": "SOL"}
        self.value["action_tasks"] = [row]
        self.write()
        out = self.root / "registered"
        register(self.source, out)
        with closing(sqlite3.connect(out / "work.sqlite")) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT settled_micro_usd FROM task_outcomes"
                ).fetchall(),
                [(None,)],
            )
