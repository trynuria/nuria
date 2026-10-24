"""Adversarial retained-evidence checks using an independent outcome verifier."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import verify_skill_results as verifier


class MemoryFile:
    def __init__(self, value):
        self.value = value

    def read_bytes(self):
        return self.value

    def read_text(self):
        return self.value.decode()


class MemoryDirectory:
    def __init__(self, values):
        self.values = values

    def __truediv__(self, name):
        return MemoryFile(self.values[name])


class SkillResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = (
            Path(__file__).resolve().parents[1]
            / "research/skill-acquisition/development-01"
        )
        cls.protocol = json.loads((directory / "protocol.json").read_text())
        cls.result = json.loads((directory / "result.json").read_text())
        cls.records = [
            json.loads(line)
            for line in gzip.decompress((directory / "traces.jsonl.gz").read_bytes())
            .decode()
            .splitlines()
        ]

    def retained(self, *, mutate=None):
        protocol = copy.deepcopy(self.protocol)
        result = copy.deepcopy(self.result)
        records = copy.deepcopy(self.records)
        if mutate:
            mutate(protocol, result, records)
        raw = gzip.compress(
            ("\n".join(verifier.serial(row) for row in records) + "\n").encode(),
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

    def test_all_retained_actual_outputs_and_failed_benefit_gates_reproduce(self):
        result = verifier.verify(self.retained(), check_sources=False)
        self.assertTrue(result["passed"])
        self.assertEqual(result["trace_records"], 840)
        self.assertFalse(result["gates"]["practical_benefit"])
        self.assertFalse(result["gates"]["acquisition_transfer"])

    def test_historical_source_snapshots_pass_after_top_level_formatting(self):
        directory = (
            Path(__file__).resolve().parents[1]
            / "research/skill-acquisition/development-01"
        )
        filename = "scripts/evaluate_skills.py"
        expected = self.protocol["source_hashes"][filename]
        self.assertNotEqual(
            hashlib.sha256((verifier.ROOT / filename).read_bytes()).hexdigest(),
            expected,
        )
        self.assertEqual(
            hashlib.sha256((directory / "sources" / filename).read_bytes()).hexdigest(),
            expected,
        )
        self.assertTrue(verifier.verify(directory)["passed"])

    def test_wrong_retained_source_never_falls_back_to_another_file(self):
        directory = (
            Path(__file__).resolve().parents[1]
            / "research/skill-acquisition/development-01"
        )
        snapshot = directory / "sources/scripts/evaluate_skills.py"
        original = Path.read_bytes

        def tampered(path):
            raw = original(path)
            return raw + b"\n# changed retained source\n" if path == snapshot else raw

        with patch.object(Path, "read_bytes", tampered):
            with self.assertRaisesRegex(ValueError, "Evaluated source changed"):
                verifier.verify(directory)

    def test_missing_snapshot_cannot_accept_changed_current_source(self):
        directory = (
            Path(__file__).resolve().parents[1]
            / "research/skill-acquisition/development-01"
        )
        snapshot = directory / "sources/scripts/evaluate_skills.py"
        original = Path.exists

        def absent(path):
            return False if path == snapshot else original(path)

        with patch.object(Path, "exists", absent):
            with self.assertRaisesRegex(ValueError, "Evaluated source changed"):
                verifier.verify(directory)

    def test_output_tamper_is_rejected_even_with_updated_trace_commitment(self):
        def change(protocol, result, records):
            records[0]["score"]["outputs"][0] = "invented"
            records[0]["score"]["actual_sha256"] = verifier.commit(
                records[0]["score"]["outputs"]
            )

        with self.assertRaisesRegex(ValueError, "Scored executable outputs changed"):
            verifier.verify(self.retained(mutate=change), check_sources=False)

    def test_uncharged_search_attempt_is_rejected(self):
        def change(protocol, result, records):
            records[0]["attempts"][0]["result"]["work"]["total"] += 1

        with self.assertRaisesRegex(ValueError, "Search meter does not sum"):
            verifier.verify(self.retained(mutate=change), check_sources=False)

    def test_feedback_control_cannot_secretly_admit(self):
        def change(protocol, result, records):
            row = next(row for row in records if row["branch"] == "no_acquisition")
            row["attempts"][0]["admission_allowed"] = True

        with self.assertRaisesRegex(ValueError, "learning leaked"):
            verifier.verify(self.retained(mutate=change), check_sources=False)

    def test_missing_reserved_or_development_task_cannot_disappear(self):
        def change(protocol, result, records):
            records.pop()

        with self.assertRaisesRegex(ValueError, "Missing/extra task records"):
            verifier.verify(self.retained(mutate=change), check_sources=False)

    def test_positive_gate_cannot_replace_a_failed_result(self):
        def change(protocol, result, records):
            result["summary"]["gates"]["practical_benefit"] = True

        with self.assertRaisesRegex(ValueError, "registered gate changed"):
            verifier.verify(self.retained(mutate=change), check_sources=False)

    def test_failed_primitive_work_is_charged_exactly(self):
        program = {
            "version": "safe-transform-v1",
            "ops": [
                {"op": "prefix", "value": "value="},
                {"op": "decimal"},
                {"op": "format"},
            ],
        }
        actual = verifier.outcome(program, ["missing"], ["1"], self.protocol["config"])
        self.assertFalse(actual["passed"])
        self.assertEqual(actual["tool_units"], 2)
        self.assertEqual(actual["outputs"], [None])

    def test_unregistered_executable_is_not_a_skill(self):
        program = {
            "version": "safe-transform-v1",
            "ops": [{"op": "exec", "value": "anything"}],
        }
        with self.assertRaisesRegex(ValueError, "unregistered"):
            verifier.outcome(program, ["1"], ["1"], self.protocol["config"])


if __name__ == "__main__":
    unittest.main()
