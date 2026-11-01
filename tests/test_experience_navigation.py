"""Work-boundary and memory-access checks; these do not measure intelligence."""

import hashlib
import json
import unittest
import uuid
from pathlib import Path

from cognition.experience_navigation import ExperienceIndex, run_navigation_work
from cognition.outcome_worker import OutcomeStore, identity
from cognition.reasoner_client import ReasonerError


def task(name="report"):
    return {
        "task_id": name,
        "source_sha256": identity(name),
        "prompt": "Read CSV files and return the row count using task_func().",
        "checker": "assert task_func() == 4  # HOST_ONLY_CHECK",
    }


def history():
    return [
        {
            "job_id": "prior-csv",
            "task_id": "old-task",
            "source_sha256": identity("old-source"),
            "prompt": "Count CSV rows including a header.",
            "code": "def task_func(): return 3  # PRIOR_PROGRAM",
            "checker": {
                "passed": False,
                "stderr": "AssertionError: header counted twice",
            },
            "accepted": False,
            "lesson": "Header behavior depends on the exact contract.",
        }
    ]


class Client:
    configuration = {"provider": "unit-fixture"}

    def __init__(self):
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages.append(messages)
        stage = (len(self.messages) - 1) % 3
        if stage == 0:
            content = {
                "code": "def task_func(): return 3  # INITIAL_ONLY",
                "checks": "assert task_func() == 3",
                "lesson": "Check rows.",
                "queries": ["csv header"],
            }
        elif stage == 1:
            body = json.loads(messages[1]["content"])
            content = {
                "checks": "assert task_func() == 4",
                "evidence_ids": [x["job_id"] for x in body["experience"]],
                "hypotheses": ["Header is excluded."],
            }
        else:
            content = {
                "code": "def task_func(): return 4",
                "checks": "assert task_func() == 4",
                "lesson": "Check the header contract before counting.",
            }
        return {
            "content": json.dumps(content),
            "usage": {"input_tokens": 1, "completion_tokens": 1},
        }


class Executor:
    def __init__(self, accepted=True):
        self.calls = []
        self.accepted = accepted

    def run(self, code, checks, **kwargs):
        self.calls.append((code, checks, kwargs))
        return {
            "passed": self.accepted if "HOST_ONLY_CHECK" in checks else True,
            "stderr": "",
            "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "checks_sha256": hashlib.sha256(checks.encode()).hexdigest(),
        }


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("navigation-" + uuid.uuid4().hex)
        )
        self.directory.mkdir(parents=True)
        self.store = OutcomeStore(self.directory / "work.sqlite")

    def test_checker_remains_hidden_and_blind_challenge_cannot_see_proposal(self):
        client, executor = Client(), Executor()
        result = run_navigation_work(
            client, executor, self.store, task(), job_id="one", experience=history()
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(len(client.messages), 3)
        self.assertNotIn("INITIAL_ONLY", json.dumps(client.messages[1]))
        self.assertNotIn("HOST_ONLY_CHECK", json.dumps(client.messages))
        self.assertIn("prior-csv", result["experience_ids"])
        self.assertEqual(len(executor.calls), 5)
        self.store.verify()

    def test_failed_independent_check_overrules_successful_probes(self):
        result = run_navigation_work(
            Client(),
            Executor(False),
            self.store,
            task(),
            job_id="one",
            experience=history(),
        )
        self.assertFalse(result["accepted"])
        self.assertTrue(result["revised_smoke"]["passed"])

    def test_identical_replay_and_changed_contract(self):
        client, executor = Client(), Executor()
        first = run_navigation_work(
            client, executor, self.store, task(), job_id="one", experience=history()
        )
        second = run_navigation_work(
            client, executor, self.store, task(), job_id="one", experience=history()
        )
        self.assertEqual(first["code"], second["code"])
        self.assertTrue(second["replayed"])
        self.assertEqual(len(client.messages), 3)
        with self.assertRaisesRegex(ValueError, "rebound"):
            run_navigation_work(
                client,
                executor,
                self.store,
                task("other"),
                job_id="one",
                experience=history(),
            )

    def test_no_memory_ablation_cannot_expose_prior_records(self):
        client = Client()
        result = run_navigation_work(
            client,
            Executor(),
            self.store,
            task(),
            job_id="one",
            method="no_memory",
            experience=history(),
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["experience_ids"], [])
        self.assertNotIn("PRIOR_PROGRAM", json.dumps(client.messages))
        self.assertNotIn("prior-csv", json.dumps(client.messages))

    def test_automatic_history_reaches_later_different_job(self):
        client = Client()
        run_navigation_work(client, Executor(), self.store, task(), job_id="one")
        second = run_navigation_work(
            client, Executor(), self.store, task("fresh-report"), job_id="two"
        )
        self.assertIn("one", second["experience_ids"])

    def test_deferred_feedback_is_only_settled_after_commitment(self):
        executor = Executor()
        result = run_navigation_work(
            Client(),
            executor,
            self.store,
            task(),
            job_id="one",
            experience=history(),
            grade=False,
        )
        self.assertIsNone(result["accepted"])
        self.assertEqual(len(executor.calls), 3)
        settled = self.store.accept("one", {"passed": False})
        self.assertFalse(settled["accepted"])
        self.assertEqual(result["code"], settled["code"])
        self.store.verify()

    def test_ambiguous_provider_failure_is_not_retried(self):
        class Broken(Client):
            def complete(self, *args, **kwargs):
                raise ReasonerError("transport_error")

        with self.assertRaises(ReasonerError):
            run_navigation_work(
                Broken(),
                Executor(),
                self.store,
                task(),
                job_id="one",
                experience=history(),
            )
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            run_navigation_work(
                Client(),
                Executor(),
                self.store,
                task(),
                job_id="one",
                experience=history(),
            )

    def test_index_handles_quotes_and_discloses_truncation(self):
        items = history()
        items[0]["code"] = '\\"\n' * 30000
        index = ExperienceIndex(items)
        self.addCleanup(index.close)
        ids = index.search(['csv " OR DROP TABLE experience; --'])
        view = index.view(ids, maximum_bytes=1024)
        self.assertLessEqual(len(json.dumps(view).encode()), 1024)
        self.assertIn("code", view[0]["truncated_fields"])
        self.assertEqual(view[0]["record_sha256"], identity(items[0]))

    def test_unchecked_or_duplicate_experience_is_refused(self):
        item = history()[0]
        with self.assertRaises(ValueError):
            ExperienceIndex([item, item])
        with self.assertRaises(ValueError):
            ExperienceIndex([{**item, "accepted": None}])

    def test_memory_methods_receive_identical_initial_information(self):
        clients = [Client(), Client()]
        for client, method in zip(clients, ["navigation", "retrieval"]):
            run_navigation_work(
                client,
                Executor(),
                self.store,
                task(),
                job_id=method,
                method=method,
                experience=history(),
            )
        self.assertEqual(clients[0].messages[0], clients[1].messages[0])

    def test_invalid_citation_is_visible_without_suppressing_the_probe(self):
        class BadCitation(Client):
            def complete(self, messages, **kwargs):
                response = super().complete(messages, **kwargs)
                value = json.loads(response["content"])
                if "evidence_ids" in value:
                    value["evidence_ids"] = ["unenrolled-record"]
                    response["content"] = json.dumps(value)
                return response

        result = run_navigation_work(
            BadCitation(),
            Executor(),
            self.store,
            task(),
            job_id="one",
            experience=history(),
        )
        self.assertEqual(result["blind_challenge"]["evidence_ids"], [])
        self.assertEqual(
            result["blind_challenge"]["rejected_evidence_ids"], ["unenrolled-record"]
        )
        self.assertTrue(result["challenge_execution"]["passed"])

    def test_failure_excerpt_preserves_the_terminal_error(self):
        records = history()
        records[0]["checker"]["stderr"] = (
            "irrelevant header\n" * 1000 + "ValueError: important terminal evidence"
        )
        index = ExperienceIndex(records)
        self.addCleanup(index.close)
        view = index.view([records[0]["job_id"]], maximum_bytes=1024)
        self.assertIn("important terminal evidence", view[0]["failure"])


if __name__ == "__main__":
    unittest.main()
