"""Actual local artifact checks; fixture programs are not model evaluations."""

import hashlib
import json
import unittest
import uuid
from pathlib import Path

from cognition.outcome_worker import IsolatedPython, OutcomeStore, identity
from cognition.program_delivery import collect


class ProgramDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("delivery-" + uuid.uuid4().hex)
        )
        self.root.mkdir(parents=True)
        self.store = OutcomeStore(self.root / "outcomes.sqlite")
        self.execution_root = self.root / "executions"
        self.output = self.root / "deliveries"
        self.task = {
            "task_id": "fixture/row-count",
            "source_sha256": identity("fixture-source-v1"),
            "prompt": "Count input rows and write report.json.",
            "inputs": {"rows.txt": "a\nb\nc\n"},
            "checker": "assert task_func() == {'rows': 3}",
        }
        self.code = (
            "import json\nfrom pathlib import Path\n"
            "def task_func():\n"
            " result = {'rows': len(Path('inputs/rows.txt').read_text().splitlines())}\n"
            " Path('report.json').write_text(json.dumps(result))\n"
            " return result\n"
        )
        checked = IsolatedPython(self.execution_root).run(
            self.code,
            self.task["checker"],
            label="report-host",
            inputs=self.task["inputs"],
        )
        self.assertTrue(checked["passed"])
        self.record = {
            "version": "nuria.experience-navigation.v3",
            "job_id": "report",
            "task_id": self.task["task_id"],
            "source_sha256": self.task["source_sha256"],
            "prompt": self.task["prompt"],
            "code": self.code,
            "checker": checked,
            "accepted": True,
        }
        self.store.begin("report", {"task": self.task})
        self.store.finish("report", self.record)
        self.enrollment = {
            "job_id": "report",
            "task_sha256": identity(self.task),
            "files": [
                {"name": "report.json", "semantic_sha256": identity({"rows": 3})}
            ],
        }

    def deliver(self):
        return collect(
            self.store, self.task, self.enrollment, self.execution_root, self.output
        )

    def test_actual_artifact_is_collected_and_replays_after_history_grows(self):
        before = self.store.verify()
        result = self.deliver()
        self.assertFalse(result["replayed"])
        self.assertEqual(result["files"][0]["semantic_sha256"], identity({"rows": 3}))
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["program_executions"], 0)
        self.assertEqual(before, self.store.verify())
        self.store.begin("later-work", {"task": "another task"})
        replay = self.deliver()
        self.assertTrue(replay["replayed"])
        self.assertEqual(result["receipt_sha256"], replay["receipt_sha256"])
        self.assertEqual(len(list(self.output.glob("*/receipt.json"))), 1)

    def test_changed_task_or_input_is_refused(self):
        changed = {**self.task, "prompt": "Another task"}
        with self.assertRaisesRegex(ValueError, "another task"):
            collect(
                self.store, changed, self.enrollment, self.execution_root, self.output
            )
        (self.execution_root / "report-host/inputs/rows.txt").write_text("changed")
        with self.assertRaisesRegex(ValueError, "input"):
            self.deliver()

    def test_unknown_and_rejected_work_is_not_a_delivery(self):
        for job, accepted in [("unknown", None), ("rejected", False)]:
            record = {**self.record, "job_id": job, "accepted": accepted}
            self.store.begin(job, {"task": self.task})
            self.store.finish(job, record)
            enrollment = {**self.enrollment, "job_id": job}
            with self.assertRaisesRegex(ValueError, "accepted"):
                collect(
                    self.store, self.task, enrollment, self.execution_root, self.output
                )

    def test_artifact_semantics_are_checked_independently(self):
        path = self.execution_root / "report-host/report.json"
        path.write_text('{"rows": 999}')
        with self.assertRaisesRegex(ValueError, "independent"):
            self.deliver()
        self.assertFalse(list(self.output.glob("*/receipt.json")))

    def test_path_traversal_and_symlink_are_refused(self):
        contract = {
            **self.enrollment,
            "files": [
                {"name": "../report.json", "semantic_sha256": identity({"rows": 3})}
            ],
        }
        with self.assertRaisesRegex(ValueError, "file enrollment"):
            collect(self.store, self.task, contract, self.execution_root, self.output)
        link = self.execution_root / "report-host/link.json"
        link.symlink_to("report.json")
        contract = {
            **self.enrollment,
            "files": [{"name": "link.json", "semantic_sha256": identity({"rows": 3})}],
        }
        with self.assertRaises(OSError):
            collect(self.store, self.task, contract, self.execution_root, self.output)

    def test_changed_retained_bundle_is_not_silently_recreated(self):
        result = self.deliver()
        folder = self.output / identity(result["binding"])
        (folder / "report.json").write_text('{"rows": 4}')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.deliver()
        self.assertEqual(json.loads((folder / "report.json").read_text()), {"rows": 4})

    def test_changed_execution_program_is_refused(self):
        (self.execution_root / "report-host/program.py").write_text("pass")
        with self.assertRaisesRegex(ValueError, "another program"):
            self.deliver()
        self.assertEqual(
            self.record["checker"]["code_sha256"],
            hashlib.sha256(self.code.encode()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
