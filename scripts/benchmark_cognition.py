"""Bounded neural evaluation and queue-capacity measurement, without RPC calls."""

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from cognition.brain import AdaptiveBrain
from cognition.learning import FEATURES, OnlineReadout
from cognition.worker import Organism


def neural_tasks(directory: Path) -> list:
    results = []
    for name, probability in (("alternation", 0.1), ("persistence", 0.9)):
        brain = AdaptiveBrain(directory / name)
        learner = OnlineReadout()
        rng = np.random.default_rng(51051)
        outcomes = [int(rng.integers(0, 2))]
        for _ in range(240):
            previous = outcomes[-1]
            outcomes.append(previous if rng.random() < probability else 1 - previous)
        examples = []
        for i in range(240):
            event = {
                "id": f"{name}-{i}",
                "source": "benchmark",
                "side": "buy" if outcomes[i] else "sell",
                "quote_amount": 1.0,
            }
            if i == 160:
                brain.exc.plastic_gain = 0
            frame = brain.advance(event, duration_ms=20)
            x = np.zeros(FEATURES)
            x[:64] = frame["features"]
            x[64] = 1
            if i < 160:
                learner.learn(x, outcomes[i + 1])
            else:
                p = learner.probability(x)
                ablated = x.copy()
                ablated[:64] = 0
                examples.append((p, learner.probability(ablated), outcomes[i + 1]))
        results.append(
            {
                "task": name,
                "train": 160,
                "held_out": 80,
                "accuracy": statistics.mean(
                    (p >= 0.5) == bool(y) for p, _, y in examples
                ),
                "brier": statistics.mean((p - y) ** 2 for p, _, y in examples),
                "neural_ablation_brier": statistics.mean(
                    (a - y) ** 2 for _, a, y in examples
                ),
                "chance_brier": 0.25,
                "structured_side_feature": False,
                "test_weights_frozen": True,
                "scope": "Controlled next-side task using actual 1024-neuron spike features; not a live-market result",
            }
        )
    return results


def throughput(directory: Path, count: int) -> dict:
    organism = Organism(directory, "benchmark-source")
    times = []
    started = time.monotonic()
    for start in range(1, count + 1, 4):
        rows = [
            (
                i,
                {
                    "id": f"capacity-{i}",
                    "source": "benchmark",
                    "side": "buy" if i % 3 else "sell",
                    "quote_amount": 0.05 + (i % 100) / 20,
                },
            )
            for i in range(start, min(count + 1, start + 4))
        ]
        frame = organism.cycle(rows, durable=organism.tick % 5 == 4)
        times.append(frame["cycle_wall_seconds"])
    organism.save()
    duration = time.monotonic() - started
    ordered = sorted(times)
    return {
        "inputs": count,
        "seconds": duration,
        "inputs_per_second_unpaced": count / duration,
        "cycle_p95_seconds": ordered[int(0.95 * (len(ordered) - 1))],
        "target_rate_per_second": 100000 / 86400,
        "paced_inputs_per_day_upper_bound": 4 * 86400 / max(1, statistics.mean(times)),
        "persisted_cursor": organism.journal.restore()[0]["cursor"],
        "journal_valid": organism.journal.verify(full=True)["valid"],
        "disk_bytes": sum(p.stat().st_size for p in directory.glob("*") if p.is_file()),
        "scope": "Bounded actual-model replay; daily number is a projection, not a 24-hour end-to-end RPC soak",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--inputs", type=int, default=1000)
    parser.add_argument("--neural-only", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "neural_tasks": neural_tasks(args.directory / "tasks"),
    }
    if not args.neural_only:
        result["capacity"] = throughput(args.directory / "capacity", args.inputs)
    (args.directory / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
