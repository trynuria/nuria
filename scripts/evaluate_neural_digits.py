"""Commit frozen digit decisions before scoring the official writer-held-out set.

The predict phase projects inputs only, verifies the publicly frozen protocol
and leaves all models unchanged. The score phase reads labels afterwards and
retains every registered model. This evaluates a bounded perception specialist,
not the original recurrent circuit or market, treasury or hiring decisions.
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
import re
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from cognition.spiking_digits import SpikingDigits
from scripts.verify_neural_digits import (
    brian_parity,
    independent_predict,
    paired_correctness,
    verify_model_predictions,
)

ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ROWS = 1797


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open("xb") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_json(path, *, maximum=16 * 1024 * 1024):
    path = Path(path)
    if not path.exists() and Path(str(path) + ".gz").exists():
        path = Path(str(path) + ".gz")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Evidence file exceeds its bounded size")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def source_check(protocol):
    for name, expected in protocol["source_hashes"].items():
        path = (ROOT / name).resolve()
        if ROOT not in path.parents or path.is_symlink():
            raise ValueError("Source identity is outside the reviewed repository")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("A frozen implementation changed: " + name)


def freeze_check(protocol_path, commit):
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError("An exact public freeze commit is required")
    raw = Path(protocol_path).read_bytes()
    url = (
        "https://raw.githubusercontent.com/trynuria/nuria/"
        + commit
        + "/research/neural-digits/protocol.json"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Nuria-research"})
    with urllib.request.urlopen(request, timeout=20) as response:
        published = response.read(2 * 1024 * 1024 + 1)
    if published != raw:
        raise ValueError("The public freeze differs from the inspected protocol")
    return {
        "commit": commit,
        "protocol_sha256": hashlib.sha256(raw).hexdigest(),
        "verified_utc": utc(),
        "method": "Exact public raw GitHub protocol bytes before test projection",
    }


def dataset(archive, protocol, *, labels=False):
    raw = Path(archive).read_bytes()
    if (
        len(raw) > 2 * 1024 * 1024
        or hashlib.sha256(raw).hexdigest() != protocol["data"]["archive_sha256"]
    ):
        raise ValueError("The upstream archive identity differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        body = source.read(protocol["data"]["test_member"])
    rows = list(csv.reader(io.StringIO(body.decode("ascii"))))
    if len(rows) != ROWS or any(len(row) != 65 for row in rows):
        raise ValueError("The complete official test partition is required")
    if labels:
        targets = [int(row[64]) for row in rows]
        if any(not 0 <= label <= 9 for label in targets):
            raise ValueError("An official test label is outside the task")
        return targets
    # Label strings are not parsed, retained or supplied to any model here.
    pixels = [[int(value) for value in row[:64]] for row in rows]
    if any(not 0 <= value <= 16 for row in pixels for value in row):
        raise ValueError("An official test input is outside the task")
    return pixels


def model_files(directory, protocol):
    root = Path(directory).resolve()
    manifest, manifest_hash = read_json(root / "manifest.json")
    if manifest_hash != protocol["training_manifest_sha256"]:
        raise ValueError("The frozen training manifest changed")
    if len(manifest["models"]) != 11:
        raise ValueError("All eleven registered model branches must remain")
    seen, result = set(), []
    for item in manifest["models"]:
        if item["id"] in seen:
            raise ValueError("A registered model identity repeats")
        seen.add(item["id"])
        path = (root / item["path"]).resolve()
        if root not in path.parents or path.is_symlink():
            raise ValueError("A checkpoint escapes its reviewed directory")
        checkpoint, file_hash = read_json(path)
        if file_hash != item["file_sha256"]:
            raise ValueError("A frozen model checkpoint changed")
        if (
            checkpoint["schema"] != "nuria.digit-checkpoint.v1"
            or checkpoint["method"] != item["method"]
            or checkpoint["model_sha256"] != item["model_sha256"]
            or checkpoint["state_sha256"] != item["state_sha256"]
        ):
            raise ValueError("Checkpoint metadata differs from its registered identity")
        if item["kind"] in ("snn", "continuous"):
            if (
                checkpoint["model"]["configuration"] != item["configuration"]
                or checkpoint["weights"] != checkpoint["model"]["weights"]
            ):
                raise ValueError("Checkpoint configuration or weights differ")
        elif (
            digest(checkpoint["model"]) != item["model_sha256"]
            or item["model_sha256"] != item["state_sha256"]
        ):
            raise ValueError("A control model identity differs from its registry")
        result.append((item, checkpoint, path.stat().st_size))
    if seen != set(protocol["model_ids"]):
        raise ValueError("The evaluated models differ from the frozen registry")
    return result


def infer(item, checkpoint, pixels):
    inputs = np.asarray(pixels, dtype=np.float64) / 16
    model = checkpoint["model"]
    started = time.process_time()
    counts = None
    if item["kind"] in ("snn", "continuous"):
        network = SpikingDigits(**model["configuration"], state=model)
        before = network.state_hash()
        if (
            before != item["state_sha256"]
            or network.model_hash() != item["model_sha256"]
        ):
            raise ValueError("The restored neural model differs from its freeze")
        logits, hidden, _, _ = network.forward(inputs)
        labels = np.argmax(logits, axis=1).astype(int).tolist()
        if item["kind"] == "snn":
            counts = np.rint(hidden * 16).astype(int).tolist()
        after = network.state_hash()
        if before != after:
            raise ValueError("An inference-only test changed learned state")
    elif item["kind"] == "softmax":
        before = after = digest(model)
        weights = np.asarray(model["weights"], dtype=np.float64)
        if weights.shape != (65, 10) or not np.isfinite(weights).all():
            raise ValueError("Linear classifier weights are invalid")
        logits = np.c_[inputs, np.ones(len(inputs))] @ weights
        labels = np.argmax(logits, axis=1).astype(int).tolist()
    elif item["kind"] == "knn":
        before = after = digest(model)
        training = np.asarray(model["inputs"], dtype=np.int64)
        targets = np.asarray(model["labels"], dtype=np.int64)
        values = np.asarray(pixels, dtype=np.int64)
        if (
            training.shape != (3823, 64)
            or targets.shape != (3823,)
            or np.any(training < 0)
            or np.any(training > 16)
            or np.any(targets < 0)
            or np.any(targets > 9)
            or model["k"] != 5
        ):
            raise ValueError(
                "The complete same-label-budget memory control is required"
            )
        distances = (
            np.sum(values * values, axis=1)[:, None]
            + np.sum(training * training, axis=1)[None, :]
            - 2 * values @ training.T
        )
        nearest = np.argsort(distances, axis=1, kind="stable")[:, :5]
        logits = np.stack(
            [np.bincount(targets[indices], minlength=10) for indices in nearest]
        )
        labels = np.argmax(logits, axis=1).astype(int).tolist()
    else:
        raise ValueError("Unregistered prediction mechanism")
    if before != item["state_sha256"] or before != after:
        raise ValueError("A model state identity changed")
    if item["kind"] in ("softmax", "knn") and before != item["model_sha256"]:
        raise ValueError("A control model hash differs")
    result = {
        "id": item["id"],
        "method": item["method"],
        "kind": item["kind"],
        "seed": item["seed"],
        "model_sha256": item["model_sha256"],
        "state_before_sha256": before,
        "state_after_sha256": after,
        "labels": labels,
        "logits": logits.tolist(),
        "inference_cpu_seconds": time.process_time() - started,
    }
    if counts is not None:
        result["spike_counts"] = counts
    return result


def predict(args):
    protocol, protocol_hash = read_json(args.protocol)
    source_check(protocol)
    models = model_files(args.models, protocol)
    public = freeze_check(args.protocol, args.freeze_commit)
    target = Path(args.output)
    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    write_new(
        target / "started.json",
        {
            "schema": "nuria.neural-digit-start.v1",
            "started_utc": utc(),
            "public_freeze": public,
            "scope": "No test label has been supplied to any model or scoring function",
        },
    )
    pixels = dataset(args.archive, protocol)
    outputs = []
    for item, checkpoint, size in models:
        output = infer(item, checkpoint, pixels)
        output["checkpoint_file_bytes"] = size
        outputs.append(output)
    source_check(protocol)
    artifact = {
        "schema": "nuria.neural-digit-predictions.v1",
        "committed_utc": utc(),
        "protocol_sha256": protocol_hash,
        "public_freeze": public,
        "rows": len(pixels),
        "inputs_sha256": digest(pixels),
        "models": outputs,
        "test_learning_enabled": False,
        "scope": "All frozen predictions committed before scoring labels; source, weights and optimizer states unchanged",
    }
    write_new(target / "predictions.json", artifact)
    _, prediction_hash = read_json(
        target / "predictions.json", maximum=64 * 1024 * 1024
    )
    write_new(
        target / "PREDICTIONS-COMMITTED.json",
        {
            "schema": "nuria.neural-digit-prediction-commit.v1",
            "committed_utc": utc(),
            "predictions_sha256": prediction_hash,
            "protocol_sha256": protocol_hash,
            "rows": len(pixels),
            "models": len(outputs),
            "scoring_labels_read": False,
            "public_freeze": public,
        },
    )
    return {"predictions_committed": True, "rows": len(pixels), "models": len(outputs)}


def validate_commitment(protocol_hash, prediction, prediction_hash, commit, frozen):
    public = prediction.get("public_freeze")
    if not isinstance(public, dict) or not isinstance(public.get("commit"), str):
        raise ValueError("The public freeze identity is malformed")
    if (
        commit.get("schema") != "nuria.neural-digit-prediction-commit.v1"
        or prediction.get("schema") != "nuria.neural-digit-predictions.v1"
        or commit.get("rows") != ROWS
        or commit.get("models") != 11
        or commit.get("scoring_labels_read") is not False
        or prediction.get("rows") != ROWS
        or prediction.get("test_learning_enabled") is not False
        or commit.get("public_freeze") != prediction.get("public_freeze")
        or prediction.get("public_freeze", {}).get("protocol_sha256") != protocol_hash
        or not COMMIT.fullmatch(prediction.get("public_freeze", {}).get("commit", ""))
        or commit.get("predictions_sha256") != prediction_hash
        or commit.get("protocol_sha256") != protocol_hash
        or prediction.get("protocol_sha256") != protocol_hash
        or len(prediction.get("models", [])) != len(frozen)
        or {item["id"] for item in prediction.get("models", [])} != set(frozen)
    ):
        raise ValueError("The complete prediction commitment differs")


def prediction_identity(published, item, size):
    if any(
        published[key] != item[key]
        for key in ("method", "kind", "seed", "model_sha256")
    ) or (
        published["state_before_sha256"] != item["state_sha256"]
        or published["state_after_sha256"] != item["state_sha256"]
        or published["checkpoint_file_bytes"] != size
        or type(published["inference_cpu_seconds"]) not in (int, float)
        or not math.isfinite(published["inference_cpu_seconds"])
        or published["inference_cpu_seconds"] < 0
        or len(published["labels"]) != ROWS
        or any(
            type(label) is not int or not 0 <= label <= 9
            for label in published["labels"]
        )
    ):
        raise ValueError(
            "Published prediction identities or metadata differ from the freeze"
        )


def score(args):
    protocol, protocol_hash = read_json(args.protocol)
    source_check(protocol)
    registry = model_files(args.models, protocol)
    frozen = {item["id"]: (item, ckpt) for item, ckpt, _ in registry}
    sizes = {item["id"]: size for item, _, size in registry}
    target = Path(args.output)
    prediction, prediction_hash = read_json(
        target / "predictions.json", maximum=64 * 1024 * 1024
    )
    commit, _ = read_json(target / "PREDICTIONS-COMMITTED.json")
    validate_commitment(protocol_hash, prediction, prediction_hash, commit, frozen)
    if args.freeze_commit != prediction["public_freeze"]["commit"]:
        raise ValueError("Scoring must use the original public freeze")
    freeze_check(args.protocol, args.freeze_commit)
    for published in prediction["models"]:
        item, _ = frozen[published["id"]]
        prediction_identity(published, item, sizes[item["id"]])
    pixels = dataset(args.archive, protocol)
    if digest(pixels) != prediction["inputs_sha256"]:
        raise ValueError("Scoring inputs differ from committed predictions")
    checks = []
    for published in prediction["models"]:
        item, checkpoint = frozen[published["id"]]
        if item["kind"] == "snn":
            checked = verify_model_predictions(
                checkpoint["weights"],
                pixels,
                {k: published[k] for k in ("labels", "logits", "spike_counts")},
            )
            checked["id"] = item["id"]
            checked["brian2"] = brian_parity(checkpoint["weights"], pixels[:8])
            checks.append(checked)
        else:
            rerun = infer(item, checkpoint, pixels)
            if rerun["labels"] != published["labels"] or not np.allclose(
                rerun["logits"], published["logits"], rtol=1e-12, atol=1e-10
            ):
                raise ValueError("A non-spiking control failed deterministic replay")
    # This is the first evaluator call that parses official test targets.
    targets = dataset(args.archive, protocol, labels=True)
    scored = []
    for published in prediction["models"]:
        item, checkpoint = frozen[published["id"]]
        truth = np.asarray(targets)
        labels = np.asarray(published["labels"])
        if labels.shape != truth.shape:
            raise ValueError("A model omitted official test rows")
        entry = {
            "id": item["id"],
            "method": item["method"],
            "seed": item["seed"],
            "correct": int(np.count_nonzero(labels == truth)),
            "rows": len(truth),
            "accuracy": float(np.mean(labels == truth)),
            "model_sha256": item["model_sha256"],
            "inference_cpu_seconds": published["inference_cpu_seconds"],
            "checkpoint_file_bytes": published["checkpoint_file_bytes"],
        }
        if item["kind"] == "snn":
            checked = next(v for v in checks if v["id"] == item["id"])
            entry["hidden_spikes"] = checked["total_hidden_spikes"]
            if item["method"] == "snn_trained":
                initial = SpikingDigits(**checkpoint["model"]["configuration"])
                reverted = copy.deepcopy(checkpoint["weights"])
                reverted.update(W=initial.W.tolist(), H=initial.H.tolist())
                restored = independent_predict(reverted, pixels)["labels"]
                zeroed = np.full(len(targets), np.argmax(checkpoint["weights"]["C"]))
                entry["reverted_input_correct"] = int(
                    np.sum(np.asarray(restored) == truth)
                )
                entry["zero_spike_correct"] = int(np.sum(zeroed == truth))
        scored.append(entry)
    linear = next(item for item in prediction["models"] if item["method"] == "linear")
    knn = next(item for item in prediction["models"] if item["method"] == "knn5")
    trained = [item for item in prediction["models"] if item["method"] == "snn_trained"]
    input_frozen = [item for item in scored if item["method"] == "snn_input_frozen"]
    selected = [item for item in scored if item["method"] == "snn_trained"]
    linear_accuracy = float(
        np.mean(np.asarray(linear["labels"]) == np.asarray(targets))
    )
    neural_accuracy = float(np.mean([item["accuracy"] for item in selected]))
    frozen_accuracy = float(np.mean([item["accuracy"] for item in input_frozen]))
    margin = protocol["gates"]["minimum_accuracy_gain"]
    result = {
        "schema": "nuria.neural-digit-result.v1",
        "scored_utc": utc(),
        "protocol_sha256": protocol_hash,
        "predictions_sha256": prediction_hash,
        "rows": len(targets),
        "models": scored,
        "paired": {
            item["id"]: {
                "linear": paired_correctness(targets, item["labels"], linear["labels"]),
                "knn5": paired_correctness(targets, item["labels"], knn["labels"]),
            }
            for item in trained
        },
        "gates": {
            "neural_over_linear": neural_accuracy - linear_accuracy >= margin
            and all(item["accuracy"] > linear_accuracy for item in selected),
            "learned_input_contribution": neural_accuracy - frozen_accuracy >= margin
            and all(
                item["accuracy"]
                > next(v["accuracy"] for v in input_frozen if v["seed"] == item["seed"])
                for item in selected
            ),
            "all_rows_and_models_retained": len(scored) == 11 and len(targets) == ROWS,
        },
        "neural_mean_accuracy": neural_accuracy,
        "linear_accuracy": linear_accuracy,
        "input_frozen_mean_accuracy": frozen_accuracy,
        "scope": protocol["scope"],
        "uncertainty": "Exact finite-benchmark comparisons. Rows share writers; writer identifiers are unavailable. No independent-writer population confidence claim or independent execution witness.",
    }
    write_new(target / "verification.json", {"verified": True, "models": checks})
    write_new(target / "result.json", result)
    return {"scored": True, "gates": result["gates"], "rows": len(targets)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("predict", "score"))
    parser.add_argument("protocol", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("models", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--freeze-commit")
    args = parser.parse_args()
    print(json.dumps(predict(args) if args.phase == "predict" else score(args)))


if __name__ == "__main__":
    main()
