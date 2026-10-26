"""Small development checks; these do not execute the held-out course."""

import json
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.evaluate_analytical_skills import (
    CHALLENGE_VARIANTS,
    expected_rows,
    generated_rows,
    make_revision,
    run_course,
    validate_protocol,
)
from scripts.verify_analytical_skills import (
    bounded_query,
    read_only_authorizer,
    receipt_identity,
    record_identity,
    sha,
    truth,
    verify,
)


class AnalyticalSkillCourseTests(unittest.TestCase):
    def test_independent_truth_preserves_nulls_duplicates_and_boundaries(self):
        rows = [[1, None, 2, -1, 1, 0, None], [1, None, 1, None, 2, 1, None]]
        expected = [[None, 2, 1, 0, 1, 1, 1]]
        self.assertEqual(expected_rows(rows), expected)
        self.assertEqual(truth(rows), expected)
        self.assertEqual(truth(rows + rows), [[None, 4, 2, 0, 2, 2, 2]])

    def test_development_generator_determinism_and_distinct_rows(self):
        rows = generated_rows(31, "standard")
        self.assertEqual(rows, generated_rows(31, "standard"))
        self.assertNotEqual(rows, generated_rows(32, "standard"))
        self.assertEqual(generated_rows(31, "empty"), [])
        self.assertEqual(len(CHALLENGE_VARIANTS), 12)

    def test_development_registry_is_content_bound(self):
        root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("skill-course-" + uuid.uuid4().hex)
        )
        registry, rows = make_revision(root, 31, "standard")
        self.assertEqual(json.loads((root / "input.json").read_text())["rows"], rows)
        manifest = json.loads(registry.read_text())
        self.assertTrue(manifest["revision"].startswith("sha256:"))
        with self.assertRaises(FileExistsError):
            make_revision(root, 31, "standard")

    def test_challenge_protocol_requires_exact_frozen_identity(self):
        root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("skill-protocol-" + uuid.uuid4().hex)
        )
        root.mkdir(parents=True)
        protocol = root / "protocol.json"
        protocol.write_text('{"schema":"nuria.analytical-skill-course.v1"}')
        with self.assertRaisesRegex(ValueError, "identity changed"):
            validate_protocol(protocol, "0" * 64, Path(__file__).resolve().parents[1])


class AnalyticalSkillReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("skill-replay-" + uuid.uuid4().hex)
        )
        run_course(cls.base, [{"seed": 33, "variant": "standard"}], phase="development")

    def copied(self):
        destination = self.base.parent / ("skill-tamper-" + uuid.uuid4().hex)
        shutil.copytree(self.base, destination)
        return destination

    def save_modified_results(self, directory, result):
        file = directory / "results.json"
        file.write_text(
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        )
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["files"]["results.json"] = sha(file)
        for index, record in enumerate(result["records"]):
            case = directory / f"completed-case-{index:02d}.json"
            case.write_text(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
            manifest["files"][case.name] = sha(case)
        (directory / "manifest.json").write_text(json.dumps(manifest))

    def test_actual_development_course_replays_independently(self):
        result = verify(self.base)
        self.assertTrue(result["passed"])
        self.assertEqual(result["queries_replayed"], 3)
        self.assertTrue(
            all(value["accepted"] == 1 for value in result["counts"].values())
        )

    def test_rehashed_wrong_rows_cannot_replace_sql_execution(self):
        directory = self.copied()
        result = json.loads((directory / "results.json").read_text())
        receipt = result["records"][0]["controls"]["exact-query-cache"]["receipt"]
        receipt["rows"][0][1] += 1
        receipt["result_sha256"] = receipt_identity(receipt)
        self.save_modified_results(directory, result)
        with self.assertRaisesRegex(ValueError, "verdict differs"):
            verify(directory)

    def test_missing_control_cannot_shrink_the_denominator(self):
        directory = self.copied()
        result = json.loads((directory / "results.json").read_text())
        result["records"][0]["controls"].pop("exact-query-cache")
        self.save_modified_results(directory, result)
        with self.assertRaisesRegex(ValueError, "arm is missing"):
            verify(directory)

    def test_rehashed_fake_promoted_origin_cannot_replace_accepted_memory(self):
        directory = self.copied()
        result = json.loads((directory / "results.json").read_text())
        promoted = result["origin"]["promoted"]
        promoted["origin"]["episode_id"] = "0" * 64
        promoted["skill_id"] = record_identity(
            {
                k: v
                for k, v in promoted.items()
                if k not in {"skill_id", "schema_inspection_receipt"}
            }
        )
        self.save_modified_results(directory, result)
        with self.assertRaisesRegex(ValueError, "Promoted origin differs"):
            verify(directory)

    def test_infinite_or_oversized_sql_replay_stops(self):
        connection = sqlite3.connect(":memory:")
        connection.set_authorizer(read_only_authorizer)
        try:
            with self.assertRaisesRegex(ValueError, "finite bound"):
                bounded_query(
                    connection,
                    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT SUM(x) FROM n",
                )
            with self.assertRaisesRegex(ValueError, "finite output"):
                bounded_query(connection, "SELECT 1 UNION ALL SELECT 2", max_rows=1)
            with self.assertRaisesRegex(ValueError, "finite output"):
                bounded_query(connection, "SELECT '123456789'", max_bytes=2)
        finally:
            connection.close()

    def test_second_case_rejection_preserves_controls_and_unexecuted_denominator(self):
        from cognition.analytical_skills import AnalyticalSkills

        directory = self.base.parent / ("skill-incomplete-" + uuid.uuid4().hex)
        original = AnalyticalSkills.settle

        def reject_second(bank, job_id, *, verify_result):
            if job_id == "report-01":

                def verify_result(request):
                    return {
                        "accepted": False,
                        "evidence_sha256": "f" * 64,
                        "checker_id": "independent-ledger-grouping-v1",
                    }

            return original(bank, job_id, verify_result=verify_result)

        cases = [
            {"seed": 31, "variant": "standard"},
            {"seed": 32, "variant": "nulls"},
            {"seed": 33, "variant": "standard"},
        ]
        with patch.object(AnalyticalSkills, "settle", new=reject_second):
            with self.assertRaisesRegex(ValueError, "revoked"):
                run_course(directory, cases, phase="development")
        failure = json.loads((directory / "failure.json").read_text())
        self.assertEqual(failure["selected_method_cases"], 9)
        self.assertEqual(failure["completed_method_cases"], 6)
        self.assertEqual(failure["uncompleted_method_cases"], 3)
        second = json.loads((directory / "completed-case-01.json").read_text())
        self.assertFalse(second["skill_qualification"]["accepted"])
        self.assertEqual(
            set(second["controls"]), {"exact-query-cache", "conventional-template"}
        )
        with self.assertRaisesRegex(ValueError, "Incomplete course cannot pass"):
            verify(directory)


if __name__ == "__main__":
    unittest.main()
