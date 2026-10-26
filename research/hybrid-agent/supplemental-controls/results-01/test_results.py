"""Synthetic boundary checks; no experimental courses or live models are read."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "supplement_result_checker", HERE / "verify_results.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def state(known=0, prompt=0, pending=0, unknown=False, elapsed=1, stop=None):
    return {
        "new_known_completion_tokens": known,
        "new_known_prompt_tokens": prompt,
        "pending_completion_reservation": pending,
        "new_unknown_usage": unknown,
        "other_supplementary_reserved_completion_tokens": 0,
        "other_supplementary_known_completion_tokens": 3499,
        "control_conservative_charge": known + pending,
        "additional_conservative_charge": known + pending + 3499,
        "prior_conservative_charge": 186103,
        "cumulative_conservative_charge": 186103 + known + pending,
        "stop_reason": stop,
        "elapsed_seconds": elapsed,
        "remaining_seconds": 3600 - elapsed,
    }


def course_fixture(executed):
    tasks = [{"id": "fixture-" + str(i)} for i in range(12)]
    rows = [
        {
            "condition": c,
            "task_id": t["id"],
            "status": "not_executed",
            "agent": None,
            "evaluation": None,
            "observed_usage": {
                "completion_tokens": 0,
                "prompt_tokens": 0,
                "unknown_additional_usage": False,
            },
        }
        for c in checker.CONDITIONS
        for t in tasks
    ]
    for row in rows[:executed]:
        row.update(status="proposed", agent={"synthetic": True})
    proposals = copy.deepcopy(rows[:executed])
    outcomes = []
    for condition in checker.CONDITIONS:
        batch = [r for r in rows if r["condition"] == condition]
        if all(r["agent"] is not None for r in batch):
            for row in batch:
                row["evaluation"] = {"accepted": True}
            outcomes.extend(copy.deepcopy(batch))
    return rows, proposals, outcomes, tasks


def scripted_host_run(actions, initial_tool=None):
    """Exercise real host message construction with protocol-only tool/model fixtures."""
    root = Path(os.environ.get("NURIA_TEST_REPOSITORY", HERE.parents[4]))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from cognition.grounded_agent import GroundedAnalyst
    from cognition.procedural_memory import ProceduralMemory
    from cognition.reasoner_client import ReasonerError

    class Workspace:
        registry_sha256 = "0" * 64
        limits: ClassVar[dict] = {"fixture": True}
        tool_schema: ClassVar[dict] = {
            "list_tables": {},
            "read_knowledge": {"section": "schema"},
            "describe_table": {"table": "events"},
            "query": {"sql": "SELECT"},
        }

        def call(self, db_id, tool, arguments):
            receipt = {
                "tool": tool,
                "source": {"fixture": True},
                "status": "ok",
                "complete": True,
                "columns": ["amount"],
                "rows": [[7]],
                "content": "events(amount INTEGER)",
                "cost": {"vm_progress_callbacks": 1},
            }
            receipt["result_sha256"] = checker.digest(receipt)
            return receipt

    class Reasoner:
        enable_thinking = True

        def __init__(self):
            self.actions = iter(actions)
            self.calls = 0

        def complete(self, messages, *, max_tokens, seed):
            self.calls += 1
            body = {
                "model": "fixture",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0,
                "seed": seed,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": True},
            }
            request_hash = checker.digest(body)
            action = next(self.actions)
            usage = {"completion_tokens": 5, "prompt_tokens": 10, "total_tokens": 15}
            if action == "incomplete":
                raise ReasonerError(
                    "model_output_incomplete",
                    usage=usage,
                    diagnostic={"request_sha256": request_hash},
                )
            content = action if isinstance(action, str) else json.dumps(action)
            return {
                "content": content,
                "usage": usage,
                "request_sha256": request_hash,
                "response_sha256": checker.digest(content),
                "finish_reason": "stop",
                "wall_seconds": 0.0,
            }

    directory = Path(tempfile.mkdtemp(prefix="nuria-supplement-host-fixture-"))
    os.chmod(directory, 0o700)
    memory = ProceduralMemory(
        directory / "memory.sqlite",
        source_id="fixture",
        model_id="fixture",
        tool_version="fixture",
    )
    reasoner = Reasoner()
    workspace = Workspace()
    task = {"id": "fixture", "question": "What is the sum?", "db_id": "events"}
    try:
        agent = GroundedAnalyst(
            reasoner, workspace, memory, initial_tool=initial_tool
        ).run(
            job_id=task["id"],
            question=task["question"],
            db_id=task["db_id"],
            seed=20261025,
        )
    finally:
        memory.close()
    return agent, task, workspace.tool_schema, reasoner.calls


class CheckerTests(unittest.TestCase):
    def test_complete_condition_partial_control(self):
        self.assertEqual(
            checker.check_coverage(*course_fixture(13)),
            {"schema_first": True, "stateless": False},
        )

    def test_incomplete_condition_stays_unknown(self):
        self.assertEqual(
            checker.check_coverage(*course_fixture(11)),
            {"schema_first": False, "stateless": False},
        )

    def test_partial_grade_rejected(self):
        fixture = list(course_fixture(11))
        fixture[0][0]["evaluation"] = {"accepted": True}
        with self.assertRaises(ValueError):
            checker.check_coverage(*fixture)

    def test_missing_selected_record_rejected(self):
        fixture = list(course_fixture(12))
        fixture[0].pop()
        with self.assertRaises(ValueError):
            checker.check_coverage(*fixture)

    def test_changed_committed_proposal_rejected(self):
        fixture = list(course_fixture(12))
        fixture[0][0]["status"] = "changed"
        with self.assertRaises(ValueError):
            checker.check_coverage(*fixture)

    def test_conservative_closed_account(self):
        reserved = {
            "event": "job_reserved",
            "condition": "schema_first",
            "task_id": "x",
            **state(pending=6000),
        }
        closed = {"event": "job_closed", **state(known=200, prompt=800, elapsed=2)}
        proposals = [
            {
                "condition": "schema_first",
                "task_id": "x",
                "observed_usage": {
                    "completion_tokens": 200,
                    "prompt_tokens": 800,
                    "unknown_additional_usage": False,
                },
            }
        ]
        result = checker.check_allowance(
            [reserved, closed],
            proposals,
            {
                "allowance": state(
                    known=200,
                    prompt=800,
                    elapsed=3600,
                    stop="supplementary_wall_or_runtime_deadline",
                )
            },
        )
        self.assertEqual(result["cumulative_conservative_charge"], 186303)
        self.assertEqual(result["historical_unknown_completion_ceiling"], 12214)

    def test_unknown_usage_retains_remainder(self):
        reserved = {
            "event": "job_reserved",
            "condition": "schema_first",
            "task_id": "x",
            **state(pending=6000),
        }
        closed = {
            "event": "job_closed",
            **state(
                known=200,
                prompt=800,
                pending=5800,
                unknown=True,
                elapsed=2,
                stop="new_unreconciled_model_usage",
            ),
        }
        proposals = [
            {
                "condition": "schema_first",
                "task_id": "x",
                "observed_usage": {
                    "completion_tokens": 200,
                    "prompt_tokens": 800,
                    "unknown_additional_usage": True,
                },
            }
        ]
        result = checker.check_allowance(
            [reserved, closed],
            proposals,
            {"allowance": {k: v for k, v in closed.items() if k != "event"}},
        )
        self.assertEqual(result["pending_completion_ceiling"], 5800)
        altered = copy.deepcopy(closed)
        altered["pending_completion_reservation"] = 0
        with self.assertRaises(ValueError):
            checker.check_allowance(
                [reserved, altered], proposals, {"allowance": altered}
            )

    def test_extra_call_after_unknown_rejected(self):
        proposal = {
            "condition": "schema_first",
            "task_id": "x",
            "observed_usage": {
                "completion_tokens": 200,
                "prompt_tokens": 800,
                "unknown_additional_usage": True,
            },
        }
        reserved = {
            "event": "job_reserved",
            "condition": "schema_first",
            "task_id": "x",
            **state(pending=6000),
        }
        closed = {
            "event": "job_closed",
            **state(
                known=200,
                prompt=800,
                pending=5800,
                unknown=True,
                elapsed=2,
                stop="new_unreconciled_model_usage",
            ),
        }
        with self.assertRaises(ValueError):
            checker.check_allowance(
                [reserved, closed, reserved, closed],
                [proposal, proposal],
                {"allowance": closed},
            )

    def test_shared_extra_cap_and_historical_charge(self):
        with self.assertRaises(ValueError):
            checker.account_fields(
                state(known=56502), known=56502, prompt=0, pending=0, unknown=False
            )
        altered = state()
        altered["prior_conservative_charge"] -= 12214
        with self.assertRaises(ValueError):
            checker.account_fields(altered, known=0, prompt=0, pending=0, unknown=False)

    def test_unmatched_reservation_not_closed(self):
        with self.assertRaises(ValueError):
            checker.check_allowance(
                [{"event": "job_reserved"}], [], {"allowance": state()}
            )

    def test_active_guard_precedes_record_access(self):
        with (
            patch.object(checker, "alive", return_value=True),
            patch.object(Path, "is_file", side_effect=AssertionError("record opened")),
            self.assertRaises(ValueError),
        ):
            checker.ended_guard(Path("fixture"), [123], True)

    def test_canonical_model_request_and_tamper(self):
        root = Path(os.environ.get("NURIA_TEST_REPOSITORY", HERE.parents[4]))
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root))
        from cognition.grounded_agent import SYSTEM
        from cognition.working_evidence import WorkingEvidence

        task = {"id": "fixture", "question": "What is the sum?", "db_id": "sample"}
        schema = {"query": {"sql": "SELECT"}}
        initial = {
            "job_id": "fixture",
            "question": task["question"],
            "db_id": "sample",
            "tools": schema,
            "historical_experience": "",
            "neural_context": None,
            "allowance": {"turns": 12, "completion_tokens": 6000},
            "current_evidence": WorkingEvidence().view(),
        }
        body = {
            "model": "fixture",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(initial, ensure_ascii=False)},
            ],
            "max_tokens": 1536,
            "temperature": 0,
            "seed": 20261025,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        event = {
            "kind": "invalid_action",
            "turn": 0,
            "model": {
                "request_sha256": checker.digest(body),
                "usage": {
                    "completion_tokens": 1,
                    "prompt_tokens": 1,
                    "total_tokens": 2,
                },
            },
        }
        agent = {"trace": [event]}
        self.assertEqual(
            checker.replay_request_hashes(
                agent, task, {"model_alias": "fixture"}, schema
            ),
            1,
        )
        event["model"]["request_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            checker.replay_request_hashes(
                agent, task, {"model_alias": "fixture"}, schema
            )

    def test_schema_inspection_then_query_and_final_requests(self):
        query = {
            "tool": "query",
            "arguments": {"sql": "SELECT sum(amount) FROM events"},
        }
        final = {"tool": "final", "arguments": query["arguments"]}
        agent, task, schema, calls = scripted_host_run(
            [query, final], initial_tool="read_knowledge"
        )
        self.assertEqual(agent["status"], "proposed")
        self.assertEqual(
            checker.replay_request_hashes(
                agent, task, {"model_alias": "fixture"}, schema
            ),
            calls,
        )
        changed = copy.deepcopy(agent)
        next(r for r in changed["trace"] if r["kind"] == "tool_receipt")["receipt"][
            "content"
        ] = "different schema"
        with self.assertRaises(ValueError):
            checker.replay_request_hashes(
                changed, task, {"model_alias": "fixture"}, schema
            )

    def test_unmatched_final_retry_requests(self):
        query = {
            "tool": "query",
            "arguments": {"sql": "SELECT sum(amount) FROM events"},
        }
        final = {"tool": "final", "arguments": query["arguments"]}
        agent, task, schema, calls = scripted_host_run([final, query, final])
        self.assertEqual(agent["status"], "proposed")
        self.assertEqual(
            checker.replay_request_hashes(
                agent, task, {"model_alias": "fixture"}, schema
            ),
            calls,
        )

    def test_invalid_incomplete_and_truncated_history_requests(self):
        query = {
            "tool": "query",
            "arguments": {"sql": "SELECT sum(amount) FROM events"},
        }
        sequence = [
            "invalid JSON",
            "incomplete",
            {"tool": "list_tables", "arguments": {}},
            {"tool": "read_knowledge", "arguments": {"section": "columns"}},
            {"tool": "describe_table", "arguments": {"table": "events"}},
            query,
            {"tool": "final", "arguments": query["arguments"]},
        ]
        agent, task, schema, calls = scripted_host_run(sequence)
        self.assertEqual(agent["status"], "proposed")
        self.assertEqual(calls, 7)
        self.assertEqual(
            checker.replay_request_hashes(
                agent, task, {"model_alias": "fixture"}, schema
            ),
            calls,
        )

    def test_sticky_known_stop_rejects_further_reservation(self):
        proposal = {
            "condition": "schema_first",
            "task_id": "x",
            "status": "host_error",
            "observed_usage": {
                "completion_tokens": 0,
                "prompt_tokens": 0,
                "unknown_additional_usage": False,
            },
        }
        reserved = {
            "event": "job_reserved",
            "condition": "schema_first",
            "task_id": "x",
            **state(pending=6000),
        }
        closed = {
            "event": "job_closed",
            **state(elapsed=2, stop="supplementary_host_error_requires_review"),
        }
        with self.assertRaises(ValueError):
            checker.check_allowance(
                [reserved, closed, reserved, closed],
                [proposal, proposal],
                {"allowance": closed},
            )

    def test_unjustified_early_stop_and_cap_stop(self):
        with self.assertRaises(ValueError):
            checker.check_allowance([], [], {"allowance": state()})
        with self.assertRaises(ValueError):
            checker.check_allowance(
                [],
                [],
                {"allowance": state(stop="additional_completion_reservation_limit")},
            )

    def test_changed_inference_end_snapshot(self):
        allowance = {
            "known_completion_tokens": 200,
            "known_prompt_tokens": 800,
            "pending_completion_ceiling": 0,
            "new_unknown_usage": False,
        }
        final = state(known=200, prompt=800, elapsed=3)
        events = [
            {
                "event": "supplementary_started",
                "original_study_mutated": False,
                "account": state(),
            },
            {
                "event": "all_supplementary_inference_ended",
                "account": state(known=200, prompt=800, elapsed=2),
            },
        ]
        checker.check_events(events, {"allowance": final}, allowance)
        events[1]["account"]["new_known_completion_tokens"] += 1
        with self.assertRaises(ValueError):
            checker.check_events(events, {"allowance": final}, allowance)

    def test_archive_traversal_and_corruption(self):
        directory = Path(tempfile.mkdtemp(prefix="nuria-supplement-check-"))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escape", b"x")
        raw = buffer.getvalue()
        (directory / "retained-controls.zip").write_bytes(raw)
        manifest = {
            "schema": "nuria.supplementary-controls-package.v1",
            "protocol_sha256": checker.PROTOCOL_SHA,
            "closed_before_packaging": True,
            "archive_sha256": checker.sha(raw),
            "files": [{"path": "../escape", "bytes": 1, "sha256": checker.sha(b"x")}],
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            checker.package_records(directory)
        manifest["archive_sha256"] = "0" * 64
        (directory / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            checker.package_records(directory)


if __name__ == "__main__":
    unittest.main()
