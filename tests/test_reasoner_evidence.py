"""Offline integrity controls; deterministic fixtures are not model results."""

import copy
import json
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.experience_navigation import VERSION
from cognition.outcome_worker import OutcomeStore, identity
from scripts.package_experience_navigation import read_store
from scripts.verify_reasoner_transfer import (
    audit_requests,
    verify_commitments,
    verify_journals,
)


class ReasonerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/tmp") / ("nuria-evidence-check-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700)
        self.protocol = {
            "conditions": {
                name: {
                    "model": name,
                    "reasoning_effort": "medium" if name == "astra-medium" else "low",
                }
                for name in ("sol-low", "astra-medium")
            }
        }
        self.tasks = [
            {
                "task_id": "Example/1",
                "prompt": "Return one",
                "checker": "assert task_func()==1",
                "source_sha256": "a" * 64,
                "family": "text",
            }
        ]
        self.history = []
        self.seal = {
            "protocol_sha256": identity(self.protocol),
            "records": {},
            "terminal_checks_revealed": False,
            "tasks": 1,
        }
        self.records, self.events = [], []
        for name, configuration in self.protocol["conditions"].items():
            task = self.tasks[0]
            row = {
                "job_id": name,
                "task_id": task["task_id"],
                "prompt": task["prompt"],
                "implementation_sha256": identity(
                    (
                        Path(__file__).resolve().parents[1]
                        / "cognition/experience_navigation.py"
                    ).read_text()
                ),
                "experience_sha256": identity([]),
                "experience_ids": [],
                "method": "retrieval",
                "code": "def task_func(): return 1",
                "accepted": None,
                "checker": None,
                "attempts": [
                    {
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        "content": "{}",
                        "response_sha256": identity("{}"),
                    }
                    for _ in range(3)
                ],
            }
            row["proposed_code_sha256"] = identity(row["code"])
            self.seal["records"][name] = [copy.deepcopy(row)]
            contract = {
                "version": VERSION,
                "implementation_sha256": row["implementation_sha256"],
                "task": task,
                "method": "retrieval",
                "model": configuration,
                "experience": identity([]),
                "grade": False,
                "max_tokens": 2400,
                "max_calls": 3,
                "correction_catalog": "automatic-checked-prior-receipts",
            }
            store = OutcomeStore(self.root / (name + ".sqlite"))
            store.begin(name, contract)
            store.finish(name, row)
            store.accept(name, {"passed": True})
            records, events = read_store(store.path)
            self.records.extend({"condition": name, "record": row} for row in records)
            self.events.extend({"condition": name, **row} for row in events)

    def test_actual_sqlite_journals_bind_both_model_contracts(self):
        verify_commitments(
            self.protocol, self.tasks, self.history, self.seal, self.records
        )
        verify_journals(
            self.protocol,
            self.tasks,
            self.history,
            self.seal,
            self.records,
            self.events,
            [],
        )

    def test_read_only_snapshot_closes_on_success_and_query_failure(self):
        original_connect = sqlite3.connect
        opened = []

        class TrackedConnection(sqlite3.Connection):
            closed = False

            def close(self):
                self.closed = True
                super().close()

        def connect(*args, **kwargs):
            database = original_connect(*args, **kwargs, factory=TrackedConnection)
            opened.append(database)
            return database

        empty = self.root / "empty.sqlite"
        empty.touch()
        with patch(
            "scripts.package_experience_navigation.sqlite3.connect", side_effect=connect
        ):
            read_store(self.root / "sol-low.sqlite")
            with self.assertRaises(sqlite3.OperationalError):
                read_store(empty)
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(database.closed for database in opened))
        for database in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                database.execute("SELECT 1")

    def test_changed_program_and_missing_arm_refuse(self):
        changed = copy.deepcopy(self.records)
        changed[0]["record"]["code"] = "def task_func(): return 2"
        changed[0]["record"]["proposed_code_sha256"] = identity(
            changed[0]["record"]["code"]
        )
        with self.assertRaisesRegex(ValueError, "pre-feedback"):
            verify_commitments(self.protocol, self.tasks, [], self.seal, changed)
        with self.assertRaises(ValueError):
            verify_commitments(
                self.protocol, self.tasks, [], self.seal, self.records[:1]
            )

    def test_relabelled_configuration_refuses_original_contract(self):
        changed = copy.deepcopy(self.protocol)
        changed["conditions"]["astra-medium"]["model"] = "another-model"
        with self.assertRaisesRegex(ValueError, "execution contract"):
            verify_journals(
                changed, self.tasks, [], self.seal, self.records, self.events, []
            )

    def test_missing_acceptance_and_revealed_seal_refuse(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            verify_journals(
                self.protocol,
                self.tasks,
                [],
                self.seal,
                self.records,
                [row for row in self.events if row["event"]["event"] != "acceptance"],
                [],
            )
        changed = copy.deepcopy(self.seal)
        changed["records"]["sol-low"][0]["accepted"] = True
        with self.assertRaisesRegex(ValueError, "pre-feedback"):
            verify_commitments(self.protocol, self.tasks, [], changed, self.records)

    def provider_records(self):
        rows = []
        for name, configuration in self.protocol["conditions"].items():
            for stage in ("proposal", "blind_challenge", "revision"):
                body = {"task": "Return one", "experience": []}
                if stage == "proposal":
                    body["runtime"] = "Python 3.14"
                if stage == "revision":
                    body.update(
                        {
                            "initial_proposal": {},
                            "initial_error": None,
                            "smoke": {},
                            "blind_challenge": {},
                            "challenge_execution": {},
                        }
                    )
                rows.append(
                    {
                        "request": {
                            "model": configuration["model"],
                            "reasoning": {"effort": configuration["reasoning_effort"]},
                            "max_output_tokens": 2400,
                            "input": [
                                {"role": "system", "content": "Same policy"},
                                {"role": "user", "content": json.dumps(body)},
                            ],
                        },
                        "response": {
                            "content": "{}",
                            "configured_model": name,
                            "reported_model": name,
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        },
                    }
                )
        return rows

    def test_actual_request_mapping_and_shared_information(self):
        for item in self.records:
            for attempt, stage in zip(
                item["record"]["attempts"], ("proposal", "blind_challenge", "revision")
            ):
                attempt["stage"] = stage
        report = audit_requests(self.protocol, self.records, self.provider_records())
        self.assertEqual(report["verified_response_transcripts"], 6)
        self.assertIs(report["initial_and_blind_information_equal"], True)

    def test_hidden_checker_in_request_refuses(self):
        rows = self.provider_records()
        body = json.loads(rows[0]["request"]["input"][1]["content"])
        body["checker"] = "Unenrolled answer oracle"
        rows[0]["request"]["input"][1]["content"] = json.dumps(body)
        with self.assertRaisesRegex(ValueError, "interface"):
            audit_requests(self.protocol, self.records, rows)


if __name__ == "__main__":
    unittest.main()
