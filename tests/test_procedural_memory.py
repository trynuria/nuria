import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cognition.procedural_memory import ProceduralMemory, canonical, digest


class ProceduralMemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "memory.sqlite3"
        self.binding = {
            "source_id": "test-data@" + "a" * 40,
            "model_id": "test-model@" + "b" * 40 + ":q4",
            "tool_version": "test-select-only-v1@" + "c" * 40,
        }
        self.memory = self.open()
        self.data = sqlite3.connect(":memory:")
        self.data.execute("CREATE TABLE purchases(region TEXT,amount INTEGER)")
        self.data.executemany(
            "INSERT INTO purchases VALUES(?,?)",
            [("east", 7), ("east", 13), ("west", 40)],
        )
        self.addCleanup(self.data.close)
        self.addCleanup(lambda: self.memory.close())
        self.checker_calls = 0

    def open(self, **changes):
        return ProceduralMemory(self.path, **{**self.binding, **changes})

    def episode(self, job="job-1", question="What is total east purchase amount?"):
        return self.memory.begin(job, question, digest("schema-v1"))

    def action(
        self,
        episode,
        sql="SELECT sum(amount) FROM purchases WHERE region='east'",
        action="query-1",
    ):
        try:
            result = self.data.execute(sql).fetchall()
            ok, error = True, None
        except sqlite3.Error:
            result, ok, error = [], False, "sqlite_error"
        return self.memory.record_action(
            episode, action, "query", sql, ok, 3, digest(result), error
        )

    def propose(
        self, episode, sql="SELECT sum(amount) FROM purchases WHERE region='east'"
    ):
        self.action(episode, sql)
        return self.memory.propose(
            episode,
            sql,
            "Aggregate purchase amount after filtering the requested region",
            "The region filter must be applied before summing",
            "A purchases table with region and amount columns is available",
        )

    def checker(self, request):
        self.checker_calls += 1
        observed = self.data.execute(request["final_sql"]).fetchall()
        # Private checker truth is not a memory API argument or retrieved text.
        expected = [(20,)]
        return {
            "accepted": observed == expected,
            "evidence_sha256": digest([request["origin_sha256"], observed, expected]),
            "checker_id": "independent-test-grader-v1",
        }

    def test_only_terminally_checked_own_procedure_enters_retrieval(self):
        episode = self.episode()
        proposal = self.propose(episode)
        self.assertEqual(
            self.memory.retrieve("east purchases", mode="procedure")["item_count"], 0
        )
        receipt = self.memory.settle(episode, verify_terminal=self.checker)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["proposal_sha256"], proposal)
        retained = self.memory.retrieve(
            "east purchases", mode="procedure", max_tokens=6000
        )
        self.assertEqual(retained["item_count"], 1)
        self.assertEqual(len(retained["items"]), 1)
        self.assertIn("Aggregate purchase amount", retained["text"])
        self.assertNotIn("expected", retained["text"])
        self.assertEqual(self.memory.feedback(episode), receipt)

    def test_a_valid_but_wrong_query_is_counterexample_not_a_skill(self):
        episode = self.episode()
        self.propose(episode, "SELECT sum(amount) FROM purchases")
        receipt = self.memory.settle(episode, verify_terminal=self.checker)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["terminal_reward"], 0)
        self.assertEqual(
            self.memory.retrieve("east purchase", mode="procedure")["item_count"], 0
        )
        recalled = self.memory.retrieve(
            "east purchase", mode="episode", max_tokens=6000
        )
        self.assertEqual(recalled["item_count"], 1)
        self.assertFalse(json.loads(recalled["text"])[0]["accepted"])
        statistics = self.memory.outcome_stats("query")
        self.assertEqual(statistics["tool_successes"], 1)
        self.assertNotIn("terminal_reward", statistics)

    def test_tool_and_settlement_retries_do_not_double_count(self):
        episode = self.episode()
        self.propose(episode)
        self.action(episode)
        receipt = self.memory.settle(episode, verify_terminal=self.checker)
        retried = self.memory.settle(
            episode, verify_terminal=lambda request: self.fail("Duplicate checker call")
        )
        self.assertEqual(receipt, retried)
        self.assertEqual(self.checker_calls, 1)
        self.assertEqual(self.memory.outcome_stats("query")["total_cost_units"], 3)
        with self.assertRaises(ValueError):
            self.memory.record_action(
                episode,
                "query-1",
                "query",
                receipt["proposal_sha256"],
                True,
                3,
                digest([]),
            )

    def test_origin_and_trace_freeze_refuse_changed_inputs(self):
        episode = self.episode()
        self.propose(episode)
        self.assertEqual(self.episode(), episode)
        with self.assertRaises(ValueError):
            self.memory.begin("job-1", "Another question", digest("schema-v1"))
        with self.assertRaises(ValueError):
            self.memory.begin(
                "job-1", "What is total east purchase amount?", digest("other-schema")
            )
        with self.assertRaises(ValueError):
            self.action(episode, action="later-query")
        with self.assertRaises(ValueError):
            self.memory.propose(episode, "SELECT 20")

    def test_a_query_from_another_episode_cannot_be_admitted(self):
        original, another = self.episode(), self.episode("job-2")
        self.action(original)
        with self.assertRaises(ValueError):
            self.memory.propose(
                another, "SELECT sum(amount) FROM purchases WHERE region='east'"
            )
        self.action(another, "SELECT missing FROM purchases")
        with self.assertRaises(ValueError):
            self.memory.propose(another, "SELECT missing FROM purchases")
        self.assertEqual(self.memory.outcome_stats("query")["tool_errors"], 1)

    def test_checker_error_remains_pending_and_survives_restart(self):
        episode = self.episode()
        self.propose(episode)

        def unavailable(request):
            raise RuntimeError("Do not persist arbitrary checker exception text")

        failed = self.memory.settle(episode, verify_terminal=unavailable)
        self.assertEqual(failed["status"], "unresolved")
        self.assertIsNone(self.memory.feedback(episode))
        self.assertNotIn("Do not persist", canonical(self.memory.export()))
        self.memory.close()
        self.memory = self.open()
        receipt = self.memory.settle(episode, verify_terminal=self.checker)
        self.assertTrue(receipt["accepted"])
        self.assertTrue(
            any(
                record["kind"] == "checker_error"
                for record in self.memory.export()["records"]
            )
        )

    def test_transaction_failure_cannot_expose_a_qualified_procedure(self):
        episode = self.episode()
        self.propose(episode)
        append = self.memory._append

        def fail_settlement(kind, *args):
            if kind == "settlement":
                raise RuntimeError("Injected transaction failure")
            return append(kind, *args)

        with patch.object(self.memory, "_append", side_effect=fail_settlement):
            with self.assertRaises(RuntimeError):
                self.memory.settle(episode, verify_terminal=self.checker)
        self.assertIsNone(self.memory.feedback(episode))
        self.assertEqual(
            self.memory.retrieve("east purchase", mode="procedure")["item_count"], 0
        )
        self.assertTrue(
            self.memory.settle(episode, verify_terminal=self.checker)["accepted"]
        )

    def test_retrieval_controls_share_evidence_but_obey_prompt_budgets(self):
        episode = self.episode()
        self.propose(episode)
        self.memory.settle(episode, verify_terminal=self.checker)
        count = self.memory.verify()["events"]
        self.assertEqual(self.memory.retrieve("east purchase", mode="none")["text"], "")
        for mode in ("episode", "procedure"):
            for byte_limit, token_limit in ((40, 6000), (6000, 1), (6000, 6000)):
                result = self.memory.retrieve(
                    "east purchase",
                    mode=mode,
                    max_bytes=byte_limit,
                    max_tokens=token_limit,
                    count_tokens=lambda value: len(value.encode()),
                )
                self.assertLessEqual(result["bytes"], byte_limit)
                self.assertLessEqual(result["tokens"], token_limit)
        self.assertEqual(self.memory.verify()["events"], count)

    def test_actual_tool_evidence_and_uncertainty_are_separate_from_terminal_credit(
        self,
    ):
        episode = self.episode()
        self.memory.record_action(
            episode,
            "schema",
            "describe_table",
            None,
            True,
            2,
            digest(["region", "amount"]),
        )
        self.action(episode, "SELECT missing FROM purchases")
        self.assertIsNone(self.memory.feedback(episode))
        self.assertEqual(self.memory.outcome_stats("describe_table")["observations"], 1)
        self.assertEqual(
            self.memory.outcome_stats("query")["success_probability_mean"], 1 / 3
        )
        self.assertGreater(
            self.memory.outcome_stats("query")["posterior_standard_deviation"], 0
        )
        self.assertEqual(
            self.memory.outcome_stats("unseen")["success_probability_mean"], 0.5
        )

    def test_reopen_binds_exact_model_source_and_tool_version(self):
        self.episode()
        self.memory.close()
        for key in self.binding:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.open(**{key: "changed"})
        self.memory = self.open()
        detached = self.memory.binding
        detached["source_id"] = "changed"
        self.assertEqual(self.memory.binding["source_id"], self.binding["source_id"])
        self.assertEqual(self.memory.verify()["events"], 1)

    def test_history_corruption_is_refused_and_json_export_is_explicitly_partial(self):
        self.episode()
        exported = self.memory.export(max_events=0)
        self.assertTrue(exported["truncated"])
        self.assertEqual(
            json.loads(canonical(exported))["schema"], "nuria.procedural-memory.v1"
        )
        self.memory.db.execute(
            "UPDATE memory_events SET payload=replace(payload,'east','west') WHERE seq=1"
        )
        with self.assertRaises(ValueError):
            self.memory.verify()
        self.memory.close()
        with self.assertRaises(ValueError):
            self.open()
        # Cleanup retains a live connection handle for the registered cleanup.
        self.memory = sqlite3.connect(":memory:")

    def test_abstention_has_terminal_zero_credit_and_cannot_become_a_skill(self):
        episode = self.episode()
        committed = self.memory.abstain(episode, "turn_limit")
        self.assertEqual(committed, self.memory.abstain(episode, "turn_limit"))
        invalid = self.memory.settle(
            episode,
            verify_terminal=lambda request: {
                "accepted": True,
                "evidence_sha256": digest(request),
                "checker_id": "invalid-checker",
            },
        )
        self.assertEqual(invalid["status"], "unresolved")
        self.assertIsNone(self.memory.feedback(episode))
        seen = []

        def rejected(request):
            seen.append(request)
            return {
                "accepted": False,
                "evidence_sha256": digest(request),
                "checker_id": "abstention-checker-v1",
            }

        receipt = self.memory.settle(episode, verify_terminal=rejected)
        self.assertEqual(receipt["terminal_reward"], 0)
        self.assertTrue(seen[0]["abstained"])
        self.assertIsNone(seen[0]["final_sql"])
        self.assertEqual(
            self.memory.retrieve("east purchase", mode="procedure")["items"], []
        )
        self.assertEqual(
            self.memory.retrieve("east purchase", mode="episode", max_tokens=6000)[
                "item_count"
            ],
            1,
        )

    def test_recovery_view_is_detached_bounded_and_origin_bound(self):
        episode = self.episode()
        self.action(episode)
        self.action(episode, "SELECT count(*) FROM purchases", "query-2")
        view = self.memory.episode_state(episode, max_actions=1)
        self.assertTrue(view["actions_truncated"])
        self.assertEqual(view["action_count"], 2)
        self.assertEqual(len(view["actions"]), 1)
        view["origin"]["question"] = "mutated"
        view["actions"][0]["cost_units"] = 999
        actual = self.memory.episode_state(episode)
        self.assertNotEqual(actual["origin"]["question"], "mutated")
        self.assertEqual(actual["actions"][0]["cost_units"], 3)
        self.assertIsNone(actual["proposal"])
        with self.assertRaises(ValueError):
            self.memory.episode_state("0" * 64)

    def test_exported_records_allow_independent_hash_reconstruction(self):
        import hashlib

        episode = self.episode()
        self.propose(episode)
        self.memory.settle(episode, verify_terminal=self.checker)
        exported = self.memory.export()
        parent = "0" * 64
        for seq, row in enumerate(exported["records"], 1):
            self.assertEqual(row["seq"], seq)
            self.assertEqual(row["previous_hash"], parent)
            payload = {
                key: value
                for key, value in row.items()
                if key not in ("seq", "previous_hash", "hash")
            }
            parent = hashlib.sha256((parent + canonical(payload)).encode()).hexdigest()
            self.assertEqual(parent, row["hash"])
        self.assertEqual(parent, exported["head"])
        self.assertFalse(exported["truncated"])

    def test_unknown_execution_cost_stays_unknown_through_history_and_retrieval(self):
        episode = self.episode()
        self.memory.record_action(
            episode,
            "timed-out",
            "query",
            "SELECT sum(amount) FROM purchases",
            False,
            None,
            digest("deadline"),
            "deadline",
        )
        self.memory.abstain(episode, "query_deadline")
        self.memory.settle(
            episode,
            verify_terminal=lambda request: {
                "accepted": False,
                "evidence_sha256": digest(request),
                "checker_id": "deadline-checker-v1",
            },
        )
        statistics = self.memory.outcome_stats("query")
        self.assertIsNone(statistics["total_cost_units"])
        self.assertIsNone(statistics["mean_cost_units"])
        self.assertEqual(statistics["cost_unknown_count"], 1)
        recalled = self.memory.retrieve(
            "east purchase", mode="episode", max_tokens=6000
        )
        self.assertIsNone(recalled["items"][0]["actions"][0]["cost_units"])
        another = self.episode("job-2")
        self.action(another)
        mixed = self.memory.outcome_stats("query")
        self.assertIsNone(mixed["total_cost_units"])
        self.assertEqual(mixed["known_cost_units"], 3)
        self.assertEqual(mixed["mean_cost_units"], 3)
        self.assertEqual(mixed["cost_known_count"], 1)


if __name__ == "__main__":
    unittest.main()
