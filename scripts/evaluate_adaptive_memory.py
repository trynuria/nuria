"""Fixed, paired forecast and virtual information-choice comparisons."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import platform
import resource
import subprocess
import time
from collections import deque
from pathlib import Path
from statistics import mean, stdev

import numpy as np

from cognition.adaptive_forecast import (
    MAX_DEPTH,
    MAX_LAG,
    AdaptiveForecast,
    OutcomeMemory,
)
from cognition.forecast import ForecastCouncil, context
from cognition.journal import canonical

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "research/adaptive-memory/protocol.json"
PRIMARY = ("delayed_copy", "parity", "lag_switch", "context_switch")
CONTROLS = ("stable_context", "first_order", "iid_fair", "iid_biased")
SOURCE_FILES = (
    "cognition/adaptive_forecast.py",
    "cognition/forecast.py",
    "scripts/evaluate_adaptive_memory.py",
)


def rng_for(seed: int, *parts) -> np.random.Generator:
    digest = hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "little"))


def stream(
    task: str, seed: int, transitions: int
) -> tuple[list[dict], list[dict], list[int]]:
    """Environment only: hidden rules never enter the forecaster's observation."""
    rng = rng_for(seed, task, "environment")
    offers_rng = rng_for(seed, task, "offers")
    lag = int(rng.choice([3, 4, 5, 7, 9, 12]))
    parity_order = int(rng.choice([2, 3]))
    repeat_probability = float(rng.choice([0.1, 0.9]))
    bias = float(rng.choice([0.15, 0.35, 0.65, 0.85]))
    stable_kind = int(rng.integers(2))
    mappings = {}
    sides, rows, offers, measurements = [], [], [], []
    side = int(rng.integers(2))
    for index in range(transitions + 1):
        amount_bin, fee_bin = map(int, rng.integers(0, 16, 2))
        amount = 2 ** (amount_bin + 0.25) / 16
        sides.append(side)
        rows.append(
            {
                "id": hashlib.sha256(
                    f"memory:{seed}:{task}:{index}".encode()
                ).hexdigest(),
                "source": "synthetic:adaptive-memory",
                "side": "buy" if side else "sell",
                "quote_amount": amount,
                "creator_fee": amount * (fee_bin * 5 + 2) / 10000,
            }
        )
        # This offer is available now; its measurement is delivered only after
        # the following outcome. Quality/cost do not disclose its future side.
        offers.append(
            {"quality": 0.9, "cost": float(offers_rng.choice([0.05, 0.15, 0.30, 0.60]))}
        )
        if index:
            measurements.append(side if offers_rng.random() < 0.9 else 1 - side)
        else:
            measurements.append(-1)
        next_index = index + 1
        if task == "delayed_copy":
            target = sides[-lag] if next_index >= lag else int(rng.integers(2))
            side = target if rng.random() < 0.9 else 1 - target
        elif task == "parity":
            target = (
                int(sum(sides[-parity_order:]) % 2)
                if len(sides) >= parity_order
                else int(rng.integers(2))
            )
            side = target if rng.random() < 0.9 else 1 - target
        elif task == "lag_switch":
            phase = next_index // 384
            if phase not in mappings:
                phase_rng = rng_for(seed, task, "rule", phase)
                mappings[phase] = (
                    int(phase_rng.choice([3, 5, 9])),
                    int(phase_rng.integers(2)),
                )
            phase_lag, invert = mappings[phase]
            target = (
                (sides[-phase_lag] ^ invert)
                if len(sides) >= phase_lag
                else int(rng.integers(2))
            )
            side = target if rng.random() < 0.9 else 1 - target
        elif task in ("context_switch", "stable_context"):
            phase = next_index // 512 if task == "context_switch" else 0
            if phase not in mappings:
                mappings[phase] = rng_for(seed, task, "rule", phase).permutation(
                    [0.1] * 8 + [0.9] * 8
                )
            use_fee = phase % 2 if task == "context_switch" else stable_kind
            p_repeat = float(mappings[phase][fee_bin if use_fee else amount_bin])
            side = side if rng.random() < p_repeat else 1 - side
        elif task == "first_order":
            side = side if rng.random() < repeat_probability else 1 - side
        elif task in ("iid_fair", "iid_biased"):
            side = int(rng.random() < (0.5 if task == "iid_fair" else bias))
        else:
            raise ValueError("Unregistered task")
    return rows, offers, measurements


class SimpleMemory:
    """Strong sequence-only alternatives: learned lag mixture or suffix backoff.

    Neither receives the generating lag, parity order, regime or task name.
    They retain the same 16-bit buffer and use the same public count smoothing.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self.bank = OutcomeMemory(0.999)
        self.bits = deque(maxlen=MAX_LAG)
        self.clock = 0
        self.pending = None
        self.weights = np.full(MAX_LAG, 1 / MAX_LAG)

    def observe(self, event: dict, neural_probability=0.5, *, learn=True) -> dict:
        side, _, _ = context(event)
        feedback = None
        if self.pending:
            p = self.pending["probability"]
            feedback = {"probability": p, "loss": (p - side) ** 2}
            if learn:
                for key in self.pending["keys"]:
                    self.bank.learn(key, side, self.clock)
                if self.kind == "lag":
                    probs = np.asarray(self.pending["probabilities"])
                    likelihood = probs if side else 1 - probs
                    log_weights = 0.995 * np.log(self.weights) + np.log(
                        np.clip(likelihood, 1e-6, 1 - 1e-6)
                    )
                    log_weights -= log_weights.max()
                    self.weights = np.exp(log_weights)
                    self.weights = (
                        0.99 * self.weights / self.weights.sum() + 0.01 / MAX_LAG
                    )
        self.clock += int(learn)
        self.bits.append(side)
        bits = list(self.bits)
        if self.kind == "lag":
            keys = [f"l:{lag}:{bits[-lag]}" for lag in range(1, len(bits) + 1)]
            probabilities = [
                self.bank.probability(
                    keys[lag - 1] if lag <= len(keys) else "buy", self.clock
                )
                for lag in range(1, MAX_LAG + 1)
            ]
            p = float(self.weights @ probabilities)
        else:
            keys = [
                "s:" + "".join(map(str, bits[-depth:]))
                for depth in range(1, min(MAX_DEPTH, len(bits)) + 1)
            ]
            p = self.bank.probability("buy", self.clock)
            for key in keys:
                positive, total = self.bank.counts(key, self.clock)
                p = (positive + 4 * p) / (total + 4)
            probabilities = [p]
        self.pending = {
            "keys": ["buy", *keys],
            "probabilities": probabilities,
            "probability": p,
        }
        return {"prediction_buy": p, "feedback": feedback}

    def state(self) -> dict:
        return {
            "kind": self.kind,
            "bank": self.bank.state(),
            "bits": list(self.bits),
            "clock": self.clock,
            "pending": self.pending,
            "weights": self.weights.tolist(),
        }


def models() -> dict:
    return {
        "current": ForecastCouncil(),
        "adaptive": AdaptiveForecast(),
        "no_sequence": AdaptiveForecast(sequence=False),
        "no_fast": AdaptiveForecast(fast=False),
        "frozen": AdaptiveForecast(),
        "lag_only": SimpleMemory("lag"),
        "suffix_only": SimpleMemory("suffix"),
    }


def information_choice(
    probability: float, offer: dict, measurement: int, outcome: int
) -> tuple[bool, float]:
    buy = 2 * offer["quality"] - 1 - offer["cost"] > abs(2 * probability - 1)
    direction = measurement if buy else int(probability >= 0.5)
    net = (1 if direction == outcome else -1) - (offer["cost"] if buy else 0)
    return bool(buy), net


def interval(values: list[float], critical: float) -> dict:
    se = stdev(values) / math.sqrt(len(values))
    gain = mean(values)
    return {
        "mean": gain,
        "lower_95": gain - critical * se,
        "upper_95": gain + critical * se,
        "standard_error": se,
        "positive_seeds": sum(value > 0 for value in values),
    }


def summarize(trials: list[dict], protocol: dict, phase: str) -> tuple[dict, dict]:
    seeds = protocol[f"{phase}_seeds"]
    critical = 2.200985160082949 if phase == "validation" else 4.302652729696142
    summary = {}
    for task in protocol["tasks"]:
        rows = [row for row in trials if row["task"] == task]
        scores = {
            branch: {
                metric: mean(row["scores"][branch][metric] for row in rows)
                for metric in (
                    "brier",
                    "accuracy",
                    "net_reward",
                    "purchase_rate",
                    "virtual_cost",
                    "free_only_reward",
                )
            }
            for branch in protocol["branches"]
        }
        contrasts = {
            branch: interval(
                [
                    row["scores"]["current"]["brier"] - row["scores"][branch]["brier"]
                    for row in rows
                ],
                critical,
            )
            for branch in protocol["branches"]
            if branch != "current"
        }
        summary[task] = {"scores": scores, "brier_gains_vs_current": contrasts}
    seed_net = [
        mean(
            row["scores"]["adaptive"]["net_reward"]
            - row["scores"]["current"]["net_reward"]
            for row in trials
            if row["seed"] == seed and row["task"] in PRIMARY
        )
        for seed in seeds
    ]
    net = interval(seed_net, critical)
    capabilities = all(
        summary[task]["brier_gains_vs_current"]["adaptive"]["mean"] >= 0.01
        and summary[task]["brier_gains_vs_current"]["adaptive"]["lower_95"] > 0
        for task in PRIMARY
    )
    regression = all(
        -summary[task]["brier_gains_vs_current"]["adaptive"]["lower_95"] <= 0.005
        for task in CONTROLS
    )
    decision = net["mean"] >= 0.01 and net["lower_95"] > 0
    return summary, {
        "capability_gate": capabilities,
        "control_nonregression_gate": regression,
        "virtual_decision_gate": decision,
        "virtual_net_reward_gain": net,
        "all_predictive_gates_passed": capabilities and regression and decision,
        "scope": "Statistical component and virtual information choices; production promotion additionally requires continuity, isolation and failure tests",
    }


def evaluate(directory: Path, phase: str) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    raw = PROTOCOL.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PROTOCOL.with_suffix(".sha256").read_text().split()[0]:
        raise ValueError("Fixed protocol changed")
    protocol = json.loads(raw)
    hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }
    if phase == "validation":
        subprocess.run(
            ["git", "diff", "--exit-code", "HEAD", "--", *SOURCE_FILES],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        tracked = subprocess.check_output(
            ["git", "ls-files", "--", *SOURCE_FILES], cwd=ROOT, text=True
        ).splitlines()
        if set(tracked) != set(SOURCE_FILES):
            raise ValueError("Commit the complete implementation before validation")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    trials, trace_files = [], {}
    started = time.perf_counter()
    for task in protocol["tasks"]:
        path = directory / f"{task}.jsonl.gz"
        trace_digest = hashlib.sha256()
        with (
            path.open("xb") as target,
            gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as output,
        ):
            for seed in protocol[f"{phase}_seeds"]:
                learners = models()
                rows, offers, measurements = stream(
                    task, seed, protocol["transitions_per_trial"]
                )
                aggregates = {
                    name: {
                        "n": 0,
                        "brier": 0.0,
                        "accuracy": 0,
                        "net_reward": 0.0,
                        "purchases": 0,
                        "virtual_cost": 0.0,
                        "free_only_reward": 0.0,
                        "wall_seconds": 0.0,
                    }
                    for name in learners
                }
                previous = None
                for index, event in enumerate(rows):
                    next_probabilities = []
                    outcome = int(event["side"] == "buy")
                    for branch, learner in learners.items():
                        begin = time.perf_counter()
                        observed = learner.observe(
                            event,
                            0.5,
                            learn=branch != "frozen"
                            or index <= protocol["warmup_transitions"],
                        )
                        aggregates[branch]["wall_seconds"] += (
                            time.perf_counter() - begin
                        )
                        next_probabilities.append(observed["prediction_buy"])
                        if index > protocol["warmup_transitions"]:
                            probability = previous[branch]
                            loss = (probability - outcome) ** 2
                            if not math.isclose(
                                loss, observed["feedback"]["loss"], abs_tol=1e-12
                            ):
                                raise RuntimeError(
                                    "A model scored a different prior forecast"
                                )
                            buy, net = information_choice(
                                probability,
                                offers[index - 1],
                                measurements[index],
                                outcome,
                            )
                            score = aggregates[branch]
                            score["n"] += 1
                            score["brier"] += loss
                            score["accuracy"] += int(
                                (probability >= 0.5) == bool(outcome)
                            )
                            score["net_reward"] += net
                            score["purchases"] += int(buy)
                            score["virtual_cost"] += (
                                offers[index - 1]["cost"] if buy else 0
                            )
                            score["free_only_reward"] += (
                                1 if (probability >= 0.5) == bool(outcome) else -1
                            )
                    previous = dict(zip(learners, next_probabilities))
                    trace = {
                        "seed": seed,
                        "task": task,
                        "index": index,
                        "event": event,
                        "offer_for_next_outcome": offers[index],
                        "measurement_for_observed_outcome": measurements[index],
                        "next_probabilities": next_probabilities,
                    }
                    line = canonical(trace) + b"\n"
                    output.write(line)
                    trace_digest.update(line)
                scores, states = {}, {}
                for branch, learner in learners.items():
                    score = aggregates[branch]
                    n = score["n"]
                    scores[branch] = {
                        "n": n,
                        **{
                            metric: score[metric] / n
                            for metric in (
                                "brier",
                                "accuracy",
                                "net_reward",
                                "virtual_cost",
                                "free_only_reward",
                            )
                        },
                        "purchase_rate": score["purchases"] / n,
                        "mean_observation_seconds": score["wall_seconds"] / len(rows),
                    }
                    raw_state = canonical(learner.state())
                    states[branch] = {
                        "bytes": len(raw_state),
                        "sha256": hashlib.sha256(raw_state).hexdigest(),
                    }
                trial = {
                    "seed": seed,
                    "task": task,
                    "stream_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
                    "scores": scores,
                    "final_states": states,
                    "adaptive_attention": dict(
                        zip(
                            learners["adaptive"].pending["weights"],
                            learners["adaptive"].mixture.tolist(),
                        )
                    ),
                    "adaptive_memory_cells": learners["adaptive"].metrics()[
                        "memory_cells"
                    ],
                }
                trials.append(trial)
                (directory / "partial-trials.json").write_bytes(canonical(trials))
                print(
                    json.dumps(
                        {
                            "phase": phase,
                            "task": task,
                            "seed": seed,
                            "current": scores["current"]["brier"],
                            "adaptive": scores["adaptive"]["brier"],
                            "elapsed_seconds": round(time.perf_counter() - started, 2),
                        }
                    ),
                    flush=True,
                )
        trace_files[path.name] = {
            "gzip_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "uncompressed_sha256": trace_digest.hexdigest(),
        }
    summary, gates = summarize(trials, protocol, phase)
    result = {
        "schema": "nuria.adaptive-memory.result.v1",
        "phase": phase,
        "implementation_commit": commit,
        "source_hashes": hashes,
        "protocol_sha256": digest,
        "summary": summary,
        "gates": gates,
        "trials": trials,
        "trace_files": trace_files,
        "elapsed_seconds": time.perf_counter() - started,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "scope": "New environment seeds within declared synthetic families. No biological, complete-organism, live-market, actual resource purchase or consciousness inference.",
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--phase", choices=("development", "validation"), required=True)
    args = parser.parse_args()
    result = evaluate(args.directory, args.phase)
    print(json.dumps(result["gates"], indent=2))


if __name__ == "__main__":
    main()
