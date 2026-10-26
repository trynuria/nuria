"""Synthetic-only tests: no live experiment, model, course or oracle reads."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location(
    "supplement_proposal_03", HERE / "evaluate_controls.py"
)
proposal = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proposal
spec.loader.exec_module(proposal)
sys.path.insert(0, str(ROOT))
SCHEMA = importlib.import_module("cognition.analyst_workspace").SCHEMA
reasoner_module = importlib.import_module("cognition.reasoner_client")
ReasonerError, digest = reasoner_module.ReasonerError, reasoner_module.digest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


class SupplementTests(unittest.TestCase):
    def setUp(self):
        # Preserve every synthetic fixture rather than deleting test artifacts.
        self.directory = Path(tempfile.mkdtemp(prefix="nuria-supplement-fixture-"))
        self.original = self.directory / "original"
        self.main = self.directory / "main"
        self.post = self.directory / "post"
        self.main.mkdir()
        self.post.mkdir()
        inputs = self.original / "inputs"
        inputs.mkdir(parents=True)
        database = inputs / "events.sqlite"
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE events(amount INTEGER)")
            db.execute("INSERT INTO events VALUES(7)")
        docs = {}
        for section in ("business", "columns", "schema"):
            path = inputs / (section + ".txt")
            path.write_text("events(amount INTEGER): additive amount")
            docs[section] = {"path": path.name, "sha256": proposal.sha(path)}
        registry = {
            "schema": SCHEMA,
            "revision": "sha256:" + hashlib.sha256(b"fixture-source").hexdigest(),
            "databases": {
                "events": {
                    "database": {
                        "path": database.name,
                        "sha256": proposal.sha(database),
                    },
                    "documents": docs,
                }
            },
        }
        write(inputs / "registry.json", registry)
        self.tasks = [
            {
                "id": "reserved-fixture-" + str(i),
                "db_id": "events",
                "question": "What is the total amount?",
            }
            for i in range(12)
        ]
        write(inputs / "reserved.json", {"tasks": self.tasks})
        write(inputs / "teaching.json", {"tasks": []})
        write(
            inputs / "oracle.json",
            {
                "tasks": {
                    t["id"]: {
                        "expected_rows": [[7]],
                        "column_count": 1,
                        "ordered": False,
                        "db_id": "events",
                    }
                    for t in self.tasks
                }
            },
        )
        protocol = {
            "selected_task_ids": {"reserved": [t["id"] for t in self.tasks]},
            "endpoint": "http://127.0.0.1:1/v1/chat/completions",
            "model_alias": "fixture",
            "model_identity": "synthetic-fixed-reasoner",
            "enable_thinking": True,
            "artifacts": {
                key: {
                    "path": "inputs/" + name + ".json",
                    "sha256": proposal.sha(inputs / (name + ".json")),
                }
                for key, name in [
                    ("registry", "registry"),
                    ("teaching_course", "teaching"),
                    ("reserved_course", "reserved"),
                    ("oracle", "oracle"),
                ]
            },
        }
        self.original_protocol = self.original / "protocol.json"
        write(self.original_protocol, protocol)
        names = [
            "cognition/reasoner_client.py",
            "cognition/grounded_agent.py",
            "cognition/analyst_workspace.py",
            "cognition/procedural_memory.py",
            "cognition/working_evidence.py",
            "cognition/hybrid_neural.py",
            "cognition/brain.py",
            "cognition/task_brain.py",
            "scripts/evaluate_grounded_agent.py",
            "requirements.txt",
            "research/hybrid-agent/attempts/comparison-03-protocol.json",
            "research/hybrid-agent/runtime/model-manifest.json",
            "research/hybrid-agent/runtime/requirements-macos-arm64.lock",
            "scripts/ask_grounded_analyst.py",
            "scripts/run_grounded_agent.py",
            "scripts/register_work_snapshot.py",
            "scripts/verify_grounded_comparison.py",
        ]
        self.freeze = self.original / "source-freeze.json"
        write(self.freeze, {"files": {n: proposal.sha(ROOT / n) for n in names}})
        self.main_check = self.directory / "independent.json"
        write(
            self.main_check,
            {
                "schema": "nuria.grounded-comparison-verification.v1",
                "passed": True,
                "protocol_sha256": proposal.sha(self.original_protocol),
                "source_freeze_sha256": proposal.sha(self.freeze),
                "selected_records": 120,
            },
        )
        selected = [
            {"arm": arm, "phase": phase, "task_id": taskid, "agent": None}
            for arm in (
                "stateless",
                "episodes",
                "procedures",
                "schema_first",
                "hybrid_fixed",
                "hybrid_neural",
            )
            for phase, ids in [
                ("teaching", ["teaching-fixture-" + str(i) for i in range(8)]),
                ("reserved", [t["id"] for t in self.tasks]),
            ]
            for taskid in ids
        ]
        write(self.main / "selected-results.json", selected)
        self.base = {
            "known_completion_tokens": 190000,
            "prior_unknown_completion_ceiling": 768,
            "pending_completion_reservation": 0,
            "current_unknown_usage": False,
            "prior_usage_unknown": True,
            "conservative_completion_charge": 190768,
        }
        write(self.main / "summary.json", {"allowance": self.base})
        (self.post / "postrun-04-lifecycle.jsonl").write_text(
            json.dumps({"event": "useful_source_deferred"}) + "\n"
        )
        self.context = {
            "repository": ROOT,
            "original_protocol": self.original_protocol,
            "original_freeze": self.freeze,
            "main_run": self.main,
            "main_check": self.main_check,
            "post_root": self.post,
            "guard_pids": [2147483647],
            "confirmed": True,
        }
        self.protocol_path = self.directory / "supplement.json"
        self.publication = self.directory / "publication.json"
        self.calls = []
        self.fixed_now = dt.datetime(2026, 10, 25, 23, 0, tzinfo=dt.timezone.utc)

    def patched(self):
        return patch.multiple(
            proposal,
            ORIGINAL_PROTOCOL=proposal.sha(self.original_protocol),
            ORIGINAL_FREEZE=proposal.sha(self.freeze),
            utcnow=lambda: self.fixed_now,
        )

    def prepare(self):
        with self.patched():
            proposal.prepare_protocol(output=self.protocol_path, **self.context)
        write(
            self.publication,
            {
                "schema": "nuria.supplementary-controls-publication.v1",
                "exact_bytes_verified": True,
                "helper_sha256": proposal.sha(Path(proposal.__file__)),
                "protocol_sha256": proposal.sha(self.protocol_path),
                "public_commit": "a" * 40,
            },
        )

    def factory(self, unknown=False):
        calls = self.calls

        class ScriptedReasoner:
            enable_thinking = True

            def __init__(self, *args, **kwargs):
                pass

            def complete(self, messages, *, max_tokens, seed):
                text = json.dumps(messages)
                assert "expected_rows" not in text and "reference_sql" not in text
                calls.append(
                    {"messages": messages, "seed": seed, "max_tokens": max_tokens}
                )
                if unknown:
                    raise ReasonerError("fixture_ambiguous_usage")
                queried = False
                for message in messages:
                    if message["role"] != "user":
                        continue
                    try:
                        value = json.loads(message["content"])
                    except ValueError:
                        continue
                    queried = (
                        queried
                        or value.get("observed_tool_receipt", {}).get("tool") == "query"
                    )
                action = (
                    {
                        "tool": "final",
                        "arguments": {"sql": "SELECT SUM(amount) FROM events"},
                    }
                    if queried
                    else {
                        "tool": "query",
                        "arguments": {"sql": "SELECT SUM(amount) FROM events"},
                    }
                )
                content = json.dumps(action)
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 5,
                        "total_tokens": 10,
                    },
                    "request_sha256": digest(messages),
                    "response_sha256": digest(content),
                    "finish_reason": "stop",
                    "wall_seconds": 0.0,
                }

        return ScriptedReasoner

    def run_fixture(self, unknown=False):
        with (
            self.patched(),
            patch("cognition.reasoner_client.LocalReasoner", self.factory(unknown)),
        ):
            return proposal.run_supplement(
                protocol_path=self.protocol_path,
                protocol_sha256=proposal.sha(self.protocol_path),
                publication=self.publication,
                publication_sha256=proposal.sha(self.publication),
                output=self.directory / "output",
                inputs_root=self.original,
                **self.context,
            )

    def test_static_preflight_and_live_process_guard_precede_artifact_reads(self):
        with (
            patch.object(proposal, "load", side_effect=AssertionError("No body reads")),
            patch.object(proposal, "sha", side_effect=AssertionError("No reads")),
        ):
            self.assertFalse(proposal.preflight()["artifact_bodies_read"])
            with self.assertRaisesRegex(ValueError, "confirmation"):
                proposal.ended_guard(self.main, [2147483647], False)
            with (
                patch.object(proposal, "alive", return_value=True),
                self.assertRaisesRegex(ValueError, "remains present"),
            ):
                proposal.ended_guard(self.main, [99], True)

    def test_complete_fixed_order_conditions_hold_all_calls_before_grading(self):
        self.prepare()
        original_load = proposal.load
        oracle_call_counts = []

        def load(path):
            if Path(path) == self.original / "inputs/oracle.json":
                oracle_call_counts.append(len(self.calls))
            return original_load(path)

        with patch.object(proposal, "load", side_effect=load):
            summary = self.run_fixture()
        self.assertTrue(summary["complete"])
        self.assertEqual(len(self.calls), 48)
        self.assertEqual(oracle_call_counts, [48])
        for condition in proposal.CONDITIONS:
            self.assertEqual(summary["conditions"][condition]["selected"], 12)
            self.assertEqual(summary["conditions"][condition]["graded"], 12)
            self.assertEqual(summary["conditions"][condition]["accepted"], 12)
        initial = [
            json.loads(call["messages"][1]["content"]) for call in self.calls[::2]
        ]
        self.assertEqual(
            [r["job_id"] for r in initial], [t["id"] for t in self.tasks] * 2
        )
        self.assertTrue(
            all(
                r["historical_experience"] == "" and r["neural_context"] is None
                for r in initial
            )
        )
        self.assertEqual(summary["allowance"]["new_known_completion_tokens"], 240)
        self.assertEqual(
            summary["allowance"]["other_supplementary_reserved_completion_tokens"], 6000
        )
        self.assertEqual(summary["allowance"]["cumulative_conservative_charge"], 197008)
        with self.assertRaises(FileExistsError):
            self.run_fixture()

    def test_original_complete_condition_is_skipped_without_score_selection(self):
        selected = proposal.load(self.main / "selected-results.json")
        for row in selected:
            if row["arm"] == "schema_first" and row["phase"] == "reserved":
                row["agent"] = {"fixture_committed": True}
        write(self.main / "selected-results.json", selected)
        self.prepare()
        summary = self.run_fixture()
        self.assertEqual(len(self.calls), 24)
        self.assertTrue(
            summary["conditions"]["schema_first"]["skipped_original_complete"]
        )
        self.assertEqual(summary["conditions"]["schema_first"]["executed"], 0)
        self.assertEqual(summary["conditions"]["stateless"]["graded"], 12)

    def test_new_ambiguous_usage_stops_and_preserves24denominator(self):
        self.prepare()
        summary = self.run_fixture(unknown=True)
        self.assertFalse(summary["complete"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(summary["planned_records"], 24)
        self.assertEqual(summary["allowance"]["pending_completion_reservation"], 6000)
        self.assertEqual(
            summary["allowance"]["stop_reason"], "new_unreconciled_model_usage"
        )
        self.assertEqual(sum(v["graded"] for v in summary["conditions"].values()), 0)
        self.assertEqual(
            len(proposal.load(self.directory / "output/selected-results.json")), 24
        )

    def test_allowance_preserves_semantic_headroom_and_absolute_deadline(self):
        protocol = {
            "starting_account": {"prior_conservative_charge": 254001},
            "other_supplementary_reserved_completion_tokens": 6000,
            "other_supplementary_known_completion_tokens": 0,
            "absolute_deadline_utc": proposal.DEADLINE,
        }
        a = proposal.Allowance(protocol, clock=lambda: 0, now=lambda: self.fixed_now)
        self.assertFalse(a.reserve())
        self.assertEqual(a.stop, "cumulative_completion_reservation_limit")
        protocol["starting_account"]["prior_conservative_charge"] = 190768
        a = proposal.Allowance(protocol, clock=lambda: 0, now=lambda: self.fixed_now)
        a.known = 48001
        self.assertFalse(a.reserve())
        self.assertEqual(a.stop, "additional_completion_reservation_limit")
        a = proposal.Allowance(
            protocol,
            clock=lambda: 0,
            now=lambda: dt.datetime.fromisoformat(proposal.DEADLINE),
        )
        self.assertFalse(a.reserve())
        self.assertEqual(a.stop, "supplementary_wall_or_runtime_deadline")

    def test_closed_semantic_usage_releases_only_verified_unused_headroom(self):
        semantic = self.post / "semantic-input-01"
        helper = self.directory / "semantic-helper.py"
        helper.write_text("# synthetic semantic helper identity")
        checker = self.post / "supervise_postrun_04.py"
        checker.write_text("# synthetic host checker identity")
        sem_protocol = semantic / "protocol.json"
        model = "synthetic-fixed-reasoner"
        write(
            sem_protocol,
            {
                "schema": "nuria.semantic-work-trial.v1",
                "host_helper_sha256": proposal.sha(helper),
                "host_checker_sha256": proposal.sha(checker),
                "maximum_completion_tokens": 6000,
                "maximum_additional_cpu_encodes": 1,
                "model_identity": model,
                "new_source": {
                    "cognition/reasoner_client.py": proposal.sha(
                        ROOT / "cognition/reasoner_client.py"
                    )
                },
            },
        )
        job = "semantic-recorded-work-audit-01"
        key = hashlib.sha256(job.encode()).hexdigest()
        jobs = semantic / "semantic-work-trial-01/jobs"
        usage = {
            "completion_tokens": 10,
            "prompt_tokens": 10,
            "total_tokens": 20,
            "unknown_additional_usage": False,
        }
        artifact = {
            "job_id": job,
            "episode_id": "e" * 64,
            "proposal_sha256": "f" * 64,
            "model_usage": usage,
            "trace": [
                {
                    "model": {
                        "usage": {
                            "prompt_tokens": 5,
                            "completion_tokens": 5,
                            "total_tokens": 10,
                        }
                    }
                },
                {
                    "model": {
                        "usage": {
                            "prompt_tokens": 5,
                            "completion_tokens": 5,
                            "total_tokens": 10,
                        }
                    }
                },
            ],
        }
        artifact["result_sha256"] = digest(artifact)
        artifact_path = jobs / (key + ".artifact.json")
        write(artifact_path, artifact)
        decision_path = jobs / (key + ".decision.json")
        write(decision_path, {"synthetic_decision": True})
        report = {
            "job_id": job,
            "episode_id": artifact["episode_id"],
            "model_identity": model,
            "artifact_sha256": proposal.sha(artifact_path),
            "decision_file_sha256": proposal.sha(decision_path),
            "model_usage": usage,
        }
        report["result_sha256"] = digest(report)
        write(jobs / (key + ".result.json"), report)
        terminal = {
            "protocol_sha256": proposal.sha(sem_protocol),
            "allowance": {
                **self.base,
                "known_completion_tokens": 190010,
                "conservative_completion_charge": 190778,
                "reserved_cpu_encodes": 1,
                "completed_cpu_encodes": 1,
                "unresolved_cpu_encode_ceiling": 0,
            },
            "qualification": {
                "accepted": True,
                "terminal": {
                    "accepted": True,
                    "episode_id": artifact["episode_id"],
                    "proposal_sha256": artifact["proposal_sha256"],
                },
            },
        }
        write(semantic / "semantic-work-trial-terminal-01.json", terminal)
        self.context.update(
            semantic_protocol=sem_protocol,
            semantic_protocol_sha256=proposal.sha(sem_protocol),
            semantic_helper=helper,
        )
        self.prepare()
        protocol = proposal.load(self.protocol_path)
        self.assertEqual(protocol["other_supplementary_reserved_completion_tokens"], 0)
        self.assertEqual(protocol["other_supplementary_known_completion_tokens"], 10)
        self.assertEqual(
            protocol["starting_account"]["prior_conservative_charge"], 190778
        )
        summary = self.run_fixture()
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["allowance"]["additional_conservative_charge"], 250)
        self.assertEqual(summary["allowance"]["cumulative_conservative_charge"], 191018)
        with self.assertRaisesRegex(ValueError, "pinned closed accounting"):
            proposal.semantic_account(
                ROOT, self.post, proposal.initial_account(self.base)
            )

    def test_fresh_main_ambiguity_refuses_preparation(self):
        write(
            self.main / "summary.json",
            {
                "allowance": {
                    **self.base,
                    "current_unknown_usage": True,
                    "pending_completion_reservation": 6000,
                    "conservative_completion_charge": 196768,
                }
            },
        )
        with self.patched(), self.assertRaisesRegex(ValueError, "Fresh main"):
            proposal.prepare_protocol(output=self.protocol_path, **self.context)
        self.assertEqual(self.calls, [])

    def test_started_post_job_requires_closed_unambiguous_usage(self):
        write(
            self.post / "useful-source-01/postrun-04-reservation.json",
            {
                "schema": "nuria.useful-source-reservation.v1",
                "job_id": "recorded-work-audit-01",
                "allowance": {
                    **self.base,
                    "pending_completion_reservation": 6000,
                    "current_unknown_usage": True,
                    "conservative_completion_charge": 196768,
                },
            },
        )
        with self.patched(), self.assertRaisesRegex(ValueError, "Fresh post-job"):
            proposal.prepare_protocol(output=self.protocol_path, **self.context)
        write(
            self.post / "useful-source-01/postrun-04-accounting.json",
            {
                "job_id": "recorded-work-audit-01",
                "allowance": {
                    **self.base,
                    "known_completion_tokens": 190100,
                    "pending_completion_reservation": 5900,
                    "current_unknown_usage": True,
                    "conservative_completion_charge": 196768,
                },
                "model_usage": {
                    "completion_tokens": 100,
                    "unknown_additional_usage": True,
                },
            },
        )
        with self.patched(), self.assertRaisesRegex(ValueError, "Fresh post-job"):
            proposal.prepare_protocol(output=self.protocol_path, **self.context)
        self.assertEqual(self.calls, [])

    def test_same_protocol_cannot_renew_allowance_with_new_output(self):
        self.prepare()
        self.run_fixture()
        before = len(self.calls)
        with (
            self.patched(),
            patch("cognition.reasoner_client.LocalReasoner", self.factory()),
            self.assertRaises(FileExistsError),
        ):
            proposal.run_supplement(
                protocol_path=self.protocol_path,
                protocol_sha256=proposal.sha(self.protocol_path),
                publication=self.publication,
                publication_sha256=proposal.sha(self.publication),
                output=self.directory / "another-output",
                inputs_root=self.original,
                **self.context,
            )
        self.assertEqual(len(self.calls), before)

    def test_old_unknown_and_post_job_pending_are_never_released(self):
        reservation = {
            "allowance": {
                **self.base,
                "pending_completion_reservation": 6000,
                "current_unknown_usage": True,
                "conservative_completion_charge": 196768,
            }
        }
        account = proposal.initial_account(self.base, reservation)
        self.assertEqual(account["unknown_completion_ceiling"], 6768)
        self.assertEqual(account["prior_conservative_charge"], 196768)
        closed = {
            "allowance": {
                **self.base,
                "known_completion_tokens": 190100,
                "pending_completion_reservation": 5900,
                "current_unknown_usage": True,
                "conservative_completion_charge": 196768,
            },
            "model_usage": {"completion_tokens": 100, "unknown_additional_usage": True},
        }
        account = proposal.initial_account(self.base, reservation, closed)
        self.assertEqual(account["known_completion_tokens"], 190100)
        self.assertEqual(account["unknown_completion_ceiling"], 6668)
        self.assertEqual(account["prior_conservative_charge"], 196768)


if __name__ == "__main__":
    unittest.main(verbosity=2)
