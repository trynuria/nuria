"""Private persistent analyst entry point, using no real model or network."""

import copy
import hashlib
import io
import json
import sqlite3
import stat
import unittest
import uuid
from contextlib import closing, redirect_stderr
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
from cognition.reasoner_client import ReasonerError, digest
from scripts.ask_grounded_analyst import ask, main


class OfflineReasoner:
    def __init__(self, actions, *, enable_thinking):
        self.actions = iter(actions)
        self.enable_thinking = enable_thinking
        self.messages = []

    def complete(self, messages, *, max_tokens, seed):
        self.messages.append(copy.deepcopy(messages))
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        content = action if isinstance(action, str) else json.dumps(action)
        completion = min(5, max_tokens)
        return {
            "content": content,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": completion,
                "total_tokens": 10 + completion,
            },
            "request_sha256": digest(messages),
            "response_sha256": digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0.0,
        }


class OfflineFactory:
    """The injected factory never creates an HTTP client."""

    def __init__(self, actions):
        self.actions = actions
        self.calls = []
        self.instances = []

    def __call__(self, endpoint, model_alias, *, enable_thinking):
        self.calls.append(
            {
                "endpoint": endpoint,
                "model_alias": model_alias,
                "enable_thinking": enable_thinking,
            }
        )
        reasoner = OfflineReasoner(self.actions, enable_thinking=enable_thinking)
        self.instances.append(reasoner)
        return reasoner

    @property
    def inference_calls(self):
        return sum(len(reasoner.messages) for reasoner in self.instances)


class AskGroundedAnalystTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("ask-grounded-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        self.database = self.root / "events.sqlite"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE events(id INTEGER,amount INTEGER)")
            connection.executemany(
                "INSERT INTO events VALUES(?,?)", [(1, None), (2, 1), (3, 1), (4, 1)]
            )
        documents = {}
        for section in ("business", "columns", "schema"):
            path = self.root / (section + ".txt")
            path.write_text("Events have id and additive amount; NULL means missing.")
            documents[section] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        self.registry = {
            "schema": SCHEMA,
            "revision": "0" * 40,
            "databases": {
                "events": {
                    "database": {
                        "path": self.database.name,
                        "sha256": hashlib.sha256(
                            self.database.read_bytes()
                        ).hexdigest(),
                    },
                    "documents": documents,
                }
            },
        }
        self.manifest = self.root / "registry.json"
        self.manifest.write_text(json.dumps(self.registry))
        self.state = self.root / "private-state"
        self.question = "ASK_PRIVATE_QUESTION_5a1c: What is the total amount?"
        self.sql = "SELECT sum(amount) FROM events"

    def actions(self, sql=None):
        sql = sql or self.sql
        return [
            {"tool": "query", "arguments": {"sql": sql}},
            {
                "tool": "final",
                "arguments": {
                    "sql": sql,
                    "procedure": "Inspect the additive quantity and aggregate it.",
                    "preconditions": ["The field is additive"],
                },
            },
        ]

    def invoke(self, factory, **changes):
        arguments = {
            "registry": self.manifest,
            "state": self.state,
            "model_identity": "offline-scripted-fixture",
            "job_id": "private-job-one",
            "db_id": "events",
            "question": self.question,
            "max_turns": 4,
            "max_output_tokens": 32,
            "max_tokens_per_turn": 16,
            "max_tool_calls": 4,
            "reasoner_factory": factory,
        }
        arguments.update(changes)
        return ask(**arguments)

    def assert_private_state(self):
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        paths = list(self.state.rglob("*"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertFalse(path.is_symlink())
                self.assertEqual(
                    stat.S_IMODE(path.stat().st_mode),
                    0o700 if path.is_dir() else 0o600,
                )

    def assert_refused_without_inference(self, **changes):
        unused = OfflineFactory(self.actions())
        with self.assertRaises((ValueError, FileExistsError)):
            self.invoke(unused, **changes)
        self.assertEqual(unused.inference_calls, 0)

    def test_actual_query_returns_rows_source_and_no_semantic_self_acceptance(self):
        factory = OfflineFactory(self.actions())
        report = self.invoke(factory, enable_thinking=False)
        self.assertEqual(report["final_sql"], self.sql)
        self.assertEqual(report["rows"], [[3]])
        self.assertEqual(report["columns"], ["sum(amount)"])
        self.assertIsNone(report["semantic_accepted"])
        self.assertEqual(report["source"]["db_id"], "events")
        self.assertEqual(
            report["source"]["db_sha256"],
            self.registry["databases"]["events"]["database"]["sha256"],
        )
        self.assertEqual(
            report["source"]["registry_sha256"],
            AnalystWorkspace(self.manifest).registry_sha256,
        )
        for key in ("execution_receipt_sha256", "episode_id", "proposal_sha256"):
            self.assertRegex(report[key], r"^[a-f0-9]{64}$")
        self.assertEqual(report["model_usage"]["completion_tokens"], 10)
        self.assertFalse(report["model_usage"]["unknown_additional_usage"])
        self.assertEqual(report["reserved_completion_tokens"], 32)
        self.assertEqual(report["unresolved_completion_ceiling"], 0)
        self.assertEqual(factory.inference_calls, 2)
        self.assertEqual(
            factory.calls,
            [
                {
                    "endpoint": "http://127.0.0.1:19487/v1/chat/completions",
                    "model_alias": "default_model",
                    "enable_thinking": False,
                }
            ],
        )
        self.assert_private_state()

    def test_successful_wrong_answer_remains_semantically_unknown(self):
        wrong_sql = "SELECT max(amount) FROM events"
        report = self.invoke(OfflineFactory(self.actions(wrong_sql)))
        self.assertEqual(report["final_sql"], wrong_sql)
        self.assertEqual(report["rows"], [[1]])
        self.assertIsNone(report["semantic_accepted"])
        self.assertNotIn("accepted", report)
        self.assertNotIn("reward", report)

    def test_final_without_own_query_cannot_deliver_an_answer(self):
        factory = OfflineFactory([self.actions()[1]])
        report = self.invoke(factory, max_turns=1)
        self.assertIsNone(report["final_sql"])
        self.assertFalse(report.get("rows"))
        self.assertFalse(report.get("execution_receipt_sha256"))
        self.assertIsNone(report["semantic_accepted"])
        self.assertEqual(factory.inference_calls, 1)

    def test_same_job_cannot_retry_or_overwrite_an_existing_private_artifact(self):
        self.invoke(OfflineFactory(self.actions()))
        immutable_files = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.state.rglob("*")
            if path.is_file() and path.suffix == ".json"
        }
        self.assertTrue(immutable_files)
        self.assert_refused_without_inference()
        self.assert_refused_without_inference(question="A replacement question")
        for path, original_hash in immutable_files.items():
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), original_hash
            )

    def test_changed_model_identity_is_refused_for_a_new_job_in_the_same_state(self):
        self.invoke(OfflineFactory(self.actions()))
        self.assert_refused_without_inference(
            job_id="private-job-two", model_identity="different-model-identity"
        )

    def test_changed_registry_is_refused_even_when_its_revision_label_is_reused(self):
        self.invoke(OfflineFactory(self.actions()))
        changed = copy.deepcopy(self.registry)
        document = self.root / "changed-business.txt"
        document.write_text("A different registered business interpretation.")
        changed["databases"]["events"]["documents"]["business"] = {
            "path": document.name,
            "sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        }
        manifest = self.root / "changed-registry.json"
        manifest.write_text(json.dumps(changed))
        self.assert_refused_without_inference(
            registry=manifest, job_id="private-job-two"
        )

    def test_keyboard_interrupt_leaves_private_reservation_and_cannot_retry(self):
        factory = OfflineFactory([KeyboardInterrupt("offline interruption")])
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(factory)
        self.assertEqual(factory.inference_calls, 1)
        self.assert_private_state()
        self.assertTrue(list(self.state.rglob("*.json")))
        self.assert_refused_without_inference()
        self.assert_private_state()

    def test_unknown_model_usage_retains_liability_without_acceptance_or_retry(self):
        factory = OfflineFactory([ReasonerError("model_transport_error")])
        report = self.invoke(factory)
        self.assertEqual(report["status"], "model_transport_error")
        self.assertTrue(report["model_usage"]["unknown_additional_usage"])
        self.assertEqual(report["unresolved_completion_ceiling"], 32)
        self.assertIsNone(report["semantic_accepted"])
        self.assertIsNone(report["final_sql"])
        self.assertFalse(report.get("rows"))
        self.assertEqual(factory.inference_calls, 1)
        self.assert_refused_without_inference()
        self.assert_private_state()

    def test_invalid_caps_are_refused_before_inference(self):
        for field in (
            "max_turns",
            "max_output_tokens",
            "max_tokens_per_turn",
            "max_tool_calls",
        ):
            with self.subTest(field=field):
                self.assert_refused_without_inference(
                    state=self.root / ("invalid-" + field), **{field: 0}
                )

    def test_cli_cannot_expand_the_reviewed_inference_defaults(self):
        for field, ceiling in (
            ("max_turns", 12),
            ("max_output_tokens", 6000),
            ("max_tokens_per_turn", 1536),
            ("max_tool_calls", 12),
        ):
            for invalid in (ceiling + 1, True):
                with self.subTest(field=field, invalid=invalid):
                    self.assert_refused_without_inference(
                        state=self.root / ("expanded-" + field + str(invalid)),
                        **{field: invalid},
                    )

    def test_symlink_or_directory_memory_is_refused_without_inference(self):
        self.state.mkdir(mode=0o700)
        target = self.root / "unrelated.txt"
        target.write_text("preserve unrelated data")
        original = target.read_bytes()
        (self.state / "memory.sqlite").symlink_to(target)
        self.assert_refused_without_inference()
        self.assertEqual(target.read_bytes(), original)

        second_state = self.root / "directory-memory-state"
        second_state.mkdir(mode=0o700)
        (second_state / "memory.sqlite").mkdir(mode=0o700)
        self.assert_refused_without_inference(state=second_state)

    def test_cli_rejects_oracle_arguments_before_any_execution(self):
        arguments = [
            "--registry",
            str(self.manifest),
            "--state",
            str(self.state),
            "--model-identity",
            "offline-scripted-fixture",
            "--job-id",
            "cli-job",
            "--db-id",
            "events",
            "--question",
            self.question,
            "--oracle",
            str(self.root / "not-an-authorized-input.json"),
        ]
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(arguments)
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
