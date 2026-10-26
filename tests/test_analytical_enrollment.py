"""Accepted-episode enrollment fixtures over retained real SQLite databases."""

import copy
import hashlib
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_analytical_report_worker as fixtures

from cognition.analytical_enrollment import (
    enroll_report,
    load_enrollment,
    snapshot_identity,
)
from cognition.analytical_skills import AnalyticalSkills
from cognition.procedural_memory import digest
from scripts.enroll_analytical_report import main as enroll_main
from scripts.run_analytical_report import main as run_main


class AnalyticalEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalyticalReportWorkerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.worker.close()
        self.fixture.bank.close()
        self.fixture.memory.close()
        self.root = self.fixture.root
        self.memory = self.root / "memory.sqlite"
        self.origin = self.fixture.skill["origin"]
        self.receipt = self.origin["execution_receipt"]
        self.episode = self.origin["episode_id"]
        self.inputs = {
            "memory_snapshot": self.memory,
            "episode_id": self.episode,
            "execution_receipt": self.receipt,
            "registry": self.fixture.a,
            "db_id": "work",
            "report_id": fixtures.REPORT,
        }
        self.before = self.memory.read_bytes()
        self.output = self.root / "enrolled"

    def enroll(self, **changes):
        return enroll_report(self.output, **{**self.inputs, **changes})

    def run_enrolled(self, registry=None, job="report-one"):
        with redirect_stdout(io.StringIO()):
            return run_main(
                [
                    "--enrollment",
                    str(self.output / "enrollment.json"),
                    "--registry",
                    str(registry or self.fixture.b),
                    "--job-id",
                    job,
                ]
            )

    def test_accepted_episode_enrolls_and_runs_on_fresh_rows(self):
        descriptor = self.enroll()
        self.assertEqual(self.memory.read_bytes(), self.before)
        self.assertEqual(
            (self.output / "retained-memory.sqlite").read_bytes(), self.before
        )
        self.assertEqual(descriptor["source_id"], fixtures.SOURCE)
        self.assertEqual(descriptor["checker_id"], fixtures.CHECKER)
        self.assertEqual(
            descriptor["paths"],
            {"skill_bank": "skills.sqlite", "worker_state": "worker"},
        )
        self.assertIsNone(descriptor["neural_decision"])
        result = self.run_enrolled()
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(
            result["artifact"]["execution"]["receipt"]["rows"],
            [["alpha", 1, 1, 0, 1, 1, 0], ["beta", 1, 0, 0, 0, 0, 1]],
        )
        self.assertEqual(self.memory.read_bytes(), self.before)

    def test_completed_enrollment_and_job_are_idempotent(self):
        original = self.enroll()
        self.assertEqual(original, self.enroll())
        first = self.run_enrolled()
        with patch.object(
            AnalyticalSkills, "execute", side_effect=AssertionError("duplicate query")
        ):
            repeat = self.run_enrolled()
        self.assertEqual(first["artifact"], repeat["artifact"])
        self.assertTrue(repeat["replayed"])
        self.assertEqual(
            repeat["invocation_work"],
            {
                "workspace_query_calls": 0,
                "independent_check_calls": 0,
                "model_calls": 0,
            },
        )
        with self.assertRaises(ValueError):
            self.enroll(report_id="changed-report")

    def test_live_wal_and_rollback_journal_refuse_without_mutating_source(self):
        for suffix in ("-wal", "-shm", "-journal"):
            retained = self.root / ("snapshot" + suffix + ".sqlite")
            retained.write_bytes(self.before)
            os.chmod(retained, 0o600)
            sidecar = Path(str(retained) + suffix)
            sidecar.write_bytes(b"retained-active-journal")
            with self.assertRaises(ValueError):
                self.enroll(memory_snapshot=retained)
            self.assertEqual(retained.read_bytes(), self.before)
            self.assertEqual(self.memory.read_bytes(), self.before)
            self.assertFalse(self.output.exists())

    def test_source_symlinks_and_public_memory_are_refused(self):
        alias = self.root / "memory-alias.sqlite"
        alias.symlink_to(self.memory)
        with self.assertRaises(ValueError):
            self.enroll(memory_snapshot=alias)
        os.chmod(self.memory, 0o644)
        with self.assertRaises(ValueError):
            snapshot_identity(self.memory)
        self.assertEqual(self.memory.read_bytes(), self.before)

    def test_changed_receipt_cannot_promote_and_failure_is_retained(self):
        receipt = copy.deepcopy(self.receipt)
        receipt["rows"] = [["wrong"]]
        with self.assertRaises(ValueError):
            self.enroll(execution_receipt=receipt)
        self.assertTrue((self.output / "request.json").exists())
        self.assertFalse((self.output / "enrollment.json").exists())
        with self.assertRaises(ValueError):
            self.enroll(execution_receipt=receipt)
        self.assertEqual(self.memory.read_bytes(), self.before)

    def test_interrupted_enrollment_is_not_retried(self):
        with patch.object(
            AnalyticalSkills, "promote", side_effect=RuntimeError("interrupted")
        ):
            with self.assertRaises(RuntimeError):
                self.enroll()
        with patch.object(
            AnalyticalSkills, "promote", side_effect=AssertionError("automatic retry")
        ):
            with self.assertRaises(ValueError):
                self.enroll()
        self.assertTrue((self.output / "retained-memory.sqlite").exists())
        self.assertEqual(self.memory.read_bytes(), self.before)

    def test_descriptor_path_tampering_refuses(self):
        self.enroll()
        file = self.output / "enrollment.json"
        descriptor = json.loads(file.read_text())
        descriptor["paths"]["skill_bank"] = "../other.sqlite"
        body = {
            key: value
            for key, value in descriptor.items()
            if key != "enrollment_sha256"
        }
        descriptor["enrollment_sha256"] = digest(body)
        file.write_text(json.dumps(descriptor))
        with self.assertRaises(ValueError):
            load_enrollment(file)

    def test_enrollment_cli_accepts_the_retained_artifact_receipt(self):
        artifact = self.root / "artifact.json"
        artifact.write_text(json.dumps({"selected_receipt": self.receipt}))
        with redirect_stdout(io.StringIO()):
            descriptor = enroll_main(
                [
                    "--output",
                    str(self.output),
                    "--memory-snapshot",
                    str(self.memory),
                    "--episode-id",
                    self.episode,
                    "--execution-receipt",
                    str(artifact),
                    "--registry",
                    str(self.fixture.a),
                    "--db-id",
                    "work",
                    "--report-id",
                    fixtures.REPORT,
                ]
            )
        self.assertEqual(
            descriptor["origin"]["memory_snapshot_sha256"],
            hashlib.sha256(self.before).hexdigest(),
        )
        self.assertEqual(self.run_enrolled()["status"], "accepted")

    def test_enrollment_cli_forbids_overriding_its_manual_identities(self):
        self.enroll()
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            run_main(
                [
                    "--enrollment",
                    str(self.output / "enrollment.json"),
                    "--registry",
                    str(self.fixture.a),
                    "--job-id",
                    "same",
                    "--source-id",
                    "different",
                ]
            )
