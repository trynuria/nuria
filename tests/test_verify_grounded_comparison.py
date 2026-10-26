"""Offline consistency and tamper cases, not benchmark or neural gain results."""

import copy
import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from cognition.analyst_workspace import AnalystWorkspace
from cognition.grounded_agent import GroundedAnalyst
from cognition.procedural_memory import ProceduralMemory
from scripts import verify_grounded_comparison as verification


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


class ScriptedReasoner:
    enable_thinking = False

    def __init__(self, sql):
        self.sql, self.calls = sql, 0

    def complete(self, messages, *, max_tokens, seed):
        action = {"tool": "query", "arguments": {"sql": self.sql}}
        if self.calls:
            action = {
                "tool": "final",
                "arguments": {
                    "sql": self.sql,
                    "procedure": "Sum the additive amount with a NULL-aware aggregate.",
                },
            }
        self.calls += 1
        content = json.dumps(action)
        return {
            "content": content,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "request_sha256": verification.digest(messages),
            "response_sha256": verification.digest(content),
            "finish_reason": "stop",
            "wall_seconds": 0.0,
        }


class ComparisonVerifierTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("grounded-verifier-" + uuid.uuid4().hex)
        )
        self.root.mkdir(mode=0o700, parents=True)
        self.database = self.root / "fixture.sqlite"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE events(amount INTEGER)")
            connection.executemany(
                "INSERT INTO events VALUES(?)", [(1,), (1,), (None,)]
            )
        docs = {}
        for section in ("schema", "business", "columns"):
            path = self.root / (section + ".txt")
            path.write_text("events has one additive amount column; NULL means unknown")
            docs[section] = {"path": path.name, "sha256": verification.sha(path)}
        self.registry = {
            "schema": "nuria.analyst-workspace.v1",
            "revision": "f" * 40,
            "databases": {
                "fixture": {
                    "database": {
                        "path": self.database.name,
                        "sha256": verification.sha(self.database),
                    },
                    "documents": docs,
                }
            },
        }
        self.registry_path = self.root / "registry.json"
        write(self.registry_path, self.registry)
        self.binding = {
            "schema": "nuria.procedural-memory.v1",
            "source_id": "f" * 40,
            "model_id": "offline-fixture",
            "tool_version": "nuria.analyst-workspace.v1",
        }

    def agent_fixture(self):
        directory = self.root / "agent"
        directory.mkdir(mode=0o700)
        memory = ProceduralMemory(
            directory / "memory.sqlite",
            source_id=self.binding["source_id"],
            model_id=self.binding["model_id"],
            tool_version=self.binding["tool_version"],
        )
        try:
            task = {
                "id": "fixture-job",
                "db_id": "fixture",
                "question": "What is the sum?",
            }
            sql = "SELECT sum(amount) FROM events"
            agent = GroundedAnalyst(
                ScriptedReasoner(sql),
                AnalystWorkspace(self.registry_path),
                memory,
                memory_mode="none",
            ).run(
                job_id=task["id"],
                question=task["question"],
                db_id=task["db_id"],
                seed=20261025,
            )
            write(directory / "memory-export.json", memory.export())
        finally:
            memory.close()
        episodes, _ = verification.memory_records(directory, self.binding)
        sources = {
            "cognition/analyst_workspace.py": verification.sha(
                Path(__file__).resolve().parents[1] / "cognition/analyst_workspace.py"
            )
        }
        return directory, agent, task, episodes[agent["episode_id"]], sources

    def check_agent(self, agent, task, records, sources):
        return verification.verify_agent(
            agent,
            task,
            "stateless",
            self.registry,
            verification.digest(self.registry, ascii=False),
            records,
            sources,
        )

    def completed_empty_run(self):
        self.run = self.root / "run"
        self.run.mkdir(mode=0o700)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        for name in verification.SOURCE:
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                'SYSTEM = "fixture-system"\n'
                if name.endswith("grounded_agent.py")
                else "# fixture source\n"
            )
        courses = {}
        oracle = {"tasks": {}}
        for phase, count in (("teaching", 8), ("reserved", 12)):
            courses[phase] = [
                {
                    "id": phase + str(i),
                    "db_id": "fixture",
                    "question": "Fixture question " + str(i),
                }
                for i in range(count)
            ]
            write(self.root / (phase + ".json"), {"tasks": courses[phase]})
            for task in courses[phase]:
                oracle["tasks"][task["id"]] = {
                    "db_id": "fixture",
                    "split": phase,
                    "gradeable": True,
                    "expected_rows": [[2]],
                    "column_count": 1,
                    "ordered": False,
                    "reference_sql": "SELECT sum(amount) FROM events",
                }
        write(self.root / "oracle.json", oracle)
        self.protocol = {
            "schema": verification.VERSION,
            "arms": list(verification.ARMS),
            "execution_schedule": "primary-pair-then-secondary-blocks-v2",
            "comparison_groups": [list(group) for group in verification.GROUPS],
            "seed": 20261025,
            "limits": verification.LIMITS,
            "global_completion_cap": 200000,
            "global_wall_seconds": 10800,
            "prior_known_completion_tokens": 194001,
            "prior_unknown_completion_ceiling": 0,
            "prior_known_prompt_tokens": 0,
            "prior_prompt_tokens_unknown": False,
            "prior_usage_unknown": False,
            "prior_wall_seconds": 0,
            "model_identity": "offline-fixture",
            "enable_thinking": False,
            "selected_task_ids": {k: [t["id"] for t in v] for k, v in courses.items()},
            "artifacts": {
                k: {"path": v, "sha256": verification.sha(self.root / v)}
                for k, v in (
                    ("registry", "registry.json"),
                    ("teaching_course", "teaching.json"),
                    ("reserved_course", "reserved.json"),
                    ("oracle", "oracle.json"),
                )
            },
        }
        self.protocol_path = self.root / "protocol.json"
        write(self.protocol_path, self.protocol)
        self.freeze = {
            "schema": verification.VERSION,
            "parent_commit": "a" * 40,
            "protocol_sha256": verification.sha(self.protocol_path),
            "files": {
                name: verification.sha(self.repository / name)
                for name in verification.SOURCE
            },
        }
        self.freeze_path = self.root / "freeze.json"
        write(self.freeze_path, self.freeze)
        write(
            self.run / "run-freeze.json",
            {
                "protocol": self.protocol,
                "source_freeze": self.freeze,
                "source_freeze_sha256": verification.sha(self.freeze_path),
            },
        )
        selected, events = [], []
        for group_index, group in enumerate(verification.GROUPS):
            block_number = 0
            for phase, course in courses.items():
                for task in course:
                    offset = block_number % len(group)
                    arms = group[offset:] + group[:offset]
                    block_number += 1
                    events.append(
                        {
                            "event": "matched_block",
                            "phase": phase,
                            "task_id": task["id"],
                            "arm_order": list(arms),
                            "permitted": False,
                            "required_completion_headroom": 6000 * len(group),
                            "known_completion_tokens": 194001,
                            "elapsed_global_seconds": 1.0,
                        }
                    )
                    for arm in arms:
                        selected.append(
                            {
                                "arm": arm,
                                "comparison_group": group_index,
                                "phase": phase,
                                "task_id": task["id"],
                                "db_id": "fixture",
                                "agent": None,
                                "settlement": None,
                                "status": "not_executed",
                                "attempted": False,
                                "reason": "global_matched_block_reservation_limit",
                                "observed_usage": {
                                    "prompt_tokens": 0,
                                    "completion_tokens": 0,
                                    "unknown_additional_usage": False,
                                },
                            }
                        )
        for arm in verification.ARMS:
            directory = self.run / arm
            directory.mkdir(mode=0o700)
            memory = ProceduralMemory(
                directory / "memory.sqlite",
                source_id=self.binding["source_id"],
                model_id=self.binding["model_id"],
                tool_version=self.binding["tool_version"],
            )
            try:
                write(directory / "memory-export.json", memory.export())
            finally:
                memory.close()
        self.summary = {
            "schema": verification.VERSION,
            "allowance": {
                "known_completion_tokens": 194001,
                "prior_unknown_completion_ceiling": 0,
                "pending_completion_reservation": 0,
                "current_unknown_usage": False,
                "conservative_completion_charge": 194001,
                "stop_reason": "global_matched_block_reservation_limit",
            },
            "arms": {
                arm: {
                    phase: verification.summarize(
                        [r for r in selected if r["arm"] == arm and r["phase"] == phase]
                    )
                    for phase in courses
                }
                for arm in verification.ARMS
            },
            "primary_arms": list(verification.GROUPS[0]),
            "primary_terminal_results_settled": False,
            "primary_complete": False,
            "all_terminal_results_settled": False,
            "source_freeze_verified_at_end": True,
            "complete": False,
        }
        write(self.run / "selected-results.json", selected)
        write(self.run / "summary.json", self.summary)
        (self.run / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events)
        )
        return selected

    def verify_empty(self):
        return verification.verify(
            self.run,
            self.protocol_path,
            self.freeze_path,
            verification.sha(self.freeze_path),
            repository=self.repository,
        )

    def test_unfinished_run_refuses_before_reading_any_artifact(self):
        run = self.root / "unfinished"
        run.mkdir()
        with self.assertRaisesRegex(ValueError, "Finished summary"):
            verification.verify(
                run,
                self.root / "missing-protocol",
                self.root / "missing-freeze",
                "0" * 64,
            )

    def test_complete_selected_denominator_can_honestly_report_no_execution(self):
        self.completed_empty_run()
        report = self.verify_empty()
        self.assertTrue(report["passed"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["selected_records"], 120)
        self.assertEqual(report["attempted_records"], 0)

    def test_missing_selected_record_is_rejected(self):
        selected = self.completed_empty_run()
        write(self.run / "selected-results.json", selected[:-1])
        with self.assertRaisesRegex(ValueError, "120 selected"):
            self.verify_empty()

    def test_reordered_method_task_records_are_rejected(self):
        selected = self.completed_empty_run()
        selected[0], selected[1] = selected[1], selected[0]
        write(self.run / "selected-results.json", selected)
        with self.assertRaisesRegex(ValueError, "planned order"):
            self.verify_empty()

    def test_changed_frozen_source_is_rejected(self):
        self.completed_empty_run()
        (self.repository / verification.SOURCE[0]).write_text("changed fixture source")
        with self.assertRaisesRegex(ValueError, "frozen source"):
            self.verify_empty()

    def test_actual_own_sql_receipt_and_model_accounting_pass(self):
        _, agent, task, records, sources = self.agent_fixture()
        usage = self.check_agent(agent, task, records, sources)
        self.assertEqual(usage, {"prompt_tokens": 20, "completion_tokens": 10})
        rows, columns = verification.sql_result(self.database, agent["final_sql"])
        self.assertEqual(rows, [[2]])
        self.assertEqual(columns, 1)

    def test_forged_selected_rows_fail_even_after_agent_checksum_is_rebuilt(self):
        _, agent, task, records, sources = self.agent_fixture()
        agent["selected_receipt"]["rows"] = [[999]]
        agent["result_sha256"] = verification.digest(
            {k: v for k, v in agent.items() if k != "result_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "own execution"):
            self.check_agent(agent, task, records, sources)

    def test_forged_usage_fails_even_after_agent_checksum_is_rebuilt(self):
        _, agent, task, records, sources = self.agent_fixture()
        agent["model_usage"]["completion_tokens"] += 1
        agent["model_usage"]["total_tokens"] += 1
        agent["result_sha256"] = verification.digest(
            {k: v for k, v in agent.items() if k != "result_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "token accounting"):
            self.check_agent(agent, task, records, sources)

    def test_final_sql_cannot_be_rebound_to_another_executed_answer(self):
        _, agent, task, records, sources = self.agent_fixture()
        agent["final_sql"] = "SELECT max(amount) FROM events"
        agent["result_sha256"] = verification.digest(
            {k: v for k, v in agent.items() if k != "result_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "Final proposal"):
            self.check_agent(agent, task, records, sources)

    def test_memory_tampering_is_detected_without_opening_a_writer(self):
        directory, _, _, _, _ = self.agent_fixture()
        with (
            closing(sqlite3.connect(directory / "memory.sqlite")) as connection,
            connection,
        ):
            connection.execute(
                "UPDATE memory_events SET payload=replace(payload,'What is the sum?','Different question') WHERE kind='begin'"
            )
        with self.assertRaisesRegex(ValueError, "journal chain"):
            verification.memory_records(directory, self.binding)

    def test_retrieval_reconstruction_excludes_later_terminal_labels(self):
        directory, first, _, _, sources = self.agent_fixture()
        memory = ProceduralMemory(
            directory / "memory.sqlite",
            source_id=self.binding["source_id"],
            model_id=self.binding["model_id"],
            tool_version=self.binding["tool_version"],
        )
        try:
            memory.settle(
                first["episode_id"],
                verify_terminal=lambda request: {
                    "accepted": True,
                    "evidence_sha256": "0" * 64,
                    "checker_id": "fixture-only",
                },
            )
            task = {
                "id": "second-job",
                "db_id": "fixture",
                "question": "What is the sum again?",
            }
            second = GroundedAnalyst(
                ScriptedReasoner("SELECT sum(amount) FROM events"),
                AnalystWorkspace(self.registry_path),
                memory,
                memory_mode="procedure",
            ).run(
                job_id=task["id"],
                question=task["question"],
                db_id=task["db_id"],
                seed=20261025,
            )
            self.assertEqual(second["memory_retrieval"]["item_count"], 1)
            memory.settle(
                second["episode_id"],
                verify_terminal=lambda request: {
                    "accepted": False,
                    "evidence_sha256": "1" * 64,
                    "checker_id": "later-fixture",
                },
            )
            write(directory / "memory-export.json", memory.export())
        finally:
            memory.close()
        episodes, records = verification.memory_records(directory, self.binding)
        verification.verify_agent(
            second,
            task,
            "procedures",
            self.registry,
            verification.digest(self.registry, ascii=False),
            episodes[second["episode_id"]],
            sources,
            history=records,
        )
        changed = copy.deepcopy(second)
        changed["memory_retrieval"]["item_count"] = 2
        changed["result_sha256"] = verification.digest(
            {k: v for k, v in changed.items() if k != "result_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "Memory retrieval"):
            verification.verify_agent(
                changed,
                task,
                "procedures",
                self.registry,
                verification.digest(self.registry, ascii=False),
                episodes[second["episode_id"]],
                sources,
                history=records,
            )

    def test_read_only_replay_rejects_writes_attaches_and_extension_loading(self):
        original = verification.sha(self.database)
        for sql in (
            "DELETE FROM events",
            "ATTACH DATABASE ':memory:' AS other",
            "SELECT load_extension('never-load')",
            "PRAGMA journal_mode=WAL",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                verification.sql_result(self.database, sql)
        self.assertEqual(verification.sha(self.database), original)

    def test_typed_duplicate_null_and_ordering_comparison(self):
        self.assertFalse(verification.rows_equal([[1]], [[1.0]], False))
        self.assertFalse(verification.rows_equal([[None]], [["NULL"]], False))
        self.assertFalse(verification.rows_equal([[1], [1]], [[1]], False))
        self.assertTrue(verification.rows_equal([[1], [None]], [[None], [1]], False))
        self.assertFalse(verification.rows_equal([[1], [None]], [[None], [1]], True))

    def test_unknown_usage_liability_cannot_be_released_or_followed_by_a_retry(self):
        proposal = {
            "arm": "stateless",
            "phase": "teaching",
            "task_id": "one",
            "observed_usage": {
                "completion_tokens": 13,
                "prompt_tokens": 20,
                "unknown_additional_usage": True,
            },
        }
        protocol = {
            "prior_known_completion_tokens": 7,
            "prior_unknown_completion_ceiling": 768,
            "prior_wall_seconds": 0,
            "global_completion_cap": 200000,
        }
        reserve = {
            "event": "job_reserved",
            "arm": "stateless",
            "phase": "teaching",
            "task_id": "one",
            "known_completion_tokens": 7,
            "prior_unknown_completion_ceiling": 768,
            "pending_completion_reservation": 6000,
            "conservative_completion_charge": 6775,
            "elapsed_global_seconds": 1.0,
        }
        close = {
            "event": "job_closed",
            "known_completion_tokens": 20,
            "prior_unknown_completion_ceiling": 768,
            "pending_completion_reservation": 5987,
            "conservative_completion_charge": 6775,
            "elapsed_global_seconds": 2.0,
            "current_unknown_usage": True,
        }
        summary = {"allowance": copy.deepcopy(close)}
        verification.verify_allowance([reserve, close], [proposal], protocol, summary)
        altered = copy.deepcopy(summary)
        altered["allowance"]["pending_completion_reservation"] = 0
        with self.assertRaisesRegex(ValueError, "Summary allowance"):
            verification.verify_allowance(
                [reserve, close], [proposal], protocol, altered
            )
        with self.assertRaisesRegex(ValueError, "reservation"):
            verification.verify_allowance(
                [reserve, close, reserve, close],
                [proposal, proposal],
                protocol,
                summary,
            )

    def test_neural_capsule_hashes_are_checked_without_unpickling(self):
        directory = self.root / "brain"
        terms, lab = {"fixture": "not an actual neural experiment"}, "a" * 32
        write(directory / "LAB.json", {"terms": terms, "lab_id": lab})
        origin = {"job_id": "one", "fixture": True}
        decision = {"job_id": "one", "origin_sha256": verification.digest(origin)}
        decision["decision_sha256"] = verification.digest(decision)
        state = {
            "terms_sha256": verification.digest(terms),
            "lab_id": lab,
            "jobs": {
                "one": {"origin": origin, "decision": decision, "settlement": None}
            },
        }
        capsule = directory / "capsules" / ("000000-" + "b" * 16)
        capsule.mkdir(parents=True)
        (capsule / "network.pkl").write_bytes(
            b"This fixture is deliberately not a pickle."
        )
        write(capsule / "metadata.json", {"state": state})
        manifest = {
            "sequence": 0,
            "terms_sha256": verification.digest(terms),
            "lab_id": lab,
            "previous_capsule": None,
            "previous_manifest_sha256": None,
            "metadata.json": verification.sha(capsule / "metadata.json"),
            "network.pkl": verification.sha(capsule / "network.pkl"),
        }
        write(capsule / "manifest.json", manifest)
        write(
            directory / "HEAD.json",
            {
                "capsule": capsule.name,
                "manifest_sha256": verification.sha(capsule / "manifest.json"),
                "sequence": 0,
            },
        )
        rows = [{"task_id": "one", "neural_decision": decision}]
        report = verification.verify_capsules(directory, rows)
        self.assertFalse(report["pickle_loaded"])
        self.assertEqual(report["capsules"], 1)
        (capsule / "network.pkl").write_bytes(b"tampered content")
        with self.assertRaisesRegex(ValueError, "payload checksum"):
            verification.verify_capsules(directory, rows)


if __name__ == "__main__":
    unittest.main()
