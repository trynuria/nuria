"""Checked repair provenance and actual isolated acquisition, not an AI score."""

import copy
import hashlib
import json
import os
import sys
import unittest
import uuid
from pathlib import Path

from cognition.checked_repairs import (
    acquire_corrections,
    correction_candidates,
    expanded_experience,
    observed_catalog,
    validate_corrections,
)
from cognition.experience_navigation import ExperienceIndex, run_navigation_work
from cognition.outcome_worker import IsolatedPython, OutcomeStore, identity
from tests.test_experience_navigation import Client, history, task


def record(job, code, accepted):
    return {
        "job_id": job,
        "task_id": "count",
        "source_sha256": identity("count-source"),
        "prompt": "Return twice the CSV row count using solve(x).",
        "code": code,
        "accepted": accepted,
        "checker": {"passed": accepted},
        "lesson": "A proposed lesson, not a verified general rule.",
    }


class MemoryDiversityTests(unittest.TestCase):
    def test_source_variants_do_not_displace_distinct_tasks(self):
        rows = [
            record("bad", "def solve(x): return x", False),
            record("good", "def solve(x): return 2*x", True),
        ]
        for i in range(2):
            rows.append(
                {
                    **record("other" + str(i), "def solve(x): return x", True),
                    "task_id": "other" + str(i),
                    "source_sha256": identity(i),
                }
            )
        index = ExperienceIndex(rows)
        self.addCleanup(index.close)
        found = index.search(["CSV row count"], limit=3)
        self.assertEqual(len(found), 3)
        self.assertEqual(len({index.records[x]["task_id"] for x in found}), 3)
        selected = [
            index.records[x] for x in found if index.records[x]["task_id"] == "count"
        ]
        self.assertTrue(selected[0]["accepted"])

    def test_candidate_requires_recorded_rejected_and_accepted_programs(self):
        failed = record("bad", "def solve(x): return x", False)
        self.assertEqual(correction_candidates([failed]), [])
        self.assertEqual(
            correction_candidates(
                [failed, {**failed, "job_id": "same-code", "accepted": True}]
            ),
            [],
        )


@unittest.skipUnless(
    sys.platform == "darwin" or os.environ.get("NURIA_TEST_LINUX_ISOLATION") == "1",
    "Needs the enrolled native execution backend",
)
class CheckedRepairTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("checked-repair-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        self.executor = IsolatedPython(self.root / "executions")
        self.records = [
            record("bad", "def solve(x): return x", False),
            record("good", "def solve(x): return 2*x", True),
        ]
        self.contract = {
            "task_id": "count",
            "source_sha256": identity("count-source"),
            "checker": "assert solve(3) == 6",
        }

    def test_actual_before_failure_after_success_and_tamper_detection(self):
        catalog = acquire_corrections(self.records, [self.contract], self.executor)
        admitted = validate_corrections(catalog, self.records)
        self.assertEqual(len(admitted), 1)
        row = catalog["corrections"][0]
        self.assertFalse(row["before_check"]["passed"])
        self.assertTrue(row["after_check"]["passed"])
        changed = copy.deepcopy(catalog)
        changed["corrections"][0]["after_code"] = "def solve(x): return 0"
        changed["artifact_sha256"] = identity(
            {k: v for k, v in changed.items() if k != "artifact_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "program differs"):
            validate_corrections(changed, self.records)

    def test_actual_replay_can_refuse_the_reported_improvement(self):
        contract = {**self.contract, "checker": "assert solve(3) > 0"}
        catalog = acquire_corrections(self.records, [contract], self.executor)
        self.assertFalse(catalog["corrections"][0]["verified"])
        self.assertEqual(validate_corrections(catalog, self.records), {})

    def test_source_change_refused_before_execution(self):
        with self.assertRaisesRegex(ValueError, "another source"):
            acquire_corrections(
                self.records,
                [{**self.contract, "source_sha256": identity("other")}],
                self.executor,
            )

    def test_completed_real_work_acquires_a_checked_correction_for_future_memory(self):
        client = Client()
        store = OutcomeStore(self.root / "work.sqlite")
        result = run_navigation_work(
            client,
            self.executor,
            store,
            task(),
            job_id="actual-work",
            experience=history(),
        )
        self.assertTrue(result["accepted"])
        self.assertTrue(result["checked_correction"]["corrected_failure"])
        records = store.experiences()
        expanded = expanded_experience(records)
        self.assertEqual(len(expanded), 2)
        self.assertEqual(sum(row["accepted"] for row in expanded), 1)
        catalog = observed_catalog(records)
        self.assertEqual(len(validate_corrections(catalog, records)), 1)
        memory = ExperienceIndex(records)
        self.addCleanup(memory.close)
        view = memory.view(
            memory.search(["CSV row count"], limit=1), include_corrections=True
        )
        self.assertIn("checked_correction", view[0])
        self.assertIn("correction", view[0])
        before_calls = len(client.messages)
        replay = run_navigation_work(
            client,
            self.executor,
            store,
            task(),
            job_id="actual-work",
            experience=history(),
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(client.messages), before_calls)
        store.verify()

    def test_false_initial_check_cannot_be_fabricated(self):
        store = OutcomeStore(self.root / "work.sqlite")
        result = run_navigation_work(
            Client(),
            self.executor,
            store,
            task(),
            job_id="actual-work",
            experience=history(),
        )
        result = json.loads(json.dumps(result))
        result["checked_correction"]["initial_code_sha256"] = hashlib.sha256(
            b"another program"
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "checked programs"):
            expanded_experience([result])


if __name__ == "__main__":
    unittest.main()
