"""Portable evidence and privacy checks; fixtures do not measure intelligence."""

import hashlib
import io
import json
import sqlite3
import unittest
import uuid
import zipfile
from pathlib import Path

from cognition.experience_navigation import METHODS
from cognition.outcome_worker import IsolatedPython, OutcomeStore, identity
from scripts.evaluate_outcome_loop import exact_paired
from scripts.package_experience_navigation import (
    check_archive,
    check_public,
    compressed_rows,
    package,
    read_store,
    redact_terminal_paths,
    seal_proposals,
    transcripts,
    verify_proposals,
)


class NavigationExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("navigation-export-" + uuid.uuid4().hex)
        )
        self.directory.mkdir(parents=True)

    def course(self, *, deferred=False):
        root = self.directory / "course"
        root.mkdir()
        code = "def task_func(): return 4"
        checker = "assert task_func() == 4"
        original = {
            "task_id": "export-fixture",
            "complete_prompt": "Return four from task_func().",
            "test": checker,
        }
        task = {
            "task_id": original["task_id"],
            "prompt": original["complete_prompt"],
            "checker": checker,
            "source_sha256": identity(original),
        }
        enrollment = {
            "task_ids": [task["task_id"]],
            "experience_sha256": identity([]),
            "implementation_sha256": identity("export fixture"),
            "correction_catalog_sha256": None,
        }
        checked = IsolatedPython(root / "native").run(
            code, checker, label="known-program"
        )
        self.assertTrue(checked["passed"])
        for method in METHODS:
            job = "test-" + method + "-fixture"
            store = OutcomeStore(root / ("test-" + method + ".sqlite"))
            store.begin(job, {"task": task})
            store.finish(
                job,
                {
                    "method": method,
                    "job_id": job,
                    "task_id": task["task_id"],
                    "source_sha256": task["source_sha256"],
                    "prompt": task["prompt"],
                    "implementation_sha256": enrollment["implementation_sha256"],
                    "correction_catalog_sha256": None,
                    "experience_sha256": identity([]),
                    "experience_ids": [],
                    "navigation": {"view": []},
                    "code": code,
                    "proposed_code_sha256": identity(code),
                    "accepted": None,
                    "checker": None,
                    "attempts": [
                        {
                            "content": "fixture",
                            "response_sha256": identity("fixture"),
                            "usage": {"completion_tokens": 1},
                        }
                    ]
                    * 3,
                },
            )
        (root / "test-enrollment.json").write_text(json.dumps(enrollment))
        (root / "test-experience.json").write_text("[]")
        (root / "tasks.jsonl.gz").write_bytes(compressed_rows([task]))
        (root / "upstream-tasks.jsonl.gz").write_bytes(compressed_rows([original]))
        (root / "proposal-commitment-before-grading.json").write_text(
            json.dumps(seal_proposals(root))
        )
        if deferred:
            return root
        for method in METHODS:
            OutcomeStore(root / ("test-" + method + ".sqlite")).accept(
                "test-" + method + "-fixture", checked
            )
        (root / "held-out-results.json").write_text(
            json.dumps(
                {
                    "accepted": dict.fromkeys(METHODS, 1),
                    "comparisons": {
                        method: exact_paired([True], [True]) for method in METHODS[1:]
                    },
                }
            )
        )
        return root

    def test_proposal_seal_refuses_late_creation_and_detects_changed_code(self):
        root = self.course()
        with self.assertRaisesRegex(ValueError, "already exist"):
            seal_proposals(root)
        output = self.directory / "public"
        package(root, output)
        self.assertEqual(verify_proposals(output)["sealed_proposals_verified"], 3)
        from scripts.evaluate_experience_navigation import read_rows

        rows = read_rows(output / "executions.jsonl.gz")
        rows[0]["code"] = "def task_func(): return 999"
        (output / "executions.jsonl.gz").write_bytes(compressed_rows(rows))
        with self.assertRaisesRegex(ValueError, "public proposal seal"):
            verify_proposals(output)

    def test_only_terminal_operator_paths_are_redacted_with_exact_hashes(self):
        original = [
            {
                "method": "navigation",
                "job_id": "fixture",
                "code": "pass",
                "accepted": False,
                "checker": {
                    "passed": False,
                    "stderr": "File '/"
                    + "Users/operator/private.py', line 7: ValueError",
                },
            }
        ]
        before = json.dumps(original, sort_keys=True)
        public, receipt = redact_terminal_paths(original)
        self.assertEqual(before, json.dumps(original, sort_keys=True))
        check_public(json.dumps(public))
        self.assertEqual(public[0]["code"], original[0]["code"])
        self.assertFalse(public[0]["accepted"])
        self.assertIn("ValueError", public[0]["checker"]["stderr"])
        self.assertEqual(
            public[0]["checker"]["stderr_original_sha256"],
            hashlib.sha256(original[0]["checker"]["stderr"].encode()).hexdigest(),
        )
        self.assertEqual(receipt[0]["original_result_sha256"], identity(original[0]))
        self.assertEqual(receipt[0]["public_result_sha256"], identity(public[0]))
        a, b = dict(original[0]), dict(public[0])
        a.update({"accepted": None, "checker": None})
        b.update({"accepted": None, "checker": None})
        self.assertEqual(identity(a), identity(b))

    def test_complete_export_preserves_exact_records_and_is_reproducible(self):
        root = self.course()
        before = {
            file.name: hashlib.sha256(file.read_bytes()).hexdigest()
            for file in root.glob("*.sqlite")
        }
        first = self.directory / "public-one"
        second = self.directory / "public-two"
        report = package(root, first)
        self.assertEqual(report["programs"], 3)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["verification"]["accepted"], dict.fromkeys(METHODS, 1))
        package(root, second)
        for file in first.iterdir():
            self.assertEqual(file.read_bytes(), (second / file.name).read_bytes())
        self.assertEqual(
            before,
            {
                file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                for file in root.glob("*.sqlite")
            },
        )

    def test_ungraded_and_missing_methods_refuse_before_writing(self):
        root = self.course(deferred=True)
        output = self.directory / "not-written"
        with self.assertRaisesRegex(ValueError, "terminal"):
            package(root, output)
        self.assertFalse(output.exists())

    def test_missing_enrolled_task_refuses_before_writing(self):
        root = self.course()
        path = root / "test-enrollment.json"
        enrolled = json.loads(path.read_text())
        enrolled["task_ids"].append("missing-fixture")
        path.write_text(json.dumps(enrolled))
        output = self.directory / "not-written"
        with self.assertRaisesRegex(ValueError, "omits"):
            package(root, output)
        self.assertFalse(output.exists())

    def test_journal_tampering_and_private_text_are_refused(self):
        root = self.course()
        with sqlite3.connect(root / "test-navigation.sqlite") as db:
            db.execute("UPDATE events SET previous=? WHERE seq=1", ("a" * 64,))
        with self.assertRaisesRegex(ValueError, "journal"):
            read_store(root / "test-navigation.sqlite")
        with self.assertRaisesRegex(ValueError, "private"):
            check_public("Host file at /" + "Users/operator/private.txt")
        check_public("Use password=input_value according to the public task.")

    def test_source_archive_is_inspected_before_publication(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("source.py", "path = '/" + "Users/operator/private.txt'")
        with self.assertRaisesRegex(ValueError, "private"):
            check_archive(raw.getvalue())

    def test_provider_export_keeps_usage_without_private_provider_identifiers(self):
        folder = self.directory / "provider-transcripts"
        folder.mkdir()
        item = {
            "request": {"model": "fixture", "input": []},
            "response": {
                "content": "Visible program",
                "usage": {"completion_tokens": 2},
                "reservation_id": "PRIVATE_RESERVATION",
                "request_sha256": "private-provider-request",
                "accounted_cost_micro_usd": 10,
            },
        }
        (folder / "1.json").write_text(json.dumps(item))
        public = transcripts(self.directory)
        self.assertNotIn("PRIVATE_RESERVATION", json.dumps(public))
        self.assertNotIn("request_sha256", json.dumps(public))
        self.assertEqual(public[0]["response"]["accounted_cost_micro_usd"], 10)


if __name__ == "__main__":
    unittest.main()
