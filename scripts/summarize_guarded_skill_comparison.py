"""Preregistered paired-seed analysis of a fixed guarded-skill comparison.

The chosen method and primary control are constants, never selected from the
reserved results. Resampling operates on whole generated streams, preserving
within-stream dependence. This is a finite synthetic engineering comparison,
not independent external, neural, consciousness or customer-work evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
from pathlib import Path

CHOSEN = "composed_fixed4"
PRIMARY = "enumerative_fixed4"
BOOTSTRAP_REPLICATES = 10000
BOOTSTRAP_SEED = 20261024
ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAMES = (
    "scripts/summarize_guarded_skill_comparison.py",
    "tests/test_guarded_skill_comparison.py",
)


def serial(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def percentile(values, probability):
    require(bool(values) and 0 <= probability <= 1, "Invalid quantile input")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def interval(values):
    return [percentile(values, 0.025), percentile(values, 0.975)]


def sign_permutation(differences):
    """Exact one-sided paired-cluster sign-flip test, including zero ties."""
    require(
        1 <= len(differences) <= 16
        and all(type(value) is int for value in differences),
        "Sign-permutation differences must be bounded integer seed totals",
    )
    observed = sum(differences)
    nonzero = [abs(value) for value in differences if value]
    assignments = 2 ** len(nonzero)
    extreme = sum(
        sum(sign * value for sign, value in zip(signs, nonzero, strict=True))
        >= observed
        for signs in itertools.product((-1, 1), repeat=len(nonzero))
    )
    return {
        "one_sided_p": extreme / assignments,
        "nonzero_seed_pairs": len(nonzero),
        "sign_assignments": assignments,
        "null": "Within-seed method labels are exchangeable; directional exact-file advantage is positive",
    }


def paired_metrics(chosen, control):
    jobs = sum(row["jobs"] for row in chosen)
    require(jobs > 0, "Comparison has no jobs")
    totals = {
        key: sum(row[key] for row in chosen)
        for key in (
            "exact_successes",
            "wrong_proposals",
            "proposed",
            "labels",
            "total_work",
            "cpu_seconds",
            "core_cpu_seconds",
        )
    }
    baseline = {key: sum(row[key] for row in control) for key in totals}
    require(
        all(
            baseline[key] > 0
            for key in ("total_work", "cpu_seconds", "core_cpu_seconds")
        ),
        "Ratios require positive baseline work and CPU",
    )
    return {
        "jobs": jobs,
        "exact_jobs_delta": totals["exact_successes"] - baseline["exact_successes"],
        "exact_job_rate_delta": (
            totals["exact_successes"] - baseline["exact_successes"]
        )
        / jobs,
        "wrong_proposals_delta": totals["wrong_proposals"]
        - baseline["wrong_proposals"],
        "proposed_delta": totals["proposed"] - baseline["proposed"],
        "labels_delta": totals["labels"] - baseline["labels"],
        "labels_per_job_delta": (totals["labels"] - baseline["labels"]) / jobs,
        "work_ratio": totals["total_work"] / baseline["total_work"],
        "cpu_ratio": totals["cpu_seconds"] / baseline["cpu_seconds"],
        "core_cpu_ratio": totals["core_cpu_seconds"] / baseline["core_cpu_seconds"],
    }


def paired_bootstrap(chosen, control):
    require(len(chosen) == len(control) >= 1, "Unpaired stream totals")
    rng = random.Random(BOOTSTRAP_SEED)
    samples = {
        key: []
        for key in (
            "exact_job_rate_delta",
            "labels_per_job_delta",
            "work_ratio",
            "cpu_ratio",
            "core_cpu_ratio",
        )
    }
    for _ in range(BOOTSTRAP_REPLICATES):
        indices = [rng.randrange(len(chosen)) for _ in chosen]
        metrics = paired_metrics(
            [chosen[i] for i in indices], [control[i] for i in indices]
        )
        for key in samples:
            samples[key].append(metrics[key])
    return {key: interval(values) for key, values in samples.items()}


def validate_totals(values):
    integer_fields = (
        "jobs",
        "exact_successes",
        "proposed",
        "wrong_proposals",
        "abstentions",
        "labels",
        "final_rows",
        "exact_final_rows",
        "total_work",
        "retained_selections",
    )
    for key in integer_fields:
        require(
            type(values[key]) is int and values[key] >= 0, "Invalid finite study count"
        )
    for key in ("cpu_seconds", "core_cpu_seconds"):
        require(
            type(values[key]) in (int, float)
            and math.isfinite(values[key])
            and values[key] > 0,
            "Invalid positive CPU measurement",
        )
    require(
        values["exact_successes"] + values["wrong_proposals"] == values["proposed"]
        and values["proposed"] + values["abstentions"] == values["jobs"]
        and values["final_rows"] == values["jobs"] * 24
        and values["exact_final_rows"] <= values["final_rows"]
        and values["labels"] <= values["jobs"] * 4
        and values["cpu_seconds"] >= values["core_cpu_seconds"],
        "Study denominator or count relationship changed",
    )


def analyze(summary, verification, protocol):
    require(
        protocol["chosen_method"] == CHOSEN and protocol["primary_control"] == PRIMARY,
        "The method or primary control was selected after the frozen decision",
    )
    require(
        verification.get("verified") is True
        and verification["summary_sha256"] == digest(summary)
        and verification["source_hashes"]
        == summary["source_hashes"]
        == protocol["source_hashes"]
        and verification["methods"] == summary["methods"]
        and verification["per_seed"] == summary["per_seed"],
        "Summary, independent verification and protocol differ",
    )
    for name in SOURCE_NAMES:
        require(
            name in protocol["source_hashes"]
            and hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            == protocol["source_hashes"][name],
            "Statistical analysis sources differ from their freeze",
        )
    terms = protocol["terms"]
    seeds, methods, jobs = terms["seeds"], terms["methods"], terms["jobs"]
    require(
        len(seeds) == len(set(seeds)) == 12
        and jobs == 48
        and len(methods) == len(set(methods)) == 9
        and CHOSEN in methods
        and PRIMARY in methods
        and set(methods) == set(summary["methods"])
        and [row["seed"] for row in summary["per_seed"]] == seeds
        and verification["seeds"] == seeds
        and verification["records"] == len(seeds) * jobs * len(methods),
        "Frozen paired-stream qualification dimensions changed",
    )
    by_method = {method: [] for method in methods}
    for stream in summary["per_seed"]:
        require(
            set(stream["methods"]) == set(methods), "Per-seed method coverage changed"
        )
        for method, values in stream["methods"].items():
            validate_totals(values)
            require(values["jobs"] == jobs, "A failed stream changed its denominator")
            by_method[method].append(values)
    for method, total in summary["methods"].items():
        validate_totals(total)
        for key, observed in total.items():
            computed = sum(row[key] for row in by_method[method])
            require(
                math.isclose(computed, observed, rel_tol=1e-12, abs_tol=1e-12),
                "Per-seed aggregate totals disagree",
            )
    exact = {}
    for row in verification["public_results"]:
        key = (row["seed"], row["ordinal"], row["method"])
        require(
            key not in exact
            and row["seed"] in seeds
            and row["method"] in methods
            and type(row["ordinal"]) is int
            and 0 <= row["ordinal"] < jobs
            and type(row["exact"]) is bool,
            "Per-job pairing or exactness changed",
        )
        exact[key] = row["exact"]
    require(
        len(exact) == verification["records"], "Per-job paired outcomes are incomplete"
    )
    for stream in summary["per_seed"]:
        for method in methods:
            require(
                sum(exact[(stream["seed"], ordinal, method)] for ordinal in range(jobs))
                == stream["methods"][method]["exact_successes"],
                "Per-job outcomes disagree with verified stream totals",
            )
    comparisons = {}
    for control in methods:
        if control == CHOSEN:
            continue
        points = paired_metrics(by_method[CHOSEN], by_method[control])
        confidence = paired_bootstrap(by_method[CHOSEN], by_method[control])
        paired = {
            key: 0 for key in ("chosen_only", "control_only", "both_pass", "both_fail")
        }
        for seed in seeds:
            for ordinal in range(jobs):
                left, right = (
                    exact[(seed, ordinal, CHOSEN)],
                    exact[(seed, ordinal, control)],
                )
                key = (
                    "both_pass"
                    if left and right
                    else "chosen_only"
                    if left
                    else "control_only"
                    if right
                    else "both_fail"
                )
                paired[key] += 1
        comparisons[control] = {
            "primary": control == PRIMARY,
            "point_estimates": points,
            "paired_seed_bootstrap_95pct": confidence,
            "paired_job_counts": paired,
            "exact_seed_sign_permutation": sign_permutation(
                [
                    left["exact_successes"] - right["exact_successes"]
                    for left, right in zip(
                        by_method[CHOSEN], by_method[control], strict=True
                    )
                ]
            ),
            "inference_role": "Preregistered primary"
            if control == PRIMARY
            else "Exploratory unadjusted secondary; no familywise significance claim",
        }
    primary = comparisons[PRIMARY]
    point, confidence = (
        primary["point_estimates"],
        primary["paired_seed_bootstrap_95pct"],
    )
    eligible = summary["phase"] == "reserved"
    best_count = max(
        summary["methods"][method]["exact_successes"]
        for method in methods
        if method != CHOSEN
    )
    return {
        "schema": "nuria.guarded-skill-comparison.v1",
        "chosen_method": CHOSEN,
        "primary_control": PRIMARY,
        "phase": summary["phase"],
        "summary_sha256": digest(summary),
        "protocol_sha256": digest(protocol),
        "verification_sha256": digest(verification),
        "source_hashes": summary["source_hashes"],
        "resampling": {
            "unit": "Whole paired seed stream",
            "streams": 12,
            "replicates": BOOTSTRAP_REPLICATES,
            "rng_seed": BOOTSTRAP_SEED,
            "interval": "Two-sided percentile 95%; no independent-row assumption",
        },
        "gates": {
            "eligible_reserved_qualification": eligible,
            "primary_competence_improvement": eligible
            and confidence["exact_job_rate_delta"][0] > 0
            and point["labels_delta"] <= 0,
            "primary_efficiency_improvement": eligible
            and point["exact_jobs_delta"] == 0
            and point["proposed_delta"] == 0
            and point["labels_delta"] <= 0
            and confidence["work_ratio"][1] < 1
            and confidence["cpu_ratio"][1] < 1,
            "chosen_beats_every_other_aggregate_exact_count": summary["methods"][
                CHOSEN
            ]["exact_successes"]
            > best_count,
        },
        "strongest_other_aggregate_exact_methods": [
            method
            for method in methods
            if method != CHOSEN
            and summary["methods"][method]["exact_successes"] == best_count
        ],
        "strongest_other_aggregate_exact_count": best_count,
        "methods": summary["methods"],
        "comparisons": comparisons,
        "limits": [
            "Conditional on this authored synthetic workload, fixed grammar, solver/search quotas and seed-generation process; not independent external generalization or neural superiority.",
            "Twelve seed clusters limit interval precision. Paired bootstrap uncertainty describes generated streams, not an independently sampled population of customer tasks.",
            "Exact sign permutation assumes within-seed exchangeability under its null. Secondary comparisons are unadjusted and exploratory; the primary gate was fixed before outcomes.",
            "Four-example and three-example methods have different information costs; every comparison reports label, work and CPU axes separately.",
            "CPU is one counterbalanced process measurement per job, not a hardware-attested speed guarantee; declared work is an instrumented unit, not money or all machine instructions.",
            "Frozen guarded uses a different controller from composed fixed4; it does not isolate the causal effect of retaining programs in the chosen method.",
            "Correct terminal abstention artifacts are not delivered successes; all disposition costs remain in total work and CPU.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("summary", "verification", "protocol", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = analyze(
        *(
            json.loads(getattr(args, name).read_bytes())
            for name in ("summary", "verification", "protocol")
        )
    )
    with args.output.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(
            json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
    print(serial({"chosen_method": CHOSEN, "gates": report["gates"]}))


if __name__ == "__main__":
    main()
