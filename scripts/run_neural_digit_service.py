"""Private inference CLI and durable reproduction of the fixed digit specialist.

Caller-input requests accept one already segmented 8 by 8 digit, with no claim
of dataset membership. The separate reproduction command accepts only the
original upstream archive and all 1,797 official test rows. It is a reproduction,
not another unseen experiment. Both modes are inference-only: no model training,
network listener, external request, wallet key or payment authority is present.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import secrets
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cognition import digit_service
from cognition.digit_service import DigitService, digest, model_digest, pixels

ROOT = Path(__file__).resolve().parents[1]
VERSION = "nuria.digit-service-runner.v1"
PROTOCOL_SHA256 = "024d75219d4c8de71e3ff2f308b5f497a2262b2f24d0fdd28ca03ab38bf20fd2"
ARCHIVE_SHA256 = "0d7b054fea010270e9b3f06411c654c5e59547732ad626381980baffe0a23fb0"
MANIFEST_SHA256 = "6c847cabe427f5ad8e26edfa8ea86f9b5f8d219049845fa566f822cf6522576f"
MODEL_ID = "snn_trained-202611"
MODEL_SHA256 = "acdb30bc6394ba1261a2b61ec195c01654c6c0782bd1191f138d798aaffab2c0"
STATE_SHA256 = "d722dc54fd45fe72b6b0fa7abd39654693ce786d27846d4a172b0292de1f19d2"
ROWS = 1797
MEMBER = "optdigits.tes"
MEMBER_BYTES = 264712
MEMBER_CRC32 = 784219063
SOURCE = "nuria:optdigits-official-test-snn202611-v1"
CALLER_SOURCE = "nuria:segmented-digit-inference-snn202611-v1"
INPUT_CONTRACT = {
    "schema": "nuria.segmented-digit-inputs.v1",
    "pixels": 64,
    "minimum": 0,
    "maximum": 16,
    "membership": "Unverified caller inputs; this identity commits the input contract, not a dataset.",
}
MAX_JSON = 16 * 1024 * 1024
MAX_REQUEST = 16 * 1024


def read_json(path):
    path = Path(path)
    if not path.exists() and Path(str(path) + ".gz").exists():
        path = Path(str(path) + ".gz")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("Reviewed digit JSON exceeds its size bound")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def write_new(path, value):
    path = Path(path)
    with path.open("x") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _reviewed_path(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("A reviewed model path must remain inside its directory")
    candidate = Path(root)
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("Reviewed digit artifacts cannot traverse symlinks")
    if Path(root).resolve() not in candidate.resolve().parents:
        raise ValueError("A reviewed digit artifact escapes its directory")
    return candidate


def load_model(protocol_path, model_directory):
    protocol, protocol_sha256 = read_json(protocol_path)
    if protocol_sha256 != PROTOCOL_SHA256:
        raise ValueError("Only the exact frozen digit protocol is supported")
    if (
        protocol["data"]["archive_sha256"] != ARCHIVE_SHA256
        or protocol["data"]["test_rows"] != ROWS
        or protocol["data"]["test_member"] != MEMBER
        or protocol["training_manifest_sha256"] != MANIFEST_SHA256
    ):
        raise ValueError("Frozen digit data identities differ from the runner")
    for name, expected in protocol["source_hashes"].items():
        path = _reviewed_path(ROOT, name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("A frozen numerical digit implementation changed: " + name)
    manifest, manifest_sha256 = read_json(Path(model_directory) / "manifest.json")
    if manifest_sha256 != MANIFEST_SHA256:
        raise ValueError("The frozen model manifest changed")
    entries = [item for item in manifest["models"] if item["id"] == MODEL_ID]
    if len(entries) != 1:
        raise ValueError("The fixed first-seed neural model is not uniquely registered")
    entry = entries[0]
    checkpoint, checkpoint_sha256 = read_json(
        _reviewed_path(model_directory, entry["path"])
    )
    if (
        checkpoint_sha256 != entry["file_sha256"]
        or checkpoint.get("schema") != "nuria.digit-checkpoint.v1"
        or checkpoint.get("method") != "snn_trained"
        or entry["seed"] != 202611
        or entry["kind"] != "snn"
        or entry["model_sha256"] != MODEL_SHA256
        or entry["state_sha256"] != STATE_SHA256
        or checkpoint["model_sha256"] != MODEL_SHA256
        or checkpoint["state_sha256"] != STATE_SHA256
        or digest(checkpoint["model"]) != STATE_SHA256
        or model_digest(checkpoint["model"]) != MODEL_SHA256
        or checkpoint["weights"] != checkpoint["model"]["weights"]
        or checkpoint["model"]["configuration"] != entry["configuration"]
    ):
        raise ValueError("The digit specialist differs from its reviewed checkpoint")
    return (
        protocol,
        checkpoint,
        {
            "protocol_sha256": protocol_sha256,
            "training_manifest_sha256": manifest_sha256,
            "checkpoint_file_sha256": checkpoint_sha256,
            "model_id": MODEL_ID,
            "model_sha256": MODEL_SHA256,
            "state_sha256": STATE_SHA256,
            "seed": 202611,
        },
    )


def _official_body(archive):
    with Path(archive).open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA256:
        raise ValueError(
            "Only the original hash-pinned optical-digit archive is accepted"
        )
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        info = source.getinfo(MEMBER)
        if info.file_size != MEMBER_BYTES or info.CRC != MEMBER_CRC32:
            raise ValueError("The official test archive member differs")
        body = source.read(MEMBER)
    rows = list(csv.reader(io.StringIO(body.decode("ascii"))))
    if len(rows) != ROWS or any(len(row) != 65 for row in rows):
        raise ValueError(
            "All official test rows, in their original order, are required"
        )
    return rows


def load_inputs(archive):
    # Target strings are neither parsed nor passed to the model here.
    return [
        pixels([int(value) for value in row[:64]]) for row in _official_body(archive)
    ]


def load_targets(archive):
    labels = [int(row[64]) for row in _official_body(archive)]
    if any(not 0 <= label <= 9 for label in labels):
        raise ValueError("The official digit target is out of range")
    return labels


def _terms(identity, mode):
    return {
        "schema": VERSION,
        "mode": mode,
        **identity,
        "source_id": SOURCE if mode == "official_reproduction" else CALLER_SOURCE,
        "input_identity": ARCHIVE_SHA256
        if mode == "official_reproduction"
        else digest(INPUT_CONTRACT),
        "input_identity_basis": "Original upstream archive"
        if mode == "official_reproduction"
        else INPUT_CONTRACT["membership"],
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "service_sha256": hashlib.sha256(
            Path(digit_service.__file__).read_bytes()
        ).hexdigest(),
        "training_enabled": False,
        "wallet_signing_authority": False,
        "financial_transactions": 0,
        "external_requests": 0,
    }


def _prepare(directory, terms):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = directory / "DIGIT-RUNNER.json"
    if marker.exists():
        existing, _ = read_json(marker)
        if existing != terms:
            raise ValueError(
                "Existing digit runner history has another mode or reviewed identity"
            )
    else:
        if any(directory.iterdir()):
            raise ValueError(
                "An unmarked directory cannot become a digit runner history"
            )
        write_new(marker, terms)
    return directory


def _service(directory, checkpoint, terms):
    return DigitService(
        directory / "history",
        terms["source_id"],
        checkpoint["model"],
        dataset_sha256=terms["input_identity"],
        partition="qualification",
        training_enabled=False,
    )


def _membership(service, inputs):
    expected = [f"optdigits-test-{index:04d}" for index in range(ROWS)]
    requests = service.db.execute("SELECT * FROM requests ORDER BY id").fetchall()
    decisions = [
        json.loads(row[0])
        for row in service.db.execute(
            "SELECT payload FROM journal WHERE kind='decision' ORDER BY sequence"
        )
    ]
    if [row["request_id"] for row in decisions] != expected[: len(requests)] or len(
        requests
    ) > ROWS:
        raise ValueError(
            "Digit reproduction membership is not the original ordered prefix"
        )
    for index, row in enumerate(requests):
        if (
            row["id"] != expected[index]
            or row["input_sha256"] != digest(inputs[index])
            or row["purpose"] != "inference"
            or row["label_commitment"] is not None
        ):
            raise ValueError(
                "A saved digit request is not the corresponding official input"
            )
    counts = service.summary()
    if (
        counts["training"]
        or counts["revision"]
        or counts["state_sha256"] != STATE_SHA256
    ):
        raise ValueError("The qualification model or optimizer changed")
    if counts["evaluations"] and len(requests) != ROWS:
        raise ValueError(
            "Targets were compared before all digit predictions were committed"
        )
    last_decision = service.db.execute(
        "SELECT sequence FROM journal WHERE kind='decision' ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    first_comparison = service.db.execute(
        "SELECT sequence FROM journal WHERE kind='comparison' ORDER BY sequence LIMIT 1"
    ).fetchone()
    if first_comparison and (
        not last_decision or first_comparison[0] <= last_decision[0]
    ):
        raise ValueError(
            "The digit journal interleaves target comparisons with predictions"
        )
    return counts


def reproduce(protocol_path, archive, model_directory, directory, *, progress=None):
    """Reproduce every official example through separate durable request commits."""
    started = time.process_time()
    _, checkpoint, identity = load_model(protocol_path, model_directory)
    inputs = load_inputs(archive)
    terms = _terms(identity, "official_reproduction")
    directory = _prepare(directory, terms)
    with _service(directory, checkpoint, terms) as service:
        before = _membership(service, inputs)
        answers = []
        for index, values in enumerate(inputs):
            answers.append(service.submit(f"optdigits-test-{index:04d}", values))
            if progress and (index + 1) % 256 == 0:
                progress(
                    {"phase": "predictions", "committed": index + 1, "required": ROWS}
                )
        prediction_state = _membership(service, inputs)
        if prediction_state["requests"] != ROWS:
            raise ValueError("Incomplete digit predictions cannot reach label scoring")
        last_decision = service.db.execute(
            "SELECT sequence,sha256 FROM journal WHERE kind='decision' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        # Only now can official target integers reach the buyer comparison path.
        targets = load_targets(archive)
        comparisons = [
            service.evaluate(answer["request_id"], label)
            for answer, label in zip(answers, targets, strict=True)
        ]
        after = _membership(service, inputs)
        if (
            after["requests"] != ROWS
            or after["evaluations"] != ROWS
            or after["training"]
            or after["revision"]
        ):
            raise ValueError(
                "The complete inference-only digit denominator is required"
            )
        correct = sum(result["correct"] for result in comparisons)
        receipt = {
            **terms,
            "schema": "nuria.digit-service-reproduction.v1",
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "terms_sha256": digest(terms),
            "rows": ROWS,
            "correct": correct,
            "incorrect": ROWS - correct,
            "accuracy": correct / ROWS,
            "existing_requests_at_start": before["requests"],
            "existing_comparisons_at_start": before["evaluations"],
            "all_predictions_committed_before_target_parsing": True,
            "prediction_commitment_sha256": digest(
                [answer["sha256"] for answer in answers]
            ),
            "predicted_labels_sha256": digest(
                [answer["prediction"]["label"] for answer in answers]
            ),
            "targets_sha256": digest(targets),
            "last_prediction_event": {
                "sequence": last_decision[0],
                "sha256": last_decision[1],
            },
            "before_state_sha256": STATE_SHA256,
            "after_state_sha256": after["state_sha256"],
            "journal": after,
            "attempt_cpu_seconds": time.process_time() - started,
            "scope": "Fixed first-seed reproduction of an already inspected official digit set, through a private standalone service. Not a new unseen test, production customer work, original recurrent-circuit gain or commerce integration. Interrupted local computation may repeat; committed answers and comparisons are idempotent. This is a hash-bound local receipt, not an externally signed attestation.",
        }
        receipt["sha256"] = digest(receipt)
        receipts = directory / "receipts"
        receipts.mkdir(mode=0o700, exist_ok=True)
        path = receipts / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-"
            + secrets.token_hex(8)
            + ".json"
        )
        write_new(path, receipt)
        return {"receipt_path": str(path), "receipt": receipt}


def caller_request(
    protocol_path, model_directory, directory, request, *, compare=False
):
    """One caller-provided image or a separate compare-only buyer label."""
    expected = {"request_id", "label"} if compare else {"request_id", "pixels"}
    if type(request) is not dict or set(request) != expected:
        raise ValueError("Digit stdin fields do not match the selected command")
    if not compare:
        pixels(request["pixels"])
    _, checkpoint, identity = load_model(protocol_path, model_directory)
    terms = _terms(identity, "caller_inputs")
    directory = _prepare(directory, terms)
    with _service(directory, checkpoint, terms) as service:
        if compare:
            return service.evaluate(request["request_id"], request["label"])
        return service.submit(request["request_id"], request["pixels"])


def stdin_request():
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        raise ValueError("Digit stdin exceeds the bounded single-request size")
    return json.loads(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("predict", "compare", "reproduce"):
        command = commands.add_parser(name)
        command.add_argument("protocol")
        if name == "reproduce":
            command.add_argument("archive")
        command.add_argument("models")
        command.add_argument("state_directory")
    arguments = parser.parse_args(argv)
    if arguments.command == "reproduce":
        result = reproduce(
            arguments.protocol,
            arguments.archive,
            arguments.models,
            arguments.state_directory,
            progress=lambda value: print(
                json.dumps(value), file=sys.stderr, flush=True
            ),
        )
    else:
        result = caller_request(
            arguments.protocol,
            arguments.models,
            arguments.state_directory,
            stdin_request(),
            compare=arguments.command == "compare",
        )
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
