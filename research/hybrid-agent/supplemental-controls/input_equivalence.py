"""Synthetic host-input equality check; no model inference or real course reads."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def prove(repository):
    sys.path.insert(0, str(repository))
    from cognition.analyst_workspace import SCHEMA, AnalystWorkspace
    from cognition.grounded_agent import GroundedAnalyst
    from cognition.procedural_memory import ProceduralMemory
    from cognition.reasoner_client import digest

    directory = Path(tempfile.mkdtemp(prefix="nuria-history-input-fixture-"))
    database = directory / "events.sqlite"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE events(amount INTEGER)")
        db.execute("INSERT INTO events VALUES(7)")
    docs = {}
    for name in ("business", "columns", "schema"):
        path = directory / (name + ".txt")
        path.write_text("events(amount INTEGER): one row with amount 7")
        docs[name] = {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    registry = directory / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "revision": "sha256:"
                + hashlib.sha256(b"fixture-history-irrelevance-v1").hexdigest(),
                "databases": {
                    "events": {
                        "database": {
                            "path": database.name,
                            "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                        },
                        "documents": docs,
                    }
                },
            }
        )
    )
    actual = AnalystWorkspace(registry)
    actual_receipt = actual.call("events", "read_knowledge", {"section": "schema"})

    class StopBeforeInference(BaseException):
        pass

    class Capture:
        enable_thinking = True

        def __init__(self):
            self.requests = []

        def complete(self, messages, *, max_tokens, seed):
            self.requests.append(
                {
                    "messages": copy.deepcopy(messages),
                    "max_tokens": max_tokens,
                    "seed": seed,
                }
            )
            raise StopBeforeInference

    class FixedObservation:
        registry_sha256 = actual.registry_sha256
        limits = actual.limits
        tool_schema = actual.tool_schema

        def call(self, db_id, tool, arguments):
            assert (db_id, tool, arguments) == (
                "events",
                "read_knowledge",
                {"section": "schema"},
            )
            return copy.deepcopy(actual_receipt)

    reports = []
    for condition, first in (("schema_first", "read_knowledge"), ("stateless", None)):
        captures, memory_heads = [], []
        for primed in (False, True):
            memory = ProceduralMemory(
                directory
                / (condition + ("-primed" if primed else "-fresh"))
                / "memory.sqlite",
                source_id="sha256:"
                + hashlib.sha256(b"fixture-history-irrelevance-v1").hexdigest(),
                model_id="synthetic-request-capture",
                tool_version=SCHEMA,
            )
            try:
                if primed:
                    for index in range(8):
                        episode = memory.begin_once(
                            "teaching-fixture-" + str(index),
                            "Fixture teaching question",
                            digest({"fixture": index}),
                        )
                        memory.record_action(
                            episode,
                            "fixture-query",
                            "query",
                            "SELECT 1",
                            True,
                            1,
                            digest({"fixture_receipt": index}),
                        )
                        memory.propose(
                            episode,
                            "SELECT 1",
                            procedure_text="Synthetic retained procedure",
                        )
                        memory.settle(
                            episode,
                            verify_terminal=lambda _: {
                                "accepted": True,
                                "checker_id": "fixture-only-terminal-check",
                                "evidence_sha256": digest("fixture"),
                            },
                        )
                before = memory.verify()
                memory_heads.append(before)
                reasoner = Capture()
                agent = GroundedAnalyst(
                    reasoner,
                    FixedObservation(),
                    memory,
                    memory_mode="none",
                    initial_tool=first,
                    initial_section="schema",
                    neural_context=None,
                    max_turns=12,
                    max_output_tokens=6000,
                    max_tokens_per_turn=1536,
                    max_tool_calls=12,
                    memory_bytes=6000,
                )
                try:
                    agent.run(
                        job_id="reserved-fixture-same-identity",
                        question="What is the total amount?",
                        db_id="events",
                        seed=20261025,
                    )
                except StopBeforeInference:
                    pass
                else:
                    raise AssertionError("Fixture did not stop before inference")
                assert len(reasoner.requests) == 1
                request = reasoner.requests[0]
                initial = json.loads(request["messages"][1]["content"])
                assert (
                    initial["historical_experience"] == ""
                    and initial["neural_context"] is None
                )
                captures.append(request)
            finally:
                memory.close()
        assert captures[0] == captures[1]
        assert memory_heads[0] != memory_heads[1]
        reports.append(
            {
                "condition": condition,
                "same_first_request": True,
                "request_sha256": digest(captures[0]),
                "teaching_episodes_in_primed_fixture": 8,
                "different_memory_heads": True,
                "same_host_observation": True,
            }
        )
    return {
        "schema": "nuria.nonlearning-history-input-proof.v1",
        "passed": True,
        "conditions": reports,
        "model_calls": 0,
        "actual_course_or_oracle_read": False,
        "scope": "Synthetic first-request equality with and without settled teaching records, holding the genuine host receipt fixed. Real schema-read wall times can vary between executions. This establishes history irrelevance for the none-memory input path, not capability, bit-identical runtime generations, or an unseen preregistered evaluation.",
    }


if __name__ == "__main__":
    result = prove(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        with Path(sys.argv[2]).open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    print(json.dumps(result, indent=2))
