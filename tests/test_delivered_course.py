"""Native work-to-delivery integration using an explicit non-model fixture."""

import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.outcome_worker import identity
from scripts.run_delivered_course import prepare, run_delivered


class Client:
    configuration = {"provider": "delivery-test-fixture"}

    def __init__(self, *, writes_file=True):
        self.calls = []
        self.writes_file = writes_file

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        code = (
            "import json\nfrom pathlib import Path\n"
            "def task_func():\n"
            " result={'rows':len(Path('inputs/rows.txt').read_text().splitlines())}\n"
        )
        if self.writes_file:
            code += " Path('report.json').write_text(json.dumps(result))\n"
        code += " return result\n"
        stage = (len(self.calls) - 1) % 3
        content = {
            "code": code,
            "checks": "assert task_func()=={'rows':3}",
            "lesson": "Fixture output",
        }
        if stage == 0:
            content["queries"] = ["row report"]
        elif stage == 1:
            content = {
                "checks": "assert task_func()=={'rows':3}",
                "evidence_ids": [],
                "hypotheses": [],
            }
        return {
            "content": json.dumps(content),
            "usage": {"input_tokens": 1, "completion_tokens": 1},
        }


class DeliveredCourseTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("delivered-course-" + uuid.uuid4().hex)
        )
        self.task = {
            "task_id": "fixture/row-report",
            "source_sha256": identity("fixture-rows"),
            "prompt": "Return the row count and write the same object to report.json.",
            "inputs": {"rows.txt": "a\nb\nc\n"},
            "checker": "assert task_func()=={'rows':3}",
        }
        self.enrollment = {
            "jobs": [
                {
                    "job_id": "row-report",
                    "task": self.task,
                    "delivery": {
                        "job_id": "row-report",
                        "task_sha256": identity(self.task),
                        "files": [
                            {
                                "name": "report.json",
                                "semantic_sha256": identity({"rows": 3}),
                            }
                        ],
                    },
                }
            ]
        }

    def test_real_work_delivery_and_repeat_add_no_calls_or_executions(self):
        client = Client()
        original = identity(self.enrollment)
        with patch("scripts.run_experience_course.load_reasoner", return_value=client):
            first = run_delivered(self.enrollment, self.root, "unused-fixture")
            executions = len(list((self.root / "executions").iterdir()))
            repeat = run_delivered(self.enrollment, self.root, "unused-fixture")
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(len(list((self.root / "executions").iterdir())), executions)
        self.assertEqual(first["work"]["accepted"], 1)
        self.assertEqual(repeat["work"]["replayed"], 1)
        self.assertEqual(len(first["delivery"]["deliveries"]), 1)
        self.assertEqual(repeat["delivery"]["replayed"], 1)
        self.assertEqual(original, identity(self.enrollment))
        self.assertNotIn("semantic_sha256", json.dumps(client.calls))
        self.assertNotIn("_nuria_check_deliveries", json.dumps(client.calls))

    def test_plausible_return_without_artifact_stays_rejected_in_memory(self):
        client = Client(writes_file=False)
        with patch("scripts.run_experience_course.load_reasoner", return_value=client):
            result = run_delivered(self.enrollment, self.root, "unused-fixture")
        self.assertEqual(result["work"]["accepted"], 0)
        self.assertEqual(result["rejected_jobs"], ["row-report"])
        self.assertEqual(result["delivery"]["deliveries"], [])
        self.assertTrue(result["work"]["jobs"][0]["revised_smoke"]["passed"])
        self.assertFalse(result["work"]["jobs"][0]["accepted"])

    def test_delivery_enrollment_is_checked_before_inference(self):
        self.enrollment["jobs"][0]["delivery"]["task_sha256"] = "0" * 64
        with patch("scripts.run_experience_course.load_reasoner") as load:
            with self.assertRaisesRegex(ValueError, "original task"):
                run_delivered(self.enrollment, self.root, "unused-fixture")
            load.assert_not_called()

    def test_original_host_checks_remain_and_compilation_is_deterministic(self):
        a, b = prepare(self.enrollment), prepare(self.enrollment)
        self.assertEqual(a, b)
        checker = a["jobs"][0]["task"]["checker"]
        self.assertTrue(checker.startswith(self.task["checker"]))
        self.assertIn("_nuria_check_deliveries", checker)
        self.assertEqual(
            a["jobs"][0]["delivery"]["task_sha256"], identity(a["jobs"][0]["task"])
        )


if __name__ == "__main__":
    unittest.main()
