"""Isolated local fixtures for the live proof method; no production attestation."""

import copy
import hashlib
import json
import shutil
import sqlite3
import time
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_skill_tasks import execution_task, provider, task

from cognition.skill_expressions import DEFAULT_EXPRESSION_CONFIG
from cognition.worker import Organism
from commerce.action_loop import ActionLoop
from commerce.commissioning import Commissioner
from commerce.ledger import Ledger
from commerce.task_runtime import run_actions
from commerce.tasks import digest
from scripts import verify_live_skills as verifier


def fixture(directory):
    """Execute real learning, correct serving and buyer-rejected serving jobs."""
    data, public = directory / "data", directory / "public"
    (data / "commerce").mkdir(parents=True)
    (public / "commerce").mkdir(parents=True)
    (public / "cognition").mkdir(parents=True)
    ledger = Ledger(data / "commerce/commerce.sqlite3")
    Commissioner(ledger)
    ActionLoop(ledger)
    organism = Organism(data / "cognition", "live-verifier-isolated-fixture")
    config = copy.deepcopy(DEFAULT_EXPRESSION_CONFIG)
    config.update(
        operations=[
            {"op": "field", "index": 0},
            {"op": "field", "index": 1},
            {"op": "decimal"},
            {"op": "add"},
            {"op": "format"},
        ],
        max_rows=16,
        max_macros=0,
    )
    controls = provider()
    controls.update(
        config=config, maximum_work_per_job=100000, maximum_work_per_day=1000000
    )

    def rows(start, count):
        inputs = [
            json.dumps([str(i), str(i * 3 + 1), "private-fixture-row", "unused"])
            for i in range(start, start + count)
        ]
        return inputs, [str(i * 4 + 1) for i in range(start, start + count)]

    def run(contract):
        organism.agency_tasks = [contract]
        for _ in range(5):
            organism.cycle([])
        original = organism.journal.recent("decision")[0]
        result = run_actions(
            ledger,
            SimpleNamespace(),
            SimpleNamespace(enabled=False),
            None,
            "",
            {"planning_enabled": True, "tasks": [contract]},
            {"enabled": False, "adapters": [controls]},
            {
                "phase": "running",
                "committed_tick": original["tick"],
                "agency": {
                    "committed_feedback_cursor": organism.committed_agency_cursor
                },
            },
            [original],
            time.time(),
        )
        organism.task_feedback(result["feedback"])
        organism.save()
        return next(
            item
            for item in result["tasks"]
            if item["contract_sha256"] == digest(contract)
        )

    try:
        learning = task("isolated-learning")
        learning["acceptance"].update(config=config, maximum_work=100000)
        for phase, start, count in (
            ("training", 10, 4),
            ("validation", 100, 8),
            ("heldout", 200, 12),
        ):
            inputs, outputs = rows(start, count)
            learning["acceptance"][phase + "_inputs"] = inputs
            learning["acceptance"][phase + "_outputs"] = outputs
        learned = run(learning)
        if not learned["acceptance"]["passed"]:
            raise AssertionError("Isolated fixture failed its actual learning job")
        serving = execution_task(learned["acceptance"]["program_sha256"])
        inputs, outputs = rows(500, 6)
        serving["acceptance"].update(
            config=config, inputs=inputs, expected_outputs=outputs, maximum_work=100000
        )
        if not run(serving)["acceptance"]["passed"]:
            raise AssertionError("Isolated fixture failed its actual serving job")
        rejected = execution_task(
            learned["acceptance"]["program_sha256"], ident="isolated-wrong-format"
        )
        inputs, outputs = rows(700, 6)
        rejected["acceptance"].update(
            config=config,
            inputs=inputs,
            # Deliberately request a different target format from the selected
            # retained sum program. The actual buyer must reject the delivery.
            expected_outputs=[str(int(value) + 5) for value in outputs],
            maximum_work=100000,
        )
        rejected_result = run(rejected)
        if (
            rejected_result["acceptance"]["passed"]
            or rejected_result["outcome"]["reward"] >= 0
        ):
            raise AssertionError(
                "Isolated wrong-format job was not negatively evaluated"
            )
        for component, value in (
            (
                "commerce",
                {
                    "phase": "guarded",
                    "policy": {"enabled": False},
                    "financial_execution": False,
                    "actions": {"execution_enabled": False},
                },
            ),
            ("cognition", {"phase": "running"}),
        ):
            value["updated_utc"] = datetime.now(timezone.utc).isoformat()
            (public / component / "status.json").write_text(json.dumps(value))
        (directory / "release.json").write_text(json.dumps({"fixture": True}))
        return [learning, serving, rejected]
    finally:
        ledger.db.close()
        organism.journal.db.close()


class LiveSkillProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(".test-state") / ("live-skill-proof-" + uuid.uuid4().hex)
        cls.base.mkdir(parents=True)
        cls.contracts = fixture(cls.base)

    def setUp(self):
        # Each mutation owns a new retained copy, so cases cannot contaminate
        # each other or alter the actual fixture computation's original record.
        self.directory = self.base / uuid.uuid4().hex
        for component in ("commerce", "cognition"):
            destination = self.directory / "data" / component
            destination.mkdir(parents=True)
            name = component + ".sqlite3"
            source = sqlite3.connect(self.base / "data" / component / name)
            target = sqlite3.connect(destination / name)
            source.backup(target)
            source.close()
            target.close()
            cache = self.directory / "public" / component
            cache.mkdir(parents=True)
            shutil.copyfile(
                self.base / "public" / component / "status.json", cache / "status.json"
            )
        shutil.copyfile(self.base / "release.json", self.directory / "release.json")
        self.args = verifier.configure_paths(
            SimpleNamespace(
                data_root=str(self.directory / "data"),
                public_root=str(self.directory / "public"),
                release_file=self.directory / "release.json",
                source_root=Path(__file__).resolve().parents[1],
                source_commit="a" * 40,
                provider="included-skills",
                skill_source="nuria:public-repair-test",
                cognition_service="fixture-cognition.service",
                commerce_service="fixture-commerce.service",
                baseline_json=None,
                minimum_learning=1,
                minimum_serving=1,
                expected_learning=1,
                expected_serving=2,
            )
        )

    def collect(self):
        # Service/process attestation cannot exist in a unit-test fixture.
        # Only that external check is replaced; databases, work, safe-program
        # execution and neural receipts below come from the actual local loop.
        with patch.object(
            verifier,
            "source_identity",
            return_value={
                "fixture_only": True,
                "scope": "Isolated computation; no production runtime attestation",
                "commit": "a" * 40,
            },
        ):
            return verifier.collect(self.args)

    def mutate(self, database, sql, values=()):
        with (
            closing(
                sqlite3.connect(getattr(self.args, database + "_db"))
            ) as connection,
            connection,
        ):
            connection.execute(sql, values)

    def test_actual_fixture_roundtrip_and_public_projection(self):
        private, public = self.collect()
        self.assertTrue(public["complete"])
        self.assertTrue(public["source"]["fixture_only"])
        self.assertEqual(public["counts"]["checked_and_neurally_acknowledged_jobs"], 3)
        self.assertEqual(public["counts"]["exact_final_rows"], 18)
        self.assertEqual(public["counts"]["independently_checked_final_rows"], 24)
        self.assertEqual(public["financial"]["new_financial_activity"], 0)
        self.assertEqual(len(public["retained_programs"]), 1)
        serialized = verifier.canonical(public)
        for contract in self.contracts:
            self.assertNotIn(contract["evaluation_nonce"], serialized)
            rule = contract["acceptance"]
            for field in (
                "training_inputs",
                "validation_inputs",
                "heldout_inputs",
                "inputs",
            ):
                for row in rule.get(field, []):
                    self.assertNotIn(row, serialized)
        for forbidden in (
            "private-fixture-row",
            "heldout_inputs",
            "expected_outputs",
            "evaluation_nonce",
            "operations",
            str(self.directory.resolve()),
            str(Path(__file__).resolve().parents[1]),
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("program", public["retained_programs"][0])
        self.assertIn("read_only_sources", private)

    def test_failed_buyer_check_retains_actual_work_and_negative_origin_feedback(self):
        _, public = self.collect()
        self.assertTrue(public["complete"])
        self.assertEqual(public["counts"]["serving_jobs"], 2)
        self.assertEqual(public["counts"]["serving_passed"], 1)
        self.assertEqual(public["counts"]["serving_failed"], 1)
        self.assertEqual(public["counts"]["failed_jobs_retained"], 1)
        failed = next(job for job in public["jobs"] if not job["passed"])
        self.assertEqual(failed["evaluation_rows"], 6)
        self.assertEqual(failed["exact_rows"], 0)
        self.assertLess(failed["reward"], 0)
        self.assertGreater(failed["provider_work"], 0)
        self.assertGreater(failed["buyer_final_check_work"], 0)
        self.assertEqual(
            failed["total_job_work"],
            failed["provider_work"] + failed["buyer_final_check_work"],
        )
        with closing(sqlite3.connect(self.args.commerce_db)) as db:
            actual = json.loads(
                db.execute(
                    "SELECT outcome FROM action_tasks WHERE id=?", (failed["job_id"],)
                ).fetchone()[0]
            )
        self.assertEqual(failed["reward"], actual["reward"])
        with closing(sqlite3.connect(self.args.cognition_db)) as db:
            origin = json.loads(
                db.execute(
                    "SELECT payload FROM records WHERE hash=?",
                    (failed["neural_outcome_hash"],),
                ).fetchone()[0]
            )
        self.assertEqual(origin["intent_id"], failed["intent_id"])
        self.assertEqual(origin["reward"], actual["reward"])
        self.assertEqual(origin["result_sha256"], failed["artifact_sha256"])
        self.assertEqual(
            public["counts"]["actual_provider_tool_units"],
            sum(job["provider_work"] for job in public["jobs"]),
        )

    def test_altered_delivery_and_source_are_refused(self):
        with closing(sqlite3.connect(self.args.commerce_db)) as db, db:
            ident, raw = db.execute(
                "SELECT job_id,delivery FROM local_skill_jobs ORDER BY job_id LIMIT 1"
            ).fetchone()
            delivery = json.loads(raw)
            delivery["contract_sha256"] = "f" * 64
            db.execute(
                "UPDATE local_skill_jobs SET delivery=? WHERE job_id=?",
                (verifier.canonical(delivery), ident),
            )
        with self.assertRaisesRegex(ValueError, "bound to this task"):
            self.collect()
        self.mutate(
            "commerce",
            "UPDATE local_skill_jobs SET delivery=? WHERE job_id=?",
            (raw, ident),
        )
        self.mutate(
            "commerce",
            "UPDATE local_skill_state SET source_id=?",
            ("fixture:foreign-source",),
        )
        with self.assertRaisesRegex(ValueError, "source is missing"):
            self.collect()

    def test_rehashed_wrong_neural_reward_is_refused(self):
        with closing(sqlite3.connect(self.args.cognition_db)) as db, db:
            seq, previous, raw = db.execute(
                "SELECT seq,previous_hash,payload FROM records WHERE kind='task_outcome' ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            payload = json.loads(raw)
            payload["reward"] = -0.75
            encoded = verifier.canonical(payload)
            digest = hashlib.sha256((previous + encoded).encode()).hexdigest()
            db.execute(
                "UPDATE records SET payload=?,hash=? WHERE seq=?",
                (encoded, digest, seq),
            )
            metadata = json.loads(
                db.execute("SELECT metadata FROM checkpoint").fetchone()[0]
            )
            self.assertEqual(metadata["record_seq"], seq)
            metadata["record_head"] = digest
            db.execute(
                "UPDATE checkpoint SET metadata=?", (verifier.canonical(metadata),)
            )
        with self.assertRaisesRegex(
            ValueError, "differs from the actual commerce feedback"
        ):
            self.collect()

    def test_missing_financial_schema_is_unknown_not_zero(self):
        # Rename rather than delete a fixture table, preserving the record.
        self.mutate(
            "commerce",
            "ALTER TABLE authorizations RENAME TO fixture_archived_authorizations",
        )
        with self.assertRaisesRegex(ValueError, "financial ledger table is missing"):
            self.collect()
        self.mutate(
            "commerce",
            "ALTER TABLE fixture_archived_authorizations RENAME TO authorizations",
        )
        self.mutate(
            "commerce",
            "ALTER TABLE signing_requests RENAME COLUMN created TO fixture_archived_created",
        )
        with self.assertRaisesRegex(ValueError, "financial ledger column is missing"):
            self.collect()

    def test_release_commit_mismatch_fails_before_process_inspection(self):
        with self.assertRaisesRegex(ValueError, "expected commit"):
            verifier.source_identity(
                self.args.source_root, {"commit": "b" * 40}, self.args.source_commit, {}
            )


if __name__ == "__main__":
    unittest.main()
