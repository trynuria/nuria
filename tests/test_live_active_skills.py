"""Actual local diagnostic episodes checked without production attestation."""

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

from test_active_skill_tasks import provider, task

from cognition.worker import Organism
from commerce.action_loop import ActionLoop
from commerce.commissioning import Commissioner
from commerce.ledger import Ledger
from commerce.task_runtime import run_actions
from scripts import verify_live_active_skills as verifier


def fixture(directory, *, include_unsupported=False):
    data, public = directory / "data", directory / "public"
    (data / "commerce").mkdir(parents=True)
    for component in ("commerce", "cognition"):
        (public / component).mkdir(parents=True)
    ledger = Ledger(data / "commerce/commerce.sqlite3")
    Commissioner(ledger)
    ActionLoop(ledger)
    organism = Organism(data / "cognition", "active-proof-fixture")
    controls = {"enabled": False, "adapters": [provider(mode="active_audited")]}
    metadata = json.loads(
        organism.journal.db.execute("SELECT metadata FROM checkpoint").fetchone()[0]
    )
    baseline = {
        "snapshots": {
            "cognition": {
                "checkpoint": {
                    key: metadata[key]
                    for key in ("genesis_utc", "cursor", "record_seq", "record_head")
                }
            },
            "commerce": {
                "journal": {
                    "sequence": ledger.verified_seq,
                    "head": ledger.verified_head,
                }
            },
        }
    }
    contracts = [
        task(ident="first", offset=10),
        task(ident="reuse", offset=40),
        task(ident="rejected", offset=70),
    ]
    contracts[-1]["acceptance"]["heldout_outputs"] = ["private-final-target"] * 8
    if include_unsupported:
        control = task(ident="unsupported-sha256", offset=110)
        for partition in ("diagnostic", "heldout"):
            control["acceptance"][partition + "_outputs"] = [
                hashlib.sha256(value.encode()).hexdigest()
                for value in control["acceptance"][partition + "_inputs"]
            ]
        contracts.append(control)
    try:
        for contract in contracts:
            organism.agency_tasks = [contract]
            for _ in range(5):
                organism.cycle([])
            original = organism.journal.recent("decision")[0]
            for iteration in range(20):
                report = run_actions(
                    ledger,
                    SimpleNamespace(),
                    SimpleNamespace(enabled=False),
                    None,
                    "",
                    {"planning_enabled": iteration == 0, "tasks": [contract]},
                    controls,
                    {
                        "phase": "running",
                        "committed_tick": organism.tick,
                        "agency": {
                            "committed_feedback_cursor": organism.committed_agency_cursor
                        },
                    },
                    [original] if iteration == 0 else [],
                    time.time(),
                )
                selected = [
                    v
                    for v in report["tasks"]
                    if v["contract_sha256"] == verifier.digest(contract)
                ]
                if selected and selected[0]["state"] == "evaluated":
                    break
            else:
                raise AssertionError("Fixture episode did not complete")
            organism.task_feedback(report["feedback"])
            organism.save()
        for component, value in (
            (
                "commerce",
                {
                    "policy": {"enabled": False},
                    "financial_execution": False,
                    "actions": {"execution_enabled": False},
                },
            ),
            ("cognition", {"phase": "running"}),
        ):
            value["updated_utc"] = datetime.now(timezone.utc).isoformat()
            (public / component / "status.json").write_text(json.dumps(value))
        for name, value in (
            ("controls", controls),
            ("baseline", baseline),
            ("release", {"fixture": True}),
        ):
            (directory / (name + ".json")).write_text(json.dumps(value))
    finally:
        ledger.db.close()
        organism.journal.db.close()
    return contracts


class LiveActiveProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(".test-state") / ("live-active-proof-" + uuid.uuid4().hex)
        cls.base.mkdir(parents=True)
        cls.contracts = fixture(cls.base)

    def setUp(self):
        self.directory = self.base / uuid.uuid4().hex
        for component in ("commerce", "cognition"):
            target = self.directory / "data" / component
            target.mkdir(parents=True)
            with (
                closing(
                    sqlite3.connect(
                        self.base / "data" / component / (component + ".sqlite3")
                    )
                ) as source,
                closing(
                    sqlite3.connect(target / (component + ".sqlite3"))
                ) as destination,
            ):
                source.backup(destination)
            cache = self.directory / "public" / component
            cache.mkdir(parents=True)
            shutil.copyfile(
                self.base / "public" / component / "status.json", cache / "status.json"
            )
        for name in ("controls", "baseline", "release"):
            shutil.copyfile(
                self.base / (name + ".json"), self.directory / (name + ".json")
            )
        self.args = verifier.configure_paths(
            SimpleNamespace(
                data_root=self.directory / "data",
                public_root=self.directory / "public",
                release_file=self.directory / "release.json",
                controls_file=self.directory / "controls.json",
                baseline_json=self.directory / "baseline.json",
                source_root=Path(__file__).resolve().parents[1],
                source_commit="a" * 40,
                provider=provider()["id"],
                skill_source=provider()["source_id"],
                cognition_service="fixture",
                commerce_service="fixture",
                minimum_acquired=1,
                minimum_reused=1,
                expected_episodes=3,
            )
        )

    def collect(self):
        with patch.object(
            verifier,
            "source_identity",
            return_value={"fixture_only": True, "commit": "a" * 40},
        ):
            return verifier.collect(self.args)

    def mutate(self, component, sql, values=()):
        with closing(sqlite3.connect(getattr(self.args, component + "_db"))) as db, db:
            db.execute(sql, values)

    def test_actual_loop_and_independent_public_certificate(self):
        private, public = self.collect()
        self.assertTrue(public["complete"])
        self.assertEqual(public["counts"]["actual_episodes"], 3)
        self.assertEqual(public["counts"]["diagnostic_labels_disclosed"], 6)
        self.assertEqual(public["counts"]["failed_episodes_retained"], 1)
        self.assertEqual(public["counts"]["exact_final_rows"], 16)
        self.assertEqual(public["financial"]["new_financial_activity"], 0)
        self.assertTrue(all(public["cognition"]["history_continuation"].values()))
        raw = verifier.canonical(public)
        for contract in self.contracts:
            self.assertNotIn(contract["evaluation_nonce"], raw)
            for name in (
                "diagnostic_inputs",
                "diagnostic_outputs",
                "heldout_inputs",
                "heldout_outputs",
            ):
                for value in contract["acceptance"][name]:
                    if len(value) > 5:
                        self.assertNotIn(value, raw)
        for value in (
            "private-final-target",
            "evaluation_nonce",
            str(self.directory),
            "operations",
        ):
            self.assertNotIn(value, raw)
        self.assertIn("read_only_sources", private)

    def test_changed_probe_action_or_work_is_refused(self):
        self.mutate(
            "commerce", "UPDATE active_skill_actions SET work=work+1 WHERE kind='probe'"
        )
        with self.assertRaises(ValueError):
            self.collect()

    def test_changed_final_program_or_receipt_is_refused(self):
        with closing(sqlite3.connect(self.args.commerce_db)) as db, db:
            ident, raw = db.execute(
                "SELECT job_id,delivery FROM local_skill_jobs ORDER BY rowid LIMIT 1"
            ).fetchone()
            body = json.loads(raw)
            body["result"]["program_sha256"] = "f" * 64
            db.execute(
                "UPDATE local_skill_jobs SET delivery=? WHERE job_id=?",
                (verifier.canonical(body), ident),
            )
        with self.assertRaises(ValueError):
            self.collect()

    def test_missing_neural_ack_stays_incomplete(self):
        with closing(sqlite3.connect(self.args.cognition_db)) as db, db:
            metadata = json.loads(
                db.execute("SELECT metadata FROM checkpoint").fetchone()[0]
            )
            metadata["agency"]["feedback_cursor"] = 0
            db.execute(
                "UPDATE checkpoint SET metadata=?", (verifier.canonical(metadata),)
            )
        _, public = self.collect()
        self.assertFalse(public["complete"])
        self.assertEqual(len(public["gaps"]), 3)

    def test_genesis_or_current_authority_cannot_be_silently_changed(self):
        baseline = json.loads(self.args.baseline_json.read_text())
        baseline["snapshots"]["cognition"]["checkpoint"]["genesis_utc"] = (
            "2000-01-01T00:00:00+00:00"
        )
        self.args.baseline_json.write_text(json.dumps(baseline))
        with self.assertRaisesRegex(ValueError, "genesis"):
            self.collect()

    def test_missing_financial_schema_is_unknown(self):
        self.mutate(
            "commerce",
            "ALTER TABLE authorizations RENAME TO archived_fixture_authorizations",
        )
        with self.assertRaisesRegex(ValueError, "financial ledger table"):
            self.collect()


class LiveActiveAbstentionTests(unittest.TestCase):
    def test_unsupported_sha256_keeps_rejected_costed_and_acknowledged_abstention(self):
        directory = Path(".test-state") / ("live-active-abstention-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        contracts = fixture(directory, include_unsupported=True)
        args = verifier.configure_paths(
            SimpleNamespace(
                data_root=directory / "data",
                public_root=directory / "public",
                release_file=directory / "release.json",
                controls_file=directory / "controls.json",
                baseline_json=directory / "baseline.json",
                source_root=Path(__file__).resolve().parents[1],
                source_commit="a" * 40,
                provider=provider()["id"],
                skill_source=provider()["source_id"],
                cognition_service="fixture",
                commerce_service="fixture",
                minimum_acquired=1,
                minimum_reused=1,
                expected_episodes=4,
            )
        )
        with patch.object(
            verifier,
            "source_identity",
            return_value={"fixture_only": True, "commit": "a" * 40},
        ):
            _, certificate = verifier.collect(args)
        self.assertTrue(certificate["complete"])
        self.assertEqual(certificate["counts"]["checked_and_neurally_acknowledged"], 4)
        control_hash = verifier.digest(contracts[-1])
        public = next(
            value
            for value in certificate["jobs"]
            if value["contract_sha256"] == control_hash
        )
        self.assertFalse(public["passed"])
        self.assertIsNone(public["program_sha256"])
        self.assertIsNone(public["selection"])
        self.assertEqual(public["probes_used"], 2)
        self.assertLess(public["reward"], 0)
        self.assertEqual(public["exact_rows"], 0)
        self.assertGreater(public["provider_work"], 0)
        self.assertGreater(public["buyer_final_work"], 0)
        with closing(sqlite3.connect(args.commerce_db)) as db:
            delivery = json.loads(
                db.execute(
                    "SELECT delivery FROM local_skill_jobs WHERE job_id=?",
                    (public["job_id"],),
                ).fetchone()[0]
            )["result"]
            self.assertEqual(delivery["status"], "abstained")
            self.assertIsNone(delivery["program"])
            self.assertGreater(delivery["work"]["probe_work"], 0)
            self.assertGreater(delivery["work"]["synthesis_work"], 0)
            library = json.loads(
                db.execute("SELECT state FROM local_skill_state").fetchone()[0]
            )
            self.assertEqual(len(library["programs"]), 1)
        with closing(sqlite3.connect(args.cognition_db)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM records WHERE kind='task_outcome' AND json_extract(payload,'$.intent_id')=?",
                    (public["intent_id"],),
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
