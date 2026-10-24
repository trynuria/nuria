"""Independent bounded arithmetic for outcome-trained policy decisions.

This reference imports neither the policy nor an evaluator. It checks hard LIF
counts, normalized observations, tanh readout values, legal greedy choices, and
the source/configuration/feature/model identities in supplied decision records.
An optional actual Brian2 network checks at most eight observations. These are
internal numerical and data-coherence checks, not external attestation of a
buyer, reward, training run, useful improvement, or prediction-before-outcome
ordering. Historical choices require their exact historical weight snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from numbers import Integral, Real
from pathlib import Path

import numpy as np

SCHEMA = "nuria.spiking-decision-policy.v1"
REFERENCE_SCHEMA = "nuria.spiking-decision-reference.v1"
MAX_ROWS = 128
MAX_BRIAN_ROWS = 8
MAX_INTEGER = 2**53 - 1
WEIGHT_BOUND = 20.0
SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
CONFIGURATION_KEYS = {
    "source_id",
    "feature_dimension",
    "arm_ids",
    "seed",
    "width",
    "ticks",
    "mechanism",
    "frozen_input",
    "learning_rate",
    "replay_capacity",
    "updates",
    "batch_size",
    "exploration",
    "warmup",
}
DECISION_KEYS = {
    "schema",
    "source_id",
    "decision_id",
    "ordinal",
    "arm",
    "legal_arms",
    "values",
    "spike_counts",
    "reason",
    "features_sha256",
    "model_sha256",
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _real(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _identity(value):
    return type(value) is str and SHA256.fullmatch(value) is not None


def validated_configuration(configuration):
    """Accept the full bounded declaration, with the policy's float normalization."""
    _require(
        type(configuration) is dict and set(configuration) == CONFIGURATION_KEYS,
        "The complete declared policy configuration is required",
    )
    c = configuration
    _require(
        type(c["source_id"]) is str
        and SOURCE.fullmatch(c["source_id"])
        and _integer(c["feature_dimension"], 1, 512)
        and type(c["arm_ids"]) is list
        and 2 <= len(c["arm_ids"]) <= 16
        and all(type(a) is str and SOURCE.fullmatch(a) for a in c["arm_ids"])
        and len(set(c["arm_ids"])) == len(c["arm_ids"])
        and _integer(c["seed"], 0, 2**32 - 1)
        and _integer(c["width"], 1, 256)
        and _integer(c["ticks"], 1, 64)
        and c["mechanism"] in ("spiking", "continuous")
        and type(c["frozen_input"]) is bool
        and _real(c["learning_rate"], 1e-6, 0.1)
        and _integer(c["replay_capacity"], 1, 512)
        and _integer(c["updates"], 1, 16)
        and _integer(c["batch_size"], 1, 128)
        and _real(c["exploration"], 0, 1)
        and _integer(c["warmup"], 0, 4096),
        "Policy source, architecture or learning limits are invalid",
    )
    return c | {
        "arm_ids": list(c["arm_ids"]),
        "learning_rate": float(c["learning_rate"]),
        "exploration": float(c["exploration"]),
    }


def _array(value, shape, description, low, high, *, integers=False):
    try:
        raw = np.asarray(value, dtype=object)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{description} must be rectangular numerical data") from error
    _require(raw.shape == shape, f"{description} has the wrong shape")
    kind = Integral if integers else Real
    _require(
        all(
            isinstance(x, kind) and not isinstance(x, (bool, np.bool_))
            for x in raw.flat
        ),
        f"{description} contains a boolean, string, or unsupported numeric type",
    )
    try:
        numeric = raw.astype(np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{description} cannot be represented in float64") from error
    _require(
        np.isfinite(numeric).all()
        and np.all(numeric >= low)
        and np.all(numeric <= high),
        f"{description} violates finite numerical bounds",
    )
    return numeric.astype(np.int64) if integers else numeric


def validated_weights(configuration, weights):
    c = validated_configuration(configuration)
    _require(
        type(weights) is dict and set(weights) == {"W", "H", "V", "C"},
        "Weights must contain exactly W, H, V and C",
    )
    shapes = {
        "W": (c["feature_dimension"], c["width"]),
        "H": (c["width"],),
        "V": (c["width"], len(c["arm_ids"])),
        "C": (len(c["arm_ids"]),),
    }
    return {
        name: _array(
            weights[name], shape, f"Weight {name}", -WEIGHT_BOUND, WEIGHT_BOUND
        )
        for name, shape in shapes.items()
    }


def validated_features(configuration, features, *, maximum_rows=MAX_ROWS):
    c = validated_configuration(configuration)
    _require(_integer(maximum_rows, 1, MAX_ROWS), "The batch bound is invalid")
    try:
        raw = np.asarray(features, dtype=object)
    except (TypeError, ValueError) as error:
        raise ValueError("Features must be a rectangular normalized batch") from error
    _require(
        raw.ndim == 2 and 1 <= len(raw) <= maximum_rows,
        "Features require a nonempty bounded observation batch",
    )
    return _array(raw, (len(raw), c["feature_dimension"]), "Features", 0, 1)


def model_hash(configuration, weights):
    c, w = (
        validated_configuration(configuration),
        validated_weights(configuration, weights),
    )
    return digest(
        {
            "schema": SCHEMA,
            "configuration": c,
            "weights": {key: value.tolist() for key, value in w.items()},
        }
    )


def verify_initialization(configuration, weights):
    """Check only a claimed *initial* snapshot against its seed, never trained weights."""
    c, w = (
        validated_configuration(configuration),
        validated_weights(configuration, weights),
    )
    generator = np.random.default_rng(c["seed"])
    expected = {
        "W": generator.normal(0, math.sqrt(2 / c["feature_dimension"]), w["W"].shape),
        "H": np.full(c["width"], 0.15),
        "V": generator.normal(0, 0.05, w["V"].shape),
        "C": np.zeros(len(c["arm_ids"])),
    }
    _require(
        all(np.array_equal(w[k], expected[k]) for k in expected),
        "Claimed initial weights differ from the declared seed/architecture",
    )
    return {
        "verified": True,
        "seed": c["seed"],
        "model_sha256": model_hash(c, weights),
        "scope": "Seeded initialization only; no claim that trained weights must remain initial",
    }


def independent_forward(configuration, weights, features):
    """Execute the declared float64 recurrence; one subtractive reset per tick."""
    c = validated_configuration(configuration)
    w, x = validated_weights(c, weights), validated_features(c, features)
    drive = x @ w["W"] + w["H"]
    membrane = np.zeros_like(drive)
    counts = np.zeros(drive.shape, dtype=np.int64)
    if c["mechanism"] == "continuous":
        hidden = np.clip((drive - 0.1) / 0.8, 0, 1)
    else:
        for _ in range(c["ticks"]):
            membrane = 0.8 * membrane + drive
            above = membrane >= 1.0
            counts += above.astype(np.int64)
            membrane = membrane - above.astype(np.float64)
        hidden = counts.astype(np.float64) / c["ticks"]
    values = np.tanh(hidden @ w["V"] + w["C"])
    _require(np.isfinite(values).all(), "Independent readout became nonfinite")
    return {
        "values": values.tolist(),
        "hidden": hidden.tolist(),
        "spike_counts": counts.tolist(),
        "final_membrane": membrane.tolist(),
    }


def verify_forward(configuration, weights, features, published):
    """Check both counts and values; continuous controls explicitly have zero spikes."""
    c = validated_configuration(configuration)
    reference = independent_forward(c, weights, features)
    rows = len(reference["values"])
    _require(
        type(published) is dict
        and {"values", "spike_counts"} <= set(published)
        and set(published) <= {"values", "spike_counts", "hidden", "final_membrane"},
        "Published forward data must include values and integer spike counts",
    )
    values = _array(
        published["values"], (rows, len(c["arm_ids"])), "Published values", -1, 1
    )
    counts = _array(
        published["spike_counts"],
        (rows, c["width"]),
        "Published spike counts",
        0,
        c["ticks"],
        integers=True,
    )
    _require(
        np.array_equal(counts, reference["spike_counts"]),
        "Published hard LIF counts differ from independent execution",
    )
    error = float(np.max(np.abs(values - reference["values"])))
    _require(
        np.allclose(values, reference["values"], rtol=1e-12, atol=1e-12),
        "Published tanh values differ from independent execution",
    )
    for name, low, high in (("hidden", 0, 1), ("final_membrane", -100_000, 100_000)):
        if name in published:
            actual = _array(published[name], (rows, c["width"]), name, low, high)
            _require(
                np.allclose(actual, reference[name], rtol=1e-12, atol=1e-12),
                f"Published {name} differs from independent execution",
            )
    x = validated_features(c, features)
    return {
        "schema": REFERENCE_SCHEMA,
        "verified": True,
        "rows": rows,
        "mechanism": c["mechanism"],
        "ticks": c["ticks"],
        "model_sha256": model_hash(c, weights),
        "configuration_sha256": digest(c),
        "features_sha256": digest(x.tolist()),
        "spike_counts_sha256": digest(reference["spike_counts"]),
        "values_sha256": digest(reference["values"]),
        "total_hidden_spikes": int(counts.sum()),
        "maximum_value_error": error,
        "scope": "Independent arithmetic and declared identity coherence; no external attestation or outcome-improvement claim",
    }


def verify_decision(
    configuration, weights, features, decision, *, expected_decision_id=None
):
    """Check one choice against the exact historical model; randomness is not attested."""
    c = validated_configuration(configuration)
    x = validated_features(c, [features])[0].tolist()
    _require(
        type(decision) is dict and set(decision) == DECISION_KEYS,
        "A complete bounded policy decision record is required",
    )
    d, arms = decision, c["arm_ids"]
    _require(
        d["schema"] == SCHEMA
        and d["source_id"] == c["source_id"]
        and _identity(d["decision_id"])
        and _integer(d["ordinal"], 1, MAX_INTEGER)
        and type(d["legal_arms"]) is list
        and d["legal_arms"]
        and all(type(a) is str and a in arms for a in d["legal_arms"])
        and d["legal_arms"] == [a for a in arms if a in d["legal_arms"]]
        and d["arm"] in d["legal_arms"]
        and type(d["values"]) is dict
        and set(d["values"]) == set(d["legal_arms"])
        and d["reason"]
        in ("learned_value", "declared_initial_coverage", "declared_exploration")
        and d["features_sha256"] == digest(x)
        and d["model_sha256"] == model_hash(c, weights),
        "Decision source, legal mask, bounds or declared identities differ",
    )
    if expected_decision_id is not None:
        _require(
            _identity(expected_decision_id)
            and d["decision_id"] == expected_decision_id,
            "Decision differs from the caller's committed request identity",
        )
    initial = d["ordinal"] <= c["warmup"]
    _require(
        (d["reason"] == "declared_initial_coverage") == initial,
        "Decision reason is inconsistent with its declared warmup ordinal",
    )
    _require(
        d["reason"] != "declared_exploration" or c["exploration"] > 0,
        "Exploration is impossible under the declared configuration",
    )
    _require(
        d["reason"] != "learned_value" or c["exploration"] < 1,
        "A learned-value choice is impossible with certain exploration",
    )
    reference = independent_forward(c, weights, [x])
    counts = _array(
        d["spike_counts"],
        (c["width"],),
        "Decision spike counts",
        0,
        c["ticks"],
        integers=True,
    )
    _require(
        np.array_equal(counts, reference["spike_counts"][0]),
        "Decision spike counts differ from independent execution",
    )
    supplied = _array(
        [d["values"][a] for a in d["legal_arms"]],
        (len(d["legal_arms"]),),
        "Decision values",
        -1,
        1,
    )
    expected = np.asarray(
        [reference["values"][0][arms.index(a)] for a in d["legal_arms"]]
    )
    _require(
        np.allclose(supplied, expected, rtol=1e-12, atol=1e-12),
        "Decision tanh values differ from independent execution",
    )
    greedy = max(
        d["legal_arms"],
        key=lambda a: (reference["values"][0][arms.index(a)], -arms.index(a)),
    )
    if d["reason"] == "learned_value":
        _require(d["arm"] == greedy, "Learned-value choice is not the legal greedy arm")
    return {
        "schema": REFERENCE_SCHEMA,
        "verified": True,
        "decision_sha256": digest(d),
        "decision_id": d["decision_id"],
        "model_sha256": d["model_sha256"],
        "source_id": c["source_id"],
        "reason": d["reason"],
        "greedy_choice_checked": d["reason"] == "learned_value",
        "legal_greedy_arm": greedy,
        "total_hidden_spikes": int(counts.sum()),
        "scope": "Numerical/source coherence only; exploration draw, coverage history, external buyer and reward require separate evidence",
    }


def brian_parity(configuration, weights, features):
    """Run a separate actual Euler LIF network on one to eight observations.

    Euler algebra can differ slightly in membrane rounding. Counts must still
    match exactly; there is no tolerant repair of threshold/spike differences.
    Continuous controls are not claimed to be a spiking Brian2 network.
    """
    c = validated_configuration(configuration)
    _require(
        c["mechanism"] == "spiking",
        "Brian2 LIF parity applies only to the spiking mechanism",
    )
    w, x = (
        validated_weights(c, weights),
        validated_features(c, features, maximum_rows=MAX_BRIAN_ROWS),
    )
    reference = independent_forward(c, weights, x)
    import brian2 as b

    previous_target = b.prefs.codegen.target
    b.prefs.codegen.target = "numpy"
    try:
        clock = b.Clock(dt=1 * b.ms)
        neurons = b.NeuronGroup(
            len(x) * c["width"],
            "dv/dt = (-0.2 * v + drive) / ms : 1\ndrive : 1",
            threshold="v >= 1",
            reset="v -= 1",
            method="euler",
            clock=clock,
        )
        neurons.v = 0
        neurons.drive = (x @ w["W"] + w["H"]).reshape(-1)
        monitor = b.SpikeMonitor(neurons)
        network = b.Network(neurons, monitor)
        network.run(c["ticks"] * b.ms, namespace={})
        counts = np.asarray(monitor.count[:], dtype=np.int64).reshape(
            len(x), c["width"]
        )
        voltage = np.asarray(neurons.v[:], dtype=np.float64).reshape(len(x), c["width"])
        values = np.tanh((counts / c["ticks"]) @ w["V"] + w["C"])
        _require(
            np.array_equal(counts, reference["spike_counts"]),
            "Actual Brian2 spike counts differ from independent recurrence",
        )
        _require(
            np.allclose(values, reference["values"], rtol=1e-12, atol=1e-12),
            "Actual Brian2 tanh values differ from independent recurrence",
        )
        _require(
            np.allclose(voltage, reference["final_membrane"], rtol=1e-12, atol=1e-10),
            "Actual Brian2 membrane differs from independent recurrence",
        )
        spikes = [
            [int(i), float(t / b.ms)]
            for i, t in zip(monitor.i[:], monitor.t[:], strict=True)
        ]
        return {
            "schema": "nuria.spiking-decision-brian-parity.v1",
            "verified": True,
            "rows": len(x),
            "ticks": c["ticks"],
            "brian2_version": b.__version__,
            "model_sha256": model_hash(c, weights),
            "dt_milliseconds": 1,
            "matched_spike_counts": True,
            "matched_tanh_values": True,
            "spike_counts_sha256": digest(counts.tolist()),
            "actual_spikes_sha256": digest(spikes),
            "total_hidden_spikes": int(counts.sum()),
            "maximum_membrane_error": float(
                np.max(np.abs(voltage - reference["final_membrane"]))
            ),
            "scope": "Actual Brian2 parity on supplied samples only; not a training or useful-task comparison",
        }
    finally:
        b.prefs.codegen.target = previous_target


def verify_artifact(artifact, *, include_brian=True):
    """Verify one static model's records, not a changing historical trajectory."""
    _require(
        type(artifact) is dict
        and {"configuration", "weights", "features", "forward"} <= set(artifact)
        and set(artifact)
        <= {"configuration", "weights", "features", "forward", "decisions", "initial"},
        "Artifact fields differ from the static policy reference contract",
    )
    c, weights, features = (
        artifact["configuration"],
        artifact["weights"],
        artifact["features"],
    )
    report = verify_forward(c, weights, features, artifact["forward"])
    if "initial" in artifact:
        _require(
            type(artifact["initial"]) is bool, "Initial-snapshot flag must be boolean"
        )
        if artifact["initial"]:
            report["initialization"] = verify_initialization(c, weights)
    if "decisions" in artifact:
        _require(
            type(artifact["decisions"]) is list
            and len(artifact["decisions"]) == len(features),
            "Every observation must have exactly one decision record",
        )
        report["decisions"] = [
            verify_decision(c, weights, x, d)
            for x, d in zip(features, artifact["decisions"], strict=True)
        ]
        _require(
            len({d["decision_id"] for d in artifact["decisions"]}) == len(features),
            "A static artifact repeats a decision identity",
        )
    if include_brian and validated_configuration(c)["mechanism"] == "spiking":
        report["brian_parity"] = brian_parity(c, weights, features[:MAX_BRIAN_ROWS])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--skip-brian", action="store_true")
    args = parser.parse_args()
    _require(
        args.artifact.stat().st_size <= 16 * 1024 * 1024,
        "Artifact exceeds the file-size bound",
    )
    report = verify_artifact(
        json.loads(args.artifact.read_text()), include_brian=not args.skip_brian
    )
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"verified": True, "rows": report["rows"]}))


if __name__ == "__main__":
    main()
