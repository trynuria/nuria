"""Chronological recorded-organoid forecasting with matched neural controls."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from cognition.biology import digest, write_new
from cognition.biology_comparison import ridge_readout
from cognition.nwb_spikes import SOURCE, validate_binned

RULES = {
    "version": 1,
    "source_sha256": SOURCE["sha256"],
    "interval_seconds": [0, 120],
    "bin_seconds": 0.25,
    "units": "All curated Units rows, in stored order; no activity-based selection",
    "target": "log1p of all-unit spike count in the next 250 ms bin",
    "inputs": "Four latest all-unit count vectors, cumulative mean and causal EMA(alpha=0.25)",
    "partition": "Chronological 50% train / 25% validation / 25% test by target bin",
    "normalization": "Train-only mean and scale; fixed ridge choices 0.01, 1, 100 selected on validation",
    "baseline_candidates": ["training_mean", "last_bin", "ema", "ridge_history"],
    "baseline_selection": "Lowest validation MSE, then method name; no test-score selection",
    "neural_candidates": ["history_plus_recording_drive", "history_plus_zero_drive"],
    "encoding": "0.18*tanh(log1p(unit_count)/3) into fixed partitions of 128 sensory neurons",
    "neural_time": "250 simulated ms per recorded 250 ms bin; continuous separate circuit per arm",
    "feedback": "No target, label, reward or readout feedback to either circuit",
    "metric": "Mean squared error in log1p-count units; lower is better",
    "wall_admission_seconds": 180,
    "scope": "One recorded organoid and short interval; no live tissue, human-brain simulation, general intelligence or biological-learning claim",
}


def design(counts):
    """Every row observes bins at or before t and predicts bin t+1."""
    counts = np.asarray(counts, dtype=float)
    logged = np.log1p(counts)
    ema = np.zeros(counts.shape[1])
    features, targets, origins = [], [], []
    population = np.log1p(counts.sum(axis=1))
    cumulative = np.zeros(counts.shape[1])
    for t in range(len(counts) - 1):
        ema = logged[t] if t == 0 else 0.25 * logged[t] + 0.75 * ema
        cumulative += logged[t]
        if t >= 3:
            features.append(
                np.concatenate(
                    (logged[t - 3 : t + 1].ravel(), cumulative / (t + 1), ema)
                )
            )
            targets.append(population[t + 1])
            origins.append(t)
    return np.asarray(features), np.asarray(targets), np.asarray(origins)


def drive_for_counts(counts):
    counts = np.asarray(counts, dtype=float)
    if (
        counts.ndim != 1
        or not 1 <= len(counts) <= 128
        or not np.isfinite(counts).all()
        or np.any(counts < 0)
    ):
        raise ValueError("Finite nonnegative unit counts required")
    drive = np.zeros(1024)
    for group, value in zip(np.array_split(np.arange(128), len(counts)), counts):
        drive[group] = 0.18 * np.tanh(np.log1p(value) / 3)
    return drive


def score(features, neural, counts):
    x, y, origins = design(counts)
    bins = len(counts)
    target_bins = origins + 1
    train, validation, test = (
        target_bins < bins // 2,
        (target_bins >= bins // 2) & (target_bins < 3 * bins // 4),
        target_bins >= 3 * bins // 4,
    )
    if not all(mask.any() for mask in (train, validation, test)):
        raise ValueError("Empty chronological partition")
    if np.asarray(features).shape != x.shape or not np.allclose(
        features, x, rtol=1e-12, atol=1e-13
    ):
        raise ValueError("Retained past-only features disagree with source counts")
    methods = {}

    def add(name, predictions, extra=None):
        methods[name] = {
            "validation_mse": float(
                np.mean((predictions[validation] - y[validation]) ** 2)
            ),
            "test_mse": float(np.mean((predictions[test] - y[test]) ** 2)),
            "test_predictions": predictions[test].tolist(),
            **(extra or {}),
        }

    population = np.log1p(np.asarray(counts).sum(axis=1))
    add("training_mean", np.full(len(y), y[train].mean()))
    add("last_bin", population[origins])
    ema = np.empty(len(population))
    ema[0] = population[0]
    for t in range(1, len(ema)):
        ema[t] = 0.25 * population[t] + 0.75 * ema[t - 1]
    add("ema", ema[origins])
    for name, matrix in (
        ("ridge_history", x),
        *(
            (name, np.column_stack((x, np.asarray(values)[origins])))
            for name, values in neural.items()
        ),
    ):
        fitted = ridge_readout(
            matrix[train], matrix[validation], matrix[test], y[train], y[validation]
        )
        methods[name] = {
            "validation_mse": fitted["validation_mse"],
            "test_mse": float(
                np.mean((np.asarray(fitted["predictions"]) - y[test]) ** 2)
            ),
            "test_predictions": fitted["predictions"],
            "fit": fitted,
        }
    baseline = min(
        RULES["baseline_candidates"],
        key=lambda name: (methods[name]["validation_mse"], name),
    )
    return {
        "partition_sizes": [int(mask.sum()) for mask in (train, validation, test)],
        "target_bin_indices": target_bins[test].tolist(),
        "test_targets": y[test].tolist(),
        "methods": methods,
        "selected_simple_baseline": baseline,
    }


def run(binned_path, directory):
    from cognition.brain import AdaptiveBrain

    binned = json.loads(Path(binned_path).read_bytes())
    counts = validate_binned(binned)
    if (
        binned["bin_seconds"] != RULES["bin_seconds"]
        or [binned["start_seconds"], binned["stop_seconds"]]
        != RULES["interval_seconds"]
    ):
        raise ValueError("Recording interval differs from fixed protocol")
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_new(
        directory / "protocol.json",
        {
            "rules": RULES,
            "rules_sha256": digest(RULES),
            "binned_sha256": digest(binned),
        },
    )
    features, _, _ = design(counts)
    observations, neural = {}, {}
    started = time.monotonic()
    for arm, name in (
        ("zero_drive", "history_plus_zero_drive"),
        ("recording_drive", "history_plus_recording_drive"),
    ):
        brain = AdaptiveBrain(directory / arm)
        initial = brain.state_hash()
        windows = []
        for index, row in enumerate(counts):
            if time.monotonic() - started > RULES["wall_admission_seconds"]:
                write_new(
                    directory / "interrupted.json",
                    {
                        "arm": arm,
                        "completed_windows": len(windows),
                        "wall_seconds": time.monotonic() - started,
                    },
                )
                raise TimeoutError("Bounded replay wall admission exhausted")
            drive = (
                drive_for_counts(row) if arm == "recording_drive" else np.zeros(1024)
            )
            observed = brain.advance(duration_ms=250, regional_drive=drive)
            windows.append(
                {
                    "input_bin": index,
                    **{
                        key: observed[key]
                        for key in (
                            "before_state_sha256",
                            "after_state_sha256",
                            "spikes_sha256",
                            "spike_count",
                            "features",
                        )
                    },
                }
            )
        observations[arm] = {"initial_state_sha256": initial, "windows": windows}
        neural[name] = [item["features"] for item in windows]
    result = {
        "schema": 1,
        "mode": "recorded_biological",
        "live": False,
        "source": SOURCE,
        "rules_sha256": digest(RULES),
        "binned_sha256": digest(binned),
        "input_features": features.tolist(),
        "neural_features": neural,
        "neural_observations": observations,
        **score(features, neural, counts),
        "wall_seconds": time.monotonic() - started,
        "model_calls": 0,
        "scope": RULES["scope"],
    }
    result["matched_initial_state"] = (
        len({v["initial_state_sha256"] for v in observations.values()}) == 1
    )
    result["changed_spike_windows"] = sum(
        a["spikes_sha256"] != b["spikes_sha256"]
        for a, b in zip(
            observations["zero_drive"]["windows"],
            observations["recording_drive"]["windows"],
        )
    )
    write_new(directory / "result.json", result)
    return {
        key: result[key]
        for key in (
            "selected_simple_baseline",
            "partition_sizes",
            "matched_initial_state",
            "changed_spike_windows",
            "wall_seconds",
        )
    } | {
        "test_mse": {key: value["test_mse"] for key, value in result["methods"].items()}
    }


def verify(binned_path, result_path):
    binned = json.loads(Path(binned_path).read_bytes())
    result = json.loads(Path(result_path).read_bytes())
    counts = validate_binned(binned)
    if result["rules_sha256"] != digest(RULES) or result["binned_sha256"] != digest(
        binned
    ):
        raise ValueError("Experiment/source digest mismatch")
    expected = score(
        np.asarray(result["input_features"]), result["neural_features"], counts
    )
    if (
        result["selected_simple_baseline"] != expected["selected_simple_baseline"]
        or result["partition_sizes"] != expected["partition_sizes"]
        or not np.allclose(
            result["test_targets"], expected["test_targets"], rtol=1e-12, atol=1e-13
        )
        or result["target_bin_indices"] != expected["target_bin_indices"]
        or set(result["methods"]) != set(expected["methods"])
    ):
        raise ValueError(
            "Chronological partition, targets or baseline selection changed"
        )
    for arm, name in (
        ("zero_drive", "history_plus_zero_drive"),
        ("recording_drive", "history_plus_recording_drive"),
    ):
        observations = result["neural_observations"][arm]["windows"]
        if (
            len(observations) != len(counts)
            or [w["input_bin"] for w in observations] != list(range(len(counts)))
            or [w["features"] for w in observations] != result["neural_features"][name]
        ):
            raise ValueError("Neural observations and readout features disagree")
        previous = result["neural_observations"][arm]["initial_state_sha256"]
        for window in observations:
            if window["before_state_sha256"] != previous:
                raise ValueError("Neural state sequence changed")
            previous = window["after_state_sha256"]
    arms = result["neural_observations"]
    matched = (
        arms["zero_drive"]["initial_state_sha256"]
        == arms["recording_drive"]["initial_state_sha256"]
    )
    changed = sum(
        a["spikes_sha256"] != b["spikes_sha256"]
        for a, b in zip(
            arms["zero_drive"]["windows"], arms["recording_drive"]["windows"]
        )
    )
    if (
        result["matched_initial_state"] != matched
        or result["changed_spike_windows"] != changed
    ):
        raise ValueError("Matched-arm claim does not reconstruct")
    for name, method in expected["methods"].items():
        for key in ("validation_mse", "test_mse", "test_predictions"):
            if not np.allclose(
                method[key], result["methods"][name][key], rtol=1e-8, atol=1e-10
            ):
                raise ValueError("Readout or score mismatch: " + name)
        if "fit" in method:
            retained = result["methods"][name].get("fit", {})
            if set(retained) != set(method["fit"]):
                raise ValueError("Retained fitted model fields changed")
            for key, value in method["fit"].items():
                if not np.allclose(value, retained[key], rtol=1e-8, atol=1e-10):
                    raise ValueError("Retained fitted model does not reconstruct")
    return {
        "verified": True,
        "scope": "Source-bound past-only design, readout refitting and score reconstruction; not independent raw-file provenance or neural-feature regeneration",
    }
