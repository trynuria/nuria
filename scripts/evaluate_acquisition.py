"""Matched synthetic purchase opportunities, delayed feedback and paid failures."""

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

from cognition.acquisition import Acquisition

PROTOCOL = {
    "schema": "nuria.acquisition.validation.v1",
    "seeds": list(range(301, 313)),
    "opportunities": 1600,
    "warmup": 300,
    "tasks": ["selective", "reversal", "useless", "outages"],
    "branches": [
        "adaptive",
        "contextless",
        "no_forgetting",
        "frozen",
        "free",
        "always_paid",
    ],
    "cost": 0.03,
    "maximum_cost": 0.1,
    "delay_steps": "2 + opportunity modulo 7",
    "score": "Free Brier minus selected Brier minus actual virtual price, including failed delivery",
    "learning": "Selected-source gross Brier gain, contextual sample mean with update rate at least 0.08; bounded deterministic exploration at max(0.02, 0.2/sqrt(1+n/10))",
    "observed_before_selection": ["context", "free forecast", "both provider quotes"],
    "withheld": [
        "task",
        "generating probabilities",
        "future label",
        "unchosen provider forecasts and delivery status",
    ],
    "controls": "Shared latent opportunity stream and delayed feedback; no neural features, RPC, signing or real money",
}


def worlds(task, seed, count):
    rng = random.Random(seed)
    for step in range(count):
        hard, label = rng.random() < 0.5, int(rng.random() < 0.5)
        free_accuracy = 0.6 if hard else 0.9
        useful = "B" if task == "reversal" and step >= count // 2 else "A"
        accuracies = {"A": 0.6, "B": 0.6}
        if task == "useless":
            accuracies = {"A": free_accuracy, "B": free_accuracy}
        elif hard:
            accuracies[useful] = 0.9
            if task == "outages":
                accuracies["B"] = 0.8
        else:
            accuracies = {"A": 0.9, "B": 0.9}

        def forecast(accuracy):
            signal = label if rng.random() < accuracy else 1 - label
            return accuracy if signal else 1 - accuracy

        free = forecast(free_accuracy)
        values = {p: forecast(a) for p, a in accuracies.items()}
        failed = {
            p: task == "outages" and p == "A" and rng.random() < 0.35 for p in values
        }
        # A useless purchase is exactly the already available forecast, rather
        # than an additional independent signal with possible ensemble value.
        if task == "useless":
            values = {"A": free, "B": free}
        yield {
            "step": step,
            "context": "uncertain" if hard else "confident",
            "free": free,
            "label": label,
            "forecasts": values,
            "failed": failed,
        }


def trial(protocol, task, seed, branch):
    model = (
        Acquisition(
            ":memory:",
            f"synthetic:acquisition:{seed}",
            contextual=branch != "contextless",
            adaptive=branch != "no_forgetting",
        )
        if branch not in ("free", "always_paid")
        else None
    )
    pending, scores, choices = [], [], {"free": 0, "A": 0, "B": 0}
    stream_hash = hashlib.sha256()

    def close(clock):
        ready = [row for row in pending if row[0] <= clock]
        for due, row, selection, delivered in ready:
            step, label = row["step"], row["label"]
            price = 0 if selection == "free" else protocol["cost"]
            if model:
                outcome = model.settle(
                    str(step),
                    label,
                    due * 10 + 2,
                    learn=branch != "frozen" or step < protocol["warmup"],
                )
            else:
                prediction = row["free"] if delivered is None else delivered
                baseline, selected = (
                    (row["free"] - label) ** 2,
                    (prediction - label) ** 2,
                )
                outcome = {
                    "baseline_brier": baseline,
                    "selected_brier": selected,
                    "gross_gain": baseline - selected,
                    "net_gain": baseline - selected - price,
                    "cost": price,
                    "delivered": delivered is not None,
                }
            if step >= protocol["warmup"]:
                scores.append({"step": step, "selection": selection, **outcome})
                choices[selection] += 1
            pending.remove((due, row, selection, delivered))

    try:
        for row in worlds(task, seed, protocol["opportunities"]):
            step = row["step"]
            stream_hash.update(
                json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            )
            close(step)
            if model:
                terms = model.choose(
                    str(step),
                    row["context"],
                    row["free"],
                    {"A": protocol["cost"], "B": protocol["cost"]},
                    step * 10,
                    maximum_cost=protocol["maximum_cost"],
                )
                selection = terms["selection"]
            else:
                selection = "free" if branch == "free" else "A"
            delivered = (
                row["free"]
                if selection == "free"
                else None
                if row["failed"][selection]
                else row["forecasts"][selection]
            )
            if model:
                model.deliver(str(step), delivered, step * 10 + 1)
            pending.append((step + 2 + step % 7, row, selection, delivered))
        close(protocol["opportunities"] + 10)
        return {
            "task": task,
            "seed": seed,
            "branch": branch,
            "scored": len(scores),
            "choices": choices,
            "net_gain": mean(s["net_gain"] for s in scores),
            "baseline_brier": mean(s["baseline_brier"] for s in scores),
            "selected_brier": mean(s["selected_brier"] for s in scores),
            "mean_cost": mean(s["cost"] for s in scores),
            "failed_deliveries": sum(not s["delivered"] for s in scores),
            "second_half_net_gain": mean(
                s["net_gain"]
                for s in scores
                if s["step"] >= protocol["opportunities"] // 2
            ),
            "last_quarter_paid_fraction": mean(
                s["selection"] != "free"
                for s in scores
                if s["step"] >= protocol["opportunities"] * 3 // 4
            ),
            "world_sha256": stream_hash.hexdigest(),
            "journal": model.verify() if model else None,
        }
    finally:
        if model:
            model.db.close()


def evaluate(protocol):
    trials = [
        trial(protocol, task, seed, branch)
        for task in protocol["tasks"]
        for seed in protocol["seeds"]
        for branch in protocol["branches"]
    ]
    for task in protocol["tasks"]:
        for seed in protocol["seeds"]:
            hashes = {
                t["world_sha256"]
                for t in trials
                if t["task"] == task and t["seed"] == seed
            }
            if len(hashes) != 1:
                raise ValueError(
                    "Branches did not receive the same latent opportunities"
                )
    summaries = []
    for task in protocol["tasks"]:
        values = {
            b: [t for t in trials if t["task"] == task and t["branch"] == b]
            for b in protocol["branches"]
        }
        comparisons = {}
        for branch in protocol["branches"][1:]:
            differences = [
                a["net_gain"] - b["net_gain"]
                for a, b in zip(values["adaptive"], values[branch], strict=True)
            ]
            se = stdev(differences) / math.sqrt(len(differences))
            comparisons[branch] = {
                "mean_difference": mean(differences),
                "paired_standard_error": se,
                "descriptive_lower_mean_minus_2se": mean(differences) - 2 * se,
                "wins": sum(d > 0 for d in differences),
                "seeds": len(differences),
            }
        summaries.append(
            {
                "task": task,
                "mean_net_gain": {
                    b: mean(t["net_gain"] for t in rows) for b, rows in values.items()
                },
                "adaptive_last_quarter_paid_fraction": mean(
                    t["last_quarter_paid_fraction"] for t in values["adaptive"]
                ),
                "paired_comparisons": comparisons,
            }
        )
    return {
        "protocol": protocol,
        "summary": summaries,
        "trials": trials,
        "scope": "Synthetic statistical resource-selection results; not paid purchases, neural advantage or evidence of consciousness",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    raw = json.dumps(PROTOCOL, sort_keys=True, indent=2).encode()
    (args.directory / "protocol.json").write_bytes(raw)
    result = evaluate(PROTOCOL)
    result["created_utc"] = datetime.now(timezone.utc).isoformat()
    result["protocol_sha256"] = hashlib.sha256(raw).hexdigest()
    result["implementation_sha256"] = {
        str(p.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in (
            Path(__file__),
            Path(__file__).resolve().parents[1] / "cognition/acquisition.py",
        )
    }
    (args.directory / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "protocol_sha256": result["protocol_sha256"],
                "summary": result["summary"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
