"""Prospective, chronological ridge-readout comparison on labelled waveforms.

The baseline sees the same waveform statistics. The candidate adds a fixed
Brian2 transform. Ridge strength is selected on validation only. Synthetic
fixtures test the method, not the value of biological computation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from cognition.biology import (
    MAX_BYTES,
    digest,
    validate_recording,
    waveform_features,
    write_new,
)

RIDGES = (0.01, 1.0, 100.0)
RULES = {
    "version": 1,
    "task": "Predict a separately supplied scalar target",
    "split": "Chronological 50% train / 25% validation / 25% test",
    "ridge": list(RIDGES),
    "metric": "Test mean squared error; lower is better",
    "candidate": "Same statistics plus isolated fixed Brian2 transform",
    "baseline": "Ridge regression on the identical waveform statistics",
    "normalization": "Train-only mean and standard deviation",
    "neural_state": "Reset before each window; no label/reward feedback to the circuit",
    "publication_gate": "No claim of biological learning, general intelligence or superiority from a fixture",
}


def load_course(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Course byte limit")
    course = json.loads(path.read_bytes())
    if (
        course.get("schema") != 1
        or not isinstance(course.get("target_definition"), str)
        or not course["target_definition"].strip()
    ):
        raise ValueError(
            "A labelled task with an explicit target definition is required"
        )
    rows = course.get("rows", [])
    if not 12 <= len(rows) <= 128:
        raise ValueError("Course needs 12–128 chronological windows")
    identities, seen, previous_stop = set(), set(), None
    for row in rows:
        recording = row["recording"]
        values = validate_recording(recording)
        if recording["mode"] == "public_observed":
            raise ValueError(
                "Public-view frames lack acquisition timing and task labels for this comparison"
            )
        from cognition.biology import utc

        start = utc(row["start_utc"])
        stop = utc(row["stop_utc"])
        if start >= stop or (previous_stop is not None and start < previous_stop):
            raise ValueError("Chronological non-overlapping windows required")
        previous_stop = stop
        if recording["acquired_utc"] != row["start_utc"]:
            raise ValueError("Recording and task-window start disagree")
        if recording.get("sample_rate_hz") is None:
            raise ValueError("Comparison requires a declared acquisition rate")
        expected_duration = values.shape[1] / recording["sample_rate_hz"]
        if abs((stop - start).total_seconds() - expected_duration) > 1e-6:
            raise ValueError("Sample count/rate must match the task-window duration")
        identities.add(
            (
                recording["mode"],
                recording["provider"],
                recording["recording_id"],
                tuple(recording["channels"]),
                recording["units"],
                recording["sample_rate_hz"],
            )
        )
        sample_hash = digest(
            {"channels": recording["channels"], "samples": recording["samples"]}
        )
        if sample_hash in seen:
            raise ValueError(
                "Duplicate waveform windows cannot cross evaluation partitions"
            )
        seen.add(sample_hash)
        if (
            type(row["target"]) not in (int, float)
            or not np.isfinite(row["target"])
            or abs(row["target"]) > 1e9
        ):
            raise ValueError("Finite bounded target required")
    if len(identities) != 1:
        raise ValueError("One consistent recording identity required per course")
    return course


def ridge_readout(train, validation, test, train_y, validation_y):
    mean, scale = train.mean(axis=0), train.std(axis=0)
    scale = np.maximum(scale, 1e-9)

    def design(values):
        normalized = (values - mean) / scale
        return np.column_stack((np.ones(len(values)), normalized))

    a, v, t = map(design, (train, validation, test))
    candidates = []
    for strength in RIDGES:
        penalty = np.eye(a.shape[1]) * strength
        penalty[0, 0] = 0
        weights = np.linalg.solve(a.T @ a + penalty, a.T @ train_y)
        loss = float(np.mean((v @ weights - validation_y) ** 2))
        candidates.append((loss, strength, weights))
    loss, strength, weights = min(candidates, key=lambda c: (c[0], c[1]))
    return {
        "validation_mse": loss,
        "ridge": strength,
        "predictions": (t @ weights).tolist(),
        "weights": weights.tolist(),
        "train_mean": mean.tolist(),
        "train_scale": scale.tolist(),
    }


def run_comparison(course_path, directory):
    from cognition.biology import sensory_drive
    from cognition.brain import AdaptiveBrain

    course = load_course(course_path)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    # This file is created before running either model or inspecting any scores.
    write_new(
        directory / "protocol.json", {"rules": RULES, "course_sha256": digest(course)}
    )
    x, neural, targets = [], [], []
    for index, row in enumerate(course["rows"]):
        features = waveform_features(row["recording"])
        x.append(
            sum(
                (
                    features[k]
                    for k in (
                        "rms",
                        "standard_deviation",
                        "line_length_per_sample",
                        "spectral_centroid_cycles_per_sample",
                    )
                ),
                [],
            )
        )
        brain = AdaptiveBrain(directory / f"window-{index:03}")
        observed = brain.advance(
            duration_ms=100, regional_drive=sensory_drive(row["recording"])
        )
        neural.append(observed["features"])
        targets.append(row["target"])
    x, neural, y = np.asarray(x), np.asarray(neural), np.asarray(targets)
    cut_a, cut_b = len(y) // 2, 3 * len(y) // 4
    result = {
        "schema": 1,
        "mode": course["rows"][0]["recording"]["mode"],
        "course_sha256": digest(course),
        "rules_sha256": digest(RULES),
        "partition_sizes": [cut_a, cut_b - cut_a, len(y) - cut_b],
        "baseline_features": x.tolist(),
        "neural_features": neural.tolist(),
        "targets": y.tolist(),
        "methods": {},
        "biological_learning_demonstrated": False,
        "model_calls": 0,
    }
    for name, features in (
        ("waveform_statistics", x),
        ("statistics_plus_brian2", np.column_stack((x, neural))),
    ):
        fitted = ridge_readout(
            features[:cut_a],
            features[cut_a:cut_b],
            features[cut_b:],
            y[:cut_a],
            y[cut_a:cut_b],
        )
        fitted["test_mse"] = float(
            np.mean((np.asarray(fitted["predictions"]) - y[cut_b:]) ** 2)
        )
        result["methods"][name] = fitted
    result["scope"] = (
        "One task and recording; no biological-learning or general intelligence inference. A fixture result only verifies software."
    )
    write_new(directory / "result.json", result)
    return result


def verify_comparison(path):
    """Recompute both readouts and all scores from retained numeric features."""
    result = json.loads(Path(path).read_bytes())
    a, middle, _ = result["partition_sizes"]
    b = a + middle
    x, neural, y = (
        np.asarray(result[k], dtype=float)
        for k in ("baseline_features", "neural_features", "targets")
    )
    if result["rules_sha256"] != digest(RULES):
        raise ValueError("Comparison rule identity changed")
    for name, features in (
        ("waveform_statistics", x),
        ("statistics_plus_brian2", np.column_stack((x, neural))),
    ):
        expected = ridge_readout(
            features[:a], features[a:b], features[b:], y[:a], y[a:b]
        )
        retained = result["methods"][name]
        expected["test_mse"] = float(
            np.mean((np.asarray(expected["predictions"]) - y[b:]) ** 2)
        )
        if set(expected) != set(retained):
            raise ValueError("Readout evidence fields changed")
        for key in expected:
            if not np.allclose(expected[key], retained[key], rtol=1e-9, atol=1e-10):
                raise ValueError("Retained readout does not reproduce: " + key)
    return {
        "verified": True,
        "scope": "Independent score/readout reconstruction, not raw signal provenance or Brian2 feature regeneration",
    }
