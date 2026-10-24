"""Adversarial checks of recurring-format output and retention evidence."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import unittest
from pathlib import Path

from scripts import verify_skill_retention as verifier


class MemoryFile:
    def __init__(self, data):
        self.data = data

    def read_bytes(self):
        return self.data

    def read_text(self):
        return self.data.decode()


class MemoryDirectory:
    def __init__(self, files):
        self.files = files

    def __truediv__(self, name):
        return MemoryFile(self.files[name])


class RetentionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = (
            Path(__file__).resolve().parents[1]
            / "research/skill-acquisition/retention-development-02"
        )
        cls.protocol = json.loads((root / "protocol.json").read_text())
        cls.result = json.loads((root / "result.json").read_text())
        cls.records = [
            json.loads(line)
            for line in gzip.decompress((root / "traces.jsonl.gz").read_bytes())
            .decode()
            .splitlines()
        ]

    def evidence(self, mutation=None):
        protocol, result, records = (
            copy.deepcopy(self.protocol),
            copy.deepcopy(self.result),
            copy.deepcopy(self.records),
        )
        if mutation:
            mutation(protocol, result, records)
        raw = gzip.compress(
            ("\n".join(verifier.serial(record) for record in records) + "\n").encode(),
            mtime=0,
        )
        result["trace_sha256"] = hashlib.sha256(raw).hexdigest()
        return MemoryDirectory(
            {
                "protocol.json": json.dumps(protocol).encode(),
                "result.json": json.dumps(result).encode(),
                "traces.jsonl.gz": raw,
            }
        )

    def test_independent_results_and_registered_gate_reproduce(self):
        check = verifier.verify(self.evidence(), check_sources=False)
        self.assertTrue(check["passed"])
        self.assertEqual(check["jobs"], 1224)
        self.assertEqual(check["gates"], self.result["summary"]["gates"])

    def test_changed_output_fails_even_when_trace_hash_is_updated(self):
        def mutate(p, r, records):
            records[0]["score"]["outputs"][0] = "fabricated"
            records[0]["score"]["actual_sha256"] = verifier.digest(
                records[0]["score"]["outputs"]
            )

        with self.assertRaisesRegex(ValueError, "Scored executable outputs changed"):
            verifier.verify(self.evidence(mutate), check_sources=False)

    def test_frozen_branch_cannot_admit_future_skills(self):
        def mutate(p, r, records):
            record = next(
                x
                for x in records
                if x["branch"] == "frozen_before_unseen"
                and x["job"]["phase"] == "serve"
            )
            record["attempts"][0]["retain"] = True

        with self.assertRaisesRegex(ValueError, "Retention control leaked"):
            verifier.verify(self.evidence(mutate), check_sources=False)

    def test_uncharged_search_operation_is_rejected(self):
        def mutate(p, r, records):
            records[0]["attempts"][0]["proposal"]["work"]["total"] += 1

        with self.assertRaisesRegex(ValueError, "Resource meter does not sum"):
            verifier.verify(self.evidence(mutate), check_sources=False)

    def test_future_scoring_label_cannot_enter_visible_examples(self):
        def mutate(p, r, records):
            record = records[0]
            record["attempts"][0]["inputs"].append(record["job"]["inputs"][12])
            record["attempts"][0]["outputs"].append(record["job"]["outputs"][12])

        with self.assertRaisesRegex(
            ValueError, "Undeclared information reached search"
        ):
            verifier.verify(self.evidence(mutate), check_sources=False)

    def test_receipt_for_another_program_is_rejected(self):
        def mutate(p, r, records):
            records[0]["attempts"][0]["receipt"]["program_sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "Buyer receipt origin changed"):
            verifier.verify(self.evidence(mutate), check_sources=False)

    def test_dropped_failure_or_success_file_is_rejected(self):
        def mutate(p, r, records):
            records.pop()

        with self.assertRaisesRegex(ValueError, "Missing or extra job records"):
            verifier.verify(self.evidence(mutate), check_sources=False)

    def test_evidence_cannot_change_its_registered_gate(self):
        def mutate(p, r, records):
            r["summary"]["gates"]["practical_retention_benefit"] = not r["summary"][
                "gates"
            ]["practical_retention_benefit"]

        with self.assertRaisesRegex(ValueError, "Summary or registered gate changed"):
            verifier.verify(self.evidence(mutate), check_sources=False)


if __name__ == "__main__":
    unittest.main()
