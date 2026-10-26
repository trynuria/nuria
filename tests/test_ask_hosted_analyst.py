"""Real analyst/client wiring with in-process HTTP fixtures and no network."""

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cognition.analyst_workspace import SCHEMA
from cognition.hosted_reasoner import OpenAIReasoner, SharedReasoningBudget
from cognition.reasoner_client import digest
from cognition.reasoning_policy import VERSION, policy_identity
from scripts.ask_hosted_analyst import REPOSITORY, ask_hosted, load_reasoner, main


def private_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path


class FixtureResponse:
    def __init__(self, action):
        self.raw = json.dumps(
            {
                "status": "completed",
                "model": "fixture-model",
                "service_tier": "default",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": json.dumps(action)}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
        ).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, maximum):
        return self.raw[:maximum]


class FixtureReasoner(OpenAIReasoner):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []
        self.actions = iter(
            [
                {
                    "tool": "query",
                    "arguments": {"sql": "SELECT sum(amount) FROM events"},
                },
                {
                    "tool": "final",
                    "arguments": {"sql": "SELECT sum(amount) FROM events"},
                },
            ]
        )
        self._opener = self
        self.instances.append(self)

    def open(self, request, **kwargs):
        self.requests.append(json.loads(request.data))
        return FixtureResponse(next(self.actions))


class AskHostedAnalystTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / (
            "nuria-hosted-fixture-" + uuid.uuid4().hex
        )
        self.root.mkdir(mode=0o700)
        self.key = self.root / "provider.key"
        self.key.write_text("fixture_not_an_actual_provider_key")
        self.key.chmod(0o600)
        self.config_value = {
            "key_file": str(self.key),
            "budget_file": str(self.root / "budget.sqlite"),
            "max_cost_micro_usd": 1000000,
            "max_requests": 8,
            "model": "fixture-model",
            "reasoning_effort": "low",
            "input_micro_usd_per_million_tokens": 100000,
            "output_micro_usd_per_million_tokens": 400000,
        }
        self.config = private_json(self.root / "provider.json", self.config_value)
        database = self.root / "events.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE events(amount INTEGER)")
            connection.executemany(
                "INSERT INTO events VALUES(?)", [(None,), (1,), (2,)]
            )
        documents = {}
        for section in ("schema", "business", "columns"):
            path = self.root / (section + ".txt")
            path.write_text("Events have an additive integer amount; NULL is missing.")
            documents[section] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        self.registry = private_json(
            self.root / "registry.json",
            {
                "schema": SCHEMA,
                "revision": "0" * 40,
                "databases": {
                    "events": {
                        "database": {
                            "path": database.name,
                            "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                        },
                        "documents": documents,
                    }
                },
            },
        )
        self.state = self.root / "state"
        FixtureReasoner.instances = []

    def invoke(self, **changes):
        arguments = {
            "provider_config": self.config,
            "registry": self.registry,
            "state": self.state,
            "job_id": "fixture-job",
            "db_id": "events",
            "question": "What is the total amount?",
            "max_turns": 4,
            "max_output_tokens": 32,
            "max_tokens_per_turn": 16,
            "max_tool_calls": 4,
        }
        arguments.update(changes)
        with patch("scripts.ask_hosted_analyst.OpenAIReasoner", FixtureReasoner):
            return ask_hosted(**arguments)

    def policy(self, suffix="Check result multiplicity before committing."):
        artifact = {
            "version": VERSION,
            "selected_policy": {"policy_id": policy_identity(suffix), "suffix": suffix},
        }
        artifact["artifact_sha256"] = digest(artifact)
        return private_json(self.root / "policy.json", artifact)

    def test_real_ask_executes_query_and_preserves_unknown_semantic_grade(self):
        result = self.invoke()
        self.assertEqual(result["rows"], [[3]])
        self.assertTrue(result["delivery_complete"])
        self.assertIsNone(result["semantic_accepted"])
        client = FixtureReasoner.instances[0]
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(result["model_identity"], digest(client.configuration))
        self.assertEqual(client.budget.snapshot()["requests"], 2)
        self.assertEqual(client.budget.snapshot()["settled_cost_micro_usd"], 6)
        self.assertFalse(client.budget.snapshot()["blocked"])
        self.assertEqual(
            json.loads((self.state / "identity.json").read_bytes())["model_identity"],
            result["model_identity"],
        )

    def test_policy_is_applied_and_bound_without_changing_tools_or_allowance(self):
        policy = self.policy()
        artifact = json.loads(policy.read_bytes())
        result = self.invoke(policy=policy)
        client = FixtureReasoner.instances[0]
        self.assertIn(
            artifact["selected_policy"]["suffix"],
            client.requests[0]["input"][0]["content"],
        )
        self.assertNotEqual(result["model_identity"], digest(client.configuration))
        self.assertEqual(client.requests[0]["max_output_tokens"], 16)
        self.assertIsNone(result["semantic_accepted"])

    def test_changed_policy_cannot_reuse_model_bound_state(self):
        self.invoke()
        with self.assertRaisesRegex(ValueError, "another source or model"):
            self.invoke(policy=self.policy(), job_id="new-job")
        self.assertEqual(len(FixtureReasoner.instances[-1].requests), 0)

    def test_empty_key_stops_before_budget_or_job_creation(self):
        self.key.write_text(" \n")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.invoke()
        self.assertFalse(self.state.exists())
        self.assertFalse(Path(self.config_value["budget_file"]).exists())
        self.assertFalse(FixtureReasoner.instances)

    def test_nonprivate_config_and_key_are_refused(self):
        self.config.chmod(0o644)
        with self.assertRaises(ValueError):
            self.invoke()
        self.config.chmod(0o600)
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertFalse(self.state.exists())

    def test_unknown_provider_fields_and_public_state_are_refused(self):
        changed = {**self.config_value, "endpoint": "https://unconfigured.invalid"}
        private_json(self.config, changed)
        with self.assertRaisesRegex(ValueError, "fields differ"):
            self.invoke()
        private_json(self.config, self.config_value)
        with self.assertRaisesRegex(ValueError, "outside public source"):
            self.invoke(state=REPOSITORY / ".test-state" / "should-not-create")
        self.assertFalse(self.state.exists())

    def test_mutated_or_unfrozen_policy_is_refused_before_budget_creation(self):
        path = self.policy()
        artifact = json.loads(path.read_bytes())
        artifact["selected_policy"]["suffix"] = "Modified after freezing"
        private_json(path, artifact)
        with self.assertRaisesRegex(ValueError, "artifact identity"):
            self.invoke(policy=path)
        self.assertFalse(Path(self.config_value["budget_file"]).exists())

    def test_load_reasoner_never_requests_inference_or_resets_shared_budget(self):
        first = load_reasoner(self.config)
        second = load_reasoner(self.config)
        self.assertEqual(first.configuration, second.configuration)
        self.assertEqual(first.budget.snapshot()["requests"], 0)
        with self.assertRaisesRegex(ValueError, "silently changed"):
            SharedReasoningBudget(
                self.config_value["budget_file"], max_cost_micro_usd=2, max_requests=8
            )

    def test_cli_error_does_not_print_raw_provider_message_or_secret(self):
        arguments = [
            "--provider-config",
            str(self.config),
            "--registry",
            str(self.registry),
            "--state",
            str(self.state),
            "--job-id",
            "cli-job",
            "--db-id",
            "events",
            "--question",
            "Total?",
        ]
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch(
                "scripts.ask_hosted_analyst.ask_hosted",
                side_effect=ValueError("RAW_FIXTURE_SECRET_VALUE"),
            ),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = main(arguments)
        self.assertEqual(status, 2)
        self.assertNotIn(
            "RAW_FIXTURE_SECRET_VALUE", output.getvalue() + errors.getvalue()
        )
        self.assertEqual(
            json.loads(errors.getvalue()),
            {"status": "error", "error_type": "ValueError"},
        )

    def test_existing_cli_reviewed_caps_still_apply(self):
        with self.assertRaisesRegex(ValueError, "reviewed defaults"):
            self.invoke(max_tokens_per_turn=1537)
        self.assertFalse(self.state.exists())
        self.assertEqual(len(FixtureReasoner.instances[0].requests), 0)


if __name__ == "__main__":
    unittest.main()
