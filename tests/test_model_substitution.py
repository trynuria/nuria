"""Offline wiring checks; scripted replies are not model-intelligence evidence."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import unittest
import urllib.error
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from cognition.analyst_workspace import SCHEMA
from cognition.hosted_reasoner import OpenAIReasoner, SharedReasoningBudget
from cognition.reasoner_client import ReasonerError
from scripts import evaluate_model_substitution as study


class FixtureHTTP:
    def __init__(self, error=None):
        self.requests = []
        self.error = error

    def open(self, request, timeout):
        self.requests.append(json.loads(request.data))
        if self.error:
            raise self.error
        messages = self.requests[-1]["input"]
        tool = (
            "final"
            if any(message["role"] == "assistant" for message in messages)
            else "query"
        )
        action = {
            "tool": tool,
            "arguments": {"sql": "SELECT 1"},
            "why": "Protocol fixture",
        }
        return io.BytesIO(
            json.dumps(
                {
                    "model": "offline-fixture",
                    "status": "completed",
                    "service_tier": "default",
                    "error": None,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": json.dumps(action)}
                            ],
                        }
                    ],
                }
            ).encode()
        )


def dump(path, value):
    path.write_text(json.dumps(value))
    return path


class ModelSubstitutionTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]
        self.root = (
            self.repository / ".test-state" / ("model-substitution-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        key = self.root / "fixture.key"
        key.write_text("offline-protocol-fixture-only-not-a-real-key")
        os.chmod(key, 0o600)
        budget = SharedReasoningBudget(
            self.root / "shared.sqlite", max_cost_micro_usd=10_000_000, max_requests=700
        )
        self.reasoner = OpenAIReasoner(
            key,
            "offline-fixture",
            reasoning_effort="low",
            budget=budget,
            input_micro_usd_per_million_tokens=2_000_000,
            output_micro_usd_per_million_tokens=10_000_000,
        )
        self.http = FixtureHTTP()
        self.reasoner._opener = self.http
        self.inputs_root = self.root / "original"
        inputs = self.inputs_root / "inputs"
        inputs.mkdir(parents=True)
        database = inputs / "records.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE records(value INTEGER)")
        document = inputs / "schema.txt"
        document.write_text("CREATE TABLE records(value INTEGER)")
        registry = dump(
            inputs / "registry.json",
            {
                "schema": SCHEMA,
                "revision": "a" * 40,
                "databases": {
                    "records": {
                        "database": {
                            "path": database.name,
                            "sha256": study.sha(database),
                        },
                        "documents": {
                            name: {"path": document.name, "sha256": study.sha(document)}
                            for name in ("schema", "business", "columns")
                        },
                    }
                },
            },
        )
        self.reserved = dump(
            inputs / "reserved.json",
            {
                "tasks": [
                    {
                        "id": task_id,
                        "db_id": "records",
                        "question": "Scripted fixture " + task_id,
                    }
                    for task_id in study.TASK_IDS
                ]
            },
        )
        self.oracle = dump(
            inputs / "oracle.json",
            {
                "tasks": {
                    task_id: {
                        "expected_rows": [[1]],
                        "ordered": False,
                        "column_count": 1,
                    }
                    for task_id in study.TASK_IDS
                }
            },
        )
        historical = {
            "selected_task_ids": list(study.TASK_IDS),
            "limits": study.LIMITS,
            "model_identity": "historical-fixture",
            "frozen_source_files": {
                name: study.sha(self.repository / name) for name in study.CORE
            },
            "artifacts": {
                name: {"path": "inputs/" + path.name, "sha256": study.sha(path)}
                for name, path in (
                    ("registry", registry),
                    ("reserved_course", self.reserved),
                    ("oracle", self.oracle),
                )
            },
        }
        self.historical = dump(self.root / "historical.json", historical)
        self.historical_result = dump(
            self.root / "historical-result.json",
            {
                "passed": True,
                "complete": True,
                "conditions": {
                    "schema_first": {
                        "selected": 12,
                        "executed": 12,
                        "skipped_original_complete": False,
                        "graded": 12,
                        "accepted": 9,
                        "abstentions": 1,
                        "unknown_or_unexecuted": 0,
                    }
                },
            },
        )
        self.primary = dump(
            self.root / "primary.json",
            {
                "stop_reason": None,
                "wall_seconds": 1.0,
                "all_selected": {"committed": 54, "graded": 54},
                "global_usage_including_policy_proposals": {
                    "unknown_additional_usage": False,
                    "unresolved_completion_ceiling": 0,
                    "completion_tokens": 0,
                },
            },
        )

    def prepare(self, **changes):
        options = {
            "repository": self.repository,
            "inputs_root": self.inputs_root,
            "model_configuration": self.reasoner.configuration,
            "budget_file": self.reasoner.budget.path,
            "primary_report": self.primary,
            "historical_protocol": self.historical,
            "historical_result": self.historical_result,
        }
        options.update(changes)
        return study.prepare_protocol(self.root / "freeze", **options)

    def test_preparation_pins_complete_cohort_without_decoding_bodies(self):
        original = study.read

        def checked(path):
            self.assertNotIn(Path(path), (self.reserved, self.oracle))
            return original(path)

        with patch.object(study, "read", checked):
            receipt = self.prepare()
        public = original(receipt["public_protocol"])
        self.assertEqual(public["selected_task_ids"], list(study.TASK_IDS))
        self.assertEqual(public["planned_jobs"], 12)
        self.assertEqual(public["maximum_new_completion_tokens"], 72000)
        self.assertEqual(public["shared_budget_cap_micro_usd"], 10_000_000)
        self.assertEqual(self.http.requests, [])

    def test_complete_scripted_lifecycle_delays_all_oracle_access(self):
        receipt = self.prepare()
        output = self.root / "run"
        original = study.read
        oracle_reads = []

        def checked(path):
            if Path(path) == self.oracle:
                self.assertTrue((output / "proposals-ended.json").exists())
                self.assertEqual(len(self.http.requests), 24)
                oracle_reads.append(str(path))
            return original(path)

        with patch.object(study, "read", checked):
            report = study.evaluate(
                receipt["private_protocol"],
                receipt["private_protocol_sha256"],
                output,
                self.reasoner,
            )
        self.assertIsNone(report["stop_reason"])
        self.assertEqual(report["all_selected"]["selected"], 12)
        self.assertEqual(report["all_selected"]["accepted"], 12)
        self.assertEqual(len(oracle_reads), 1)
        self.assertEqual(report["new_usage"]["completion_tokens"], 120)
        self.assertEqual(report["aggregate_known_completion_tokens"], 120)
        self.assertEqual(report["shared_budget_audit"]["requests"], 24)
        self.assertEqual(report["shared_budget_audit"]["cap_micro_usd"], 10_000_000)
        self.assertTrue(
            all(
                message["role"] != "user"
                or "checked_training_experience" not in message["content"]
                for request in self.http.requests
                for message in request["input"]
            )
        )

    def test_unknown_transport_retains_all_cases_and_no_oracle_read(self):
        receipt = self.prepare()
        self.http.error = urllib.error.URLError("offline fixture")
        original = study.read

        def checked(path):
            self.assertNotEqual(Path(path), self.oracle)
            return original(path)

        with patch.object(study, "read", checked):
            report = study.evaluate(
                receipt["private_protocol"],
                receipt["private_protocol_sha256"],
                self.root / "run",
                self.reasoner,
            )
        self.assertEqual(report["stop_reason"], "unreconciled_model_usage")
        self.assertEqual(report["all_selected"]["selected"], 12)
        self.assertEqual(report["all_selected"]["attempted"], 1)
        self.assertEqual(report["all_selected"]["unexecuted"], 11)
        self.assertEqual(report["all_selected"]["graded"], 0)
        self.assertGreater(
            report["shared_budget_audit"]["unresolved_liability_micro_usd"], 0
        )

    def test_a_fresh_ledger_cannot_renew_the_allowance(self):
        receipt = self.prepare()
        self.reasoner.budget = SharedReasoningBudget(
            self.root / "other.sqlite", max_cost_micro_usd=10_000_000, max_requests=700
        )
        with self.assertRaisesRegex(ValueError, "same existing shared budget"):
            study.evaluate(
                receipt["private_protocol"],
                receipt["private_protocol_sha256"],
                self.root / "run",
                self.reasoner,
            )
        self.assertEqual(self.http.requests, [])

    def test_unresolved_primary_usage_blocks_preparation(self):
        value = study.read(self.primary)
        value["global_usage_including_policy_proposals"]["unknown_additional_usage"] = (
            True
        )
        dump(self.primary, value)
        with self.assertRaisesRegex(
            ValueError, "Primary unknown usage needs an explicit"
        ):
            self.prepare()
        self.assertEqual(self.http.requests, [])

    def test_loss_provision_keeps_unknown_tokens_and_full_cost_charged(self):
        self.http.error = urllib.error.URLError("offline unknown request")
        with self.assertRaises(ReasonerError) as failure:
            self.reasoner.complete(
                [{"role": "user", "content": "Fixture"}], max_tokens=2048
            )
        provision = self.reasoner.budget.close_at_ceiling(
            failure.exception.diagnostic["reservation_id"],
            evidence_sha256="a" * 64,
            reason="Offline fixture request ended; retain the entire unknown ceiling",
        )
        proof = dump(self.root / "provision.json", provision)
        primary = study.read(self.primary)
        primary.update(
            stop_reason="unreconciled_model_usage",
            all_selected={
                "committed": 32,
                "graded": 32,
                "selected": 54,
                "attempted": 32,
                "accepted": 31,
                "unexecuted": 22,
            },
        )
        primary["global_usage_including_policy_proposals"].update(
            unknown_additional_usage=True, unresolved_completion_ceiling=2048
        )
        dump(self.primary, primary)
        receipt = self.prepare(
            allow_incomplete_primary=True, loss_provision_receipt=proof
        )
        public = study.read(receipt["public_protocol"])
        self.assertEqual(public["prior_charged_unknown_completion_ceiling"], 2048)
        self.assertFalse(public["provider_usage_of_prior_ceiling_known"])
        self.assertEqual(public["primary_coverage"]["unexecuted"], 22)
        self.assertGreater(
            public["starting_budget_audit"]["conservative_charged_ceiling_micro_usd"], 0
        )
        self.assertEqual(public["starting_budget_audit"]["known_completion_tokens"], 0)
        self.assertEqual(
            public["starting_budget_audit"]["unresolved_liability_micro_usd"], 0
        )

    def test_half_cap_margin_stops_before_another_transport(self):
        reservation = self.reasoner.budget.reserve("b" * 64, "c" * 64, 4_990_000)
        self.reasoner.budget.settle(
            reservation,
            cost_micro_usd=4_990_000,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        receipt = self.prepare()
        report = study.evaluate(
            receipt["private_protocol"],
            receipt["private_protocol_sha256"],
            self.root / "run",
            self.reasoner,
        )
        self.assertEqual(report["stop_reason"], "conservative_cost_margin")
        self.assertEqual(report["all_selected"]["selected"], 12)
        self.assertEqual(report["all_selected"]["unexecuted"], 11)
        self.assertEqual(report["all_selected"]["graded"], 0)
        self.assertEqual(self.http.requests, [])

    def test_aggregate_completion_allowance_is_not_renewed(self):
        reservation = self.reasoner.budget.reserve("d" * 64, "e" * 64, 1000)
        self.reasoner.budget.settle(
            reservation,
            cost_micro_usd=1000,
            usage={
                "prompt_tokens": 0,
                "completion_tokens": 280000,
                "total_tokens": 280000,
            },
        )
        primary = study.read(self.primary)
        primary["global_usage_including_policy_proposals"]["completion_tokens"] = 280000
        primary["wall_seconds"] = 5000
        dump(self.primary, primary)
        receipt = self.prepare()
        public = study.read(receipt["public_protocol"])
        self.assertEqual(public["maximum_new_completion_tokens"], 48096)
        self.assertEqual(public["maximum_new_wall_seconds"], 400)


if __name__ == "__main__":
    unittest.main()
