"""Run the fixed TVB/Brian comparison and retain every prediction and null result."""

from __future__ import annotations

import argparse
import gc
import gzip
import hashlib
import json
import platform
import resource
import time
from importlib.metadata import distributions
from pathlib import Path

import numpy as np

from cognition.journal import canonical
from cognition.multiscale import CouplingConfig
from cognition.multiscale_loop import PerceptionPath

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "research/multiscale/protocol.json"


def events(task: str, seed: int, count: int) -> list[dict]:
    task_seed = int.from_bytes(
        hashlib.sha256(f"{task}:{seed}".encode()).digest()[:8], "little"
    )
    rng = np.random.default_rng(task_seed)
    sides, rows = [], []
    for index in range(count):
        if task == "delayed_dependency":
            side = int(rng.integers(0, 2)) if index < 4 else sides[index - 4]
            if index >= 4 and rng.random() >= 0.9:
                side = 1 - side
        elif task == "context_reversal":
            if not rows:
                side = int(rng.integers(0, 2))
            else:
                repeat = rows[-1]["quote_amount"] == 0.25
                if index >= count // 2:
                    repeat = not repeat
                probability = 0.9 if repeat else 0.1
                side = sides[-1] if rng.random() < probability else 1 - sides[-1]
        else:
            raise ValueError("Unknown frozen task")
        amount = (0.25, 2.0)[int(rng.integers(0, 2))]
        sides.append(side)
        rows.append(
            {
                "id": hashlib.sha256(
                    f"stimulus:{seed}:{task}:{index}".encode()
                ).hexdigest(),
                "side": "buy" if side else "sell",
                "quote_amount": amount,
                "creator_fee": amount * 0.005,
                "source": "synthetic:multiscale",
            }
        )
    return rows


def summarize(trials: list[dict], protocol: dict) -> dict:
    summary = {}
    for task in protocol["tasks"]:
        rows = [row for row in trials if row["task"] == task]
        means = {
            branch: float(
                np.mean([r["branches"][branch]["council_brier"] for r in rows])
            )
            for branch in protocol["branches"]
        }
        contrasts = {}
        for branch in list(protocol["branches"])[1:]:
            gains = np.array(
                [
                    row["branches"]["current"]["council_brier"]
                    - row["branches"][branch]["council_brier"]
                    for row in rows
                ]
            )
            se = float(np.std(gains, ddof=1) / np.sqrt(len(gains)))
            margin = 2.5705818366147395 * se
            contrasts[branch] = {
                "paired_gain": float(gains.mean()),
                "standard_error": se,
                "lower_95": float(gains.mean() - margin),
                "upper_95": float(gains.mean() + margin),
                "seed_wins": int(np.count_nonzero(gains > 0)),
            }
        summary[task] = {"council_brier": means, "comparisons_to_current": contrasts}
    return summary


def evaluate(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    raw = PROTOCOL_PATH.read_bytes()
    protocol = json.loads(raw)
    protocol_hash = hashlib.sha256(raw).hexdigest()
    expected = (PROTOCOL_PATH.parent / "protocol.sha256").read_text().split()[0]
    if protocol_hash != expected:
        raise ValueError("The fixed protocol changed")
    (directory / "protocol.json").write_bytes(raw)
    source_names = [
        "cognition/brain.py",
        "cognition/multiscale.py",
        "cognition/multiscale_loop.py",
        "cognition/forecast.py",
        "cognition/learning.py",
        "scripts/evaluate_multiscale.py",
    ]
    source_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in source_names
    }
    trace_path = directory / "traces.jsonl.gz"
    trace_digest = hashlib.sha256()
    trials = []
    start = time.perf_counter()
    all_zero_return_match = True
    with (
        trace_path.open("xb") as output,
        gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as trace,
    ):
        for seed_index, seed in enumerate(protocol["seeds"]):
            for task_index, task in enumerate(protocol["tasks"]):
                stream = events(task, seed, protocol["events_per_trial"])
                row = {
                    "seed": seed,
                    "task": task,
                    "stream_sha256": hashlib.sha256(canonical(stream)).hexdigest(),
                    "branches": {},
                }
                neural_hashes, action_scores = {}, {}
                names = list(protocol["branches"])
                # Rotate order to avoid always giving one branch cold-start costs.
                shift = (seed_index + task_index) % len(names)
                for branch in names[shift:] + names[:shift]:
                    setup = time.perf_counter()
                    config = (
                        None
                        if branch == "current"
                        else CouplingConfig(**protocol["branches"][branch])
                    )
                    model = PerceptionPath(
                        directory / f"state-{seed}-{task}-{branch}", config
                    )
                    setup_seconds = time.perf_counter() - setup
                    (
                        losses,
                        neural_losses,
                        baseline_losses,
                        accuracy,
                        attention,
                        timings,
                    ) = [], [], [], [], [], []
                    neural_hashes[branch], action_scores[branch] = [], []
                    for index, event in enumerate(stream):
                        record = model.observe(event)
                        neural_hashes[branch].append(
                            record["spikes_sha256"]
                            + record["features_sha256"]
                            + record["neural_state_sha256"]
                        )
                        action_scores[branch].append(record["action_spike_scores"])
                        timings.append(record["wall_seconds"])
                        if index >= protocol["warmup_events"]:
                            feedback = record["forecast"]["feedback"]
                            if feedback is None or record["neural"]["feedback"] is None:
                                raise RuntimeError(
                                    "Scored observation has no prior forecast"
                                )
                            losses.append(feedback["loss"])
                            neural_losses.append(record["neural"]["feedback"]["loss"])
                            baseline_losses.append(feedback["baseline_loss"])
                            accuracy.append(
                                int(
                                    (feedback["probability"] >= 0.5)
                                    == (event["side"] == "buy")
                                )
                            )
                            attention.append(record["forecast"]["attention"]["neural"])
                        macro = record["multiscale"]
                        public = {
                            "seed": seed,
                            "task": task,
                            "branch": branch,
                            "index": index,
                            "event": event,
                            "spikes_sha256": record["spikes_sha256"],
                            "features_sha256": record["features_sha256"],
                            "neural_state_sha256": record["neural_state_sha256"],
                            "action_spike_scores": record["action_spike_scores"],
                            "forecast": record["forecast"],
                            "neural_probability": record["neural"]["prediction_buy"],
                            "neural_feedback": record["neural"]["feedback"],
                            "joint_state_sha256": macro["after_sha256"]
                            if macro
                            else None,
                            "regional_state_sha256": macro["regional"]["after_sha256"]
                            if macro
                            else None,
                            "regional_drive_sha256": macro["regional_drive_sha256"]
                            if macro
                            else None,
                        }
                        encoded = canonical(public) + b"\n"
                        trace.write(encoded)
                        trace_digest.update(encoded)
                    row["branches"][branch] = {
                        "scored_outcomes": len(losses),
                        "council_brier": float(np.mean(losses)),
                        "neural_brier": float(np.mean(neural_losses)),
                        "repeat_brier": float(np.mean(baseline_losses)),
                        "accuracy": float(np.mean(accuracy)),
                        "mean_neural_attention": float(np.mean(attention)),
                        "setup_seconds": setup_seconds,
                        "mean_window_seconds": float(np.mean(timings)),
                        "p95_window_seconds": float(np.percentile(timings, 95)),
                        "max_window_seconds": max(timings),
                        "model_identity": model.model.identity
                        if config is not None
                        else {"model": "nuria-cognition-v1"},
                    }
                    del model
                    gc.collect()
                    print(
                        json.dumps(
                            {
                                "seed": seed,
                                "task": task,
                                "branch": branch,
                                "elapsed_seconds": round(
                                    time.perf_counter() - start, 1
                                ),
                            }
                        ),
                        flush=True,
                    )
                row["zero_return_matches_current"] = (
                    neural_hashes["current"] == neural_hashes["no_return"]
                )
                all_zero_return_match &= row["zero_return_matches_current"]
                row["coupled_changed_neural_windows"] = sum(
                    a != b
                    for a, b in zip(neural_hashes["current"], neural_hashes["coupled"])
                )
                row["coupled_changed_action_score_windows"] = sum(
                    a != b
                    for a, b in zip(action_scores["current"], action_scores["coupled"])
                )
                trials.append(row)
                (directory / "partial-trials.json").write_bytes(canonical(trials))
    summary = summarize(trials, protocol)
    promotion = (
        all(
            row["comparisons_to_current"]["coupled"]["paired_gain"] >= 0.005
            and row["comparisons_to_current"]["coupled"]["lower_95"] > 0
            for row in summary.values()
        )
        and all_zero_return_match
    )
    result = {
        "schema": "nuria.multiscale.result.v1",
        "protocol_sha256": protocol_hash,
        "source_hashes": source_hashes,
        "summary": summary,
        "trials": trials,
        "zero_return_matches_current": all_zero_return_match,
        "predictive_promotion_gate_passed": promotion,
        "trace_uncompressed_sha256": trace_digest.hexdigest(),
        "trace_gzip_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "trace_rows": len(protocol["seeds"])
        * len(protocol["tasks"])
        * len(protocol["branches"])
        * protocol["events_per_trial"],
        "elapsed_seconds": time.perf_counter() - start,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
            "packages": dict(
                sorted((d.metadata["Name"], d.version) for d in distributions())
            ),
        },
        "scope": "Controlled synthetic perception/forecast comparison. No complete organism, market profitability, biological hardware or consciousness claim.",
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = evaluate(args.directory)
    print(
        json.dumps(
            {
                "summary": result["summary"],
                "predictive_promotion_gate_passed": result[
                    "predictive_promotion_gate_passed"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
