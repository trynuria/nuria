"""Statistical mechanics use explicit synthetic summaries, never reserved tasks."""

import copy
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.summarize_guarded_skill_comparison import (
    CHOSEN,
    PRIMARY,
    SOURCE_NAMES,
    analyze,
    digest,
    percentile,
    sign_permutation,
)

ROOT = Path(__file__).resolve().parents[1]
METHODS = [
    "old_fixed2",
    "old_fixed4",
    "composed_fixed2",
    "composed_fixed3",
    "composed_fixed4",
    "enumerative_fixed3",
    "enumerative_fixed4",
    "guarded",
    "frozen_guarded",
]


def fixture(*, same_accuracy=False, stronger_guard=False):
    """Fabricated unit totals; these are not performance evidence."""
    seeds = list(range(100, 112))
    per_seed, public = [], []
    for index, seed in enumerate(seeds):
        methods = {}
        for position, method in enumerate(METHODS):
            chosen_success = 36 + index % 3
            control_success = 24 + index % 2
            success = {
                CHOSEN: control_success if same_accuracy else chosen_success,
                PRIMARY: control_success,
                "composed_fixed3": 34 + index % 3,
                "guarded": chosen_success + 1 if stronger_guard else 30 + index % 3,
            }.get(method, 20 + position + index % 2)
            wrong = 2 if method in (CHOSEN, PRIMARY) else 3
            labels_per_job = (
                2
                if "fixed2" in method
                else 3
                if "fixed3" in method or "guarded" in method
                else 4
            )
            methods[method] = {
                "jobs": 48,
                "exact_successes": success,
                "proposed": success + wrong,
                "wrong_proposals": wrong,
                "abstentions": 48 - success - wrong,
                "labels": 48 * labels_per_job,
                "final_rows": 48 * 24,
                "exact_final_rows": success * 24 + wrong * 6,
                "total_work": (1000 if method == CHOSEN else 1800) + index,
                "core_cpu_seconds": (0.1 if method == CHOSEN else 0.2) + index * 0.001,
                "cpu_seconds": (0.2 if method == CHOSEN else 0.4) + index * 0.001,
                "retained_selections": success // 3,
            }
            public.extend(
                {
                    "seed": seed,
                    "ordinal": ordinal,
                    "method": method,
                    "exact": ordinal < success,
                }
                for ordinal in range(48)
            )
        per_seed.append({"seed": seed, "methods": methods})
    totals = {
        method: {
            key: sum(stream["methods"][method][key] for stream in per_seed)
            for key in per_seed[0]["methods"][method]
        }
        for method in METHODS
    }
    hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in SOURCE_NAMES
    }
    summary = {
        "phase": "reserved",
        "methods": totals,
        "per_seed": per_seed,
        "source_hashes": hashes,
    }
    verification = {
        "verified": True,
        "summary_sha256": digest(summary),
        "source_hashes": hashes,
        "methods": totals,
        "per_seed": per_seed,
        "seeds": seeds,
        "records": 12 * 48 * 9,
        "public_results": public,
    }
    protocol = {
        "chosen_method": CHOSEN,
        "primary_control": PRIMARY,
        "source_hashes": hashes,
        "terms": {"seeds": seeds, "methods": METHODS, "jobs": 48},
    }
    return summary, verification, protocol


class GuardedSkillComparisonTests(unittest.TestCase):
    def analyze_fixture(self, values):
        # Unit tests exercise the same paired sampler with a bounded repeat count.
        # The frozen executable always uses its declared 10,000 replicates.
        with patch(
            "scripts.summarize_guarded_skill_comparison.BOOTSTRAP_REPLICATES", 200
        ):
            return analyze(*values)

    def test_quantile_interpolation_and_exact_sign_null(self):
        self.assertEqual(percentile([10, 0], 0.25), 2.5)
        self.assertEqual(sign_permutation([1, 1])["one_sided_p"], 0.25)
        self.assertEqual(sign_permutation([-1, -1])["one_sided_p"], 1)
        self.assertEqual(sign_permutation([0] * 12)["one_sided_p"], 1)
        self.assertEqual(sign_permutation([2] * 12)["one_sided_p"], 1 / 4096)

    def test_paired_analysis_is_deterministic_and_reports_every_control_axis(self):
        values = fixture()
        first = self.analyze_fixture(values)
        self.assertEqual(first, self.analyze_fixture(values))
        self.assertTrue(first["gates"]["primary_competence_improvement"])
        self.assertFalse(first["gates"]["primary_efficiency_improvement"])
        self.assertEqual(set(first["comparisons"]), set(METHODS) - {CHOSEN})
        primary = first["comparisons"][PRIMARY]
        self.assertEqual(primary["point_estimates"]["labels_delta"], 0)
        self.assertGreater(
            primary["paired_seed_bootstrap_95pct"]["exact_job_rate_delta"][0], 0
        )
        self.assertEqual(sum(primary["paired_job_counts"].values()), 576)
        self.assertLess(primary["point_estimates"]["work_ratio"], 1)
        self.assertLess(primary["point_estimates"]["cpu_ratio"], 1)
        self.assertGreater(
            first["comparisons"]["enumerative_fixed3"]["point_estimates"][
                "labels_delta"
            ],
            0,
        )

    def test_same_correctness_and_coverage_can_pass_only_efficiency_gate(self):
        result = self.analyze_fixture(fixture(same_accuracy=True))
        self.assertFalse(result["gates"]["primary_competence_improvement"])
        self.assertTrue(result["gates"]["primary_efficiency_improvement"])

    def test_stronger_other_control_is_reported_without_switching_the_chosen_method(
        self,
    ):
        result = self.analyze_fixture(fixture(stronger_guard=True))
        self.assertEqual(result["chosen_method"], CHOSEN)
        self.assertEqual(result["strongest_other_aggregate_exact_methods"], ["guarded"])
        self.assertFalse(
            result["gates"]["chosen_beats_every_other_aggregate_exact_count"]
        )
        self.assertTrue(result["gates"]["primary_competence_improvement"])

    def test_development_data_cannot_satisfy_reserved_gates(self):
        summary, verification, protocol = fixture()
        summary["phase"] = "development"
        verification["summary_sha256"] = digest(summary)
        result = self.analyze_fixture((summary, verification, protocol))
        self.assertFalse(result["gates"]["eligible_reserved_qualification"])
        self.assertFalse(result["gates"]["primary_competence_improvement"])

    def test_posthoc_selection_or_an_unverified_summary_is_refused(self):
        for key, replacement in (
            ("chosen_method", "guarded"),
            ("primary_control", "old_fixed2"),
        ):
            values = list(copy.deepcopy(fixture()))
            values[2][key] = replacement
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "selected after"):
                    self.analyze_fixture(values)
        values = list(copy.deepcopy(fixture()))
        values[1]["verified"] = False
        with self.assertRaisesRegex(ValueError, "independent verification"):
            self.analyze_fixture(values)

    def test_missing_cluster_and_outcome_changes_are_refused(self):
        values = list(copy.deepcopy(fixture()))
        values[0]["per_seed"].pop()
        values[1]["summary_sha256"] = digest(values[0])
        with self.assertRaisesRegex(ValueError, "dimensions changed"):
            self.analyze_fixture(values)
        values = list(copy.deepcopy(fixture()))
        values[1]["public_results"][0]["exact"] = not values[1]["public_results"][0][
            "exact"
        ]
        with self.assertRaisesRegex(ValueError, "Per-job outcomes disagree"):
            self.analyze_fixture(values)


if __name__ == "__main__":
    unittest.main()
