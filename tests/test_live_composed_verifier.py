"""Real four-audit jobs, independently reexecuted and deliberately corrupted."""

import ast
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

from test_composed_skill_runtime import provider, task

from cognition.worker import Organism
from commerce.action_loop import ActionLoop
from commerce.commissioning import Commissioner
from commerce.ledger import Ledger
from commerce.task_runtime import run_actions
from scripts import verify_live_composed_skills as verifier


def fixture(directory):
    data, public = directory / "data", directory / "public"
    (data / "commerce").mkdir(parents=True)
    for component in ("commerce", "cognition"):
        (public / component).mkdir(parents=True)
    ledger = Ledger(data / "commerce/commerce.sqlite3")
    Commissioner(ledger)
    ActionLoop(ledger)
    organism = Organism(data / "cognition", "composed-proof-fixture")
    controls = {"enabled": False, "adapters": [provider()]}
    ledger.event(
        {"fixture_history": "predates composed course", "financial_transactions": 0}
    )
    for _ in range(5):
        organism.cycle([])
    organism.save()
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
                "journal": dict(
                    zip(
                        ("sequence", "head"),
                        ledger.db.execute(
                            "SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1"
                        ).fetchone(),
                        strict=True,
                    )
                )
            },
        }
    }
    contracts = [
        task(ident="first", offset=10),
        task(ident="reuse", offset=40),
        task(ident="reject", offset=70),
    ]
    contracts[-1]["acceptance"]["heldout_outputs"] = [
        "private-buyer-rejection-target"
    ] * 8
    try:
        for contract in contracts:
            organism.agency_tasks = [contract]
            for _ in range(5):
                organism.cycle([])
            original = organism.journal.recent("decision")[0]
            for iteration in range(40):
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
                    value
                    for value in report["tasks"]
                    if value["contract_sha256"] == verifier.digest(contract)
                ]
                if selected and selected[0]["state"] == "evaluated":
                    break
                if report["errors"]:
                    raise AssertionError(report["errors"])
            else:
                raise AssertionError("Four-audit fixture did not complete")
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
        course = {
            "schema": "nuria.composed-skill-course.v1",
            "provider": verifier.PROVIDER,
            "source_id": verifier.SOURCE,
            "config_sha256": verifier.digest(provider()["config"]),
            "tasks": [
                {"contract_sha256": verifier.digest(contract)} for contract in contracts
            ],
            "expected_count": len(contracts),
        }
        for name, value in (
            ("controls", controls),
            ("baseline", baseline),
            ("release", {"fixture": True}),
            ("course", course),
        ):
            (directory / (name + ".json")).write_text(json.dumps(value))
    finally:
        ledger.db.close()
        organism.journal.db.close()
    return contracts


class LiveComposedVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(".test-state") / ("live-composed-proof-" + uuid.uuid4().hex)
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
        for name in ("controls", "baseline", "release", "course"):
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
                course_manifest=self.directory / "course.json",
                source_root=Path(__file__).resolve().parents[1],
                source_commit="a" * 40,
                provider=verifier.PROVIDER,
                skill_source=verifier.SOURCE,
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

    def test_actual_nested_jobs_and_rejected_outcome_have_private_safe_certificate(
        self,
    ):
        private, public = self.collect()
        self.assertTrue(public["complete"])
        self.assertEqual(public["counts"]["actual_episodes"], 3)
        self.assertEqual(public["counts"]["checked_and_neurally_acknowledged"], 3)
        self.assertEqual(public["counts"]["diagnostic_labels_disclosed"], 12)
        self.assertEqual(public["counts"]["acquired_selections"], 1)
        self.assertEqual(public["counts"]["retained_selections"], 1)
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
                    self.assertNotIn(value, raw)
        self.assertNotIn(str(self.directory), raw)
        self.assertNotIn("private-buyer-rejection-target", raw)
        self.assertIn("read_only_sources", private)

    def test_changed_actual_probe_cost_or_hidden_label_is_refused(self):
        self.mutate(
            "commerce", "UPDATE active_skill_actions SET work=work+1 WHERE kind='probe'"
        )
        with self.assertRaises(ValueError):
            self.collect()

    def test_self_consistent_journal_cannot_substitute_another_buyer_label(self):
        with closing(sqlite3.connect(self.args.commerce_db)) as db, db:
            ident, raw = db.execute(
                "SELECT id,response FROM active_skill_actions WHERE kind='probe' ORDER BY rowid LIMIT 1"
            ).fetchone()
            response = json.loads(raw)
            response["output"] = "forged-returned-label"
            db.execute(
                "UPDATE active_skill_actions SET response=? WHERE id=?",
                (verifier.canonical(response), ident),
            )
            seq, raw = db.execute(
                "SELECT seq,payload FROM events WHERE json_extract(payload,'$.active_action_id')=? AND json_extract(payload,'$.active_phase')='operation_completed'",
                (ident,),
            ).fetchone()
            event = json.loads(raw)
            event["response_sha256"] = verifier.digest(response)
            db.execute(
                "UPDATE events SET payload=? WHERE seq=?",
                (verifier.canonical(event), seq),
            )
            previous = "0" * 64
            for sequence, payload in db.execute(
                "SELECT seq,payload FROM events ORDER BY seq"
            ).fetchall():
                current = hashlib.sha256((previous + payload).encode()).hexdigest()
                db.execute(
                    "UPDATE events SET previous_hash=?,hash=? WHERE seq=?",
                    (previous, current, sequence),
                )
                previous = current
        with self.assertRaisesRegex(ValueError, "Buyer probe disclosed another label"):
            self.collect()

    def test_collection_does_not_change_database_or_control_evidence(self):
        paths = [
            self.args.commerce_db,
            self.args.cognition_db,
            self.args.controls_file,
            self.args.baseline_json,
            self.args.course_manifest,
        ]
        before = {
            str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in paths
        }
        self.collect()
        after = {
            str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in paths
        }
        self.assertEqual(after, before)

    def test_forged_final_rows_or_admission_cannot_claim_correctness(self):
        with closing(sqlite3.connect(self.args.commerce_db)) as db, db:
            ident, raw = db.execute(
                "SELECT job_id,admission FROM local_skill_jobs ORDER BY rowid LIMIT 1"
            ).fetchone()
            admission = json.loads(raw)
            admission["admission"]["correct_rows"] -= 1
            db.execute(
                "UPDATE local_skill_jobs SET admission=? WHERE job_id=?",
                (verifier.canonical(admission), ident),
            )
        with self.assertRaisesRegex(ValueError, "admission"):
            self.collect()

    def test_missing_acknowledgment_or_unresolved_job_stays_incomplete(self):
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

    def test_unresolved_reserved_work_never_disappears_from_denominator(self):
        self.mutate(
            "commerce",
            "UPDATE local_skill_jobs SET state='active_uncertain' WHERE rowid=(SELECT max(rowid) FROM local_skill_jobs)",
        )
        _, public = self.collect()
        self.assertFalse(public["complete"])
        self.assertEqual(public["counts"]["actual_episodes"], 3)
        self.assertEqual(len(public["gaps"]), 1)

    def test_manifest_order_does_not_override_actual_neural_lane_arbitration(self):
        original = json.loads(self.args.course_manifest.read_text())
        _, before = self.collect()
        changed = copy.deepcopy(original)
        changed["tasks"].reverse()
        self.args.course_manifest.write_text(json.dumps(changed))
        _, after = self.collect()
        self.assertTrue(after["complete"])
        self.assertEqual(after["jobs"], before["jobs"])
        self.assertEqual(
            after["course"]["actual_episode_order_sha256"],
            before["course"]["actual_episode_order_sha256"],
        )
        self.assertNotEqual(
            after["course"]["committed_contract_order_sha256"],
            before["course"]["committed_contract_order_sha256"],
        )

    def test_course_membership_configuration_and_denominator_are_required(self):
        original = json.loads(self.args.course_manifest.read_text())
        for mutation in ("membership", "duplicate", "source", "config", "denominator"):
            changed = copy.deepcopy(original)
            if mutation == "membership":
                changed["tasks"][0]["contract_sha256"] = "f" * 64
            elif mutation == "duplicate":
                changed["tasks"][1] = copy.deepcopy(changed["tasks"][0])
            elif mutation == "source":
                changed["source_id"] = "nuria:other-source"
            elif mutation == "config":
                changed["config_sha256"] = "0" * 64
            else:
                changed["expected_count"] = 2
            self.args.course_manifest.write_text(json.dumps(changed))
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    self.collect()
        self.args.course_manifest.write_text(json.dumps(original))
        self.args.expected_episodes = 4
        with self.assertRaises(ValueError):
            self.collect()

    def test_unproposed_committed_contract_remains_in_complete_denominator(self):
        course = json.loads(self.args.course_manifest.read_text())
        course["tasks"].append({"contract_sha256": "f" * 64})
        course["expected_count"] = 4
        self.args.course_manifest.write_text(json.dumps(course))
        self.args.expected_episodes = 4
        _, public = self.collect()
        self.assertFalse(public["complete"])
        self.assertEqual(public["course"]["expected_episodes"], 4)
        self.assertEqual(public["counts"]["actual_episodes"], 3)

    def test_runtime_identity_and_four_audit_authority_cannot_be_silently_swapped(self):
        self.mutate(
            "commerce", "UPDATE active_skill_episodes SET terms_sha256=?", ("0" * 64,)
        )
        with self.assertRaisesRegex(ValueError, "implementation identity"):
            self.collect()

    def test_neural_genesis_and_existing_prefix_are_preserved(self):
        baseline = json.loads(self.args.baseline_json.read_text())
        baseline["snapshots"]["cognition"]["checkpoint"]["record_head"] = "0" * 64
        self.args.baseline_json.write_text(json.dumps(baseline))
        with self.assertRaisesRegex(ValueError, "history or genesis"):
            self.collect()

    def test_missing_financial_evidence_is_unknown_and_collector_imports_no_learner(
        self,
    ):
        self.mutate(
            "commerce",
            "ALTER TABLE authorizations RENAME TO archived_fixture_authorizations",
        )
        with self.assertRaisesRegex(ValueError, "financial ledger table"):
            self.collect()
        source = Path(verifier.__file__).read_text()
        imports = [
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertFalse(
            any(name and name.startswith(("cognition", "commerce")) for name in imports)
        )
        self.assertIn("scripts.verify_active_skill_results", imports)


if __name__ == "__main__":
    unittest.main()
