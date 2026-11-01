"""Native retention checks; deterministic fixtures are not model intelligence."""

import copy
import json
import sqlite3
import unittest
import uuid
from pathlib import Path

from cognition.outcome_worker import VERSION, IsolatedPython, OutcomeStore, identity
from cognition.program_delivery import collect
from scripts.run_delivered_course import prepare
from scripts.run_retained_work import acquire, run

CODE = """import json
from pathlib import Path
def task_func(data=None):
    if data is None:
        data=json.loads(Path('inputs/data.json').read_text())
    result={'total':sum(data['values'])}
    Path('report.json').write_text(json.dumps(result))
    return result
"""
PROFILE = {"profile_id": "sum-json", "function_contract": "Sum values into report.json"}


def enrollment(job, values):
    result = {"total": sum(values)}
    inputs = {"data.json": json.dumps({"values": values})}
    task = {
        "task_id": "sum-json",
        "source_sha256": identity(inputs),
        "prompt": "Read data.json values and return and write their total to report.json.",
        "checker": "assert task_func()==" + repr(result),
        "inputs": inputs,
    }
    return {
        "jobs": [
            {
                "job_id": job,
                "task": task,
                "delivery": {
                    "job_id": job,
                    "task_sha256": identity(task),
                    "files": [
                        {"name": "report.json", "semantic_sha256": identity(result)}
                    ],
                },
            }
        ]
    }


class RetainedWorkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".test-state") / ("retained-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.origin = OutcomeStore(self.root / "origin.sqlite")
        self.original = prepare(enrollment("origin", [1, 2, 3]))["jobs"][0]
        task = self.original["task"]
        checked = IsolatedPython(self.root / "original-executions").run(
            CODE, task["checker"], label="origin-check", inputs=task["inputs"]
        )
        self.assertTrue(checked["passed"])
        self.origin.begin("origin", {"task": task, "fixture": True})
        self.record = {
            "version": VERSION,
            "job_id": "origin",
            "task_id": task["task_id"],
            "source_sha256": task["source_sha256"],
            "prompt": task["prompt"],
            "code": CODE,
            "checker": checked,
            "accepted": True,
        }
        self.origin.finish("origin", self.record)
        self.delivery = collect(
            self.origin,
            task,
            self.original["delivery"],
            self.root / "original-executions",
            self.root / "original-deliveries",
        )
        self.skill = acquire(
            self.origin.path,
            task,
            job_id="origin",
            profile=PROFILE,
            origin_delivery=self.delivery,
        )

    def test_new_data_is_executed_and_identical_work_reuses_delivery(self):
        before = self.origin.path.read_bytes()
        jobs = enrollment("later", [10, 20])
        work = self.root / "later"
        first = run(self.skill, self.origin.path, jobs, work, profile=PROFILE)
        self.assertTrue(first["results"][0]["accepted"])
        self.assertEqual(first["model_calls"], 0)
        self.assertEqual(len(first["deliveries"]), 1)
        journal = (work / "outcomes.sqlite").read_bytes()
        count = len(list((work / "executions").iterdir()))
        second = run(self.skill, self.origin.path, jobs, work, profile=PROFILE)
        self.assertTrue(second["results"][0]["replayed"])
        self.assertTrue(second["deliveries"][0]["replayed"])
        self.assertEqual((work / "outcomes.sqlite").read_bytes(), journal)
        self.assertEqual(len(list((work / "executions").iterdir())), count)
        self.assertEqual(self.origin.path.read_bytes(), before)

    def test_origin_input_or_delivery_substitution_is_refused(self):
        altered = copy.deepcopy(self.original["task"])
        altered["inputs"]["data.json"] = '{"values":[99]}'
        with self.assertRaisesRegex(ValueError, "delivery"):
            acquire(
                self.origin.path,
                altered,
                job_id="origin",
                profile=PROFILE,
                origin_delivery=self.delivery,
            )
        receipt = copy.deepcopy(self.delivery)
        receipt["program_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "delivery"):
            acquire(
                self.origin.path,
                self.original["task"],
                job_id="origin",
                profile=PROFILE,
                origin_delivery=receipt,
            )

    def test_changed_program_profile_or_duplicate_task_is_refused(self):
        jobs = enrollment("duplicate", [5])
        work = self.root / "duplicates"
        run(self.skill, self.origin.path, jobs, work, profile=PROFILE)
        with self.assertRaisesRegex(ValueError, "rebound"):
            run(
                self.skill,
                self.origin.path,
                enrollment("duplicate", [7]),
                work,
                profile=PROFILE,
            )
        changed = copy.deepcopy(self.skill)
        changed["code"] += "\npass\n"
        with self.assertRaisesRegex(ValueError, "changed"):
            run(changed, self.origin.path, jobs, self.root / "tamper", profile=PROFILE)
        with self.assertRaisesRegex(ValueError, "profile"):
            run(
                self.skill,
                self.origin.path,
                jobs,
                self.root / "profile",
                profile={**PROFILE, "function_contract": "Another contract"},
            )

    def test_failed_transfer_is_retained_and_replays_without_delivery(self):
        jobs = enrollment("unsupported", [2, 4])
        jobs["jobs"][0]["task"]["checker"] += (
            "\nassert False,'Unsupported profile case'"
        )
        jobs["jobs"][0]["delivery"]["task_sha256"] = identity(jobs["jobs"][0]["task"])
        work = self.root / "failed"
        first = run(self.skill, self.origin.path, jobs, work, profile=PROFILE)
        second = run(self.skill, self.origin.path, jobs, work, profile=PROFILE)
        self.assertIs(first["results"][0]["accepted"], False)
        self.assertEqual(first["deliveries"], [])
        self.assertTrue(second["results"][0]["replayed"])
        self.assertFalse(second["results"][0]["accepted"])
        self.assertEqual(len(list((work / "executions").iterdir())), 1)

    def test_origin_journal_tampering_is_refused_before_execution(self):
        with sqlite3.connect(self.origin.path) as db:
            db.execute("UPDATE events SET previous=? WHERE seq=1", ("a" * 64,))
        with self.assertRaisesRegex(ValueError, "journal"):
            run(
                self.skill,
                self.origin.path,
                enrollment("later", [5]),
                self.root / "corrupt",
                profile=PROFILE,
            )

    def test_rejected_origin_cannot_be_acquired(self):
        rejected = OutcomeStore(self.root / "rejected.sqlite")
        row = {**self.record, "accepted": False}
        rejected.begin("origin", {"fixture": "rejected"})
        rejected.finish("origin", row)
        with self.assertRaisesRegex(ValueError, "accepted"):
            acquire(
                rejected.path,
                self.original["task"],
                job_id="origin",
                profile=PROFILE,
                origin_delivery=self.delivery,
            )


if __name__ == "__main__":
    unittest.main()
