import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from cognition import journal
from cognition.intervention_runner import InterventionRunner
from cognition.sqlite_lab import SQLiteLab

CSV = (
    "hr,weekday,mnth,workingday,weathersit,season,cnt\n"
    + "".join(
        f"{i % 24},{i % 7},{i % 12 + 1},{i % 2},{i % 4 + 1},{i % 4 + 1},{i + 1}\n"
        for i in range(128)
    )
).encode()


@contextmanager
def preserved_directory():
    """Retain isolated recovery artifacts for inspection after each test."""
    yield tempfile.mkdtemp(prefix="nuria-intervention-runner-")


def workload(sequence):
    family = sequence % 3
    query = [
        {"family": 0, "a": 2, "b": 3},
        {"family": 1, "a": 3, "b": 1},
        {"family": 2, "a": 2, "b": 3},
    ][family]
    return {
        "source_id": "isolated-runner-test",
        "sequence": sequence,
        "queries": [query] * (sequence % 3 + 1),
    }


class InterventionRunnerTests(unittest.TestCase):
    def test_eight_step_joint_recovery_idempotence_and_delayed_credit(self):
        with preserved_directory() as temporary:
            directory = Path(temporary) / "lab"
            runner = InterventionRunner(directory, CSV, "isolated-runner-test")
            records = [runner.observe(workload(step)) for step in range(4)]
            self.assertEqual(runner.policy.completed, 2)
            state = runner.state()
            runner.close()
            runner = InterventionRunner(directory, CSV, "isolated-runner-test")
            try:
                self.assertEqual(runner.state(), state)
                before = runner.state()
                self.assertEqual(runner.observe(workload(1)), records[1])
                self.assertEqual(runner.state(), before)
                bad = workload(1)
                bad["queries"] = workload(0)["queries"]
                with self.assertRaises(ValueError):
                    runner.observe(bad)
                with self.assertRaises(ValueError):
                    runner.observe(workload(5))
                records += [runner.observe(workload(step)) for step in range(4, 8)]
                self.assertEqual(runner.policy.completed, 6)
                result = runner.finish()
                self.assertEqual(result["completed_receipts"], 8)
                self.assertEqual(result["pending_receipts"], 0)
                self.assertTrue(result["all_results_verified"])
                self.assertTrue(result["integrity"]["valid"])
                self.assertFalse(result["accounting_uncertain"])
                self.assertEqual(result["financial_transactions"], 0)
                self.assertGreater(result["committed_selected_vm_steps"], 0)
                self.assertGreater(result["committed_validation_vm_steps"], 0)
                credits = [
                    credit
                    for record in records
                    for credit in record["applied_delayed_feedback"]
                ]
                self.assertTrue(credits)
                self.assertEqual(
                    credits[0]["receipt"]["intent_id"], records[0]["intent"]["id"]
                )
                before = runner.state()
                self.assertFalse(runner.policy.learn(credits[0]["receipt"]))
                self.assertEqual(runner.state(), before)
                self.assertEqual(runner.finish(), result)
                with self.assertRaises(ValueError):
                    runner.observe(workload(8))
            finally:
                runner.close()
            recovered = InterventionRunner(directory, CSV, "isolated-runner-test")
            try:
                self.assertTrue(recovered.finished)
                self.assertEqual(recovered.policy.completed, 8)
                self.assertEqual(
                    recovered.summary()["neural_state_sha256"],
                    result["neural_state_sha256"],
                )
            finally:
                recovered.close()
            uninterrupted = InterventionRunner(
                Path(temporary) / "uninterrupted", CSV, "isolated-runner-test"
            )
            try:
                continuous_records = [
                    uninterrupted.observe(workload(step)) for step in range(8)
                ]
                continuous_result = uninterrupted.finish()
                for resumed, continuous in zip(
                    records, continuous_records, strict=True
                ):
                    for field in (
                        "spikes_sha256",
                        "decision_neural_state_sha256",
                        "after_neural_state_sha256",
                        "intent",
                        "originating_eligibility_sha256",
                    ):
                        self.assertEqual(resumed[field], continuous[field])
                self.assertEqual(
                    continuous_result["neural_state_sha256"],
                    result["neural_state_sha256"],
                )
                self.assertEqual(
                    continuous_result["policy_sha256"], result["policy_sha256"]
                )
            finally:
                uninterrupted.close()

    def test_interrupted_commit_restores_joint_state_and_marks_accounting_uncertain(
        self,
    ):
        with preserved_directory() as temporary:
            directory = Path(temporary) / "lab"
            runner = InterventionRunner(directory, CSV, "isolated-runner-test")
            runner.observe(workload(0))
            before = runner.state()
            with patch.object(
                runner.journal,
                "save",
                side_effect=RuntimeError("injected precommit failure"),
            ):
                with self.assertRaises(RuntimeError):
                    runner.observe(workload(1))
            with self.assertRaises(RuntimeError):
                runner.observe(workload(1))
            runner.close()
            recovered = InterventionRunner(directory, CSV, "isolated-runner-test")
            try:
                self.assertEqual(recovered.state(), before)
                self.assertTrue(recovered.accounting_uncertain)
                self.assertEqual(recovered.summary()["unfinished_attempts"], 1)
                recovered.observe(workload(1))
                self.assertEqual(recovered.last_sequence, 1)
                self.assertTrue(recovered.summary()["accounting_uncertain"])
            finally:
                recovered.close()

    def test_refuses_foreign_directory_source_dataset_and_concurrent_writer(self):
        with preserved_directory() as temporary:
            base = Path(temporary)
            unrelated = base / "unrelated"
            unrelated.mkdir()
            (unrelated / "keep.txt").write_text("existing history")
            with self.assertRaises(ValueError):
                InterventionRunner(unrelated, CSV, "isolated-runner-test")
            directory = base / "lab"
            runner = InterventionRunner(directory, CSV, "isolated-runner-test")
            try:
                with self.assertRaises(RuntimeError):
                    InterventionRunner(directory, CSV, "isolated-runner-test")
            finally:
                runner.close()
            with self.assertRaises(ValueError):
                InterventionRunner(directory, CSV, "another-source")
            with self.assertRaises(ValueError):
                InterventionRunner(
                    directory, CSV.replace(b",128\n", b",129\n"), "isolated-runner-test"
                )
            self.assertEqual((unrelated / "keep.txt").read_text(), "existing history")

    def test_wrong_result_refuses_credit_and_recovers_prior_state(self):
        with preserved_directory() as temporary:
            directory = Path(temporary) / "lab"
            runner = InterventionRunner(directory, CSV, "isolated-runner-test")
            before = runner.state()
            run = runner.lab.run
            calls = 0

            def wrong_selected(arm, queries):
                nonlocal calls
                value = run(arm, queries)
                calls += 1
                if calls == 1:
                    value["results"] = [[-1, -1]]
                return value

            with patch.object(runner.lab, "run", side_effect=wrong_selected):
                with self.assertRaises(RuntimeError):
                    runner.observe(workload(0))
            runner.close()
            recovered = InterventionRunner(directory, CSV, "isolated-runner-test")
            try:
                self.assertEqual(recovered.state(), before)
                self.assertEqual(recovered.policy.completed, 0)
                self.assertTrue(recovered.accounting_uncertain)
                self.assertEqual(recovered.summary()["observations"], 0)
            finally:
                recovered.close()

    def test_resume_refuses_changed_sqlite_build_or_journal_implementation(self):
        with preserved_directory() as temporary:
            directory = Path(temporary) / "lab"
            runner = InterventionRunner(directory, CSV, "isolated-runner-test")
            runner.observe(workload(0))
            before = runner.state()
            marker = directory / "SQLITE-NEURAL-LAB.json"
            original_marker = marker.read_bytes()
            runner.close()

            initialize = SQLiteLab.__init__

            def alternative_build(lab, *args, **kwargs):
                initialize(lab, *args, **kwargs)
                lab.compile_options = [
                    *lab.compile_options,
                    "NURIA_TEST_ALTERNATIVE_BUILD",
                ]

            # Version and source ID can match across differently compiled builds.
            with patch.object(SQLiteLab, "__init__", alternative_build):
                with self.assertRaisesRegex(ValueError, "identity or implementation"):
                    InterventionRunner(directory, CSV, "isolated-runner-test")

            alternative_journal = Path(temporary) / "journal.py"
            alternative_journal.write_bytes(Path(journal.__file__).read_bytes() + b"\n")
            with patch.object(journal, "__file__", str(alternative_journal)):
                with self.assertRaisesRegex(ValueError, "identity or implementation"):
                    InterventionRunner(directory, CSV, "isolated-runner-test")

            self.assertEqual(marker.read_bytes(), original_marker)
            recovered = InterventionRunner(directory, CSV, "isolated-runner-test")
            try:
                self.assertEqual(recovered.state(), before)
                self.assertTrue(recovered.summary()["integrity"]["valid"])
            finally:
                recovered.close()


if __name__ == "__main__":
    unittest.main()
