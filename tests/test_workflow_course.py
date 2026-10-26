"""Offline course-accounting checks; scripted proposals are not model evidence."""

import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.hosted_reasoner import SharedReasoningBudget
from cognition.workflow_planner import VERSION
from scripts.evaluate_workflow_reasoning import (
    BASE,
    CLOSURE,
    MIRROR,
    PREFIX,
    prepare,
    run,
)

ROOT = Path(__file__).resolve().parents[1]


class ScriptedPlanner:
    configuration = {
        "max_input_bytes": 120000,
        "input_micro_usd_per_million_tokens": 2_000_000,
        "output_micro_usd_per_million_tokens": 10_000_000,
    }

    def __init__(self, budget):
        self.budget = budget

    def complete(self, messages, **kwargs):
        request = json.loads(messages[1]["content"])
        reader = "read_sqlite" if len(messages) > 2 else "read_json"
        transform, render = {
            "provider_brief": ("summarize_provider", "render_provider"),
            "payment_brief": ("separate_delivery_payment", "render_payment"),
            "audit_brief": ("plan_local_audits", "render_audits"),
        }[request["goal_type"]]
        inputs = {"rows": {"node": "n1", "port": "rows"}}
        if request["goal_type"] == "audit_brief":
            inputs["policy"] = {"source": "audit_policy"}
        plan = {
            "version": VERSION,
            "case_id": request["case_id"],
            "source_sha256": request["source_sha256"],
            "nodes": [
                {
                    "id": "n1",
                    "op": reader,
                    "inputs": {"source": {"source": "snapshot"}},
                    "arguments": {},
                },
                {"id": "n2", "op": transform, "inputs": inputs, "arguments": {}},
                {
                    "id": "n3",
                    "op": render,
                    "inputs": {"evidence": {"node": "n2", "port": "evidence"}},
                    "arguments": {},
                },
            ],
            "output": {"node": "n3", "port": "artifact"},
        }
        return {"content": json.dumps(plan), "usage": {"completion_tokens": 100}}


class WorkflowCourseTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / ".test-state" / ("workflow-course-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True, mode=0o700)
        self.repository = self.directory / "source"
        for name in (BASE.as_posix(), MIRROR.as_posix(), *CLOSURE):
            target = self.repository / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.budget = SharedReasoningBudget(
            self.directory / "budget.sqlite",
            max_cost_micro_usd=10_000_000,
            max_requests=700,
        )
        self.client = ScriptedPlanner(self.budget)
        self.protocol = prepare(
            self.repository,
            budget_file=self.budget.path,
            model_configuration=self.client.configuration,
        )

    def test_full_denominator_and_actual_artifacts_with_scripted_plans(self):
        before = Path.cwd()
        with patch(
            "scripts.evaluate_workflow_reasoning.load_reasoner",
            return_value=self.client,
        ):
            report = run(
                self.repository,
                self.repository / PREFIX / "protocol-01.json",
                output=self.directory / "output",
                config_path="unused",
            )
        self.assertEqual(Path.cwd(), before)
        self.assertEqual(len(report["jobs"]), 18)
        self.assertIsNone(report["halted"])
        self.assertEqual(report["model_calls"], 7)
        for method in ("exact", "greedy", "model"):
            self.assertEqual(
                report["methods"][method],
                {"selected": 6, "attempted": 6, "accepted": 6},
            )
        boundary = json.loads(
            (self.repository / PREFIX / "sources/settlement-43.json").read_bytes()
        )
        self.assertEqual(
            sum(row["outcome"]["utility"] is None for row in boundary["action_tasks"]),
            1,
        )
        self.assertEqual(
            len(list((self.directory / "output/artifacts").rglob("*.md"))), 18
        )

    def test_ambiguous_provider_error_stops_and_retains_unexecuted_jobs(self):
        with (
            patch(
                "scripts.evaluate_workflow_reasoning.load_reasoner",
                return_value=self.client,
            ),
            patch.object(self.client, "complete", side_effect=TimeoutError),
        ):
            report = run(
                self.repository,
                self.repository / PREFIX / "protocol-01.json",
                output=self.directory / "interrupted",
                config_path="unused",
            )
        self.assertEqual(len(report["jobs"]), 18)
        self.assertEqual(sum(job["attempted"] for job in report["jobs"]), 3)
        self.assertEqual(report["halted"], "TimeoutError")
        self.assertFalse(report["claims"]["useful_execution_capability"])
        self.assertEqual(report["methods"]["model"]["accepted"], 0)

    def test_frozen_source_change_refuses_before_client_or_artifact(self):
        target = self.repository / CLOSURE[0]
        with target.open("a") as stream:
            stream.write("\n# deliberate test revision\n")
        with patch("scripts.evaluate_workflow_reasoning.load_reasoner") as loader:
            with self.assertRaisesRegex(ValueError, "frozen workflow source changed"):
                run(
                    self.repository,
                    self.repository / PREFIX / "protocol-01.json",
                    output=self.directory / "not-created",
                    config_path="unused",
                )
        loader.assert_not_called()
        self.assertFalse((self.directory / "not-created").exists())


if __name__ == "__main__":
    unittest.main()
