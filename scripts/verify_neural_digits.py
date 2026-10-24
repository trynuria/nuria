"""Independent checks for the bounded, sixteen-step digit-reading circuit.

The reference imports neither the trainable model nor its evaluator. Pixel
normalization, threshold/reset updates, readout and paired arithmetic are
implemented here. The optional Brian2 check executes a separate clocked LIF
network. Agreement checks an implementation; it does not independently witness
training, establish general OCR or show an advantage over another classifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from numbers import Integral, Real
from pathlib import Path

import numpy as np

TICKS = 16
PIXELS = 64
HIDDEN = 64
CLASSES = 10
MAX_ROWS = 10_000
MAX_BRIAN_ROWS = 8
MAX_WEIGHT = 1_000_000.0
WEIGHT_SHAPES = {"W": (64, 64), "H": (64,), "V": (64, 10), "C": (10,)}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _real_array(value, shape, description):
    try:
        objects = np.asarray(value, dtype=object)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{description} is not a rectangular numeric array") from error
    _require(objects.shape == shape, f"{description} has the wrong shape")
    _require(
        all(
            isinstance(item, Real) and not isinstance(item, (bool, np.bool_))
            for item in objects.flat
        ),
        f"{description} must contain real numbers, not strings or booleans",
    )
    try:
        values = objects.astype(np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{description} cannot be represented as float64") from error
    _require(np.isfinite(values).all(), f"{description} must be finite")
    return values


def validated_weights(weights):
    """Require the complete reviewed architecture without executable objects."""
    _require(type(weights) is dict, "Weights must be a data-only dictionary")
    _require(
        set(weights) in (set(WEIGHT_SHAPES), set(WEIGHT_SHAPES) | {"ticks"}),
        "Weights must contain exactly W, H, V, C and optionally ticks",
    )
    if "ticks" in weights:
        _require(
            type(weights["ticks"]) is int and weights["ticks"] == TICKS,
            "The reviewed circuit has exactly sixteen ticks",
        )
    output = {}
    for name, shape in WEIGHT_SHAPES.items():
        value = _real_array(weights[name], shape, f"Weight {name}")
        _require(
            np.max(np.abs(value)) <= MAX_WEIGHT,
            f"Weight {name} exceeds the finite magnitude bound",
        )
        output[name] = value
    return output


def validated_pixels(pixels, *, maximum_rows=MAX_ROWS):
    """Accept only batches of sixty-four integer intensities in [0, 16]."""
    _require(
        type(maximum_rows) is int and 1 <= maximum_rows <= MAX_ROWS,
        "The pixel batch limit is invalid",
    )
    try:
        objects = np.asarray(pixels, dtype=object)
    except (TypeError, ValueError) as error:
        raise ValueError("Pixels must form a rectangular integer batch") from error
    _require(
        objects.ndim == 2
        and objects.shape[1] == PIXELS
        and 1 <= objects.shape[0] <= maximum_rows,
        "Pixels need a bounded nonempty batch of sixty-four values per row",
    )
    _require(
        all(
            isinstance(item, Integral)
            and not isinstance(item, (bool, np.bool_))
            and 0 <= item <= 16
            for item in objects.flat
        ),
        "Pixel intensities must be integers in [0, 16]",
    )
    return objects.astype(np.float64)


def _labels(value, description):
    _require(
        isinstance(value, (list, tuple, np.ndarray)),
        f"{description} must be a label sequence",
    )
    try:
        values = list(value)
    except TypeError as error:
        raise ValueError(f"{description} must be a label sequence") from error
    _require(
        1 <= len(values) <= MAX_ROWS
        and all(
            isinstance(item, Integral)
            and not isinstance(item, (bool, np.bool_))
            and 0 <= item < CLASSES
            for item in values
        ),
        f"{description} requires integer class labels in [0, 9]",
    )
    return np.asarray(values, dtype=np.int64)


def independent_predict(weights, pixels):
    """Run the declared float64 recurrence, retaining actual spike counts.

    One neuron can spike at most once per tick. Reset subtracts one rather
    than discarding the residual membrane. Equality at threshold fires, and
    NumPy argmax resolves equal logits toward the lowest class index.
    """
    model = validated_weights(weights)
    inputs = validated_pixels(pixels)
    currents = (inputs / 16.0) @ model["W"] + model["H"]
    membrane = np.zeros((len(inputs), HIDDEN), dtype=np.float64)
    counts = np.zeros((len(inputs), HIDDEN), dtype=np.int64)
    for _ in range(TICKS):
        membrane = 0.8 * membrane + currents
        fired = membrane >= 1.0
        counts += fired
        membrane -= fired
    hidden = counts.astype(np.float64) / TICKS
    logits = hidden @ model["V"] + model["C"]
    _require(np.isfinite(logits).all(), "The reference readout became nonfinite")
    return {
        "labels": np.argmax(logits, axis=1).astype(int).tolist(),
        "spike_counts": counts.tolist(),
        "logits": logits.tolist(),
        "final_membrane": membrane.tolist(),
    }


def spike_interventions(weights, reference):
    """Inspect the readout's dependence on its actual hidden spike counts.

    These deterministic interventions establish a local causal path. They
    do not establish that training helps, outperform an agent or replace the
    separately trained frozen-hidden-layer comparator.
    """
    model = validated_weights(weights)
    counts = np.asarray(reference["spike_counts"], dtype=np.int64)
    hidden = counts.astype(np.float64) / TICKS
    original = hidden @ model["V"] + model["C"]
    zeroed = np.broadcast_to(model["C"], original.shape).copy()
    permuted = hidden[:, ::-1] @ model["V"] + model["C"]
    restored = counts.copy().astype(np.float64) / TICKS @ model["V"] + model["C"]
    labels = np.argmax(original, axis=1)
    return {
        "zero_spikes_changed_labels": int(
            np.count_nonzero(labels != np.argmax(zeroed, axis=1))
        ),
        "permuted_spikes_changed_labels": int(
            np.count_nonzero(labels != np.argmax(permuted, axis=1))
        ),
        "zero_spikes_changed_logit_rows": int(
            np.count_nonzero(np.any(original != zeroed, axis=1))
        ),
        "permuted_spikes_changed_logit_rows": int(
            np.count_nonzero(np.any(original != permuted, axis=1))
        ),
        "restoration_exact": bool(np.array_equal(original, restored)),
        "permutation": "reverse hidden-neuron order; readout weights unchanged",
        "scope": "Local spike-to-readout interventions, not a learned-capability comparison",
    }


def verify_model_predictions(weights, pixels, published):
    """Check a labels list, or a dictionary with labels/counts/logits.

    Predictions must be committed by the caller before private labels are
    supplied to any scoring process. This arithmetic function cannot itself
    attest wall-clock ordering, process isolation or absence of prior access.
    """
    reference = independent_predict(weights, pixels)
    if type(published) is dict:
        _require(
            "labels" in published
            and set(published) <= {"labels", "spike_counts", "logits"},
            "Published predictions contain unreviewed fields or omit labels",
        )
        evidence = published
    else:
        evidence = {"labels": published}
    labels = _labels(evidence["labels"], "Published predictions")
    expected = np.asarray(reference["labels"])
    _require(
        labels.shape == expected.shape and np.array_equal(labels, expected),
        "Published predictions differ from the independent neural execution",
    )
    if "spike_counts" in evidence:
        raw = _real_array(
            evidence["spike_counts"], (len(labels), HIDDEN), "Published spike counts"
        )
        _require(
            np.array_equal(raw, np.asarray(reference["spike_counts"])),
            "Published spike counts differ from the independent neural execution",
        )
    maximum_logit_error = None
    if "logits" in evidence:
        actual = _real_array(
            evidence["logits"], (len(labels), CLASSES), "Published logits"
        )
        expected_logits = np.asarray(reference["logits"])
        maximum_logit_error = float(np.max(np.abs(actual - expected_logits)))
        _require(
            np.allclose(actual, expected_logits, rtol=1e-12, atol=1e-10),
            "Published logits differ from the independent neural execution",
        )
    normalized = validated_weights(weights)
    return {
        "schema": "nuria.neural-digit-reference.v1",
        "verified": True,
        "rows": len(labels),
        "ticks": TICKS,
        "normalized_weights_sha256": _hash(
            {key: value.tolist() for key, value in normalized.items()}
        ),
        "pixels_sha256": _hash(validated_pixels(pixels).astype(int).tolist()),
        "labels_sha256": _hash(reference["labels"]),
        "spike_counts_sha256": _hash(reference["spike_counts"]),
        "total_hidden_spikes": int(np.sum(reference["spike_counts"])),
        "checked_published_spike_counts": "spike_counts" in evidence,
        "checked_published_logits": "logits" in evidence,
        "maximum_published_logit_error": maximum_logit_error,
        "interventions": spike_interventions(weights, reference),
        "scope": "Independent inference arithmetic; training provenance and prediction-before-label ordering require separate receipts",
    }


def paired_correctness(targets, first, second):
    """Return exact finite-set matched counts, without iid population claims."""
    truth = _labels(targets, "Scoring targets")
    left = _labels(first, "First predictions")
    right = _labels(second, "Second predictions")
    _require(
        truth.shape == left.shape == right.shape,
        "Paired scoring requires identical nonempty row coverage",
    )
    a, b = left == truth, right == truth
    both = int(np.count_nonzero(a & b))
    first_only = int(np.count_nonzero(a & ~b))
    second_only = int(np.count_nonzero(~a & b))
    neither = int(np.count_nonzero(~a & ~b))
    return {
        "rows": len(truth),
        "first_correct": both + first_only,
        "second_correct": both + second_only,
        "both_correct": both,
        "first_only_correct": first_only,
        "second_only_correct": second_only,
        "neither_correct": neither,
        "accuracy_difference": (first_only - second_only) / len(truth),
        "scope": "Exact matched counts on the supplied finite rows; no independence, writer-cluster or population-generalization assertion",
    }


def brian_parity(weights, pixels):
    """Execute at most eight samples in a separate actual Brian2 LIF network.

    Euler's algebra may introduce tiny continuous-state rounding differences.
    Spike counts and predicted labels must nevertheless agree exactly. An
    observed spike mismatch is a failure, never repaired by a loose tolerance.
    """
    model = validated_weights(weights)
    inputs = validated_pixels(pixels, maximum_rows=MAX_BRIAN_ROWS)
    reference = independent_predict(weights, pixels)
    import brian2 as b

    old_target = b.prefs.codegen.target
    b.prefs.codegen.target = "numpy"
    try:
        clock = b.Clock(dt=1 * b.ms)
        neurons = b.NeuronGroup(
            len(inputs) * HIDDEN,
            "dv/dt = (-0.2 * v + drive) / ms : 1\ndrive : 1",
            threshold="v >= 1",
            reset="v -= 1",
            method="euler",
            clock=clock,
        )
        neurons.v = 0
        neurons.drive = ((inputs / 16.0) @ model["W"] + model["H"]).reshape(-1)
        monitor = b.SpikeMonitor(neurons)
        network = b.Network(neurons, monitor)
        network.run(TICKS * b.ms, namespace={})
        counts = np.asarray(monitor.count[:], dtype=np.int64).reshape(-1, HIDDEN)
        membrane = np.asarray(neurons.v[:], dtype=np.float64).reshape(-1, HIDDEN)
        logits = (counts.astype(np.float64) / TICKS) @ model["V"] + model["C"]
        labels = np.argmax(logits, axis=1).astype(int)
        _require(
            np.array_equal(counts, np.asarray(reference["spike_counts"])),
            "Actual Brian2 spikes differ from the independent recurrence",
        )
        _require(
            np.array_equal(labels, np.asarray(reference["labels"])),
            "Actual Brian2 predictions differ from the independent recurrence",
        )
        membrane_error = float(
            np.max(np.abs(membrane - np.asarray(reference["final_membrane"])))
        )
        _require(
            np.allclose(membrane, reference["final_membrane"], rtol=1e-12, atol=1e-10),
            "Actual Brian2 membrane differs from the declared recurrence",
        )
        spikes = [
            [int(index), float(at / b.ms)]
            for index, at in zip(monitor.i[:], monitor.t[:], strict=True)
        ]
        return {
            "schema": "nuria.neural-digit-brian-parity.v1",
            "verified": True,
            "rows": len(inputs),
            "ticks": TICKS,
            "brian2_version": b.__version__,
            "dt_milliseconds": 1,
            "matched_spike_counts": True,
            "matched_predictions": True,
            "total_hidden_spikes": int(counts.sum()),
            "actual_spikes_sha256": _hash(spikes),
            "spike_counts_sha256": _hash(counts.tolist()),
            "labels_sha256": _hash(labels.tolist()),
            "maximum_membrane_error": membrane_error,
            "scope": "Actual Brian2 parity on these bounded supplied samples, not proof of unexecuted samples or training",
        }
    finally:
        b.prefs.codegen.target = old_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("weights", type=Path)
    parser.add_argument("pixels", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--brian", action="store_true")
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    _require(
        bool(args.targets) == bool(args.baseline),
        "Paired scoring needs both targets and baseline prediction files",
    )
    weights, pixels, published = (
        json.loads(path.read_text())
        for path in (args.weights, args.pixels, args.predictions)
    )
    report = verify_model_predictions(weights, pixels, published)
    if args.brian:
        report["brian_parity"] = brian_parity(weights, pixels[:MAX_BRIAN_ROWS])
    if args.targets:
        labels = published["labels"] if type(published) is dict else published
        report["paired"] = paired_correctness(
            json.loads(args.targets.read_text()),
            labels,
            json.loads(args.baseline.read_text()),
        )
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"verified": True, "rows": report["rows"]}))


if __name__ == "__main__":
    main()
