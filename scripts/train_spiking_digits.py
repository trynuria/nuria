"""Fit the frozen digit models using the official training partition only.

The separate qualification command owns evaluation. This command accepts one
hash-pinned training CSV, creates a new output directory, and writes JSON model
checkpoints before any qualification labels are inspected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from cognition.spiking_digits import SpikingDigits

TRAIN_SHA256 = "e1b683cc211604fe8fd8c4417e6a69f31380e0c61d4af22e93cc21e9257ffedd"
EPOCHS = 125
SEEDS = (202611, 202612, 202613)
DEVELOPMENT_SEED = 20261024


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_new(path, value):
    raw = canonical(value) + b"\n"
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def load_training(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TRAIN_SHA256:
        raise ValueError("Only the pinned official training partition is accepted")
    data = np.loadtxt(path, delimiter=",")
    if (
        data.shape != (3823, 65)
        or not np.isfinite(data).all()
        or np.any(data != np.rint(data))
        or np.any(data[:, :64] < 0)
        or np.any(data[:, :64] > 16)
        or np.any(data[:, 64] < 0)
        or np.any(data[:, 64] > 9)
    ):
        raise ValueError("Official training rows are malformed")
    return data[:, :64].astype(int), data[:, 64].astype(int)


def development_split(labels):
    rng = np.random.default_rng(DEVELOPMENT_SEED)
    train, validation = [], []
    for label in range(10):
        indices = rng.permutation(np.flatnonzero(labels == label))
        cutoff = round(0.2 * len(indices))
        validation.extend(indices[:cutoff].tolist())
        train.extend(indices[cutoff:].tolist())
    return np.asarray(sorted(train)), np.asarray(sorted(validation))


def fit_softmax(inputs, labels, l2):
    """Converged conventional multinomial regression with L-BFGS/Armijo."""
    x = np.c_[inputs, np.ones(len(inputs))]
    regularized = np.ones((65, 10))
    regularized[-1] = 0

    def objective(weights):
        weights = weights.reshape(65, 10)
        logits = x @ weights
        shift = logits.max(1, keepdims=True)
        exponentials = np.exp(logits - shift)
        probabilities = exponentials / exponentials.sum(1, keepdims=True)
        loss = float(
            np.mean(
                np.log(exponentials.sum(1))
                + shift[:, 0]
                - logits[np.arange(len(labels)), labels]
            )
            + 0.5 * l2 * np.sum(weights**2 * regularized)
        )
        probabilities[np.arange(len(labels)), labels] -= 1
        gradient = x.T @ probabilities / len(labels) + l2 * weights * regularized
        return loss, gradient.ravel()

    weights = np.zeros(650)
    loss, gradient = objective(weights)
    history = []
    for iteration in range(600):
        if np.max(np.abs(gradient)) < 1e-7:
            break
        q, alphas = gradient.copy(), []
        for step, change, rho in history[::-1]:
            alpha = rho * step @ q
            alphas.append(alpha)
            q -= alpha * change
        scale = (
            (history[-1][0] @ history[-1][1]) / (history[-1][1] @ history[-1][1])
            if history
            else 1.0
        )
        direction = scale * q
        for (step, change, rho), alpha in zip(history, alphas[::-1], strict=True):
            direction += step * (alpha - rho * change @ direction)
        direction = -direction
        if gradient @ direction >= 0:
            direction = -gradient
        rate, slope = 1.0, gradient @ direction
        accepted = False
        for _ in range(30):
            proposed = weights + rate * direction
            next_loss, next_gradient = objective(proposed)
            if next_loss <= loss + 1e-4 * rate * slope:
                accepted = True
                break
            rate *= 0.5
        if not accepted:
            raise RuntimeError("Softmax line search did not converge safely")
        step, change = proposed - weights, next_gradient - gradient
        if step @ change > 1e-12:
            history.append((step, change, 1 / (step @ change)))
            history = history[-12:]
        weights, loss, gradient = proposed, next_loss, next_gradient
    return weights.reshape(65, 10), {
        "l2": l2,
        "iterations": iteration + 1,
        "gradient_max_abs": float(np.max(np.abs(gradient))),
        "objective": loss,
        "converged": bool(np.max(np.abs(gradient)) < 1e-7),
    }


def knn_predict(training_pixels, labels, query_pixels, k):
    """Use integer squared distances and stable original-row tie ordering."""
    predictions = []
    reference_norm = np.sum(training_pixels**2, axis=1)
    for start in range(0, len(query_pixels), 256):
        batch = query_pixels[start : start + 256]
        distances = (
            np.sum(batch**2, axis=1)[:, None]
            + reference_norm[None, :]
            - 2 * batch @ training_pixels.T
        )
        nearest = np.argsort(distances, axis=1, kind="stable")[:, :k]
        predictions.extend(
            int(np.bincount(labels[row], minlength=10).argmax()) for row in nearest
        )
    return np.asarray(predictions)


def train(train_path, output):
    pixels, labels = load_training(train_path)
    normalized = pixels / 16
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    dataset = {
        "train_sha256": TRAIN_SHA256,
        "rows": len(labels),
        "pixels": 64,
        "labels": 10,
    }
    terms = {
        "epochs": EPOCHS,
        "batch_size": 128,
        "learning_rate": 0.003,
        "l2": 1e-5,
        "seeds": list(SEEDS),
        "order_seed_rule": "numpy default_rng(model seed + 707); fresh permutation each epoch; identical order across matched mechanisms",
        "architecture": {"width": 64, "ticks": 16},
        "training_labels": "All 3823 published training labels are visible to every fitted method; qualification labels never train these checkpoints",
    }
    write_new(
        output / "started.json",
        {
            "schema": "nuria.neural-digits-training-start.v1",
            "dataset": dataset,
            "training": terms,
            "implementation_sha256": {
                "train_spiking_digits.py": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "spiking_digits.py": hashlib.sha256(
                    (
                        Path(__file__).parents[1] / "cognition/spiking_digits.py"
                    ).read_bytes()
                ).hexdigest(),
            },
        },
    )
    models = []

    def checkpoint(ident, method, kind, seed, model, weights, config, training):
        if kind in ("snn", "continuous"):
            model_sha256 = digest(
                {
                    "schema": model["schema"],
                    "configuration": config,
                    "weights": weights,
                }
            )
        else:
            model_sha256 = digest(model)
        state_sha256 = digest(model)
        filename = f"{ident}.json"
        file_sha256 = write_new(
            output / filename,
            {
                "schema": "nuria.digit-checkpoint.v1",
                "method": method,
                "model": model,
                "weights": weights,
                "training": {"dataset": dataset, **training},
                "model_sha256": model_sha256,
                "state_sha256": state_sha256,
            },
        )
        models.append(
            {
                "id": ident,
                "method": method,
                "kind": kind,
                "seed": seed,
                "path": filename,
                "model_sha256": model_sha256,
                "state_sha256": state_sha256,
                "file_sha256": file_sha256,
                "configuration": config,
            }
        )

    # Complete the boundary of the original development grid before selecting a
    # regularization value. No qualification partition is read by this command.
    train_indices, validation_indices = development_split(labels)
    validation_x = np.c_[
        normalized[validation_indices], np.ones(len(validation_indices))
    ]
    linear_development = []
    for l2 in (0.0, 1e-6, 1e-5, 1e-4, 1e-3, 0.01):
        started = time.process_time()
        weights, evidence = fit_softmax(
            normalized[train_indices], labels[train_indices], l2
        )
        correct = int(
            np.sum((validation_x @ weights).argmax(1) == labels[validation_indices])
        )
        linear_development.append(
            {
                **evidence,
                "correct": correct,
                "rows": len(validation_indices),
                "cpu_seconds": time.process_time() - started,
            }
        )
    best = max(linear_development, key=lambda row: (row["correct"], row["l2"]))
    knn_development = []
    for k in (1, 3, 5):
        predictions = knn_predict(
            pixels[train_indices], labels[train_indices], pixels[validation_indices], k
        )
        knn_development.append(
            {
                "k": k,
                "correct": int(np.sum(predictions == labels[validation_indices])),
                "rows": len(validation_indices),
            }
        )
    write_new(
        output / "linear-development.json",
        {
            "scope": "Official training partition only; deterministic stratified development split. Complete grid, including the two lower boundary values.",
            "split_seed": DEVELOPMENT_SEED,
            "train_indices_sha256": digest(train_indices.tolist()),
            "validation_indices_sha256": digest(validation_indices.tolist()),
            "selection": "Highest development correct count, then larger regularization on ties",
            "candidates": linear_development,
            "selected_l2": best["l2"],
            "knn_stable_integer_distance": knn_development,
        },
    )
    started = time.process_time()
    linear_weights, linear_evidence = fit_softmax(normalized, labels, best["l2"])
    checkpoint(
        "linear",
        "linear",
        "softmax",
        None,
        {
            "schema": "nuria.softmax-digits-v1",
            "weights": linear_weights.tolist(),
            "l2": best["l2"],
        },
        linear_weights.tolist(),
        {"l2": best["l2"]},
        {"fit": linear_evidence, "cpu_seconds": time.process_time() - started},
    )
    checkpoint(
        "knn5",
        "knn5",
        "knn",
        None,
        {
            "schema": "nuria.knn-digits-v1",
            "k": 5,
            "inputs": pixels.tolist(),
            "labels": labels.tolist(),
        },
        None,
        {
            "k": 5,
            "distance": "integer squared Euclidean",
            "neighbor_tie": "original training row order",
            "vote_tie": "lowest label",
        },
        {"labels": len(labels)},
    )
    for kind, fixed, method in (
        ("snn", False, "snn_trained"),
        ("snn", True, "snn_input_frozen"),
        ("continuous", False, "continuous"),
    ):
        for seed in SEEDS:
            network = SpikingDigits(seed=seed, kind=kind, frozen_input=fixed)
            order_rng = np.random.default_rng(seed + 707)
            trace, started = [], time.process_time()
            for epoch in range(EPOCHS):
                order = order_rng.permutation(len(labels))
                losses = []
                for start in range(0, len(order), 128):
                    indices = order[start : start + 128]
                    evidence = network.train_batch(
                        normalized[indices],
                        labels[indices],
                        verified=True,
                        record_hashes=False,
                    )
                    losses.append(evidence["cross_entropy_before"] * len(indices))
                trace.append(
                    {
                        "epoch": epoch + 1,
                        "mean_training_cross_entropy": sum(losses) / len(labels),
                        "optimizer_step": network.step,
                    }
                )
            checkpoint(
                f"{method}-{seed}",
                method,
                kind,
                seed,
                network.state(),
                network.model_weights(),
                dict(network.configuration),
                {
                    **terms,
                    "cpu_seconds": time.process_time() - started,
                    "trace": trace,
                },
            )
    manifest = {
        "schema": "nuria.neural-digits-training.v1",
        "dataset": dataset,
        "training": terms,
        "models": models,
        "official_test_read": False,
    }
    write_new(output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("train_csv", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    manifest = train(args.train_csv, args.output_directory)
    print(json.dumps({"models": len(manifest["models"]), "official_test_read": False}))


if __name__ == "__main__":
    main()
