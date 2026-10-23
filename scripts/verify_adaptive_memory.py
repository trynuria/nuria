"""Reconstruct primary scores and virtual choices from the retained prior forecasts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ("delayed_copy", "parity", "lag_switch", "context_switch")
CONTROLS = ("stable_context", "first_order", "iid_fair", "iid_biased")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def close(actual, expected):
    if not math.isclose(actual, expected, abs_tol=1e-11, rel_tol=1e-10):
        raise ValueError(f"Published score differs: {actual} versus {expected}")


def contrast(values, critical):
    center = mean(values)
    se = stdev(values) / math.sqrt(len(values))
    return {
        "mean": center,
        "lower_95": center - critical * se,
        "upper_95": center + critical * se,
        "standard_error": se,
        "positive_seeds": sum(x > 0 for x in values),
    }


def verify(directory: Path, replay_environment: bool = False) -> dict:
    protocol_path = ROOT / "research/adaptive-memory/protocol.json"
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    digest = hashlib.sha256(protocol_bytes).hexdigest()
    expected = protocol_path.with_suffix(".sha256").read_text().split()[0]
    result = json.loads((directory / "result.json").read_text())
    implementation = json.loads(
        (protocol_path.parent / "implementation.json").read_text()
    )
    if (
        digest != expected
        or result["protocol_sha256"] != digest
        or implementation["protocol_sha256"] != digest
    ):
        raise ValueError("The fixed protocol changed")
    if result["source_hashes"] != implementation["source_hashes"]:
        raise ValueError("Result does not use the frozen implementation")
    for name, source_hash in implementation["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != source_hash:
            raise ValueError("Evaluated source changed: " + name)
    phase = result["phase"]
    if phase not in ("development", "validation"):
        raise ValueError("Invalid experiment phase")
    seeds = protocol[f"{phase}_seeds"]
    branches = protocol["branches"]
    expected_files = {task + ".jsonl.gz" for task in protocol["tasks"]}
    if set(result["trace_files"]) != expected_files:
        raise ValueError("Incomplete trace set")
    table = {(row["seed"], row["task"]): row for row in result["trials"]}
    expected_groups = {(seed, task) for seed in seeds for task in protocol["tasks"]}
    if set(table) != expected_groups or len(table) != len(result["trials"]):
        raise ValueError("Incomplete or duplicated trial summaries")
    totals = defaultdict(lambda: [0, 0.0, 0, 0.0, 0, 0.0, 0.0])
    observations = 0
    for task in protocol["tasks"]:
        name = task + ".jsonl.gz"
        path = directory / name
        evidence = result["trace_files"][name]
        if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["gzip_sha256"]:
            raise ValueError("Compressed trace changed")
        trace_digest, source_digests = hashlib.sha256(), {}
        previous = {}
        reference = None
        reference_seed = None
        uncompressed = 0
        with gzip.open(path, "rb") as source:
            while line := source.readline(32769):
                uncompressed += len(line)
                if len(line) > 32768 or uncompressed > 64_000_000:
                    raise ValueError("Trace exceeds its bound")
                trace_digest.update(line)
                row = json.loads(line)
                seed, index = row["seed"], row["index"]
                if seed not in seeds or row["task"] != task or type(index) is not int:
                    raise ValueError("Unregistered observation")
                prior = previous.get(seed)
                if index != (prior["index"] + 1 if prior else 0):
                    raise ValueError("Observation missing, reordered or duplicated")
                probabilities = row["next_probabilities"]
                if len(probabilities) != len(branches) or any(
                    not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities
                ):
                    raise ValueError("Invalid forecast vector")
                offer = row["offer_for_next_outcome"]
                if (
                    offer["quality"] != 0.9
                    or offer["cost"] not in protocol["information_decision"]["costs"]
                ):
                    raise ValueError("Unregistered information offer")
                event = row["event"]
                if event["side"] not in ("buy", "sell"):
                    raise ValueError("Invalid observed label")
                if replay_environment:
                    if seed != reference_seed:
                        from scripts.evaluate_adaptive_memory import stream

                        reference = stream(
                            task, seed, protocol["transitions_per_trial"]
                        )
                        reference_seed = seed
                    events, offers, measurements = reference
                    if (
                        event != events[index]
                        or offer != offers[index]
                        or row["measurement_for_observed_outcome"]
                        != measurements[index]
                    ):
                        raise ValueError("Environment replay differs")
                if not prior:
                    if row["measurement_for_observed_outcome"] != -1:
                        raise ValueError("First observation has no prior delivery")
                    source_digests[seed] = hashlib.sha256(b"[")
                else:
                    source_digests[seed].update(b",")
                    measurement = row["measurement_for_observed_outcome"]
                    if measurement not in (0, 1):
                        raise ValueError("Invalid delivered measurement")
                    if index > protocol["warmup_transitions"]:
                        outcome = int(event["side"] == "buy")
                        offered = prior["offer_for_next_outcome"]
                        for branch, probability in zip(
                            branches, prior["next_probabilities"]
                        ):
                            loss = (probability - outcome) ** 2
                            # Reconstruct the choice without calling the tested
                            # controller or trusting a stored reward/choice bit.
                            buy = 2 * offered["quality"] - 1 - offered["cost"] > abs(
                                2 * probability - 1
                            )
                            direction = measurement if buy else int(probability >= 0.5)
                            reward = (1 if direction == outcome else -1) - (
                                offered["cost"] if buy else 0
                            )
                            values = totals[seed, task, branch]
                            values[0] += 1
                            values[1] += loss
                            values[2] += int((probability >= 0.5) == bool(outcome))
                            values[3] += reward
                            values[4] += int(buy)
                            values[5] += offered["cost"] if buy else 0
                            values[6] += (
                                1 if (probability >= 0.5) == bool(outcome) else -1
                            )
                source_digests[seed].update(canonical(event))
                previous[seed] = row
                observations += 1
        if trace_digest.hexdigest() != evidence["uncompressed_sha256"]:
            raise ValueError("Uncompressed trace changed")
        if set(previous) != set(seeds):
            raise ValueError("Incomplete seed coverage")
        for seed in seeds:
            if previous[seed]["index"] != protocol["transitions_per_trial"]:
                raise ValueError("Incomplete trial history")
            source_digests[seed].update(b"]")
            if source_digests[seed].hexdigest() != table[seed, task]["stream_sha256"]:
                raise ValueError("Input stream changed")
    reconstructed = {}
    for (seed, task, branch), values in totals.items():
        n = values[0]
        if n != protocol["transitions_per_trial"] - protocol["warmup_transitions"]:
            raise ValueError("Wrong scored coverage")
        metrics = dict(
            zip(
                (
                    "brier",
                    "accuracy",
                    "net_reward",
                    "purchase_rate",
                    "virtual_cost",
                    "free_only_reward",
                ),
                [x / n for x in values[1:]],
            )
        )
        published = table[seed, task]["scores"][branch]
        if published["n"] != n:
            raise ValueError("Incorrect published coverage")
        for name, value in metrics.items():
            close(published[name], value)
        reconstructed[seed, task, branch] = metrics
    critical = 2.200985160082949 if phase == "validation" else 4.302652729696142
    contrasts = {}
    for task in protocol["tasks"]:
        summary = result["summary"][task]
        for branch in branches:
            for metric in (
                "brier",
                "accuracy",
                "net_reward",
                "purchase_rate",
                "virtual_cost",
                "free_only_reward",
            ):
                close(
                    summary["scores"][branch][metric],
                    mean(reconstructed[seed, task, branch][metric] for seed in seeds),
                )
            if branch != "current":
                gains = [
                    reconstructed[seed, task, "current"]["brier"]
                    - reconstructed[seed, task, branch]["brier"]
                    for seed in seeds
                ]
                c = contrast(gains, critical)
                for name, value in c.items():
                    close(summary["brier_gains_vs_current"][branch][name], value)
                contrasts[task, branch] = c
    primary = all(
        contrasts[task, "adaptive"]["mean"] >= 0.01
        and contrasts[task, "adaptive"]["lower_95"] > 0
        for task in PRIMARY
    )
    controls = all(
        -contrasts[task, "adaptive"]["lower_95"] <= 0.005 for task in CONTROLS
    )
    net_gains = [
        mean(
            reconstructed[seed, task, "adaptive"]["net_reward"]
            - reconstructed[seed, task, "current"]["net_reward"]
            for task in PRIMARY
        )
        for seed in seeds
    ]
    net = contrast(net_gains, critical)
    for name, value in net.items():
        close(result["gates"]["virtual_net_reward_gain"][name], value)
    decision = net["mean"] >= 0.01 and net["lower_95"] > 0
    expected_gates = {
        "capability_gate": primary,
        "control_nonregression_gate": controls,
        "virtual_decision_gate": decision,
        "all_predictive_gates_passed": primary and controls and decision,
    }
    if any(result["gates"][name] != value for name, value in expected_gates.items()):
        raise ValueError("Promotion gates disagree with recorded predictions")
    return {
        "verified": True,
        "phase": phase,
        "observations": observations,
        "scored_forecasts": sum(v[0] for v in totals.values()),
        "trial_streams": len(expected_groups),
        "environment_replayed": replay_environment,
        **expected_gates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--replay-environment", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, args.replay_environment), indent=2))


if __name__ == "__main__":
    main()
