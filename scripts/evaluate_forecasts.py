"""Reproducible, paired prequential comparisons with equal input information.

The council evaluated here is the production forecast component, not a claim
about the entire organism or the usefulness of spiking synapses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from cognition.forecast import ForecastCouncil, context

PROTOCOL = {
    "version": "forecast-comparison-v2",
    "seeds": list(range(201, 221)),
    "development_run": "v1 seeds 101–110 showed logistic was stronger after a context switch. The production council now includes that existing alternative; v2 uses new seeds and reports both outcomes.",
    "events": 2000,
    "warmup": 500,
    "switch_at": 1000,
    "tasks": ["amount_context", "fee_context", "context_switch", "unpredictable"],
    "scoring": "Next-side forecast stored before the following outcome; all models may update after scoring. Warmup excluded equally.",
    "baselines": [
        "frequency",
        "repeat",
        "amount",
        "fee_rate",
        "joint_context",
        "context_logistic",
    ],
    "ablations": [
        "no_outcome_memory",
        "uniform_attention",
        "frozen_after_warmup",
        "no_neural_expert",
    ],
    "scope": "Synthetic recurring-context and context-switch tasks; statistical forecast component. No live-market, whole-organism, consciousness or neural-plasticity inference.",
    "neural_probability": "0.5 for every sample; no fabricated spike features. Production also supplies its separate spike readout.",
    "success_rule": "Report every task, seed and comparator. A mean win is not a win on every task. Negative and null results remain in the output.",
}


def stream(task: str, seed: int, count: int, switch_at: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    amount_rule = rng.permutation([0.1] * 8 + [0.9] * 8)
    fee_rule = rng.permutation([0.1] * 8 + [0.9] * 8)
    side = int(rng.integers(0, 2))
    rows = []
    for i in range(count + 1):
        amount_bin, fee_bin = rng.integers(0, 16, 2)
        amount = 2 ** (float(amount_bin) + 0.25) / 16
        fee = amount * (float(fee_bin) * 5 + 2) / 10000
        rows.append(
            {
                "id": f"sample-{seed}-{i}",
                "source": "benchmark",
                "side": "buy" if side else "sell",
                "quote_amount": amount,
                "creator_fee": fee,
            }
        )
        if task == "unpredictable":
            repeat_probability = 0.5
        elif task == "fee_context" or (task == "context_switch" and i >= switch_at):
            repeat_probability = float(fee_rule[fee_bin])
        else:
            repeat_probability = float(amount_rule[amount_bin])
        if rng.random() >= repeat_probability:
            side = 1 - side
    return rows


class LogisticBaseline:
    """Online logistic transition model with the same binned observations.

    One-hot amount and fee context avoids handicapping it with linear raw inputs.
    Learning rate and L2 are fixed across tasks and seeds.
    """

    def __init__(self):
        self.w = np.zeros(33)
        self.pending = None

    def observe(self, event: dict) -> float | None:
        side, amount, fee = context(event)
        scored = None
        if self.pending:
            p, previous, x = self.pending
            scored = p if previous else 1 - p
            target = int(side == previous)
            self.w += 0.25 * ((target - p) * x - 0.0001 * self.w)
        x = np.zeros(33)
        x[0] = x[1 + amount] = x[17 + fee] = 1
        p = 1 / (1 + math.exp(-float(np.clip(self.w @ x, -12, 12))))
        self.pending = p, side, x
        return scored


def score(probabilities: list[float], outcomes: list[int]) -> dict:
    p, y = np.asarray(probabilities), np.asarray(outcomes)
    return {
        "n": len(p),
        "accuracy": float(np.mean((p >= 0.5) == y)),
        "brier": float(np.mean((p - y) ** 2)),
    }


def legacy_comparison() -> list[dict]:
    """Exact old generator/split, without rerunning or inventing neural scores."""
    results = []
    old = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/evaluation.json").read_text()
    )
    for name, repeat_probability in (("alternation", 0.1), ("persistence", 0.9)):
        rng = np.random.default_rng(51051)
        outcomes = [int(rng.integers(0, 2))]
        for _ in range(240):
            previous = outcomes[-1]
            outcomes.append(
                previous if rng.random() < repeat_probability else 1 - previous
            )
        repeated = sum(a == b for a, b in zip(outcomes[:160], outcomes[1:161]))
        p_repeat = (repeated + 1) / 162
        p = [p_repeat if previous else 1 - p_repeat for previous in outcomes[160:240]]
        neural = next(t for t in old["neural_tasks"] if t["task"] == name)
        results.append(
            {
                "task": name,
                "learned_repeat_probability": p_repeat,
                "simple_predictor": score(p, outcomes[161:241]),
                "published_neural": {
                    "accuracy": neural["accuracy"],
                    "brier": neural["brier"],
                },
                "conclusion": "Simple predictor matches accuracy and has lower probability error; no neural advantage established.",
            }
        )
    return results


def evaluate(protocol: dict) -> dict:
    trials = []
    for task in protocol["tasks"]:
        for seed in protocol["seeds"]:
            rows = stream(task, seed, protocol["events"], protocol["switch_at"])
            models = {
                "full": ForecastCouncil(),
                "no_outcome_memory": ForecastCouncil(memory=False),
                "uniform_attention": ForecastCouncil(attention=False),
                "frozen_after_warmup": ForecastCouncil(),
                "no_neural_expert": ForecastCouncil(neural=False),
            }
            # This ablation removes a non-informative expert, not real neurons.
            logistic = LogisticBaseline()
            predictions = {name: [] for name in (*models, *protocol["baselines"])}
            outcomes = []
            full_pending = None
            for i, event in enumerate(rows):
                values = {}
                for name, model in models.items():
                    observed = model.observe(
                        event,
                        learn=name != "frozen_after_warmup" or i <= protocol["warmup"],
                    )
                    if observed["feedback"]:
                        values[name] = observed["feedback"]["probability"]
                p_logistic = logistic.observe(event)
                if i > protocol["warmup"]:
                    side = int(event["side"] == "buy")
                    outcomes.append(side)
                    for name in models:
                        predictions[name].append(values[name])
                    for name in protocol["baselines"]:
                        predictions[name].append(
                            p_logistic
                            if name == "context_logistic"
                            else full_pending["experts"][name]
                        )
                full_pending = models["full"].pending
            trials.append(
                {
                    "task": task,
                    "seed": seed,
                    "scores": {
                        name: score(p, outcomes) for name, p in predictions.items()
                    },
                    "final_attention": models["full"].metrics()["attention"],
                    "memory_cells": models["full"].metrics()["memory_cells"],
                    "training_updates": {
                        name: model.updates for name, model in models.items()
                    },
                }
            )
    summaries = []
    for task in protocol["tasks"]:
        selected = [t for t in trials if t["task"] == task]
        means = {
            name: {
                metric: float(np.mean([t["scores"][name][metric] for t in selected]))
                for metric in ("accuracy", "brier")
            }
            for name in selected[0]["scores"]
        }
        comparisons = {}
        for name in means:
            if name == "full":
                continue
            deltas = np.asarray(
                [
                    t["scores"][name]["brier"] - t["scores"]["full"]["brier"]
                    for t in selected
                ]
            )
            comparisons[name] = {
                "mean_brier_improvement": float(deltas.mean()),
                "standard_error_across_seeds": float(
                    deltas.std(ddof=1) / np.sqrt(len(deltas))
                ),
                "paired_seed_wins": int(np.sum(deltas > 0)),
                "seeds": len(deltas),
            }
        summaries.append(
            {"task": task, "mean_scores": means, "paired_comparisons": comparisons}
        )
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "legacy_correction": legacy_comparison(),
        "summary": summaries,
        "trials": trials,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    raw = json.dumps(PROTOCOL, sort_keys=True, indent=2).encode()
    (args.directory / "protocol.json").write_bytes(raw)
    result = evaluate(PROTOCOL)
    result["protocol_sha256"] = hashlib.sha256(raw).hexdigest()
    result["implementation_sha256"] = {
        str(p.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in (
            Path(__file__),
            Path(__file__).resolve().parents[1] / "cognition/forecast.py",
        )
    }
    (args.directory / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "legacy_correction": result["legacy_correction"],
                "summary": result["summary"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
