"""Frozen-protocol comparison using measured Brian2 cue features and fresh worlds."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from cognition.discovery import BRANCHES, TASKS, DiscoveryLab, keyed
from cognition.journal import canonical
from cognition.spike_sensor import SpikeSensor


def evaluate(directory: Path, seeds: list[int], trials: int = 1200) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    if trials <= 240:
        raise ValueError("Validation needs more than 240 warmup trials")
    root = Path(__file__).resolve().parents[1]
    sources = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "cognition/discovery.py",
            "cognition/spike_sensor.py",
            "scripts/evaluate_discovery.py",
        )
    }
    protocol = {
        "seeds": seeds,
        "tasks": TASKS,
        "trials_per_task": trials,
        "warmup": 240,
        "branches": BRANCHES,
        "sources": sources,
        "primary_endpoint": "paired mean net virtual reward after equal warmup; higher is better",
        "secondary_endpoints": [
            "oracle regret",
            "Brier",
            "probe count",
            "change detections",
        ],
        "claim_gate": "spike_memory minus symbolic_memory paired mean > 0 and lower 95% t interval > 0 for each task; otherwise no neural superiority claim",
        "feature_sampling": "16 recorded windows for each of 8 cues plus blank, one fixed sensor seed; vectors replayed with seeded selection. Does not test long recurrent delays or STDP.",
        "selection": "fixed balanced tasks for validation; adaptive live curriculum evaluated separately",
        "scope": "Local software experiment, not independent preregistration or market forecasting",
    }
    protocol_hash = hashlib.sha256(canonical(protocol)).hexdigest()
    (directory / "protocol.json").write_bytes(canonical(protocol))
    sensor = SpikeSensor(directory / "sensor")
    pools = {cue: [] for cue in range(9)}
    for repeat in range(16):
        for cue in keyed(9021, "cue-order", repeat).permutation(9):
            pools[int(cue)].append(sensor(int(cue) if cue < 8 else None))
    (directory / "spike-features.json").write_bytes(
        canonical({str(k): v for k, v in pools.items()})
    )
    feature_hash = hashlib.sha256(
        (directory / "spike-features.json").read_bytes()
    ).hexdigest()
    results = []
    for seed in seeds:
        for task in TASKS:
            lab = DiscoveryLab(seed)
            counter = 0

            def recorded(cue):
                nonlocal counter
                counter += 1
                values = pools[8 if cue is None else cue]
                index = int(keyed(seed, task, "feature", counter).integers(len(values)))
                return values[index]

            scored = {branch: [] for branch in BRANCHES}

            def retain(branch, outcomes):
                for outcome in outcomes:
                    if int(outcome["decision"]["trial_id"].rsplit(":", 1)[1]) >= 240:
                        scored[branch].append(outcome)

            for index in range(trials):
                result = lab.step(recorded, task)
                for branch, outcomes in result["settlements"].items():
                    retain(branch, outcomes)
            for branch, model in lab.learners[task].items():
                retain(branch, model.settle(trials + 6))
            row = {"seed": seed, "task": task, "branches": {}}
            for branch, model in lab.learners[task].items():
                outcomes = scored[branch]
                predictions = [
                    r
                    for r in outcomes
                    if r["decision"]["predicted_success"] is not None
                ]
                n = len(outcomes)
                if n != trials - 240:
                    raise RuntimeError("Scored decision coverage differs from protocol")
                row["branches"][branch] = {
                    "scored": n,
                    "mean_net_reward": sum(r["net"] for r in outcomes) / n,
                    "mean_oracle_regret": sum(r["regret"] for r in outcomes) / n,
                    "brier": sum(
                        (r["decision"]["predicted_success"] - r["reward"]) ** 2
                        for r in outcomes
                        if r["decision"]["predicted_success"] is not None
                    )
                    / len(predictions)
                    if predictions
                    else None,
                    "predictions_evaluated": len(predictions),
                    "probes_total": model.probes,
                    "detected_changes": model.change_events,
                    "final_virtual_balance": model.balance,
                }
            results.append(row)
    summary = {}
    # t_(0.975,11)=2.201; conservative 2.6 for >=6 seeds, exploratory otherwise.
    critical = 2.6 if len(seeds) >= 6 else None
    for task in TASKS:
        rows = [r for r in results if r["task"] == task]
        means = {
            branch: float(
                np.mean([r["branches"][branch]["mean_net_reward"] for r in rows])
            )
            for branch in BRANCHES
        }
        comparisons = {}
        for branch in BRANCHES[1:]:
            difference = np.asarray(
                [
                    r["branches"]["spike_memory"]["mean_net_reward"]
                    - r["branches"][branch]["mean_net_reward"]
                    for r in rows
                ]
            )
            se = (
                float(np.std(difference, ddof=1) / np.sqrt(len(rows)))
                if len(rows) > 1
                else None
            )
            comparisons[branch] = {
                "paired_gain": float(difference.mean()),
                "paired_standard_error": se,
                "wins": int(np.count_nonzero(difference > 0)),
                "lower_95_bound": float(difference.mean() - critical * se)
                if critical
                else None,
            }
        summary[task] = {"mean_net_reward": means, "paired_comparisons": comparisons}
    result = {
        "protocol": protocol,
        "protocol_sha256": protocol_hash,
        "features_sha256": feature_hash,
        "summary": summary,
        "trials": results,
        "neural_superiority_gate_passed": all(
            s["paired_comparisons"]["symbolic_memory"]["lower_95_bound"] is not None
            and s["paired_comparisons"]["symbolic_memory"]["lower_95_bound"] > 0
            for s in summary.values()
        ),
        "limitations": [
            "One fixed sensory circuit seed; world seeds are independent",
            "Measured feature windows are reused, not freshly simulated for every decision",
            "Cue memory is a stored spike representation, not evidence of spontaneous recurrent memory",
            "Reward probabilities and task families are designed; uncertainty is unknown to the learner within that bounded world",
            "No STDP, consciousness, live trading or financial-execution inference",
        ],
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--trials", type=int, default=1200)
    args = parser.parse_args()
    seeds = list(range(101, 105)) if args.development else list(range(201, 213))
    result = evaluate(args.directory, seeds, args.trials)
    print(
        json.dumps(
            {
                "protocol_sha256": result["protocol_sha256"],
                "summary": result["summary"],
                "neural_superiority_gate_passed": result[
                    "neural_superiority_gate_passed"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
