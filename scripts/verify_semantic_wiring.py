"""Check published semantic vectors and neural frames without model inference.

This checks the consistency of retained observations. It does not externally
attest the original execution, restore a binary checkpoint or measure task gains.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from cognition.semantic_encoder import digest, semantic_currents, semantic_projection


def require(value, message):
    if not value:
        raise ValueError(message)


def verify(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "evidence-manifest.json").read_bytes())
    captured = {}
    for item in manifest["files"]:
        path = (directory / item["path"]).resolve()
        require(
            path.is_relative_to(directory) and not path.is_symlink(),
            "Evidence path escapes its bundle",
        )
        require(
            path.stat().st_size <= 2 * 1024**2, "Evidence compressed bound exceeded"
        )
        raw = path.read_bytes()
        require(
            hashlib.sha256(raw).hexdigest() == item["sha256"],
            "Evidence file checksum differs",
        )
        if item["gzip"]:
            with gzip.open(path, "rb") as stream:
                raw = stream.read(8 * 1024**2 + 1)
        require(
            len(raw) <= 8 * 1024**2
            and hashlib.sha256(raw).hexdigest() == item["uncompressed_sha256"],
            "Evidence payload checksum or bound differs",
        )
        require(item["path"] not in captured, "Duplicate evidence path")
        captured[item["path"]] = raw

    def load(name):
        return json.loads(captured["evidence/" + name])

    protocol = load("text-protocol.json")
    output = load("semantic-output.json.gz")
    summary = load("semantic-result.json")
    brian = load("brian-result.json")
    model = load("prototype-model-manifest.json")
    require(
        summary["output_sha256"]
        == brian["semantic_output_sha256"]
        == hashlib.sha256(captured["evidence/semantic-output.json.gz"]).hexdigest()
        and summary["protocol_sha256"]
        == hashlib.sha256(captured["evidence/text-protocol.json"]).hexdigest(),
        "Recorded summaries bind different observations or protocol",
    )
    require(
        len(protocol["texts"]) == 12
        and len(protocol["comparisons"]) == 6
        and summary["model_calls"] == 12,
        "Twelve-text denominator changed",
    )
    texts = {row["id"]: row["text"] for row in protocol["texts"]}
    require(
        len(texts) == 12
        and set(texts) == set(output["vectors"]) == set(output["currents"]),
        "Missing or duplicate semantic observation",
    )
    receipts = {row["text_id"]: row["observation"] for row in output["receipts"]}
    require(
        len(output["receipts"]) == len(receipts) == 12 and set(receipts) == set(texts),
        "Observation receipt denominator changed",
    )
    matrix = semantic_projection()
    require(
        [row["observation"]["call"] for row in output["receipts"]]
        == list(range(1, 13)),
        "Recorded encoder calls are missing, duplicated or reordered",
    )
    for key, values in output["vectors"].items():
        vector = np.asarray(values, dtype="<f4")
        require(
            vector.shape == (384,)
            and np.isfinite(vector).all()
            and np.isclose(np.linalg.norm(vector), 1, atol=1e-4),
            "Recorded vector is not finite and normalized",
        )
        receipt = receipts[key]
        require(
            receipt["model"] == model["model"]
            and receipt["revision"] == model["revision"]
            and receipt["model_file_sha256"] == model["model_file"]["sha256"]
            and receipt["tokenizer_git_blob_sha1"]
            == model["tokenizer_file"]["git_blob_sha1"]
            and receipt["input_bytes"] == len(texts[key].encode())
            and receipt["truncated"] is False,
            "Recorded model, tokenizer or input binding differs",
        )
        require(
            receipt["text_sha256"] == hashlib.sha256(texts[key].encode()).hexdigest()
            and receipt["vector_float32_sha256"]
            == hashlib.sha256(vector.tobytes()).hexdigest()
            and receipt["receipt_sha256"]
            == digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}),
            "Recorded observation receipt differs",
        )
        require(
            semantic_currents(vector, matrix) == output["currents"][key],
            "Recorded currents do not reproduce",
        )
    for comparison in protocol["comparisons"]:
        anchor = np.asarray(output["vectors"][comparison["anchor"]])

        def cosine(key):
            return float(
                np.dot(anchor, output["vectors"][key])
                / (np.linalg.norm(anchor) * np.linalg.norm(output["vectors"][key]))
            )

        require(
            cosine(comparison["paraphrase"]) > cosine(comparison["unrelated"]),
            "Predeclared semantic contrast failed",
        )
    windows = [
        json.loads(line)
        for line in captured["evidence/brian-windows.jsonl.gz"].splitlines()
    ]
    require(
        len(windows) == brian["actual_windows"] == 4
        and [row["label"] for row in windows] == protocol["brian_probe_inputs"],
        "Neural-window denominator changed",
    )
    for window in windows:
        frame = window["frame"]
        require(
            window["input_sha256"] == digest(output["currents"][window["text_id"]]),
            "Neural input does not bind its semantic observation",
        )
        require(
            frame["window_ms"] == 100
            and frame["before_state_sha256"] == window["matched_initial_state_sha256"],
            "Neural initial state or time window differs",
        )
        require(
            len(frame["spikes"]) == frame["spike_count"]
            and digest(frame["spikes"]) == frame["spikes_sha256"],
            "Full retained spike record does not reproduce its hash",
        )
    first, last = windows[0], windows[-1]
    require(
        first["origin"] != last["origin"]
        and first["input_sha256"] == last["input_sha256"]
        and first["frame"]["spikes"] == last["frame"]["spikes"]
        and first["frame"]["after_state_sha256"] == last["frame"]["after_state_sha256"],
        "Provenance invariance did not hold",
    )
    require(
        len({row["matched_initial_state_sha256"] for row in windows}) == 1
        and first["frame"]["spikes_sha256"] != windows[2]["frame"]["spikes_sha256"],
        "Matched-state semantic intervention did not change spike records",
    )
    return {
        "verified": True,
        "recorded_encodes": 12,
        "semantic_contrasts": 6,
        "recorded_neural_windows": 4,
        "new_model_calls": 0,
        "scope": "Retained vector/current/receipt/spike consistency; no independent execution attestation or useful-decision advantage",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "research/semantic-input",
    )
    print(json.dumps(verify(parser.parse_args(argv).directory), indent=2))


if __name__ == "__main__":
    main()
