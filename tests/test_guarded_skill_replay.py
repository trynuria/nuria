"""Replay mechanics use only small development fixtures and in-memory records."""

import copy
import unittest

from scripts.build_guarded_skill_tasks import generate
from scripts.evaluate_guarded_skills import run_job
from scripts.replay_guarded_skill_search import (
    deterministic,
    differences,
    replay_records,
)
from scripts.verify_active_skill_results import digest


class GuardedSkillReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus = generate(0, count=2)
        cls.started = {
            "seeds": [0],
            "methods": ["old_fixed2", "composed_fixed4"],
            "jobs": 2,
            "phase": "development",
            "limits": {
                "maximum_work": 200000,
                "synthesis_work": 50000,
                "maximum_probes": 4,
                "minimum_evidence": 2,
            },
        }
        cls.corpora, cls.traces = [corpus], []
        states = {method: None for method in cls.started["methods"]}
        namespace = digest(["development", [0], 0, 2])
        for index, job in enumerate(corpus["tasks"]):
            methods = cls.started["methods"]
            rotation = index % len(methods)
            for method in methods[rotation:] + methods[:rotation]:
                row, states[method] = run_job(
                    states[method],
                    job,
                    method,
                    corpus["config"],
                    "nuria:guarded-study-v1",
                    cls.started["limits"],
                    namespace,
                )
                row.update(seed=0, ordinal=index)
                cls.traces.append(row)

    def test_all_deterministic_fields_replay_exactly(self):
        result = replay_records(self.started, self.corpora, self.traces)
        self.assertTrue(result["complete"])
        self.assertEqual(result["deterministic_matches"], 4)
        self.assertEqual(result["mismatches"], [])

    def test_only_measured_cpu_fields_are_ignored(self):
        traces = copy.deepcopy(self.traces)
        for row in traces:
            row["cpu_seconds"] += 100
            row["core_cpu_seconds"] += 100
        result = replay_records(self.started, self.corpora, traces)
        self.assertEqual(result["deterministic_matches"], 4)
        changed = copy.deepcopy(traces[0])
        changed["total_work"] += 1
        self.assertNotEqual(deterministic(changed), deterministic(traces[0]))

    def test_forged_search_and_failed_work_are_not_repaired(self):
        traces = copy.deepcopy(self.traces)
        traces[0]["controller_work"]["synthesis_work"] += 1
        result = replay_records(self.started, self.corpora, traces)
        self.assertTrue(result["complete"])
        self.assertEqual(result["mismatching_records"], 1)
        self.assertEqual(result["deterministic_matches"], 3)
        self.assertIn(
            "/controller_work/synthesis_work",
            [entry["field"] for entry in result["mismatches"][0]["first_differences"]],
        )

    def test_difference_report_hashes_values_instead_of_exporting_them(self):
        result = differences({"private": "expected"}, {"private": "actual"})
        self.assertEqual(result[0]["field"], "/private")
        self.assertNotIn("expected", result[0].values())
        self.assertEqual(result[0]["expected_sha256"], digest("expected"))

    def test_execution_failure_stops_without_retry_or_replacing_history(self):
        calls = []

        def failure(*args):
            calls.append(args)
            raise RuntimeError("unit failure")

        result = replay_records(self.started, self.corpora, self.traces, runner=failure)
        self.assertFalse(result["complete"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["records_completed"], 0)
        self.assertEqual(result["execution_error"]["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
