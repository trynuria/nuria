import hashlib
import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import SCHEMA, AnalystWorkspace, grade_result
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory
from cognition.reasoner_client import ReasonerError, digest


class ScriptedReasoner:
    """A protocol fixture, not an inference or capability measurement."""

    def __init__(self, actions):
        self.actions = iter(actions)
        self.messages = []

    def complete(self, messages, *, max_tokens, seed):
        self.messages.append(messages)
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        content = action if isinstance(action, str) else json.dumps(action)
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": min(5, max_tokens)},
            "request_sha256": digest(messages),
            "response_sha256": digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0.0,
        }


class GroundedAgentTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("grounded-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        database = self.root / "records.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "CREATE TABLE events(id INTEGER,name TEXT,amount INTEGER)"
            )
            connection.executemany(
                "INSERT INTO events VALUES(?,?,?)",
                [(1, "a", None), (2, "b", 1), (3, "b", 1), (4, "c", 1)],
            )
        documents = {}
        for section in ("business", "columns", "schema"):
            path = self.root / (section + ".txt")
            path.write_text(
                "Events contain an id, name and integer amount; missing amounts are NULL."
            )
            documents[section] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        registry = {
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
        }
        manifest = self.root / "registry.json"
        manifest.write_text(json.dumps(registry))
        self.workspace = AnalystWorkspace(manifest)
        self.memory = ProceduralMemory(
            self.root / "memory.sqlite",
            source_id="test-workspace",
            model_id="protocol-fixture",
            tool_version=SCHEMA,
        )
        self.sql = "SELECT sum(amount) FROM events"

    def tearDown(self):
        self.memory.close()

    def run_actions(self, actions, **kwargs):
        self.reasoner = ScriptedReasoner(actions)
        agent = GroundedAnalyst(self.reasoner, self.workspace, self.memory, **kwargs)
        return agent.run(
            job_id="test-job", question="What is the total amount?", db_id="events"
        )

    def final(self):
        return {
            "tool": "final",
            "arguments": {
                "sql": self.sql,
                "procedure": "Inspect the numeric field and aggregate it.",
                "preconditions": ["The field is an additive quantity"],
            },
        }

    def test_real_execution_error_is_repaired_and_only_host_acceptance_retains_a_procedure(
        self,
    ):
        result = self.run_actions(
            [
                {
                    "tool": "query",
                    "arguments": {"sql": "SELECT sum(wrong_column) FROM events"},
                },
                {"tool": "describe_table", "arguments": {"table": "events"}},
                {"tool": "query", "arguments": {"sql": self.sql}},
                self.final(),
            ]
        )
        self.assertEqual(result["status"], "proposed")
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["selected_receipt"]["rows"], [[3]])
        self.assertEqual(self.memory.outcome_stats("query")["tool_errors"], 1)
        self.assertEqual(
            self.memory.retrieve("total", mode="procedure")["item_count"], 0
        )
        settled = self.memory.settle(
            result["episode_id"],
            verify_terminal=lambda request: {
                "accepted": grade_result(
                    result["selected_receipt"], [[3]], expected_columns=1
                ),
                "evidence_sha256": digest(result["selected_receipt"]),
                "checker_id": "test-result-checker",
            },
        )
        self.assertTrue(settled["accepted"])
        self.assertEqual(
            self.memory.retrieve("total", mode="procedure")["item_count"], 1
        )
        self.assertEqual(result["tool_calls"], 3)

    def test_valid_query_does_not_assign_itself_terminal_success(self):
        result = self.run_actions(
            [{"tool": "query", "arguments": {"sql": self.sql}}, self.final()]
        )
        settled = self.memory.settle(
            result["episode_id"],
            verify_terminal=lambda request: {
                "accepted": grade_result(
                    result["selected_receipt"], [[1]], expected_columns=1
                ),
                "evidence_sha256": digest(
                    {"wanted": "maximum", "result": result["selected_receipt"]}
                ),
                "checker_id": "test-result-checker",
            },
        )
        self.assertFalse(settled["accepted"])
        self.assertEqual(
            self.memory.retrieve("total", mode="procedure")["item_count"], 0
        )

    def test_unexecuted_final_is_not_a_committed_answer(self):
        result = self.run_actions(
            [
                self.final(),
                {"tool": "query", "arguments": {"sql": self.sql}},
                self.final(),
            ]
        )
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["protocol_errors"], 1)
        self.assertEqual(result["tool_calls"], 1)

    def test_model_cannot_call_settlement_or_supply_its_own_grade(self):
        result = self.run_actions(
            [
                {"tool": "settle", "arguments": {"accepted": True}},
                {"tool": "abstain", "arguments": {"reason": "No verified answer"}},
            ]
        )
        self.assertEqual(result["status"], "agent_abstained")
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(result["protocol_errors"], 1)
        self.assertIsNone(result["final_sql"])

    def test_token_exhaustion_and_unknown_inference_usage_cannot_restart_freely(self):
        result = self.run_actions(
            ["invalid JSON", "still invalid"], max_output_tokens=10
        )
        self.assertEqual(result["status"], "completion_token_limit")
        self.assertEqual(result["model_usage"]["completion_tokens"], 10)
        with self.assertRaises(ValueError):
            GroundedAnalyst(
                self.reasoner, self.workspace, self.memory, max_output_tokens=10
            ).run(
                job_id="test-job", question="What is the total amount?", db_id="events"
            )
        # A different job records unknown consumed inference rather than zero.
        reasoner = ScriptedReasoner([ReasonerError("model_transport_error")])
        unknown = GroundedAnalyst(reasoner, self.workspace, self.memory).run(
            job_id="unknown-job", question="What is the total amount?", db_id="events"
        )
        self.assertTrue(unknown["model_usage"]["unknown_additional_usage"])
        self.assertIsNone(unknown["final_sql"])


if __name__ == "__main__":
    unittest.main()
