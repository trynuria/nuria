"""Independently reconstruct the published scores from prediction-time traces."""

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


def close(actual, expected):
    if not math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-12):
        raise ValueError(f"Published value differs: {actual} versus {expected}")


def verify(directory: Path) -> dict:
    result = json.loads((directory / "result.json").read_text())
    raw_protocol = (ROOT / "research/multiscale/protocol.json").read_bytes()
    protocol = json.loads(raw_protocol)
    expected = (ROOT / "research/multiscale/protocol.sha256").read_text().split()[0]
    if (
        hashlib.sha256(raw_protocol).hexdigest() != expected
        or result["protocol_sha256"] != expected
    ):
        raise ValueError("Protocol digest differs")
    for path, expected_hash in result["source_hashes"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Evaluated source changed: " + path)
    trace_path = directory / "traces.jsonl.gz"
    if (
        hashlib.sha256(trace_path.read_bytes()).hexdigest()
        != result["trace_gzip_sha256"]
    ):
        raise ValueError("Compressed trace changed")
    observations = defaultdict(list)
    streams, hashes, action_scores = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    previous, total, bytes_read, digest = {}, 0, 0, hashlib.sha256()
    with gzip.open(trace_path, "rb") as trace:
        while line := trace.readline(262145):
            bytes_read += len(line)
            if len(line) > 262144 or bytes_read > 64_000_000:
                raise ValueError("Trace exceeds its bounds")
            digest.update(line)
            row = json.loads(line)
            key = row["seed"], row["task"], row["branch"]
            if (
                key[0] not in protocol["seeds"]
                or key[1] not in protocol["tasks"]
                or key[2] not in protocol["branches"]
            ):
                raise ValueError("Unexpected trial")
            index = row["index"]
            prior = previous.get(key)
            if index != (prior["index"] + 1 if prior else 0):
                raise ValueError("Missing, duplicated or reordered observation")
            event = row["event"]
            streams[key].append(event)
            hashes[key].append(
                row["spikes_sha256"]
                + row["features_sha256"]
                + row["neural_state_sha256"]
            )
            action_scores[key].append(row["action_spike_scores"])
            if prior is not None:
                prediction = prior["forecast"]["prediction_buy"]
                outcome = int(event["side"] == "buy")
                loss = (prediction - outcome) ** 2
                feedback = row["forecast"]["feedback"]
                close(feedback["probability"], prediction)
                close(feedback["loss"], loss)
                neural_prediction = row["neural_feedback"]["probability"]
                if abs(neural_prediction - prior["neural_probability"]) > 0.000000501:
                    raise ValueError("Neural score does not use its prior prediction")
                neural_loss = (neural_prediction - outcome) ** 2
                close(neural_loss, row["neural_feedback"]["loss"])
                if index >= protocol["warmup_events"]:
                    observations[key].append(
                        (
                            loss,
                            neural_loss,
                            feedback["baseline_loss"],
                            int((prediction >= 0.5) == bool(outcome)),
                            row["forecast"]["attention"]["neural"],
                        )
                    )
            elif row["forecast"]["feedback"] is not None:
                raise ValueError("First observation has a fabricated prior prediction")
            previous[key] = row
            total += 1
    if digest.hexdigest() != result["trace_uncompressed_sha256"]:
        raise ValueError("Uncompressed trace changed")
    expected_groups = (
        len(protocol["seeds"]) * len(protocol["tasks"]) * len(protocol["branches"])
    )
    if (
        len(previous) != expected_groups
        or total != expected_groups * protocol["events_per_trial"]
        or total != result["trace_rows"]
    ):
        raise ValueError("Incomplete trial coverage")
    table = {(r["seed"], r["task"]): r for r in result["trials"]}
    if len(table) != len(protocol["seeds"]) * len(protocol["tasks"]) or len(
        result["trials"]
    ) != len(table):
        raise ValueError("Incomplete published summary")
    all_match = True
    for seed in protocol["seeds"]:
        for task in protocol["tasks"]:
            summary = table[seed, task]
            reference = streams[seed, task, "current"]
            raw = json.dumps(
                reference, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if hashlib.sha256(raw).hexdigest() != summary["stream_sha256"]:
                raise ValueError("Input stream hash differs")
            for branch in protocol["branches"]:
                key = seed, task, branch
                if (
                    streams[key] != reference
                    or len(streams[key]) != protocol["events_per_trial"]
                ):
                    raise ValueError(
                        "Branches did not receive the same complete stream"
                    )
                rows = observations[key]
                published = summary["branches"][branch]
                if (
                    len(rows)
                    != protocol["events_per_trial"] - protocol["warmup_events"]
                    or len(rows) != published["scored_outcomes"]
                ):
                    raise ValueError("Incorrect scored coverage")
                for index, metric in enumerate(
                    (
                        "council_brier",
                        "neural_brier",
                        "repeat_brier",
                        "accuracy",
                        "mean_neural_attention",
                    )
                ):
                    close(published[metric], mean(row[index] for row in rows))
            match = hashes[seed, task, "current"] == hashes[seed, task, "no_return"]
            if match != summary["zero_return_matches_current"]:
                raise ValueError("Zero-return control was reported incorrectly")
            all_match &= match
            changed = sum(
                a != b
                for a, b in zip(
                    hashes[seed, task, "current"], hashes[seed, task, "coupled"]
                )
            )
            changed_actions = sum(
                a != b
                for a, b in zip(
                    action_scores[seed, task, "current"],
                    action_scores[seed, task, "coupled"],
                )
            )
            if (
                changed != summary["coupled_changed_neural_windows"]
                or changed_actions != summary["coupled_changed_action_score_windows"]
            ):
                raise ValueError("Reported intervention effect differs")
    promotion = all_match
    for task in protocol["tasks"]:
        summary = result["summary"][task]
        for branch in protocol["branches"]:
            close(
                summary["council_brier"][branch],
                mean(
                    table[seed, task]["branches"][branch]["council_brier"]
                    for seed in protocol["seeds"]
                ),
            )
        for branch in list(protocol["branches"])[1:]:
            gains = [
                table[seed, task]["branches"]["current"]["council_brier"]
                - table[seed, task]["branches"][branch]["council_brier"]
                for seed in protocol["seeds"]
            ]
            se = stdev(gains) / math.sqrt(len(gains))
            published = summary["comparisons_to_current"][branch]
            for name, value in [
                ("paired_gain", mean(gains)),
                ("standard_error", se),
                ("lower_95", mean(gains) - 2.5705818366147395 * se),
                ("upper_95", mean(gains) + 2.5705818366147395 * se),
            ]:
                close(published[name], value)
            if branch == "coupled":
                promotion &= mean(gains) >= 0.005 and published["lower_95"] > 0
    if (
        promotion != result["predictive_promotion_gate_passed"]
        or all_match != result["zero_return_matches_current"]
    ):
        raise ValueError("Promotion decision differs from the fixed gate")
    return {
        "verified": True,
        "windows": total,
        "scored_forecasts": sum(len(rows) for rows in observations.values()),
        "trial_groups": expected_groups,
        "zero_return_matches_current": all_match,
        "predictive_promotion_gate_passed": promotion,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2))


if __name__ == "__main__":
    main()
