"""Finite task queues retain completion independently of neural receipt eviction."""

import hashlib
import json
import time
import unittest
import uuid
from pathlib import Path

from test_action_loop import decision
from test_skill_tasks import task

from cognition.agency import NeuralTaskPolicy
from commerce.action_loop import ActionLoop
from commerce.ledger import Ledger, canonical
from commerce.skill_queue import VERSION, materialize


class SkillQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(".test-state") / ("skill-queue-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.directory.chmod(0o750)
        self.db = self.directory / "commerce.sqlite3"
        self.ledger = Ledger(self.db)
        self.addCleanup(lambda: self.ledger.db.close())
        self.tasks = [task(f"repair-{i % 2}", 10 + i * 20) for i in range(4)]
        self.manifest = self.directory / "manifest.json"
        self.controls = self.directory / "controls.json"
        self.destination = (self.directory / "private-catalogue.json").resolve()
        self.write()

    def write(self, *, enabled=True, tasks=None):
        raw = json.dumps(
            {
                "schema": VERSION,
                "source": {"description": "Unit fixture"},
                "tasks": tasks or self.tasks,
            }
        ).encode()
        self.manifest.write_bytes(raw)
        self.controls.write_text(
            json.dumps(
                {
                    "schema": VERSION,
                    "manifest": str(self.manifest.resolve()),
                    "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                    "provider": "included-skills",
                    "enabled": enabled,
                }
            )
        )

    def evaluate(self, contract, *, passed=True, state="evaluated"):
        loop = ActionLoop(self.ledger)
        now = float(int(time.time()))
        chosen = decision(NeuralTaskPolicy(), [contract], now)
        ident = loop.propose(contract, chosen, now)
        self.ledger.db.execute(
            "UPDATE action_tasks SET state=?,acceptance=? WHERE id=?",
            (state, canonical({"passed": passed}), ident),
        )
        return ident

    def test_one_concrete_job_per_lane_and_no_hidden_data_in_public_progress(self):
        catalog, public = materialize(self.ledger, self.controls, self.destination)
        self.assertEqual(catalog["tasks"], self.tasks[:2])
        self.assertTrue(catalog["planning_enabled"])
        self.assertEqual(public["total"], 4)
        self.assertNotIn("training_inputs", json.dumps(public))
        self.assertNotIn("heldout_outputs", json.dumps(public))
        self.assertEqual(json.loads(self.destination.read_text()), catalog)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o640)

    def test_completed_contract_stays_consumed_after_restart_without_neural_cache(self):
        materialize(self.ledger, self.controls, self.destination)
        self.evaluate(self.tasks[0])
        self.ledger.db.close()
        self.ledger = Ledger(self.db)
        catalog, public = materialize(self.ledger, self.controls, self.destination)
        self.assertEqual(catalog["tasks"], [self.tasks[1], self.tasks[2]])
        self.assertEqual(public["evaluated"], 1)
        self.assertEqual(public["accepted"], 1)
        self.assertEqual(public["remaining"], 3)

    def test_executed_failure_is_retained_but_unexecuted_cancel_can_retry(self):
        materialize(self.ledger, self.controls, self.destination)
        self.evaluate(self.tasks[0], passed=False)
        self.evaluate(self.tasks[1], passed=False, state="cancelled")
        catalog, public = materialize(self.ledger, self.controls, self.destination)
        self.assertNotIn(self.tasks[0], catalog["tasks"])
        self.assertIn(self.tasks[1], catalog["tasks"])
        self.assertEqual(public["evaluated"], 1)
        self.assertEqual(public["accepted"], 0)
        self.assertEqual(public["cancelled_unexecuted_attempts"], 1)

    def test_finish_disables_planning_and_does_not_generate_replacement_work(self):
        for item in self.tasks:
            self.evaluate(item)
        catalog, public = materialize(self.ledger, self.controls, self.destination)
        self.assertEqual(catalog["tasks"], [])
        self.assertFalse(catalog["planning_enabled"])
        self.assertEqual(public["remaining"], 0)

    def test_changed_manifest_and_paid_contract_cannot_gain_authority(self):
        self.manifest.write_text(self.manifest.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "commitment"):
            materialize(self.ledger, self.controls, self.destination)
        self.tasks[0]["maximum_micro_usd"] = 1
        self.write()
        with self.assertRaises(ValueError):
            materialize(self.ledger, self.controls, self.destination)

    def test_disabled_queue_preserves_identity_without_scheduling_tasks(self):
        self.write(enabled=False)
        catalog, public = materialize(self.ledger, self.controls, self.destination)
        self.assertFalse(catalog["planning_enabled"])
        self.assertEqual(catalog["tasks"], [])
        self.assertFalse(public["enabled"])

    def test_buyer_catalogue_cannot_be_published_or_world_accessible(self):
        from unittest.mock import patch

        with patch.dict(
            "os.environ", {"NURIA_COMMERCE_PUBLIC": str(self.directory.resolve())}
        ):
            with self.assertRaisesRegex(ValueError, "public"):
                materialize(self.ledger, self.controls, self.destination)
        self.directory.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            materialize(self.ledger, self.controls, self.destination)
