"""Bounded learned priorities for disclosed-example program search.

This module is numerical: it never verifies a program, authenticates a buyer
receipt, executes a tool or grants authority. Training labels describe whether
an enumerated computation has the same observed values as a proper subtree of
an earlier buyer-accepted program. A zero label is not a proof of uselessness.
All fitted controls consume the same examples and balanced minibatch schedule.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

import numpy as np

VERSION = "nuria.semantic-guidance.v1"
METHODS = ("spiking", "continuous", "frozen_input", "linear", "rff", "knn")
HASH = re.compile(r"[a-f0-9]{64}\Z")
PROVENANCE = ("job_id", "candidate_sha256", "program_sha256", "receipt_evidence_sha256")
MAX_RECORDS = 65536
MAX_BATCH = 4096


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _features(values, dimension, *, maximum=MAX_BATCH):
    objects = np.asarray(values, dtype=object)
    if (
        objects.ndim != 2
        or objects.shape[1] != dimension
        or not 1 <= len(objects) <= maximum
        or any(type(v) not in (int, float) for v in objects.flat)
    ):
        raise ValueError("Guidance requires a bounded real feature matrix")
    result = objects.astype(np.float64)
    if not np.isfinite(result).all() or np.any(result < 0) or np.any(result > 1):
        raise ValueError("Guidance observations must be finite and normalized")
    return result


def _sigmoid(values):
    values = np.clip(values, -40, 40)
    return 1 / (1 + np.exp(-values))


class SemanticGuidance:
    """Fit a declared priority model; score without changing its parameters.

    The spiking mechanism uses hard threshold/reset recurrence. Its bounded
    surrogate trains the input synapses; it is not an exact spike derivative.
    Continuous and frozen-input versions control the value of this mechanism.
    Linear, fixed random Fourier and nearest-neighbor models are conventional
    controls, rather than deliberately information-poor alternatives.
    """

    def __init__(
        self,
        *,
        method="spiking",
        dimension=100,
        width=64,
        ticks=16,
        seed=20261026,
        epochs=12,
        batch_size=128,
        learning_rate=0.003,
        regularization=0.0001,
        rff_scale=0.5,
        neighbors=9,
    ):
        if (
            method not in METHODS
            or not _integer(dimension, 1, 512)
            or not _integer(width, 1, 256)
            or not _integer(ticks, 1, 64)
            or not _integer(seed, 0, 2**32 - 1)
            or not _integer(epochs, 1, 64)
            or not _integer(batch_size, 2, 512)
            or not _integer(neighbors, 1, 128)
            or type(learning_rate) not in (int, float)
            or not 0 < learning_rate <= 0.1
            or type(regularization) not in (int, float)
            or not 0 <= regularization <= 1
            or type(rff_scale) not in (int, float)
            or not 0 < rff_scale <= 100
        ):
            raise ValueError("Invalid bounded guidance configuration")
        self.configuration = {
            "method": method,
            "dimension": dimension,
            "width": width,
            "ticks": ticks,
            "seed": seed,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": float(learning_rate),
            "regularization": float(regularization),
            "rff_scale": float(rff_scale),
            "neighbors": neighbors,
        }
        rng = np.random.default_rng(seed)
        self.W = rng.normal(0, math.sqrt(2 / dimension), (dimension, width))
        self.H = np.full(width, 0.15)
        self.V = rng.normal(0, 0.05, width)
        self.C = np.zeros(1)
        self.frequencies = rng.normal(0, rff_scale, (dimension, width))
        self.phases = rng.uniform(0, 2 * np.pi, width)
        if method == "linear":
            self.V = np.zeros(dimension)
        elif method == "rff":
            self.V = np.zeros(dimension + width)
        self.training_sha256 = None
        self.records_count = self.positive_count = self.steps = 0
        self.reference_x = self.reference_y = None

    def _hidden(self, x, *, derivatives=False):
        method = self.configuration["method"]
        if method == "linear":
            return x, None, None
        if method == "rff":
            return (
                np.column_stack(
                    (
                        x,
                        math.sqrt(2 / self.configuration["width"])
                        * np.cos(x @ self.frequencies + self.phases),
                    )
                ),
                None,
                None,
            )
        current = x @ self.W + self.H
        if method == "continuous":
            return (
                np.clip((current - 0.1) / 0.8, 0, 1),
                ((current > 0.1) & (current < 0.9)) / 0.8 if derivatives else None,
                np.zeros_like(current, dtype=np.int64),
            )
        voltage = np.zeros_like(current)
        eligibility = np.zeros_like(current)
        counts = np.zeros_like(current, dtype=np.int64)
        derivative = np.zeros_like(current)
        for _ in range(self.configuration["ticks"]):
            voltage = 0.8 * voltage + current
            fired = voltage >= 1
            counts += fired
            if derivatives:
                eligibility = 0.8 * eligibility + 1
                derivative += (
                    np.minimum(1, 1 / (1 + 4 * np.abs(voltage - 1)) ** 2) * eligibility
                )
            voltage -= fired
        ticks = self.configuration["ticks"]
        return (
            counts / ticks,
            np.clip(derivative / ticks, 0, 1.5) if derivatives else None,
            counts,
        )

    def fit(self, records):
        """Replace the fitted model using a complete declared training set.

        Provenance is validated structurally, not authenticated here. The
        orchestration layer must bind every record to its buyer and prior job.
        Retraining uses a fresh initialization and rolls back on any failure.
        """
        if type(records) is not list or not 2 <= len(records) <= MAX_RECORDS:
            raise ValueError(
                "A bounded training set containing both labels is required"
            )
        expected = {"features", "label", *PROVENANCE}
        for row in records:
            if (
                type(row) is not dict
                or set(row) != expected
                or type(row["label"]) is not int
                or row["label"] not in (0, 1)
                or any(
                    not isinstance(row[k], str) or not HASH.fullmatch(row[k])
                    for k in PROVENANCE
                )
            ):
                raise ValueError(
                    "Training needs exact labels and complete provenance hashes"
                )
        x = _features(
            [r["features"] for r in records],
            self.configuration["dimension"],
            maximum=MAX_RECORDS,
        )
        y = np.asarray([r["label"] for r in records], dtype=np.float64)
        positive, negative = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
        if not len(positive) or not len(negative):
            raise ValueError("Balanced guidance training requires both label classes")
        origins = [(r["job_id"], r["candidate_sha256"]) for r in records]
        if len(set(origins)) != len(origins):
            raise ValueError("A job/candidate pair may teach only once")
        old = copy.deepcopy(self.__dict__)
        try:
            fresh = type(self)(**self.configuration)
            self.__dict__.update(fresh.__dict__)
            self.training_sha256 = _hash(records)
            self.records_count, self.positive_count = len(records), len(positive)
            if self.configuration["method"] == "knn":
                self.reference_x, self.reference_y = x.copy(), y.copy()
                return self
            parameters = ("V", "C")
            if self.configuration["method"] in ("spiking", "continuous"):
                parameters += ("W", "H")
            first = {key: np.zeros_like(getattr(self, key)) for key in parameters}
            second = {key: np.zeros_like(getattr(self, key)) for key in parameters}
            rng = np.random.default_rng(self.configuration["seed"] + 1009)
            batch_size = self.configuration["batch_size"]
            for _ in range(self.configuration["epochs"]):
                for _ in range(math.ceil(len(records) / batch_size)):
                    indices = np.concatenate(
                        (
                            rng.choice(positive, batch_size // 2),
                            rng.choice(negative, batch_size - batch_size // 2),
                        )
                    )
                    batch, targets = x[indices], y[indices]
                    hidden, derivative, _ = self._hidden(batch, derivatives=True)
                    residual = (
                        _sigmoid(hidden @ self.V + self.C[0]) - targets
                    ) / batch_size
                    gradients = {
                        "V": hidden.T @ residual,
                        "C": np.array([residual.sum()]),
                    }
                    if "W" in parameters:
                        upstream = residual[:, None] * self.V[None, :] * derivative
                        gradients.update(W=batch.T @ upstream, H=upstream.sum(axis=0))
                    for key in parameters:
                        if key in ("V", "W"):
                            gradients[key] += self.configuration[
                                "regularization"
                            ] * getattr(self, key)
                    norm = math.sqrt(
                        sum(float(np.sum(g * g)) for g in gradients.values())
                    )
                    self.steps += 1
                    for key in parameters:
                        gradient = gradients[key] / max(1, norm)
                        first[key] = 0.9 * first[key] + 0.1 * gradient
                        second[key] = 0.999 * second[key] + 0.001 * gradient**2
                        average = first[key] / (1 - 0.9**self.steps)
                        variance = second[key] / (1 - 0.999**self.steps)
                        updated = getattr(self, key) - self.configuration[
                            "learning_rate"
                        ] * average / (np.sqrt(variance) + 1e-8)
                        if not np.isfinite(updated).all():
                            raise ValueError("Nonfinite guidance update")
                        setattr(self, key, np.clip(updated, -20, 20))
            return self
        except BaseException:
            self.__dict__.clear()
            self.__dict__.update(old)
            raise

    def score(self, features):
        x = _features(features, self.configuration["dimension"])
        if self.training_sha256 is None:
            raise ValueError("Guidance must be fitted before scoring")
        if self.configuration["method"] == "knn":
            predictions = []
            k = min(self.configuration["neighbors"], self.records_count)
            for row in x:
                distance = np.sum((self.reference_x - row) ** 2, axis=1)
                # Stable tie resolution follows the original training order.
                indices = np.argsort(distance, kind="stable")[:k]
                weights = 1 / (distance[indices] + 0.001)
                weights *= np.where(
                    self.reference_y[indices] == 1,
                    0.5 / self.positive_count,
                    0.5 / (self.records_count - self.positive_count),
                )
                predictions.append(
                    float(np.dot(weights, self.reference_y[indices]) / weights.sum())
                )
            return np.asarray(predictions)
        hidden, _, _ = self._hidden(x)
        return _sigmoid(hidden @ self.V + self.C[0])

    __call__ = score

    @classmethod
    def from_evidence(cls, evidence):
        """Restore inference after validating a complete bounded checkpoint.

        The caller must authenticate the checkpoint identity and originating
        training receipts. This validates structure and numerical bounds only.
        A later fit deliberately restarts training from its declared seed.
        """
        expected = {
            "schema",
            "configuration",
            "training_sha256",
            "records_count",
            "positive_count",
            "optimizer_steps",
            "weights",
            "reference_features",
            "reference_labels",
        }
        if (
            type(evidence) is not dict
            or set(evidence) != expected
            or evidence["schema"] != VERSION
        ):
            raise ValueError("Incomplete guidance inference checkpoint")
        configuration = evidence["configuration"]
        if type(configuration) is not dict:
            raise ValueError("Guidance checkpoint configuration is malformed")
        try:
            model = cls(**configuration)
        except TypeError:
            raise ValueError("Unknown or missing guidance configuration") from None
        if configuration != model.configuration:
            raise ValueError("Guidance configuration does not match the declared model")
        count, positive, steps = (
            evidence[k] for k in ("records_count", "positive_count", "optimizer_steps")
        )
        if (
            not _integer(count, 2, MAX_RECORDS)
            or not _integer(positive, 1, count - 1)
            or not isinstance(evidence["training_sha256"], str)
            or not HASH.fullmatch(evidence["training_sha256"])
            or type(steps) is not int
            or steps
            != (
                0
                if configuration["method"] == "knn"
                else configuration["epochs"]
                * math.ceil(count / configuration["batch_size"])
            )
        ):
            raise ValueError("Guidance training identity or counters are malformed")
        weights = evidence["weights"]
        if type(weights) is not dict or set(weights) != {
            "W",
            "H",
            "V",
            "C",
            "frequencies",
            "phases",
        }:
            raise ValueError("Guidance checkpoint weight set differs")
        for key, raw in weights.items():
            value = np.asarray(raw, dtype=object)
            if value.shape != getattr(model, key).shape or any(
                type(v) not in (int, float) for v in value.flat
            ):
                raise ValueError(
                    "Guidance checkpoint weight dimensions or types differ"
                )
            value = value.astype(float)
            bound = (
                1000 if key == "frequencies" else (2 * np.pi if key == "phases" else 20)
            )
            if not np.isfinite(value).all() or np.any(np.abs(value) > bound):
                raise ValueError("Guidance checkpoint numerical weight bounds differ")
            setattr(model, key, value.copy())
        if configuration["method"] == "frozen_input":
            original = cls(**configuration)
            if not np.array_equal(model.W, original.W) or not np.array_equal(
                model.H, original.H
            ):
                raise ValueError("Frozen input weights changed")
        # Random-feature controls cannot silently acquire learned frequencies.
        initial = cls(**configuration)
        if not np.array_equal(
            model.frequencies, initial.frequencies
        ) or not np.array_equal(model.phases, initial.phases):
            raise ValueError("Fixed random features changed from the declared seed")
        if configuration["method"] == "knn":
            x = _features(
                evidence["reference_features"],
                configuration["dimension"],
                maximum=MAX_RECORDS,
            )
            labels = np.asarray(evidence["reference_labels"], dtype=object)
            if (
                len(x) != count
                or labels.shape != (count,)
                or any(type(v) not in (int, float) or v not in (0, 1) for v in labels)
            ):
                raise ValueError("Nearest-neighbor reference data differ")
            y = labels.astype(float)
            if int(y.sum()) != positive:
                raise ValueError("Nearest-neighbor class count differs")
            model.reference_x, model.reference_y = x, y
        elif (
            evidence["reference_features"] is not None
            or evidence["reference_labels"] is not None
        ):
            raise ValueError("Only nearest neighbors retain reference rows")
        model.training_sha256 = evidence["training_sha256"]
        model.records_count, model.positive_count, model.steps = count, positive, steps
        return model

    def evidence(self):
        """Return portable inference weights and training-set identity.

        Optimizer moments are deliberately absent: fit is a deterministic full
        retraining operation, not a resumable mid-minibatch transaction.
        """
        return {
            "schema": VERSION,
            "configuration": copy.deepcopy(self.configuration),
            "training_sha256": self.training_sha256,
            "records_count": self.records_count,
            "positive_count": self.positive_count,
            "optimizer_steps": self.steps,
            "weights": {
                key: getattr(self, key).tolist()
                for key in ("W", "H", "V", "C", "frequencies", "phases")
            },
            "reference_features": None
            if self.reference_x is None
            else self.reference_x.tolist(),
            "reference_labels": None
            if self.reference_y is None
            else self.reference_y.tolist(),
        }
