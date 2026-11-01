"""Input-grounding and native wiring checks; fixtures are not intelligence tests."""

import base64
import json
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch

from cognition.input_observations import inspect_enrollment, observe_inputs, structure
from cognition.outcome_worker import identity
from scripts.run_inspected_course import run_inspected


class Fixture:
    configuration = {"provider": "input-observation-fixture"}

    def __init__(self):
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        field = (
            "outcome_utility"
            if "/outcome_utility" in json.dumps(messages)
            else "utility"
        )
        code = (
            "import json\nfrom pathlib import Path\n"
            "def task_func():\n"
            " rows=json.loads(Path('inputs/actions.json').read_text())['rows']\n"
            " result={'positive':sum(type(row.get('"
            + field
            + "')) in (int,float) and row['"
            + field
            + "']>0 for row in rows)}\n"
            " Path('report.json').write_text(json.dumps(result))\n return result\n"
        )
        stage = (len(self.calls) - 1) % 3
        content = {
            "code": code,
            "checks": "assert task_func()=={'positive':1}",
            "lesson": "Fixture shape check",
        }
        if stage == 0:
            content["queries"] = ["input field"]
        elif stage == 1:
            content = {
                "checks": "assert task_func()=={'positive':1}",
                "evidence_ids": [],
                "hypotheses": [],
            }
        return {
            "content": json.dumps(content),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class InputObservationTests(unittest.TestCase):
    def test_all_array_members_contribute_and_values_stay_private(self):
        rows = [
            {"outcome_utility": 1, "token": "SECRET_VALUE_NEVER_COPY"}
            for _ in range(40)
        ]
        rows[-1]["late_field"] = False
        value = observe_inputs({"actions.json": json.dumps({"rows": rows})})
        text = json.dumps(value)
        self.assertNotIn("SECRET_VALUE_NEVER_COPY", text)
        paths = {row["path"]: row for row in value["files"][0]["paths"]}
        self.assertEqual(paths["$/rows/*/late_field"]["types"], ["boolean"])
        self.assertEqual(paths["$/rows/*/outcome_utility"]["occurrences"], 40)
        self.assertTrue(value["files"][0]["complete"])

    def test_invalid_duplicate_and_truncated_inputs_remain_unknown(self):
        for text in ["{broken", '{"a":1,"a":2}', '{"a":NaN}']:
            self.assertFalse(observe_inputs({"a.json": text})["files"][0]["complete"])
        value = structure({"a": {"b": {"c": 1}}}, maximum_depth=1)
        self.assertFalse(value["complete"])
        self.assertIn("depth_limit", value["limits_reached"])
        self.assertFalse(structure(list(range(50)), maximum_nodes=3)["complete"])

    def test_compressed_json_is_bounded_and_large_maps_omit_member_names(self):
        data = {"private-map-key-" + str(i): i for i in range(40)}
        packed = {
            "codec": "base64+zlib+json",
            "payload": base64.b64encode(
                zlib.compress(json.dumps(data).encode())
            ).decode(),
        }
        observed = observe_inputs({"source.json": json.dumps(packed)})["files"][0]
        self.assertEqual(observed["format"], "base64+zlib+json")
        self.assertTrue(observed["paths"][0]["member_names_omitted"])
        self.assertNotIn("private-map-key-", json.dumps(observed))
        packed["payload"] = base64.b64encode(zlib.compress(b"x" * 1048577)).decode()
        self.assertFalse(
            observe_inputs({"source.json": json.dumps(packed)})["files"][0]["complete"]
        )

    def test_context_limit_discloses_omissions_without_invalid_json(self):
        data = {
            "f" + str(i): {"nested" + str(j): j for j in range(30)} for i in range(30)
        }
        observed = observe_inputs({"a.json": json.dumps(data)}, maximum_bytes=2048)
        self.assertLessEqual(len(json.dumps(observed, sort_keys=True).encode()), 2048)
        self.assertFalse(observed["files"][0]["complete"])
        self.assertGreater(observed["files"][0]["paths_omitted"], 0)

    def enrollment(self):
        task = {
            "task_id": "fixture/observed-input",
            "source_sha256": identity("fixture-input"),
            "prompt": "Count positive reported utilities and write/return {'positive': count} as report.json.",
            "inputs": {
                "actions.json": json.dumps(
                    {
                        "rows": [
                            {"outcome_utility": 1, "token": "SECRET_VALUE_NEVER_COPY"},
                            {"outcome_utility": False},
                        ]
                    }
                )
            },
            "checker": "assert task_func()=={'positive':1} # HOST_ONLY_EXPECTATION",
        }
        return {
            "jobs": [
                {
                    "job_id": "observed-input",
                    "task": task,
                    "delivery": {
                        "job_id": "observed-input",
                        "task_sha256": identity(task),
                        "files": [
                            {
                                "name": "report.json",
                                "semantic_sha256": identity({"positive": 1}),
                            }
                        ],
                    },
                }
            ]
        }

    def test_inspection_preserves_host_checks_and_original_enrollment(self):
        enrollment = self.enrollment()
        before = identity(enrollment)
        observed = inspect_enrollment(enrollment)
        job = observed["jobs"][0]
        self.assertEqual(before, identity(enrollment))
        self.assertEqual(
            job["task"]["checker"], enrollment["jobs"][0]["task"]["checker"]
        )
        self.assertNotIn("HOST_ONLY_EXPECTATION", job["task"]["prompt"])
        self.assertNotIn("SECRET_VALUE_NEVER_COPY", job["task"]["prompt"])
        self.assertEqual(job["delivery"]["task_sha256"], identity(job["task"]))

    def test_native_delivered_work_observes_real_keys_and_replays(self):
        root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("input-observed-" + uuid.uuid4().hex)
        )
        client = Fixture()
        with patch("scripts.run_experience_course.load_reasoner", return_value=client):
            first = run_inspected(
                self.enrollment(), root, "unused-fixture", method="retrieval"
            )
            count = len(list((root / "executions").iterdir()))
            replay = run_inspected(
                self.enrollment(), root, "unused-fixture", method="retrieval"
            )
        self.assertEqual(first["work"]["accepted"], 1)
        self.assertEqual(len(first["delivery"]["deliveries"]), 1)
        self.assertEqual(replay["work"]["replayed"], 1)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(count, len(list((root / "executions").iterdir())))
        self.assertNotIn("HOST_ONLY_EXPECTATION", json.dumps(client.calls))
        self.assertNotIn("SECRET_VALUE_NEVER_COPY", json.dumps(client.calls))

    def test_simpler_existing_worker_receives_the_same_observation(self):
        class DirectFixture(Fixture):
            def complete(self, messages, **kwargs):
                response = super().complete(messages, **kwargs)
                value = json.loads(response["content"])
                value.pop("queries", None)
                response["content"] = json.dumps(value)
                return response

        root = (
            Path(__file__).resolve().parents[1]
            / ".test-state"
            / ("input-direct-" + uuid.uuid4().hex)
        )
        client = DirectFixture()
        with patch("scripts.run_inspected_course.load_reasoner", return_value=client):
            first = run_inspected(
                self.enrollment(), root, "unused-fixture", strategy="direct"
            )
            repeat = run_inspected(
                self.enrollment(), root, "unused-fixture", strategy="direct"
            )
        self.assertEqual(first["work"]["accepted"], 1)
        self.assertEqual(len(first["delivery"]["deliveries"]), 1)
        self.assertEqual(repeat["work"]["replayed"], 1)
        self.assertEqual(len(client.calls), 1)

    def test_input_facts_change_with_bytes_and_unsafe_names_are_refused(self):
        a = observe_inputs({"a.json": '{"value":1}'})
        b = observe_inputs({"a.json": '{"value":"one"}'})
        self.assertNotEqual(a["files"][0]["sha256"], b["files"][0]["sha256"])
        with self.assertRaises(ValueError):
            observe_inputs({"../a.json": "{}"})


if __name__ == "__main__":
    unittest.main()
