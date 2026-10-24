"""Finite active courses preserve buyer partitions without routing metadata."""

import copy
import hashlib
import json
import stat
import unittest
import uuid
from pathlib import Path

from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG
from cognition.skills import SkillLibrary
from commerce.tasks import (
    active_verifier_ceiling,
    contractor_brief,
    digest,
    validate_task,
)
from scripts.build_active_skill_queue import build, write_manifest

PROVIDER = "fixture-active-provider"
SOURCE = "fixture:active-observation-course"


def file_entry(index, start, operation):
    pairs = [(number, number * 3 + 7) for number in range(start, start + 24)]
    return {
        "phase": "fixture-original-phase",
        "format_index": index,
        "recipe": {"operation": operation, "private_marker": "fixture-recipe-only"},
        "row_ids": list(range(start, start + 24)),
        "inputs": [
            json.dumps([str(left), str(right), "0.2", "0.8"]) for left, right in pairs
        ],
        "outputs": [
            str(left + right if operation == "add" else left * right)
            for left, right in pairs
        ],
    }


class ActiveQueueBuilderTests(unittest.TestCase):
    def setUp(self):
        config = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
        config["max_macros"] = 0
        self.corpus = {
            "schema": "unit-fixture-corpus",
            "source": {
                "description": "Deterministic unit fixture, not public observations"
            },
            "config": config,
            "tasks": [
                file_entry(987641, 1, "add"),
                file_entry(987642, 100, "multiply"),
            ],
        }

    def build(self, **controls):
        return build(self.corpus, PROVIDER, SOURCE, **controls)

    def test_semantically_different_files_have_no_caller_routing_identity(self):
        manifest = self.build(positions=[1, 0], lanes=1)
        self.assertEqual(len(manifest["tasks"]), 2)
        self.assertEqual(manifest["source"]["source_id"], SOURCE)
        self.assertIn("development", manifest["source"]["scope"].lower())
        self.assertEqual(
            [item["id"] for item in manifest["tasks"]], ["active-record-0"] * 2
        )
        nonces = set()
        for task, source in zip(
            manifest["tasks"], reversed(self.corpus["tasks"]), strict=True
        ):
            validate_task(task)
            rule = task["acceptance"]
            self.assertEqual(rule["type"], "transform_active")
            self.assertEqual(rule["diagnostic_inputs"], source["inputs"][:4])
            self.assertEqual(rule["heldout_inputs"], source["inputs"][4:])
            self.assertEqual(len(rule["heldout_inputs"]), 20)
            self.assertEqual(rule["minimum_evidence"], 2)
            self.assertEqual(task["maximum_micro_usd"], 0)
            self.assertTrue(
                set(rule["diagnostic_inputs"]).isdisjoint(rule["heldout_inputs"])
            )
            nonces.add(task["evaluation_nonce"])
            brief = contractor_brief(task, "fixture-job")
            serialized = json.dumps(brief)
            for forbidden in (
                "format_index",
                "recipe",
                "skill_id",
                "row_ids",
                "phase",
                "diagnostic_outputs",
                "heldout_inputs",
                "heldout_outputs",
                "evaluation_nonce",
                "fixture-recipe-only",
                str(source["format_index"]),
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(
                brief["acceptance"]["diagnostic_inputs"], source["inputs"][:4]
            )
        self.assertEqual(len(nonces), 2)

    def test_duplicates_are_removed_before_diagnostic_and_final_split(self):
        entry = self.corpus["tasks"][0]
        for key in ("inputs", "outputs", "row_ids"):
            entry[key].insert(1, entry[key][0])
        result = self.build(positions=[0])
        rule = result["tasks"][0]["acceptance"]
        self.assertEqual(len(rule["diagnostic_inputs"]), 4)
        self.assertEqual(len(rule["heldout_inputs"]), 20)
        self.assertEqual(result["source"]["files"][0]["duplicate_rows_removed"], 1)
        self.assertEqual(
            len(set(rule["diagnostic_inputs"] + rule["heldout_inputs"])), 24
        )

    def test_conflicting_labels_or_source_row_identity_are_refused(self):
        entry = self.corpus["tasks"][0]
        entry["inputs"][1] = entry["inputs"][0]
        with self.assertRaisesRegex(ValueError, "conflicting targets"):
            self.build(positions=[0])
        self.setUp()
        entry = self.corpus["tasks"][0]
        entry["row_ids"][1] = entry["row_ids"][0]
        with self.assertRaisesRegex(ValueError, "conflicting input/output"):
            self.build(positions=[0])

    def test_repeated_source_selection_and_metadata_disguised_duplicate_are_refused(
        self,
    ):
        for positions in ([0, 0], [False], [99], []):
            with self.subTest(positions=positions):
                with self.assertRaises(ValueError):
                    self.build(positions=positions)
        duplicate = copy.deepcopy(self.corpus["tasks"][0])
        duplicate["recipe"] = {"different": "metadata does not create a new file"}
        for key in ("inputs", "outputs", "row_ids"):
            duplicate[key].reverse()
        self.corpus["tasks"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "same deduplicated file"):
            self.build(positions=[0, 2])

    def test_too_few_distinct_rows_and_malformed_numeric_inputs_are_refused(self):
        entry = self.corpus["tasks"][0]
        for key in ("inputs", "outputs", "row_ids"):
            entry[key] = entry[key][:11] + [entry[key][0]]
        with self.assertRaisesRegex(ValueError, "at least eight"):
            self.build(positions=[0])
        self.setUp()
        for malformed in ("not-json", "[]", '["1","2",3,"4"]'):
            self.corpus["tasks"][0]["inputs"][0] = malformed
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    self.build(positions=[0])

    def test_source_and_finite_work_authority_are_explicit(self):
        for provider, source in (
            ("local-record-skills-v2", SOURCE),
            (PROVIDER, "nuria:public-record-repair-v2"),
            ("bad provider", SOURCE),
        ):
            with self.assertRaisesRegex(ValueError, "identity|namespace"):
                build(self.corpus, provider, source)
        for controls in (
            {"maximum_work": True},
            {"maximum_work": 1},
            {"synthesis_work": 0},
            {"maximum_probes": 5},
            {"minimum_evidence": 0},
        ):
            with self.subTest(controls=controls):
                with self.assertRaises(ValueError):
                    self.build(**controls)
        result = self.build(maximum_work=400000, synthesis_work=90000)
        ceiling = result["source"]["cost_authority"]
        self.assertEqual(ceiling["maximum_course_work"], 800000)
        self.assertEqual(
            ceiling["reserved_final_verifier_work_per_job"],
            active_verifier_ceiling(self.corpus["config"]),
        )
        self.assertEqual(ceiling["financial_micro_usd"], 0)
        self.assertTrue(
            all(
                task["acceptance"]["maximum_work"] == 400000 for task in result["tasks"]
            )
        )

    def test_explicit_hash_control_has_independent_labels_and_no_routing_oracle(self):
        original = copy.deepcopy(self.corpus)
        result = self.build(
            positions=[0], include_hash_control=True, control_position=1
        )
        self.assertEqual(self.corpus, original)
        self.assertEqual(len(result["tasks"]), 2)
        self.assertEqual(result["source"]["supported_development_tasks"], 1)
        self.assertEqual(result["source"]["unsupported_negative_controls"], 1)
        self.assertEqual(result["source"]["customer_tasks"], 0)
        self.assertEqual(
            result["source"]["cost_authority"]["maximum_course_work"], 1000000
        )
        control = result["tasks"][-1]
        rule = control["acceptance"]
        source_inputs = original["tasks"][1]["inputs"]
        actual_inputs = rule["diagnostic_inputs"] + rule["heldout_inputs"]
        actual_outputs = rule["diagnostic_outputs"] + rule["heldout_outputs"]
        self.assertEqual(actual_inputs, source_inputs)
        self.assertEqual(
            actual_outputs,
            [
                hashlib.sha256(value.encode("utf-8")).hexdigest()
                for value in source_inputs
            ],
        )
        self.assertEqual(len(rule["diagnostic_inputs"]), 4)
        self.assertEqual(len(rule["heldout_inputs"]), 20)
        self.assertTrue(
            set(rule["diagnostic_inputs"]).isdisjoint(rule["heldout_inputs"])
        )
        self.assertFalse(
            any(op["op"] == "sha256" for op in rule["config"]["operations"])
        )
        commitment = result["source"]["files"][-1]
        self.assertEqual(commitment["kind"], "unsupported-sha256-negative-control")
        self.assertFalse(commitment["supported_acquisition"])
        self.assertFalse(commitment["customer_task"])
        self.assertEqual(commitment["source_position"], 1)
        self.assertEqual(commitment["entry_sha256"], digest(original["tasks"][1]))
        self.assertEqual(
            commitment["deduplicated_io_sha256"],
            digest(list(zip(actual_inputs, actual_outputs, strict=True))),
        )
        self.assertNotEqual(
            commitment["original_deduplicated_io_sha256"],
            commitment["deduplicated_io_sha256"],
        )
        brief = contractor_brief(control, "fixture-control-job")
        serialized = json.dumps(brief)
        for forbidden in (
            "sha256-negative-control",
            "hashlib",
            "label_derivation",
            "supported_acquisition",
            "format_index",
            "recipe",
            "diagnostic_outputs",
            "heldout_inputs",
            "heldout_outputs",
            "987642",
            actual_outputs[0],
        ):
            self.assertNotIn(forbidden, serialized)
        validate_task(control)
        proposal = SkillLibrary(SOURCE, rule["config"]).solve(
            rule["diagnostic_inputs"],
            rule["diagnostic_outputs"],
            mode="retained_factored",
            budget=2000,
        )
        self.assertIsNone(proposal["program"])
        self.assertGreater(proposal["work"]["total"], 0)
        self.assertLessEqual(proposal["work"]["total"], 2000)

    def test_hash_control_requires_explicit_separate_source_and_task_capacity(self):
        for controls in (
            {"include_hash_control": True},
            {"include_hash_control": True, "control_position": 0},
            {"include_hash_control": True, "control_position": True},
            {"include_hash_control": True, "control_position": 99},
            {"include_hash_control": False, "control_position": 1},
            {"include_hash_control": 1, "control_position": 1},
        ):
            with self.subTest(controls=controls):
                with self.assertRaises(ValueError):
                    self.build(positions=[0], **controls)
        self.corpus["tasks"] = [
            file_entry(index, 1 + 30 * index, "add") for index in range(256)
        ]
        with self.assertRaises(ValueError):
            self.build(include_hash_control=True, control_position=255)
        result = self.build(
            positions=list(range(255)), include_hash_control=True, control_position=255
        )
        self.assertEqual(len(result["tasks"]), 256)

    def test_control_cannot_disguise_reused_source_file_with_changed_metadata(self):
        duplicate = copy.deepcopy(self.corpus["tasks"][0])
        duplicate["format_index"] = 123456
        self.corpus["tasks"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "same deduplicated file"):
            self.build(positions=[0], include_hash_control=True, control_position=2)

    def test_private_manifest_creation_never_overwrites_previous_contracts(self):
        directory = Path(".test-state") / ("active-builder-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700, parents=True)
        path = directory.resolve() / "catalogue.json"
        manifest = self.build()
        identity = write_manifest(path, manifest)
        self.assertEqual(len(identity), 64)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), manifest)
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_manifest(path, self.build())
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
