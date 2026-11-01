"""Offline delivery/replay integration; fixture responses are not intelligence."""

import hashlib
import io
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cognition.hosted_reasoner import OpenAIReasoner, SharedReasoningBudget
from cognition.outcome_worker import identity
from scripts.run_delivered_course import run_delivered
from scripts.run_interactive_course import runner


class DeliveryContinuityTests(unittest.TestCase):
    def test_checked_artifact_and_identical_replay_keep_opaque_state_private(self):
        root = Path(".test-state") / ("continuity-delivery-" + uuid.uuid4().hex)
        root.mkdir(parents=True, mode=0o700)
        key = root / "offline-fixture.key"
        key.write_text("not-a-real-provider-key")
        os.chmod(key, 0o600)
        budget = SharedReasoningBudget(
            root / "budget.sqlite", max_cost_micro_usd=20_000_000, max_requests=8
        )
        base = OpenAIReasoner(
            key,
            "gpt-6.1-sol",
            budget=budget,
            reasoning_effort="medium",
            input_micro_usd_per_million_tokens=2_000_000,
            output_micro_usd_per_million_tokens=10_000_000,
        )
        requests = []
        artifact = {"observed": 7}
        code = (
            "import json\nfrom pathlib import Path\n"
            "def task_func():\n"
            " value=json.loads(Path('inputs/source.json').read_text())\n"
            " result={'observed':value['value']}\n"
            " Path('report.json').write_text(json.dumps(result))\n"
            " return result\n"
        )

        class Wire:
            def open(self, request, timeout):
                requests.append(json.loads(request.data))
                step = {
                    "code": code,
                    "checks": "print(task_func())",
                    "lesson": "Offline fixture, not a model result",
                    "done": len(requests) == 2,
                }
                return io.BytesIO(
                    json.dumps(
                        {
                            "model": "gpt-6.1-sol-fixture",
                            "status": "completed",
                            "error": None,
                            "service_tier": "default",
                            "reasoning": {"context": "all_turns"},
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 10,
                                "total_tokens": 20,
                                "output_tokens_details": {"reasoning_tokens": 2},
                            },
                            "output": [
                                {
                                    "id": "rs_fixture",
                                    "type": "reasoning",
                                    "summary": [],
                                    "encrypted_content": "private-opaque-fixture",
                                },
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": json.dumps(step),
                                        }
                                    ],
                                },
                            ],
                        }
                    ).encode()
                )

        base._opener = Wire()
        plan = {
            "phase_start_charged_micro_usd": 0,
            "additional_phase_ceiling_micro_usd": 20_000_000,
            "phase_start_requests": 0,
            "max_provider_calls": 8,
            "max_provider_wall_seconds": 1000,
            "max_output_tokens_per_call": 2400,
            "free_disk_floor_bytes": 0,
            "max_retained_bytes": 20_000_000,
        }
        (root / "resource-plan.json").write_text(json.dumps(plan))
        task = {
            "task_id": "fixture/continuity-delivery",
            "source_sha256": "a" * 64,
            "prompt": "Read inputs/source.json, return its value as observed and write report.json",
            "checker": "assert task_func()=={'observed':7} # HOST_ONLY",
            "inputs": {"source.json": '{"value":7}'},
        }
        digest = hashlib.sha256(
            json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        enrollment = {
            "jobs": [
                {
                    "job_id": "delivery",
                    "task": task,
                    "delivery": {
                        "job_id": "delivery",
                        "task_sha256": identity(task),
                        "files": [{"name": "report.json", "semantic_sha256": digest}],
                    },
                }
            ]
        }
        with patch("scripts.run_interactive_course.load_reasoner", return_value=base):
            first = run_delivered(
                enrollment,
                root / "work",
                None,
                resource_root=root,
                runner=runner,
                method="continuity",
            )
            before = sorted((root / "work/executions").iterdir())
            second = run_delivered(
                enrollment,
                root / "work",
                None,
                resource_root=root,
                runner=runner,
                method="continuity",
            )
        self.assertEqual(first["work"]["accepted"], 1)
        self.assertEqual(len(first["delivery"]["deliveries"]), 1)
        self.assertEqual(second["work"]["replayed"], 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(before, sorted((root / "work/executions").iterdir()))
        self.assertEqual(budget.snapshot()["requests"], 2)
        self.assertNotIn("private-opaque-fixture", json.dumps(first))
        self.assertNotIn("HOST_ONLY", json.dumps(requests))
        self.assertTrue(any(x.get("type") == "reasoning" for x in requests[1]["input"]))
