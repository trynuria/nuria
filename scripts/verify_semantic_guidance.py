"""Independently check learned semantic scores and actual Brian2 spikes.

The verifier imports no fitted semantic scorer. It embeds its one-logit
sigmoid into the independently implemented two-head tanh checker using the
identity sigmoid(z) = (1 + tanh(z / 2)) / 2. This checks numerical execution,
not receipt authenticity, training labels or comparative task performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np

from scripts.verify_spiking_policy import (
    brian_parity,
    independent_forward,
    model_hash,
    validated_features,
)

METHODS = ("spiking", "frozen_input", "continuous", "linear", "rff", "knn")
CONFIGURATION_KEYS = {
    "method",
    "dimension",
    "width",
    "ticks",
    "seed",
    "epochs",
    "batch_size",
    "learning_rate",
    "regularization",
    "rff_scale",
    "neighbors",
}
EVIDENCE_KEYS = {
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
WEIGHT_KEYS = {"W", "H", "V", "C", "frequencies", "phases"}
PROVENANCE_KEYS = {
    "job_id",
    "candidate_sha256",
    "program_sha256",
    "receipt_evidence_sha256",
}
HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_RECORDS = 65_536


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _real(value, low, high, *, positive=False):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and (low < value if positive else low <= value)
        and value <= high
    )


def _identity(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _number_array(value, shape, *, bound=20, low=None):
    try:
        raw = np.asarray(value, dtype=object)
    except (ValueError, TypeError) as error:
        raise ValueError(
            "Semantic arrays must be rectangular numerical data"
        ) from error
    if raw.shape != shape or any(type(v) not in (int, float) for v in raw.flat):
        raise ValueError("Semantic weights require real arrays with exact shapes")
    try:
        result = raw.astype(float)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Semantic arrays cannot be represented in float64") from error
    if (
        not np.isfinite(result).all()
        or np.any(np.abs(result) > bound)
        or low is not None
        and np.any(result < low)
    ):
        raise ValueError("Semantic weights exceed numerical bounds")
    return result


def validate_evidence(evidence, *, training_records=None):
    """Independently enforce the complete inference/control declaration.

    Optional records bind exact training data, counts and nearest-neighbor
    contents. Their hashes remain claims about external buyers: this numerical
    reference does not authenticate receipts or prove that fitting was executed.
    """
    if (
        type(evidence) is not dict
        or set(evidence) != EVIDENCE_KEYS
        or evidence["schema"] != "nuria.semantic-guidance.v1"
    ):
        raise ValueError("Expected complete semantic guidance inference evidence")
    c = evidence["configuration"]
    if type(c) is not dict or set(c) != CONFIGURATION_KEYS:
        raise ValueError("Expected the complete declared semantic configuration")
    if not (
        type(c["method"]) is str
        and c["method"] in METHODS
        and _integer(c["dimension"], 1, 512)
        and _integer(c["width"], 1, 256)
        and _integer(c["ticks"], 1, 64)
        and _integer(c["seed"], 0, 2**32 - 1)
        and _integer(c["epochs"], 1, 64)
        and _integer(c["batch_size"], 2, 512)
        and _integer(c["neighbors"], 1, 128)
        and _real(c["learning_rate"], 0, 0.1, positive=True)
        and _real(c["regularization"], 0, 1)
        and _real(c["rff_scale"], 0, 100, positive=True)
    ):
        raise ValueError("Semantic configuration is outside independent bounds")
    count, positive, steps = (
        evidence[k] for k in ("records_count", "positive_count", "optimizer_steps")
    )
    if not (
        _identity(evidence["training_sha256"])
        and _integer(count, 2, MAX_RECORDS)
        and _integer(positive, 1, count - 1)
        and type(steps) is int
        and steps
        == (
            0
            if c["method"] == "knn"
            else c["epochs"] * math.ceil(count / c["batch_size"])
        )
    ):
        raise ValueError("Semantic training identity or counters differ")
    raw = evidence["weights"]
    if type(raw) is not dict or set(raw) != WEIGHT_KEYS:
        raise ValueError("Semantic inference weight set differs")
    dimension, width, method = c["dimension"], c["width"], c["method"]
    vshape = (
        dimension
        if method == "linear"
        else (dimension + width if method == "rff" else width)
    )
    shapes = {
        "W": (dimension, width),
        "H": (width,),
        "V": (vshape,),
        "C": (1,),
        "frequencies": (dimension, width),
        "phases": (width,),
    }
    weights = {
        name: _number_array(
            raw[name],
            shape,
            bound=1000
            if name == "frequencies"
            else (2 * np.pi if name == "phases" else 20),
            low=0 if name == "phases" else None,
        )
        for name, shape in shapes.items()
    }
    rng = np.random.default_rng(c["seed"])
    initial_w = rng.normal(0, math.sqrt(2 / dimension), (dimension, width))
    rng.normal(
        0, 0.05, width
    )  # The original one-head initialization consumes this draw.
    initial_frequencies = rng.normal(0, c["rff_scale"], (dimension, width))
    initial_phases = rng.uniform(0, 2 * np.pi, width)
    if method == "frozen_input" and (
        not np.array_equal(weights["W"], initial_w)
        or not np.array_equal(weights["H"], np.full(width, 0.15))
    ):
        raise ValueError("Frozen semantic input weights differ from the declared seed")
    if not np.array_equal(
        weights["frequencies"], initial_frequencies
    ) or not np.array_equal(weights["phases"], initial_phases):
        raise ValueError("Fixed random Fourier features differ from the declared seed")
    if method == "knn":
        reference_x = _number_array(
            evidence["reference_features"], (count, dimension), bound=1, low=0
        )
        labels = evidence["reference_labels"]
        if (
            type(labels) is not list
            or len(labels) != count
            or any(type(v) not in (int, float) or v not in (0, 1) for v in labels)
            or sum(labels) != positive
        ):
            raise ValueError("Nearest-neighbor labels or class count differ")
    elif (
        evidence["reference_features"] is not None
        or evidence["reference_labels"] is not None
    ):
        raise ValueError("Only nearest-neighbor evidence may retain reference rows")
    if training_records is not None:
        if type(training_records) is not list or len(training_records) != count:
            raise ValueError("Supplied training data do not cover the declared records")
        seen, positives = set(), 0
        for row in training_records:
            if (
                type(row) is not dict
                or set(row) != {"features", "label", *PROVENANCE_KEYS}
                or type(row["label"]) is not int
                or row["label"] not in (0, 1)
                or any(not _identity(row[k]) for k in PROVENANCE_KEYS)
            ):
                raise ValueError("Supplied training labels or provenance are malformed")
            _number_array(row["features"], (dimension,), bound=1, low=0)
            identity = (row["job_id"], row["candidate_sha256"])
            if identity in seen:
                raise ValueError("A supplied training job/candidate identity repeats")
            seen.add(identity)
            positives += row["label"]
        if (
            positives != positive
            or digest(training_records) != evidence["training_sha256"]
        ):
            raise ValueError("Exact supplied training-data identity or labels differ")
        if method == "knn" and (
            not np.array_equal(reference_x, [r["features"] for r in training_records])
            or evidence["reference_labels"] != [r["label"] for r in training_records]
        ):
            raise ValueError(
                "Nearest-neighbor evidence differs from supplied training rows"
            )
    return c, weights


def embed(evidence):
    config, weights = validate_evidence(evidence)
    if config["method"] not in ("spiking", "frozen_input", "continuous"):
        raise ValueError("LIF parity does not apply to conventional controls")
    dimension, width = config["dimension"], config["width"]
    w, h, v, c = (weights[k] for k in ("W", "H", "V", "C"))
    embedded = {
        "source_id": "nuria:semantic-guidance-verification",
        "feature_dimension": dimension,
        "arm_ids": ["candidate", "reference_zero"],
        "seed": config["seed"],
        "width": width,
        "ticks": config["ticks"],
        "mechanism": "continuous" if config["method"] == "continuous" else "spiking",
        "frozen_input": config["method"] == "frozen_input",
        "learning_rate": config["learning_rate"],
        "replay_capacity": 256,
        "updates": 4,
        "batch_size": 32,
        "exploration": 0.0,
        "warmup": 0,
    }
    converted = {
        "W": w.tolist(),
        "H": h.tolist(),
        "V": np.column_stack((v / 2, np.zeros(width))).tolist(),
        "C": [float(c[0] / 2), 0.0],
    }
    return embedded, converted


def verify(
    evidence,
    features,
    probabilities,
    spike_counts,
    *,
    brian=True,
    training_records=None,
):
    validate_evidence(evidence, training_records=training_records)
    configuration, weights = embed(evidence)
    reference = independent_forward(configuration, weights, features)
    expected = (1 + np.asarray(reference["values"])[:, 0]) / 2
    raw = np.asarray(probabilities, dtype=object)
    if raw.shape != expected.shape or any(
        type(v) not in (int, float) for v in raw.flat
    ):
        raise ValueError("Published scores have incorrect shape or type")
    published = raw.astype(float)
    if (
        not np.isfinite(published).all()
        or np.any(published < 0)
        or np.any(published > 1)
    ) or not np.allclose(published, expected, rtol=1e-12, atol=1e-12):
        raise ValueError("Semantic sigmoid scores differ from independent execution")
    counts = np.asarray(spike_counts, dtype=object)
    expected_counts = np.asarray(reference["spike_counts"], dtype=np.int64)
    if (
        counts.shape != expected_counts.shape
        or any(type(v) is not int for v in counts.flat)
        or not np.array_equal(counts, expected_counts)
    ):
        raise ValueError("Published semantic spike counts differ")
    parity = None
    if brian and configuration["mechanism"] == "spiking":
        parity = brian_parity(configuration, weights, features[:8])
    return {
        "schema": "nuria.semantic-guidance-verification.v1",
        "method": evidence["configuration"]["method"],
        "verified": True,
        "observations": len(expected),
        "total_hidden_spikes": int(expected_counts.sum()),
        "maximum_probability_error": float(np.max(np.abs(published - expected))),
        "brian2": parity,
        "training_sha256": evidence.get("training_sha256"),
        "training_records_checked": training_records is not None,
        "evidence_sha256": digest(evidence),
        "configuration_sha256": digest(evidence["configuration"]),
        "features_sha256": digest(features),
        "normalized_features_sha256": digest(
            validated_features(configuration, features).tolist()
        ),
        "probabilities_sha256": digest(published.tolist()),
        "spike_counts_sha256": digest(expected_counts.tolist()),
        "embedded_model_sha256": model_hash(configuration, weights),
        "scope": "Numerical execution only; no certification of buyer receipts, learning history, useful-task gains or consciousness",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("new_receipt", type=Path)
    args = parser.parse_args()
    if args.artifact.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Semantic reference artifact exceeds its file-size bound")
    raw = args.artifact.read_bytes()
    data = json.loads(raw)
    result = verify(
        data["evidence"],
        data["features"],
        data["probabilities"],
        data["spike_counts"],
        training_records=data.get("training_records"),
    )
    result["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
    with args.new_receipt.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
