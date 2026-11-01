"""Adaptive execution and delivery checks using explicitly non-model fixtures."""

import contextlib
import hashlib
import io
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.interactive_work import run_interactive_work
from cognition.outcome_worker import OutcomeStore, identity
from cognition.reasoner_client import ReasonerError
from scripts.run_interactive_course import main


def proposal(
    code="def task_func(a,b): return a+b",
    *,
    checks="assert task_func(2,3)==5",
    done=True,
):
    return {
        "code": code,
        "checks": checks,
        "lesson": "Fixture observation, not learned model skill",
        "done": done,
    }


class Client:
    configuration = {"provider": "interactive-fixture-not-intelligence"}

    def __init__(self, replies, tokens=1):
        self.replies, self.tokens, self.calls = replies, tokens, []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": json.loads(json.dumps(messages)), **kwargs})
        item = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(item, Exception):
            raise item
        return {
            "content": json.dumps(item),
            "usage": {
                "completion_tokens": min(self.tokens, kwargs["max_tokens"]),
                "prompt_tokens": 1,
            },
        }


class Executor:
    def __init__(self):
        self.calls = []

    def run(self, code, checks, **kwargs):
        self.calls.append((code, checks, kwargs))
        namespace, output = {}, io.StringIO()
        passed, error = True, ""
        with contextlib.redirect_stdout(output):
            try:
                exec(code, namespace)
                exec(checks, namespace)
            except (AssertionError, ValueError, NameError) as exc:
                passed, error = False, type(exc).__name__
        return {
            "passed": passed,
            "stdout": output.getvalue(),
            "stderr": error,
            "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "checks_sha256": hashlib.sha256(checks.encode()).hexdigest(),
        }


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        self.root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("interactive-" + uuid.uuid4().hex)
        )
        self.store = OutcomeStore(self.root / "outcomes.sqlite")
        self.task = {
            "task_id": "fixture/add",
            "source_sha256": "a" * 64,
            "prompt": "Return a+b",
            "checker": "assert task_func(2,3)==5 # HOST_ONLY",
        }

    def run_work(self, client, executor=None, **kwargs):
        return run_interactive_work(
            client,
            executor or Executor(),
            self.store,
            self.task,
            job_id="job",
            **kwargs,
        )

    def test_actual_execution_feedback_drives_the_next_step_and_host_stays_hidden(self):
        client = Client(
            [proposal("def task_func(a,b): return a-b", done=False), proposal()]
        )
        result = self.run_work(client)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(client.calls), 2)
        self.assertIn("AssertionError", client.calls[1]["messages"][-1]["content"])
        self.assertNotIn("HOST_ONLY", json.dumps(client.calls))
        self.assertIsNone(result["checked_correction"])

    def test_actual_completion_total_is_bounded_across_adaptive_calls(self):
        client = Client([proposal(done=False)], tokens=2000)
        result = self.run_work(client, grade=False)
        self.assertEqual(result["completion_tokens_used"], 7200)
        self.assertEqual(result["issued_completion_limits"], [2400, 2400, 2400, 1200])
        self.assertEqual(len(client.calls), 4)
        self.assertIsNone(result["accepted"])

    def test_five_step_limit_and_immutable_replay(self):
        client, executor = Client([proposal(done=False)]), Executor()
        result = self.run_work(client, executor)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(client.calls), 5)
        executions = len(executor.calls)
        replay = self.run_work(client, executor)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(client.calls), 5)
        self.assertEqual(len(executor.calls), executions)

    def test_ambiguous_request_is_never_retried(self):
        client = Client([ReasonerError("model_transport_error")])
        with self.assertRaises(ReasonerError):
            self.run_work(client)
        with self.assertRaisesRegex(ValueError, "Interrupted"):
            self.run_work(client)
        self.assertEqual(len(client.calls), 1)

    def test_known_truncation_consumes_the_same_completion_allowance(self):
        error = ReasonerError(
            "model_output_incomplete", usage={"completion_tokens": 2400}
        )
        client = Client([error])
        result = self.run_work(client, grade=False)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(result["completion_tokens_used"], 7200)
        self.assertEqual(result["code"], "")
        self.assertIsNone(result["checker"])

    def test_false_self_check_does_not_decide_acceptance(self):
        client = Client(
            [proposal("def task_func(a,b): return 0", checks="assert True")]
        )
        result = self.run_work(client)
        self.assertFalse(result["accepted"])
        self.assertTrue(result["attempts"][0]["probe"]["passed"])

    def test_real_cli_delivers_checked_artifacts_and_identical_replay_adds_no_execution(
        self,
    ):
        task = {
            "task_id": "fixture/rows",
            "source_sha256": identity("fixture rows"),
            "prompt": "Read inputs/rows.txt, return the row count and write report.json.",
            "inputs": {"rows.txt": "a\nb\nc\n"},
            "checker": "assert task_func()=={'rows':3}",
        }
        enrollment = {
            "jobs": [
                {
                    "job_id": "rows",
                    "task": task,
                    "delivery": {
                        "job_id": "rows",
                        "task_sha256": identity(task),
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
        self.root.mkdir(exist_ok=True)
        path = self.root / "enrollment.json"
        path.write_text(json.dumps(enrollment))
        code = "import json\nfrom pathlib import Path\ndef task_func():\n result={'rows':len(Path('inputs/rows.txt').read_text().splitlines())}\n Path('report.json').write_text(json.dumps(result))\n return result\n"
        client = Client([proposal(code, checks="assert task_func()=={'rows':3}")])
        argv = [
            "interactive",
            str(path),
            str(self.root / "work"),
            "--config",
            "unused-fixture",
            "--resource-root",
            str(self.root / "resource"),
        ]
        output = io.StringIO()
        with (
            patch("scripts.run_interactive_course.load_reasoner", return_value=client),
            patch(
                "scripts.run_interactive_course.BoundedClient",
                side_effect=lambda model, root: model,
            ),
            patch("sys.argv", argv),
            contextlib.redirect_stdout(output),
        ):
            main()
            executions = len(list((self.root / "work/executions").iterdir()))
            main()
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            len(list((self.root / "work/executions").iterdir())), executions
        )
        receipts = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(receipts[0]["accepted"], 1)
        self.assertEqual(receipts[0]["delivered"], 1)
        self.assertEqual(receipts[1]["replayed"], 1)
        self.assertNotIn("semantic_sha256", json.dumps(client.calls))


if __name__ == "__main__":
    unittest.main()
