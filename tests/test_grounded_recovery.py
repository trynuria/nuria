"""Interrupted inference never authorizes an implicit repeat of the same job."""

import concurrent.futures
import json
import threading
import unittest
import uuid
from pathlib import Path

from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory, digest


class NoToolWorkspace:
    tool_schema = []
    registry_sha256 = "a" * 64
    limits = {
        "vm_steps": 1000000,
        "row_limit": 200,
        "byte_limit": 65536,
        "deadline_ms": 2000,
    }

    def call(self, *args):
        raise AssertionError("These recovery fixtures must not invoke a workspace tool")


class InterruptingReasoner:
    """A deterministic protocol fixture; no model or HTTP request is made."""

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0

    def complete(self, messages, *, max_tokens, seed):
        del seed
        self.calls += 1
        value = next(self.outcomes)
        if isinstance(value, BaseException):
            raise value
        content = value if type(value) is str else json.dumps(value)
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


class GroundedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("grounded-recovery-" + uuid.uuid4().hex)
        )
        self.path = self.root / "memory.sqlite"
        self.binding = {
            "source_id": "recovery-fixture",
            "model_id": "protocol-fixture",
            "tool_version": "fixture-v1",
        }
        self.memory = ProceduralMemory(self.path, **self.binding)

    def tearDown(self):
        self.memory.close()

    def run_agent(self, reasoner, *, job_id="interrupted-job"):
        return GroundedAnalyst(reasoner, NoToolWorkspace(), self.memory).run(
            job_id=job_id,
            question="Which registered evidence answers this question?",
            db_id="fixture",
        )

    def test_empty_origin_cannot_be_reserved_twice(self):
        episode = self.memory.begin_once("job", "question", "a" * 64)
        state = self.memory.episode_state(episode)
        self.assertEqual(state["action_count"], 0)
        self.assertIsNone(state["proposal"])
        self.assertIsNone(state["settlement"])
        with self.assertRaises(ValueError):
            self.memory.begin_once("job", "question", "a" * 64)
        with self.assertRaises(ValueError):
            self.memory.begin_once("job", "changed question", "b" * 64)
        # The host's earlier inspection/recovery API remains idempotent.
        self.assertEqual(self.memory.begin("job", "question", "a" * 64), episode)

    def test_interruption_during_the_first_inference_blocks_a_silent_retry(self):
        reasoner = InterruptingReasoner([KeyboardInterrupt(), KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.run_agent(reasoner)
        with self.assertRaises(ValueError):
            self.run_agent(reasoner)
        self.assertEqual(reasoner.calls, 1)
        origins = [
            row for row in self.memory.export()["records"] if row["kind"] == "begin"
        ]
        self.assertEqual(len(origins), 1)
        state = self.memory.episode_state(origins[0]["episode_id"])
        self.assertEqual(state["action_count"], 0)
        self.assertIsNone(state["proposal"])

    def test_invalid_action_only_history_does_not_make_an_interrupted_job_fresh(self):
        reasoner = InterruptingReasoner(
            ["not valid JSON", KeyboardInterrupt(), KeyboardInterrupt()]
        )
        with self.assertRaises(KeyboardInterrupt):
            self.run_agent(reasoner)
        self.assertEqual(reasoner.calls, 2)
        with self.assertRaises(ValueError):
            self.run_agent(reasoner)
        self.assertEqual(reasoner.calls, 2)

    def test_reopened_memory_preserves_the_unresolved_reservation(self):
        reasoner = InterruptingReasoner([KeyboardInterrupt(), KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.run_agent(reasoner)
        self.memory.close()
        self.memory = ProceduralMemory(self.path, **self.binding)
        with self.assertRaises(ValueError):
            self.run_agent(reasoner)
        self.assertEqual(reasoner.calls, 1)

    def test_a_new_explicit_attempt_has_its_own_identity_and_accounting(self):
        reasoner = InterruptingReasoner(
            [
                KeyboardInterrupt(),
                {
                    "tool": "abstain",
                    "arguments": {"reason": "Need additional independent evidence"},
                },
            ]
        )
        with self.assertRaises(KeyboardInterrupt):
            self.run_agent(reasoner)
        result = self.run_agent(reasoner, job_id="explicit-second-attempt")
        self.assertEqual(reasoner.calls, 2)
        self.assertEqual(result["status"], "agent_abstained")
        self.assertEqual(result["model_usage"]["completion_tokens"], 5)
        origins = [
            row for row in self.memory.export()["records"] if row["kind"] == "begin"
        ]
        self.assertEqual(len(origins), 2)
        self.assertEqual(
            {row["record"]["job_id"] for row in origins},
            {"interrupted-job", "explicit-second-attempt"},
        )

    def test_two_connections_cannot_reserve_the_same_job(self):
        gate = threading.Barrier(2)

        def reserve():
            memory = ProceduralMemory(self.path, **self.binding)
            try:
                gate.wait(timeout=3)
                try:
                    return "reserved", memory.begin_once(
                        "racing-job", "question", "c" * 64
                    )
                except ValueError:
                    return "already_reserved", None
            finally:
                memory.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(
            sorted(row[0] for row in results), ["already_reserved", "reserved"]
        )
        origins = [
            row for row in self.memory.export()["records"] if row["kind"] == "begin"
        ]
        self.assertEqual(len(origins), 1)


if __name__ == "__main__":
    unittest.main()
