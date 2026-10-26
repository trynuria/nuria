"""Offline replay and evidence-integrity checks for the published course."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import unittest
import uuid
from pathlib import Path

from cognition.workflow_planner import digest
from scripts.extract_workflow_evidence import extract
from scripts.verify_workflow_course import verify_course


class VerifyWorkflowCourseTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]
        self.root = (
            self.repository
            / ".test-state"
            / ("verify-workflow-course-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        self.evidence = self.repository / "research/workflow-reasoning/results-01"
        self.package = extract(self.evidence, self.root / "expanded")

    @staticmethod
    def write(path, value):
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )

    def verify(self):
        return verify_course(
            self.repository, self.package, replay_workspace=self.root / "replay"
        )

    def change_job(self, index, mutate):
        path = self.package / f"job-{index:02d}.json"
        job = json.loads(path.read_bytes())
        mutate(job)
        self.write(path, job)
        report = json.loads((self.package / "report.json").read_bytes())
        report["jobs"][index - 1] = job
        self.write(self.package / "report.json", report)

    def test_all_eighteen_jobs_replay_and_verify_saved_artifacts_without_a_model(self):
        before = {
            str(path.relative_to(self.package)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in self.package.rglob("*")
            if path.is_file()
        }
        result = self.verify()
        self.assertTrue(result["verified"])
        self.assertEqual(result["retained_proposals"], 21)
        self.assertEqual(result["replayed_executions"], 21)
        self.assertEqual(result["artifact_records_verified"], 54)
        self.assertEqual(result["saved_markdown_verified"], 18)
        self.assertEqual(result["model_calls_replayed"], 0)
        self.assertFalse(result["provider_authenticity_or_billing_verified"])
        self.assertEqual(
            result["methods"],
            {
                method: {"selected": 6, "attempted": 6, "accepted": 6}
                for method in ("greedy", "exact", "model")
            },
        )
        after = {
            str(path.relative_to(self.package)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in self.package.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_dropped_job_cannot_change_denominator(self):
        path = self.package / "report.json"
        report = json.loads(path.read_bytes())
        report["jobs"].pop()
        self.write(path, report)
        with self.assertRaisesRegex(ValueError, "denominator"):
            self.verify()

    def test_raw_proposal_is_bound_to_its_execution(self):
        def mutate(job):
            attempt = job["result"]["attempts"][0]
            plan = json.loads(attempt["proposal"])
            plan["nodes"][0]["id"] = "different_read"
            attempt["proposal"] = json.dumps(plan)

        self.change_job(1, mutate)
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_execution_receipt_is_rejected(self):
        self.change_job(
            1,
            lambda job: job["result"]["attempts"][0]["execution"].update(
                accepted=False
            ),
        )
        with self.assertRaisesRegex(ValueError, "receipt hash"):
            self.verify()

    def test_trace_hash_is_checked_even_after_rehashing_receipt(self):
        def mutate(job):
            record = job["result"]["attempts"][0]["execution"]
            record["trace"][0]["input_sha256"] = "0" * 64
            record["execution_sha256"] = digest(
                {
                    key: value
                    for key, value in record.items()
                    if key != "execution_sha256"
                }
            )
            job["result"]["execution_log"][record["execution_id"]] = record

        self.change_job(1, mutate)
        with self.assertRaisesRegex(ValueError, "Trace operation/argument"):
            self.verify()

    def test_actual_saved_markdown_is_read_and_checked(self):
        job = json.loads((self.package / "job-01.json").read_bytes())
        artifact = job["result"]["attempts"][0]["execution"]["final_artifact"]["value"]
        path = self.package / artifact["path"]
        path.write_bytes(path.read_bytes() + b"Changed after delivery.\n")
        with self.assertRaisesRegex(ValueError, "saved artifact verification"):
            self.verify()

    def test_method_totals_are_recomputed(self):
        path = self.package / "report.json"
        report = json.loads(path.read_bytes())
        report["methods"]["model"]["accepted"] = 5
        self.write(path, report)
        with self.assertRaisesRegex(ValueError, "method denominator/acceptance"):
            self.verify()

    def test_configured_budget_arithmetic_is_recomputed(self):
        path = self.package / "report.json"
        report = json.loads(path.read_bytes())
        report["configured_cost_and_ceiling_delta_micro_usd"] += 1
        self.write(path, report)
        with self.assertRaisesRegex(ValueError, "budget arithmetic"):
            self.verify()

    def test_extraction_rejects_hash_consistent_parent_path(self):
        evidence = self.root / "invalid-evidence"
        evidence.mkdir()
        archive = evidence / "workflow-evidence.tar.gz"
        content = b"bounded fixture"
        with tarfile.open(archive, "w:gz") as stream:
            member = tarfile.TarInfo("../outside.txt")
            member.size = len(content)
            stream.addfile(member, io.BytesIO(content))
        self.write(
            evidence / "archive-manifest.json",
            {
                "archive": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "members": {"../outside.txt": hashlib.sha256(content).hexdigest()},
            },
        )
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            extract(evidence, self.root / "invalid-expanded")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_archive_identity_is_checked_before_expansion(self):
        evidence = self.root / "wrong-identity"
        evidence.mkdir()
        archive = evidence / "workflow-evidence.tar.gz"
        archive.write_bytes(b"not a retained archive")
        self.write(
            evidence / "archive-manifest.json",
            {
                "archive": archive.name,
                "sha256": "0" * 64,
                "members": {"file": "0" * 64},
            },
        )
        target = self.root / "not-created"
        with self.assertRaisesRegex(ValueError, "identity"):
            extract(evidence, target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
