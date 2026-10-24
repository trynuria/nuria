"""Independent reconstruction rejects altered, omitted and overclaimed evidence."""

import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_guarded_skill_tasks import generate
from scripts.evaluate_guarded_skills import run_job
from scripts.verify_active_skill_results import outcome
from scripts.verify_guarded_skill_results import digest, read, summarize, verify

ROOT = Path(__file__).resolve().parents[1]


class GuardedSkillResultsTests(unittest.TestCase):
    def test_lossless_compressed_evidence_needs_no_learner(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            value = {"complete": ["success", "failure", "abstention"]}
            with gzip.open(directory / "traces.json.gz", "wb") as stream:
                stream.write(json.dumps(value).encode())
            self.assertEqual(read(directory, "traces.json"), value)

    @classmethod
    def setUpClass(cls):
        corpus = generate(0, count=8)
        methods = [
            "old_fixed2",
            "old_fixed4",
            "composed_fixed2",
            "composed_fixed3",
            "guarded",
            "frozen_guarded",
        ]
        limits = {
            "maximum_work": 200000,
            "maximum_probes": 4,
            "synthesis_work": 5000,
            "minimum_evidence": 2,
        }
        records, states = [], {method: None for method in methods}
        for ordinal, job in enumerate(corpus["tasks"]):
            for method in methods:
                row, states[method] = run_job(
                    states[method],
                    job,
                    method,
                    corpus["config"],
                    "nuria:guarded-study-v1",
                    limits,
                    "unit-verifier-fixture",
                )
                row.update(seed=0, ordinal=ordinal)
                records.append(row)
        hashes = {
            "scripts/build_guarded_skill_tasks.py": hashlib.sha256(
                (ROOT / "scripts/build_guarded_skill_tasks.py").read_bytes()
            ).hexdigest()
        }
        cls.base = {
            "started.json": {
                "phase": "development",
                "seeds": [0],
                "jobs": 8,
                "methods": methods,
                "limits": limits,
                "source_hashes": hashes,
            },
            "corpora.json": [corpus],
            "traces.json": records,
            "summary.json": {
                "schema": "nuria.guarded-skill-study.v1",
                "phase": "development",
                "source_hashes": hashes,
            },
        }
        cls.refresh(cls.base)

    @staticmethod
    def refresh(evidence):
        summary = evidence["summary.json"]
        records = evidence["traces.json"]
        summary.update(
            methods=summarize(records),
            per_seed=[{"seed": 0, "methods": summarize(records)}],
            corpora_sha256=digest(evidence["corpora.json"]),
            traces_sha256=digest(records),
        )

    def check(self, evidence):
        with patch(
            "scripts.verify_guarded_skill_results.read",
            side_effect=lambda _, name: evidence[name],
        ):
            return verify(Path("unit-only-in-memory-evidence"))

    def test_real_development_trace_is_independently_reconstructed(self):
        receipt = self.check(copy.deepcopy(self.base))
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["records"], 48)
        self.assertEqual(len(receipt["public_results"]), 48)
        for result in receipt["public_results"]:
            self.assertNotIn("program", result)
            self.assertNotIn("outputs", result)
            self.assertNotIn("diagnostic_inputs", result)

    def test_changed_output_is_rejected_even_after_outer_commitments_are_updated(self):
        evidence = copy.deepcopy(self.base)
        evidence["traces.json"][0]["checked"]["outputs"][0] = "changed"
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "Independent final outputs"):
            self.check(evidence)

    def test_repeated_or_unavailable_label_index_is_rejected(self):
        for indices in ([0, 0], [0, 6], [0, True]):
            evidence = copy.deepcopy(self.base)
            evidence["traces.json"][0]["probe_indices"] = indices
            self.refresh(evidence)
            with self.subTest(indices=indices):
                with self.assertRaisesRegex(ValueError, "Diagnostic label index"):
                    self.check(evidence)

    def test_omitted_failed_job_cannot_shrink_denominator(self):
        evidence = copy.deepcopy(self.base)
        failed = next(
            index
            for index, row in enumerate(evidence["traces.json"])
            if not row["checked"]["passed"]
        )
        evidence["traces.json"].pop(failed)
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "chronology|Missing failed"):
            self.check(evidence)

    def test_membership_without_exact_buyer_admission_is_rejected(self):
        evidence = copy.deepcopy(self.base)
        row = next(
            row
            for row in evidence["traces.json"]
            if row["program"] is not None and not row["checked"]["passed"]
        )
        before = row["library_before"]
        row["library_after"]["programs"][row["program_sha256"]] = {
            "program": row["program"],
            "uses": 1,
            "features": [0.0] * 8,
            "evidence_sha256": digest(row["checked"]),
        }
        row["programs_after"] = sorted(row["library_after"]["programs"])
        row["library_after_sha256"] = digest(row["library_after"])
        self.assertNotEqual(before["programs"], row["library_after"]["programs"])
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "membership changed"):
            self.check(evidence)

    def test_cost_and_aggregate_tampering_are_rejected(self):
        evidence = copy.deepcopy(self.base)
        evidence["traces.json"][0]["total_work"] -= 1
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "Whole-job work"):
            self.check(evidence)
        evidence = copy.deepcopy(self.base)
        evidence["summary.json"]["methods"]["old_fixed2"]["exact_successes"] += 1
        with self.assertRaisesRegex(ValueError, "Summary total"):
            self.check(evidence)

    def test_positive_admission_for_wrong_outputs_is_rejected(self):
        evidence = copy.deepcopy(self.base)
        row = next(
            row
            for row in evidence["traces.json"]
            if row["admission"] is not None and not row["checked"]["passed"]
        )
        row["admission"]["accepted"] = True
        row["admission"]["correct_rows"] = row["admission"]["total_rows"]
        row["library_after"]["receipts"][row["proposal_id"]] = copy.deepcopy(
            row["admission"]
        )
        row["library_after_sha256"] = digest(row["library_after"])
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "Positive admission"):
            self.check(evidence)

    def test_changed_frozen_source_is_not_accepted_as_the_original(self):
        evidence = copy.deepcopy(self.base)
        evidence["started.json"]["source_hashes"][
            "scripts/build_guarded_skill_tasks.py"
        ] = "0" * 64
        evidence["summary.json"]["source_hashes"] = copy.deepcopy(
            evidence["started.json"]["source_hashes"]
        )
        with self.assertRaisesRegex(ValueError, "Evaluated source snapshot"):
            self.check(evidence)

    def test_guarded_answer_cannot_change_after_outer_checksums_are_replaced(self):
        evidence = copy.deepcopy(self.base)
        row = next(row for row in evidence["traces.json"] if row["session"] is not None)
        session = row["session"]
        record = next(iter(session["probe_receipts"].values()))
        record["receipt"]["output"] = "altered-buyer-answer"
        record["sha256"] = digest(record["receipt"])
        session["labels"][str(record["receipt"]["input_index"])] = record["receipt"][
            "output"
        ]
        session["state_sha256"] = digest(
            {key: value for key, value in session.items() if key != "state_sha256"}
        )
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "Probe answer"):
            self.check(evidence)

    def test_failed_guarded_search_and_rejection_cost_cannot_disappear(self):
        evidence = copy.deepcopy(self.base)
        row = next(
            row
            for row in evidence["traces.json"]
            if row["session"] is not None and row["session"]["diagnostic_rejections"]
        )
        session = row["session"]
        session["charges"][0]["amount"] += 1
        session["state_sha256"] = digest(
            {key: value for key, value in session.items() if key != "state_sha256"}
        )
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "charges do not add up"):
            self.check(evidence)

    def abstention_fixture(self):
        """Unit-only terminal boundary, not an evaluated performance record."""
        evidence = copy.deepcopy(self.base)
        row = next(
            row
            for row in evidence["traces.json"]
            if row["method"] == "guarded" and row["ordinal"] == 0
        )
        self.assertTrue(row["checked"]["passed"])
        evidence["traces.json"] = [row]
        evidence["corpora.json"][0]["tasks"] = evidence["corpora.json"][0]["tasks"][:1]
        evidence["corpora.json"][0]["source"]["task_count"] = 1
        evidence["started.json"].update(jobs=1, methods=["guarded"])
        original_program = row["program"]
        row["abstention_dispositions"] = [
            {
                "proposal_id": row["proposal_id"],
                "program_sha256": row["program_sha256"],
                "program": original_program,
                "checked": row["checked"],
                "receipt": row["admission"],
            }
        ]
        row["library_after"]["programs"].pop(row["program_sha256"])
        row.update(
            program=None,
            program_sha256=None,
            proposal_id=None,
            selection=None,
            admission=None,
            reason="unit-terminal-abstention",
        )
        session = row["session"]
        session.update(selected=None, stage="abstained", reason=row["reason"])
        terminal = row["actions"][-1]
        terminal.update(kind="abstain", reason=row["reason"])
        terminal["id"] = digest(
            {key: value for key, value in terminal.items() if key != "id"}
        )
        session["state_sha256"] = digest(
            {key: value for key, value in session.items() if key != "state_sha256"}
        )
        job = evidence["corpora.json"][0]["tasks"][0]
        row["checked"] = outcome(
            None,
            job["score_inputs"],
            job["score_outputs"],
            evidence["corpora.json"][0]["config"],
        )
        row["programs_after"] = []
        row["library_after_sha256"] = digest(row["library_after"])
        row["committed_proposal_sha256"] = digest(
            {"job": row["job_id"], "program": None, "probes": row["probe_indices"]}
        )
        self.reprice(row)
        self.refresh(evidence)
        return evidence

    @staticmethod
    def reprice(row):
        from scripts.verify_guarded_skill_results import units

        row["administration_work"] = (
            units(row["library_before"])
            + units(row["library_after"])
            + units(row["session"])
        )
        row["total_work"] = (
            row["controller_work"]["total"]
            + row["checked"]["total_work"]
            + row["administration_work"]
            + sum(
                item["checked"]["total_work"]
                for item in row.get("abstention_dispositions", [])
            )
        )

    def test_truthful_positive_disposition_is_consumed_without_retention(self):
        evidence = self.abstention_fixture()
        receipt = self.check(evidence)
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["methods"]["guarded"]["exact_successes"], 0)
        self.assertEqual(
            receipt["public_results"][0]["abstention_disposition_count"], 1
        )
        self.assertEqual(receipt["public_results"][0]["program_count_after"], 0)

    def test_disposition_outputs_cost_and_nonretention_are_independently_checked(self):
        evidence = self.abstention_fixture()
        evidence["traces.json"][0]["abstention_dispositions"][0]["checked"]["outputs"][
            0
        ] = "misreported"
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "disposition misreports"):
            self.check(evidence)
        evidence = self.abstention_fixture()
        row = evidence["traces.json"][0]
        row["total_work"] -= row["abstention_dispositions"][0]["checked"]["total_work"]
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "Whole-job work"):
            self.check(evidence)
        evidence = self.abstention_fixture()
        row = evidence["traces.json"][0]
        artifact = row["abstention_dispositions"][0]
        row["library_after"]["programs"][artifact["program_sha256"]] = {
            "program": artifact["program"],
            "uses": 1,
            "features": [0.0] * 8,
            "evidence_sha256": digest(artifact["checked"]),
        }
        row["programs_after"] = sorted(row["library_after"]["programs"])
        row["library_after_sha256"] = digest(row["library_after"])
        self.reprice(row)
        self.refresh(evidence)
        with self.assertRaisesRegex(ValueError, "membership changed"):
            self.check(evidence)


if __name__ == "__main__":
    unittest.main()
