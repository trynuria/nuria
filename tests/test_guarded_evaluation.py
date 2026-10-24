"""Buyer-only target changes cannot improve the current proposal retroactively."""

import copy
import json
import unittest

from cognition.text_skills import DEFAULT_TEXT_CONFIG
from scripts.evaluate_guarded_skills import run_job


class BuyerSeparationTests(unittest.TestCase):
    def test_final_targets_change_admission_but_never_the_current_choice(self):
        inputs = [json.dumps([str(n), "other-" + str(n)]) for n in range(8)]
        job = {
            "id": "buyer-separation-fixture",
            "family": "fixture-field-copy",
            "diagnostic_inputs": inputs[:4],
            "diagnostic_outputs": [str(n) for n in range(4)],
            "score_inputs": inputs[4:],
            "score_outputs": [str(n) for n in range(4, 8)],
        }
        changed = copy.deepcopy(job)
        changed["score_outputs"] = ["wrong-buyer-target"] * 4
        args = (
            "old_fixed2",
            DEFAULT_TEXT_CONFIG,
            "nuria:buyer-separation-fixture",
            {
                "maximum_work": 100000,
                "maximum_probes": 4,
                "minimum_evidence": 2,
                "synthesis_work": 10000,
            },
            "buyer-separation-fixture-v1",
        )
        accepted, positive_state = run_job(None, job, *args)
        rejected, negative_state = run_job(None, changed, *args)
        for name in ("program", "program_sha256", "probe_indices", "controller_work"):
            self.assertEqual(accepted[name], rejected[name])
        self.assertTrue(accepted["checked"]["passed"])
        self.assertFalse(rejected["checked"]["passed"])
        self.assertEqual(len(positive_state["programs"]), 1)
        self.assertFalse(negative_state["programs"])
        self.assertFalse(negative_state["pending"])
        self.assertFalse(rejected["admission"]["accepted"])
        self.assertGreater(rejected["total_work"], rejected["controller_work"]["total"])


if __name__ == "__main__":
    unittest.main()
