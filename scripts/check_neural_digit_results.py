"""Reproduce the frozen digit comparison offline, without training or tuning.

The original scorer retained paired comparisons for the three trained spiking
models, although the protocol also promised these secondary tables for every
control. ``--write-paired`` adds that arithmetic-only supplement without changing
the original result, predictions or frozen evaluator. The reported neural mean
averages three fixed initializations; it is not an ensemble or 5,391 independent
test examples. This checker has no network dependency or external witness.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import math
import os
import zipfile
from pathlib import Path

import numpy as np

from scripts.verify_neural_digits import (
    brian_parity,
    independent_predict,
    paired_correctness,
    validated_weights,
    verify_model_predictions,
)

ROOT = Path(__file__).resolve().parents[1]
FREEZE_COMMIT = "e13a97cd6aee57d8d3a619814c1d431be2797e36"
PROTOCOL_SHA256 = "024d75219d4c8de71e3ff2f308b5f497a2262b2f24d0fdd28ca03ab38bf20fd2"
SEEDS = (202611, 202612, 202613)
ROWS = 1797
TRAIN_ROWS = 3823
EXPECTED = {"linear": ("linear", "softmax", None), "knn5": ("knn5", "knn", None)}
EXPECTED.update(
    {
        f"{method}-{seed}": (method, kind, seed)
        for method, kind in (
            ("snn_trained", "snn"),
            ("snn_input_frozen", "snn"),
            ("continuous", "continuous"),
        )
        for seed in SEEDS
    }
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _constant(value):
    raise ValueError("Nonfinite JSON constants are not evidence: " + value)


def read_json(path, *, maximum=16 * 1024 * 1024):
    path = Path(path)
    if not path.exists() and Path(str(path) + ".gz").exists():
        path = Path(str(path) + ".gz")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "An evidence file exceeds its decoded byte bound")
    return (
        json.loads(raw, parse_constant=_constant),
        hashlib.sha256(raw).hexdigest(),
        len(raw),
    )


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def labels(value, *, rows=None):
    rows = ROWS if rows is None else rows
    require(
        type(value) is list
        and len(value) == rows
        and all(type(v) is int and 0 <= v <= 9 for v in value),
        "Every registered prediction needs complete, integer class labels",
    )
    return np.asarray(value, dtype=np.int64)


def logits_match(value, reference):
    actual = np.asarray(value)
    expected = np.asarray(reference, dtype=np.float64)
    require(
        actual.shape == expected.shape
        and actual.dtype.kind in "fiu"
        and np.isfinite(actual).all()
        and np.allclose(actual, expected, rtol=1e-12, atol=1e-10),
        "Published logits differ from the independent control",
    )


def source_check(protocol):
    checks = {}
    for name, expected in protocol["source_hashes"].items():
        path = (ROOT / name).resolve()
        require(ROOT in path.parents, "A frozen source path escapes the repository")
        checks[name] = hashlib.sha256(path.read_bytes()).hexdigest() == expected
    require(len(checks) == 7 and all(checks.values()), "A frozen source file changed")
    return checks


def registry(study, protocol):
    directory = study / "models"
    manifest, manifest_hash, _ = read_json(directory / "manifest.json")
    require(
        manifest_hash == protocol["training_manifest_sha256"],
        "The frozen training manifest changed",
    )
    require(
        manifest["official_test_read"] is False
        and manifest["dataset"]["train_sha256"] == protocol["data"]["train_sha256"]
        and manifest["dataset"]["rows"] == TRAIN_ROWS,
        "The recorded training boundary differs",
    )
    entries = manifest["models"]
    require(
        len(entries) == len(EXPECTED)
        and len({item["id"] for item in entries}) == len(entries)
        and {item["id"] for item in entries} == set(EXPECTED)
        and set(protocol["model_ids"]) == set(EXPECTED),
        "The complete eleven-model registry is required",
    )
    output = {}
    for item in entries:
        ident = item["id"]
        require(
            (item["method"], item["kind"], item["seed"]) == EXPECTED[ident],
            "A registered method, mechanism or seed changed",
        )
        path = (directory / item["path"]).resolve()
        require(directory.resolve() in path.parents, "A checkpoint path escapes models")
        checkpoint, file_hash, size = read_json(path)
        require(file_hash == item["file_sha256"], "A frozen model file changed")
        require(
            checkpoint["schema"] == "nuria.digit-checkpoint.v1"
            and checkpoint["method"] == item["method"]
            and checkpoint["model_sha256"] == item["model_sha256"]
            and checkpoint["state_sha256"] == item["state_sha256"],
            "Checkpoint metadata differs from the frozen registry",
        )
        model = checkpoint["model"]
        require(
            digest(model) == item["state_sha256"], "A full model state hash differs"
        )
        if item["kind"] in ("snn", "continuous"):
            expected_config = {
                "seed": item["seed"],
                "width": 64,
                "ticks": 16,
                "kind": item["kind"],
                "frozen_input": item["method"] == "snn_input_frozen",
            }
            require(
                model["schema"] == "nuria-spiking-digits-v1"
                and model["configuration"] == expected_config == item["configuration"]
                and checkpoint["weights"] == model["weights"]
                and model["optimizer"]["step"] == 3750,
                "The frozen neural architecture, weights or training step differs",
            )
            validated_weights(model["weights"])
            semantic = {
                key: model[key] for key in ("schema", "configuration", "weights")
            }
            if item["method"] == "snn_input_frozen":
                initial = np.random.default_rng(item["seed"]).normal(
                    0, np.sqrt(2 / 64), (64, 64)
                )
                require(
                    np.array_equal(initial, np.asarray(model["weights"]["W"]))
                    and model["weights"]["H"] == [0.15] * 64,
                    "An input-frozen control changed its initial representation",
                )
        else:
            semantic = model
        require(
            digest(semantic) == item["model_sha256"], "A semantic model hash differs"
        )
        output[ident] = {"item": item, "checkpoint": checkpoint, "bytes": size}
    return output


def dataset(study, protocol):
    raw = (study / "data/optdigits-original.zip").read_bytes()
    require(
        len(raw) <= 2 * 1024 * 1024
        and hashlib.sha256(raw).hexdigest() == protocol["data"]["archive_sha256"],
        "The retained upstream dataset archive differs",
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        train_raw = archive.read(protocol["data"]["train_member"])
        test_raw = archive.read(protocol["data"]["test_member"])
    require(
        hashlib.sha256(train_raw).hexdigest() == protocol["data"]["train_sha256"],
        "The official training partition differs",
    )

    def parse(body, count):
        values = [
            [int(v) for v in row]
            for row in csv.reader(io.StringIO(body.decode("ascii")))
        ]
        require(
            len(values) == count
            and all(
                len(row) == 65
                and all(0 <= v <= 16 for v in row[:64])
                and 0 <= row[64] <= 9
                for row in values
            ),
            "The complete pinned dataset rows are malformed",
        )
        return [row[:64] for row in values], [row[64] for row in values]

    train_pixels, train_targets = parse(train_raw, TRAIN_ROWS)
    test_pixels, test_targets = parse(test_raw, ROWS)
    return train_pixels, train_targets, test_pixels, test_targets


def check_commitment(prediction, prediction_hash, commit, protocol_hash, entries):
    public = prediction.get("public_freeze")
    require(
        type(public) is dict
        and public.get("commit") == FREEZE_COMMIT
        and public.get("protocol_sha256") == protocol_hash
        and commit.get("public_freeze") == public
        and prediction.get("schema") == "nuria.neural-digit-predictions.v1"
        and commit.get("schema") == "nuria.neural-digit-prediction-commit.v1"
        and prediction.get("rows") == ROWS == commit.get("rows")
        and prediction.get("protocol_sha256") == protocol_hash
        and commit.get("protocol_sha256") == protocol_hash
        and commit.get("predictions_sha256") == prediction_hash
        and commit.get("models") == 11
        and prediction.get("test_learning_enabled") is False
        and commit.get("scoring_labels_read") is False,
        "The frozen prediction commitment differs",
    )
    outputs = prediction["models"]
    require(
        len(outputs) == 11
        and len({row["id"] for row in outputs}) == 11
        and {row["id"] for row in outputs} == set(entries),
        "A committed model was omitted, duplicated or substituted",
    )
    for output in outputs:
        entry = entries[output["id"]]
        item = entry["item"]
        require(
            all(
                output[key] == item[key]
                for key in ("method", "kind", "seed", "model_sha256")
            )
            and output["state_before_sha256"] == item["state_sha256"]
            and output["state_after_sha256"] == item["state_sha256"]
            and output["checkpoint_file_bytes"] == entry["bytes"]
            and type(output["inference_cpu_seconds"]) in (int, float)
            and math.isfinite(output["inference_cpu_seconds"])
            and output["inference_cpu_seconds"] >= 0,
            "Committed model identity or measurement metadata differs",
        )
        labels(output["labels"])


def control_predictions(entry, pixels, training_pixels, training_targets):
    item, model = entry["item"], entry["checkpoint"]["model"]
    x = np.asarray(pixels, dtype=np.float64) / 16
    if item["kind"] == "continuous":
        weights = validated_weights(model["weights"])
        activation = np.clip((x @ weights["W"] + weights["H"] - 0.1) / 0.8, 0, 1)
        logits = activation @ weights["V"] + weights["C"]
    elif item["kind"] == "softmax":
        weights = np.asarray(model["weights"], dtype=np.float64)
        require(weights.shape == (65, 10), "The linear model has another architecture")
        logits = np.c_[x, np.ones(len(x))] @ weights
    elif item["kind"] == "knn":
        require(
            model["k"] == 5
            and model["inputs"] == training_pixels
            and model["labels"] == training_targets,
            "The memory control differs from the same official training set",
        )
        training = np.asarray(training_pixels, dtype=np.int64)
        truth = np.asarray(training_targets)
        norm = np.sum(training * training, axis=1)
        logits = []
        for row in np.asarray(pixels, dtype=np.int64):
            distances = np.sum(row * row) + norm - 2 * training @ row
            nearest = np.argsort(distances, kind="stable")[:5]
            logits.append(np.bincount(truth[nearest], minlength=10))
        logits = np.asarray(logits)
    else:
        raise ValueError("A control has an unregistered mechanism")
    require(np.isfinite(logits).all(), "An independent control became nonfinite")
    return {
        "labels": np.argmax(logits, axis=1).astype(int).tolist(),
        "logits": logits.tolist(),
    }


def calculate_gates(scored, margin):
    by_id = {row["id"]: row for row in scored}
    trained = [by_id[f"snn_trained-{seed}"]["accuracy"] for seed in SEEDS]
    frozen = [by_id[f"snn_input_frozen-{seed}"]["accuracy"] for seed in SEEDS]
    linear = by_id["linear"]["accuracy"]
    mean = sum(trained) / len(SEEDS)
    frozen_mean = sum(frozen) / len(SEEDS)
    return (
        {
            "neural_over_linear": mean - linear >= margin
            and all(v > linear for v in trained),
            "learned_input_contribution": mean - frozen_mean >= margin
            and all(a > b for a, b in zip(trained, frozen, strict=True)),
            "all_rows_and_models_retained": len(scored) == 11
            and set(by_id) == set(EXPECTED)
            and all(row["rows"] == ROWS for row in scored),
        },
        mean,
        frozen_mean,
    )


def verify_directory(study, *, include_brian=True, write_paired=False):
    study = Path(study)
    protocol, protocol_hash, _ = read_json(study / "protocol.json")
    require(protocol_hash == PROTOCOL_SHA256, "This is not the named frozen protocol")
    source_hashes = source_check(protocol)
    entries = registry(study, protocol)
    qualified = study / "qualification"
    prediction, prediction_hash, _ = read_json(
        qualified / "predictions.json", maximum=64 * 1024 * 1024
    )
    commit, _, _ = read_json(qualified / "PREDICTIONS-COMMITTED.json")
    result, result_hash, _ = read_json(qualified / "result.json")
    original_check, _, _ = read_json(qualified / "verification.json")
    started, _, _ = read_json(qualified / "started.json")
    check_commitment(prediction, prediction_hash, commit, protocol_hash, entries)
    require(
        started["schema"] == "nuria.neural-digit-start.v1"
        and started["public_freeze"] == prediction["public_freeze"]
        and result["schema"] == "nuria.neural-digit-result.v1"
        and result["protocol_sha256"] == protocol_hash
        and result["predictions_sha256"] == prediction_hash
        and result["rows"] == ROWS,
        "The original start, result or commitment identities disagree",
    )
    train_pixels, train_targets, pixels, targets = dataset(study, protocol)
    require(
        digest(pixels) == prediction["inputs_sha256"], "The scored input order differs"
    )
    outputs = {row["id"]: row for row in prediction["models"]}
    retained = {row["id"]: row for row in original_check["models"]}
    require(
        original_check["verified"] is True
        and len(original_check["models"]) == 6
        and set(retained)
        == {key for key, e in entries.items() if e["item"]["kind"] == "snn"},
        "The original six-branch independent checks are incomplete",
    )
    scored, neural_checks = [], []
    for ident, entry in entries.items():
        output, item = outputs[ident], entry["item"]
        before = digest(entry["checkpoint"])
        if item["kind"] == "snn":
            check = verify_model_predictions(
                entry["checkpoint"]["weights"],
                pixels,
                {key: output[key] for key in ("labels", "logits", "spike_counts")},
            )
            old = retained[ident]
            for key in (
                "schema",
                "verified",
                "rows",
                "ticks",
                "normalized_weights_sha256",
                "pixels_sha256",
                "labels_sha256",
                "spike_counts_sha256",
                "total_hidden_spikes",
                "checked_published_spike_counts",
                "checked_published_logits",
                "interventions",
            ):
                require(
                    old[key] == check[key], "A retained neural check differs: " + key
                )
            require(
                0 <= old["maximum_published_logit_error"] <= 1e-10,
                "The retained neural logit error is invalid",
            )
            if include_brian:
                brian = brian_parity(entry["checkpoint"]["weights"], pixels[:8])
                for key in (
                    "schema",
                    "verified",
                    "rows",
                    "ticks",
                    "dt_milliseconds",
                    "matched_spike_counts",
                    "matched_predictions",
                    "total_hidden_spikes",
                    "actual_spikes_sha256",
                    "spike_counts_sha256",
                    "labels_sha256",
                ):
                    require(
                        old["brian2"][key] == brian[key],
                        "Retained Brian2 parity differs: " + key,
                    )
                require(
                    0 <= old["brian2"]["maximum_membrane_error"] <= 1e-10,
                    "The retained Brian2 membrane error is invalid",
                )
            neural_checks.append(
                {
                    "id": ident,
                    "labels_sha256": check["labels_sha256"],
                    "brian_reproduced": include_brian,
                }
            )
        else:
            replay = control_predictions(entry, pixels, train_pixels, train_targets)
            require(
                replay["labels"] == output["labels"], "A control label replay differs"
            )
            logits_match(output["logits"], replay["logits"])
        require(
            before == digest(entry["checkpoint"]), "Reproduction mutated model state"
        )
        expected = {
            "id": ident,
            "method": item["method"],
            "seed": item["seed"],
            "correct": int(np.count_nonzero(labels(output["labels"]) == targets)),
            "rows": ROWS,
            "model_sha256": item["model_sha256"],
            "inference_cpu_seconds": output["inference_cpu_seconds"],
            "checkpoint_file_bytes": entry["bytes"],
        }
        expected["accuracy"] = expected["correct"] / ROWS
        if item["kind"] == "snn":
            expected["hidden_spikes"] = int(np.sum(output["spike_counts"]))
            if item["method"] == "snn_trained":
                reverted = copy.deepcopy(entry["checkpoint"]["weights"])
                reverted["W"] = (
                    np.random.default_rng(item["seed"])
                    .normal(0, np.sqrt(2 / 64), (64, 64))
                    .tolist()
                )
                reverted["H"] = [0.15] * 64
                reverted_labels = independent_predict(reverted, pixels)["labels"]
                expected["reverted_input_correct"] = int(
                    np.sum(np.asarray(reverted_labels) == targets)
                )
                expected["zero_spike_correct"] = int(
                    np.sum(np.argmax(reverted["C"]) == np.asarray(targets))
                )
        scored.append(expected)
    require(
        len(result["models"]) == 11
        and len({row["id"] for row in result["models"]}) == 11,
        "The original score tables omit or duplicate a model",
    )
    original_scores = {row["id"]: row for row in result["models"]}
    for row in scored:
        require(
            original_scores.get(row["id"]) == row,
            "An original score or cost observation differs",
        )
    pairs = {
        ident: {
            comparator: paired_correctness(
                targets, output["labels"], outputs[comparator]["labels"]
            )
            for comparator in ("linear", "knn5")
        }
        for ident, output in outputs.items()
    }
    require(
        set(result["paired"]) == {f"snn_trained-{seed}" for seed in SEEDS},
        "The original primary paired-table membership differs",
    )
    for ident, pair in result["paired"].items():
        require(pair == pairs[ident], "An original paired comparison differs")
    gates, mean, frozen_mean = calculate_gates(
        scored, protocol["gates"]["minimum_accuracy_gain"]
    )
    require(
        result["gates"] == gates
        and math.isclose(result["neural_mean_accuracy"], mean, rel_tol=0, abs_tol=1e-14)
        and result["linear_accuracy"] == original_scores["linear"]["accuracy"]
        and math.isclose(
            result["input_frozen_mean_accuracy"], frozen_mean, rel_tol=0, abs_tol=1e-14
        )
        and result["scope"] == protocol["scope"],
        "The original aggregate, frozen gate or scope differs",
    )
    supplement = {
        "schema": "nuria.neural-digit-paired-supplement.v1",
        "protocol_sha256": protocol_hash,
        "predictions_sha256": prediction_hash,
        "original_result_sha256": result_hash,
        "rows": ROWS,
        "models": 11,
        "paired": pairs,
        "scope": "Arithmetic-only secondary coverage supplement. The frozen scorer omitted paired control tables; original predictions, result, models and gates remain unchanged. Every comparison uses the same already-scored 1,797 rows. No new training, tuning, prediction or unseen qualification. The neural mean is three fixed initializations, not an ensemble.",
    }
    destination = qualified / "paired-all.json"
    # Source and checkpoints remain immutable through every reproduction step.
    source_check(protocol)
    registry(study, protocol)
    if write_paired:
        write_new(destination, supplement)
    retained_pairs, _, _ = read_json(destination)
    require(retained_pairs == supplement, "The all-model paired supplement differs")
    return {
        "verified": True,
        "rows": ROWS,
        "models": 11,
        "frozen_source_files": len(source_hashes),
        "freeze_commit": FREEZE_COMMIT,
        "protocol_sha256": protocol_hash,
        "predictions_sha256": prediction_hash,
        "original_result_sha256": result_hash,
        "gates": gates,
        "neural_mean_accuracy": mean,
        "linear_accuracy": result["linear_accuracy"],
        "input_frozen_mean_accuracy": frozen_mean,
        "independent_spiking_branches": neural_checks,
        "paired_comparisons": len(pairs) * 2,
        "supplement_secondary_omission": True,
        "scope": "Offline arithmetic and frozen-inference reproduction; no training, retuning or new unseen experiment. Brian2 parity covers eight rows per spiking branch when enabled. Timing observations are checked for retained identity, not independently proven physical measurements.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--write-paired", action="store_true")
    parser.add_argument("--without-brian", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            verify_directory(
                args.study,
                include_brian=not args.without_brian,
                write_paired=args.write_paired,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
