"""Finite authored courses preserve four-answer authority and private labels."""

import copy
import hashlib
import json
import os
import stat
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from commerce.composed_skill_runtime import (
    COMPOSED_POLICY,
    COMPOSED_PROVIDER,
    COMPOSED_SOURCE,
)
from commerce.ledger import Ledger
from commerce.skill_queue import VERSION, materialize
from commerce.tasks import (
    active_verifier_ceiling,
    contractor_brief,
    digest,
    validate_task,
)
from scripts.build_composed_skill_queue import (
    COURSE_SCHEMA,
    build,
    proof_manifest,
    write_manifests,
)
from scripts.build_guarded_skill_tasks import generate


class ComposedSkillQueueTests(unittest.TestCase):
    def setUp(self):
        # Existing development seed only; no reserved qualification access.
        self.corpus = generate(0, count=5)

    def test_entries_stay_ordered_with_four_distinct_audits_and_twenty_four_final_rows(
        self,
    ):
        original = copy.deepcopy(self.corpus)
        queue, course = build(self.corpus)
        self.assertEqual(self.corpus, original)
        self.assertEqual(queue["schema"], VERSION)
        self.assertEqual(course["schema"], COURSE_SCHEMA)
        self.assertEqual(course["expected_count"], 5)
        self.assertEqual(
            set(course),
            {
                "schema",
                "provider",
                "source_id",
                "config_sha256",
                "tasks",
                "expected_count",
            },
        )
        self.assertEqual(
            [value["id"] for value in queue["tasks"]],
            [
                "composed-record-0",
                "composed-record-1",
                "composed-record-2",
                "composed-record-3",
                "composed-record-0",
            ],
        )
        self.assertEqual(queue["source"]["customer_tasks"], 0)
        self.assertEqual(queue["source"]["external_observation_tasks"], 0)
        self.assertIn("authored synthetic", queue["source"]["scope"])
        for contract, entry, commitment in zip(
            queue["tasks"], self.corpus["tasks"], course["tasks"], strict=True
        ):
            validate_task(contract)
            rule = contract["acceptance"]
            self.assertEqual(rule["diagnostic_inputs"], entry["diagnostic_inputs"][:4])
            self.assertEqual(
                rule["diagnostic_outputs"], entry["diagnostic_outputs"][:4]
            )
            self.assertEqual(rule["heldout_inputs"], entry["score_inputs"])
            self.assertEqual(rule["heldout_outputs"], entry["score_outputs"])
            self.assertEqual(len(rule["heldout_inputs"]), 24)
            self.assertEqual(len(set(rule["diagnostic_inputs"])), 4)
            self.assertTrue(
                set(rule["diagnostic_inputs"]).isdisjoint(rule["heldout_inputs"])
            )
            self.assertEqual((rule["minimum_evidence"], rule["maximum_probes"]), (4, 4))
            self.assertEqual(rule["policy"], COMPOSED_POLICY)
            self.assertEqual(rule["source_id"], COMPOSED_SOURCE)
            self.assertEqual(contract["provider"], COMPOSED_PROVIDER)
            self.assertEqual(contract["maximum_micro_usd"], 0)
            self.assertEqual(commitment, {"contract_sha256": digest(contract)})

    def test_briefs_hide_recipes_programs_families_final_targets_and_control_identity(
        self,
    ):
        queue, _ = build(self.corpus, include_hash_control=True, control_position=4)
        for contract, entry in zip(queue["tasks"], self.corpus["tasks"], strict=True):
            brief = contractor_brief(contract, "fixture-job")
            encoded = json.dumps(brief, sort_keys=True)
            for forbidden in (
                "private_recipe",
                "program_sha256",
                "family",
                "row_ids",
                "source_position",
                "entry_sha256",
                "supported_acquisition",
                "unsupported-sha256",
                "hashlib",
                "diagnostic_outputs",
                "heldout_inputs",
                "heldout_outputs",
                "evaluation_nonce",
                contract["evaluation_nonce"],
                entry["id"],
            ):
                self.assertNotIn(forbidden, encoded)
            for private_input in entry["score_inputs"]:
                self.assertNotIn(private_input, json.dumps(brief))
            self.assertEqual(
                brief["acceptance"]["diagnostic_inputs"], entry["diagnostic_inputs"][:4]
            )

    def test_optional_last_control_has_independently_derived_hash_targets(self):
        queue, course = build(
            self.corpus, include_hash_control=True, control_position=4
        )
        self.assertEqual(course["expected_count"], 5)
        self.assertEqual(queue["source"]["supported_engineering_tasks"], 4)
        self.assertEqual(queue["source"]["unsupported_negative_controls"], 1)
        rule = queue["tasks"][-1]["acceptance"]
        entry = self.corpus["tasks"][-1]
        inputs = entry["diagnostic_inputs"][:4] + entry["score_inputs"]
        self.assertEqual(rule["diagnostic_inputs"] + rule["heldout_inputs"], inputs)
        self.assertEqual(
            rule["diagnostic_outputs"] + rule["heldout_outputs"],
            [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in inputs],
        )
        self.assertNotEqual(rule["heldout_outputs"], entry["score_outputs"])
        self.assertEqual(
            queue["source"]["files"][-1]["kind"], "unsupported-sha256-negative-control"
        )
        self.assertFalse(queue["source"]["files"][-1]["supported_acquisition"])
        self.assertEqual(
            queue["source"]["cost_authority"]["maximum_course_work"], 5 * 1050000
        )
        self.assertEqual(
            course["tasks"][-1]["contract_sha256"], digest(queue["tasks"][-1])
        )

    def test_control_cannot_filter_tasks_or_reuse_a_supported_source_entry(self):
        for controls in (
            {"include_hash_control": True},
            {"include_hash_control": True, "control_position": 0},
            {"include_hash_control": True, "control_position": True},
            {"include_hash_control": True, "control_position": 99},
            {"include_hash_control": False, "control_position": 4},
            {"include_hash_control": 1, "control_position": 4},
        ):
            with self.subTest(controls=controls), self.assertRaises(ValueError):
                build(self.corpus, **controls)

    def test_real_text_verifier_reservation_and_source_authority_are_enforced(self):
        queue, _ = build(self.corpus)
        ceiling = queue["source"]["cost_authority"]
        self.assertEqual(
            ceiling["reserved_final_verifier_work_per_job"],
            active_verifier_ceiling(self.corpus["config"]),
        )
        self.assertEqual(ceiling["reserved_final_verifier_work_per_job"], 847424)
        self.assertEqual(ceiling["financial_micro_usd"], 0)
        self.assertEqual(ceiling["maximum_synthesis_work_per_job"], 50000)
        for controls in (
            {"provider": "other"},
            {"source_id": "other:source"},
            {"lanes": 1},
            {"lanes": True},
            {"maximum_work": True},
            {"maximum_work": 847424},
            {"maximum_work": 10000001},
            {"synthesis_work": 0},
            {"synthesis_work": True},
        ):
            with self.subTest(controls=controls), self.assertRaises(ValueError):
                build(self.corpus, **controls)

    def test_changed_provenance_partitions_and_oracle_labels_are_refused(self):
        for change in (
            "schema",
            "seed",
            "count",
            "config",
            "id",
            "overlap",
            "duplicate",
            "target",
            "program",
            "row_id",
        ):
            corpus = copy.deepcopy(self.corpus)
            entry = corpus["tasks"][0]
            if change == "schema":
                corpus["schema"] = "other"
            elif change == "seed":
                corpus["source"]["seed"] = True
            elif change == "count":
                corpus["source"]["task_count"] -= 1
            elif change == "config":
                corpus["config"]["max_rows"] = 31
            elif change == "id":
                entry["id"] = "f" * 64
            elif change == "overlap":
                entry["score_inputs"][0] = entry["diagnostic_inputs"][0]
            elif change == "duplicate":
                entry["diagnostic_inputs"][1] = entry["diagnostic_inputs"][0]
            elif change == "target":
                entry["diagnostic_outputs"][0] = "not-the-target"
            elif change == "program":
                entry["private_recipe"]["program_sha256"] = "f" * 64
            else:
                entry["row_ids"]["score"][0] = "f" * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                build(corpus)

    def test_random_nonces_bind_contracts_and_duplicate_nonce_is_rejected(self):
        first, first_course = build(self.corpus)
        second, second_course = build(self.corpus)
        first_nonces = {task["evaluation_nonce"] for task in first["tasks"]}
        second_nonces = {task["evaluation_nonce"] for task in second["tasks"]}
        self.assertEqual(len(first_nonces), 5)
        self.assertTrue(first_nonces.isdisjoint(second_nonces))
        self.assertNotEqual(first_course["tasks"], second_course["tasks"])
        with patch(
            "scripts.build_composed_skill_queue.secrets.token_hex",
            return_value="f" * 64,
        ):
            with self.assertRaisesRegex(ValueError, "nonce"):
                build(self.corpus)

    def test_proof_rejects_source_and_policy_substitution(self):
        queue, course = build(self.corpus)
        self.assertEqual(proof_manifest(queue), course)
        changed = copy.deepcopy(queue)
        changed["source"]["source_id"] = "other:source"
        with self.assertRaisesRegex(ValueError, "source"):
            proof_manifest(changed)
        changed = copy.deepcopy(queue)
        changed["tasks"][0]["acceptance"]["minimum_evidence"] = 2
        with self.assertRaises(ValueError):
            proof_manifest(changed)

    def test_private_creation_preserves_every_existing_file(self):
        directory = Path(".test-state") / ("composed-queue-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700, parents=True)
        queue_path, course_path = (
            directory.resolve() / "queue.json",
            directory.resolve() / "course.json",
        )
        queue, course = build(self.corpus)
        identities = write_manifests(queue_path, course_path, queue, course)
        self.assertEqual(stat.S_IMODE(queue_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(course_path.stat().st_mode), 0o600)
        self.assertEqual(
            identities["queue_sha256"],
            hashlib.sha256(queue_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            identities["course_sha256"],
            hashlib.sha256(course_path.read_bytes()).hexdigest(),
        )
        originals = queue_path.read_bytes(), course_path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_manifests(queue_path, course_path, *build(self.corpus))
        self.assertEqual((queue_path.read_bytes(), course_path.read_bytes()), originals)
        with self.assertRaisesRegex(ValueError, "separate files"):
            write_manifests(
                directory.resolve() / "same.json",
                directory.resolve() / "same.json",
                queue,
                course,
            )
        changed = copy.deepcopy(course)
        changed["expected_count"] -= 1
        with self.assertRaisesRegex(ValueError, "commitments"):
            write_manifests(
                directory.resolve() / "other-queue.json",
                directory.resolve() / "other-course.json",
                queue,
                changed,
            )
        self.assertFalse((directory / "other-queue.json").exists())

    def test_public_parent_is_refused_before_writing(self):
        directory = Path(".test-state") / ("composed-public-parent-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o755, parents=True)
        os.chmod(directory, 0o755)
        queue, course = build(self.corpus)
        with self.assertRaisesRegex(ValueError, "private parent"):
            write_manifests(
                directory.resolve() / "queue.json",
                directory.resolve() / "course.json",
                queue,
                course,
            )
        self.assertFalse((directory / "queue.json").exists())

    def test_queue_materializes_four_private_lanes_with_no_financial_authority(self):
        directory = Path(".test-state") / ("composed-materialize-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700, parents=True)
        directory = directory.resolve()
        queue, course = build(self.corpus)
        identities = write_manifests(
            directory / "queue.json", directory / "course.json", queue, course
        )
        controls = {
            "schema": VERSION,
            "manifest": str(directory / "queue.json"),
            "manifest_sha256": identities["queue_sha256"],
            "provider": COMPOSED_PROVIDER,
            "enabled": True,
        }
        (directory / "controls.json").write_text(json.dumps(controls))
        ledger = Ledger(directory / "commerce.sqlite3")
        try:
            catalogue, progress = materialize(
                ledger, directory / "controls.json", directory / "catalogue.json"
            )
            self.assertEqual(len(catalogue["tasks"]), 4)
            self.assertEqual(progress["total"], 5)
            self.assertEqual(progress["remaining"], 5)
            self.assertEqual(progress["active_goal_lanes"], 4)
            self.assertTrue(catalogue["planning_enabled"])
            self.assertEqual(
                ledger.db.execute("SELECT count(*) FROM authorizations").fetchone()[0],
                0,
            )
            second, repeated = materialize(
                ledger, directory / "controls.json", directory / "catalogue.json"
            )
            self.assertEqual(second, catalogue)
            self.assertEqual(repeated, progress)
        finally:
            ledger.db.close()


if __name__ == "__main__":
    unittest.main()
