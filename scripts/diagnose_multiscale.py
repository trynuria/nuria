"""Locate the failed hybrid's forecast bottlenecks using its retained traces."""

import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from cognition.forecast import ForecastCouncil

ROOT = Path(__file__).resolve().parents[1]


def diagnose(directory: Path) -> dict:
    result = json.loads((directory / "result.json").read_text())
    trace_path = directory / "traces.jsonl.gz"
    if (
        hashlib.sha256(trace_path.read_bytes()).hexdigest()
        != result["trace_gzip_sha256"]
    ):
        raise ValueError("Recorded evidence changed")
    groups = defaultdict(list)
    with gzip.open(trace_path, "rt") as source:
        for line in source:
            row = json.loads(line)
            if row["branch"] in ("current", "coupled"):
                groups[row["seed"], row["task"], row["branch"]].append(row)
    trials = []
    for seed, task in sorted({key[:2] for key in groups}):
        per_branch = {}
        for branch in ("current", "coupled"):
            council = ForecastCouncil()
            without_neural = ForecastCouncil(neural=False)
            errors, weights = defaultdict(list), defaultdict(list)
            no_neural_losses = []
            replayed = 0
            for row in groups[seed, task, branch]:
                prior = council.pending
                observed = council.observe(row["event"], row["neural_probability"])
                control = without_neural.observe(row["event"])
                if not math.isclose(
                    observed["prediction_buy"],
                    row["forecast"]["prediction_buy"],
                    abs_tol=1e-14,
                ):
                    raise ValueError("Council replay differs from the original trace")
                replayed += 1
                if row["index"] >= 64:
                    outcome = int(row["event"]["side"] == "buy")
                    for expert, probability in prior["experts"].items():
                        errors[expert].append((probability - outcome) ** 2)
                        weights[expert].append(prior["weights"][expert])
                    no_neural_losses.append(control["feedback"]["loss"])
            per_branch[branch] = {
                "replayed_windows": replayed,
                "expert_brier": {key: mean(values) for key, values in errors.items()},
                "mean_expert_attention_at_prediction": {
                    key: mean(values) for key, values in weights.items()
                },
                "without_neural_council_brier": mean(no_neural_losses),
            }
        current, coupled = groups[seed, task, "current"], groups[seed, task, "coupled"]
        if [row["event"] for row in current] != [row["event"] for row in coupled]:
            raise ValueError("Different inputs cannot identify the intervention")
        trials.append(
            {
                "seed": seed,
                "task": task,
                "branches": per_branch,
                "mean_absolute_neural_probability_change": mean(
                    abs(a["neural_probability"] - b["neural_probability"])
                    for a, b in zip(current, coupled)
                ),
                "mean_absolute_council_probability_change": mean(
                    abs(
                        a["forecast"]["prediction_buy"]
                        - b["forecast"]["prediction_buy"]
                    )
                    for a, b in zip(current, coupled)
                ),
            }
        )
    return {
        "schema": "nuria.multiscale.diagnosis.v1",
        "trace_gzip_sha256": result["trace_gzip_sha256"],
        "replay_matches_original": True,
        "current_count_half_life_inputs": math.log(0.5) / math.log(0.999),
        "original_reversal_at_input": 80,
        "original_windows_per_trial": 160,
        "summary": {
            task: {
                "current_expert_brier": {
                    name: mean(
                        row["branches"]["current"]["expert_brier"][name]
                        for row in trials
                        if row["task"] == task
                    )
                    for name in trials[0]["branches"]["current"]["expert_brier"]
                },
                "mean_absolute_neural_probability_change": mean(
                    row["mean_absolute_neural_probability_change"]
                    for row in trials
                    if row["task"] == task
                ),
                "mean_absolute_council_probability_change": mean(
                    row["mean_absolute_council_probability_change"]
                    for row in trials
                    if row["task"] == task
                ),
                "without_neural_council_brier": mean(
                    row["branches"]["current"]["without_neural_council_brier"]
                    for row in trials
                    if row["task"] == task
                ),
            }
            for task in sorted({row["task"] for row in trials})
        },
        "trials": trials,
        "source_hashes": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "cognition/forecast.py",
                "cognition/learning.py",
                "cognition/multiscale.py",
            )
        },
        "scope": "Exact replay of the forecast component from original prediction traces. Half-life and omitted ordered-history features are code observations. No causal attribution of all failure to a single factor, complete-organism evaluation or new neural advantage is implied.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve existing diagnosis evidence")
    report = diagnose(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
