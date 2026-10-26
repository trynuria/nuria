"""Optional fixed CPU semantics and a bounded, identity-independent neural input.

Only question text enters the encoder. Model and source identities belong to
receipts and recovery guards; none is mixed into the semantic current geometry.
Weights stay outside source control, and imports never download or load a model.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

import numpy as np

VERSION = "nuria.semantic-encoder.v1"
OBSERVATION = "nuria.semantic-observation.v1"
MAX_MODEL_BYTES = 500 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_runtime():
    import onnxruntime
    import tokenizers

    return onnxruntime, tokenizers.Tokenizer, tokenizers.__version__


def _manifest(path):
    _require(
        path.is_file() and path.stat().st_size <= 65536,
        "Semantic manifest is missing or oversized",
    )
    manifest = json.loads(path.read_bytes())
    _require(
        manifest.get("schema") == VERSION
        and manifest.get("dimensions") == 384
        and manifest.get("maximum_input_bytes") == 5000
        and manifest.get("maximum_wordpieces") == 256
        and manifest.get("pooling") == "attention-mask mean then L2"
        and type(manifest.get("model")) is str
        and 0 < len(manifest["model"].encode()) <= 256
        and type(manifest.get("revision")) is str
        and REVISION.fullmatch(manifest["revision"]) is not None,
        "Semantic manifest contract differs",
    )
    return manifest


def binding_from_manifest(manifest_path, max_calls=12):
    """Describe the exact expected local binding without loading a CPU runtime.

    This validates metadata only. The executing encoder must still verify local
    weights and tokenizer bytes, installed versions and provider configuration.
    """
    _require(
        type(max_calls) is int and 1 <= max_calls <= 4096,
        "Semantic call ceiling is invalid",
    )
    path = Path(manifest_path).resolve()
    manifest = _manifest(path)
    runtime = manifest.get("runtime_versions")
    _require(
        type(runtime) is dict
        and set(runtime) == {"onnxruntime", "tokenizers", "numpy"}
        and all(
            type(value) is str and 0 < len(value.encode()) <= 64
            for value in runtime.values()
        ),
        "Semantic runtime version metadata is malformed",
    )
    for key, identity, pattern in (
        ("model_file", "sha256", HASH),
        ("tokenizer_file", "git_blob_sha1", REVISION),
    ):
        record = manifest.get(key)
        _require(
            type(record) is dict
            and type(record.get("local_path")) is str
            and type(record.get("bytes")) is int
            and 0 < record["bytes"] <= MAX_MODEL_BYTES
            and type(record.get(identity)) is str
            and pattern.fullmatch(record[identity]) is not None,
            "Semantic file identity metadata is malformed",
        )
    return {
        "schema": VERSION,
        "model": manifest["model"],
        "revision": manifest["revision"],
        "model_file_sha256": manifest["model_file"]["sha256"],
        "tokenizer_git_blob_sha1": manifest["tokenizer_file"]["git_blob_sha1"],
        "manifest_sha256": _sha(path),
        "implementation_sha256": _sha(Path(__file__)),
        "dimensions": 384,
        "maximum_input_bytes": 5000,
        "maximum_wordpieces": 256,
        "truncation": "refuse",
        "pooling": "attention-mask mean then L2",
        "max_calls": max_calls,
        "runtime": dict(runtime),
        "providers": ["CPUExecutionProvider"],
        "intra_op_threads": 2,
        "inter_op_threads": 1,
        "execution_mode": "sequential",
    }


def _verify_files(directory, manifest):
    paths = {}
    for key in ("model_file", "tokenizer_file"):
        record = manifest[key]
        _require(
            type(record.get("local_path")) is str
            and type(record.get("bytes")) is int
            and 0 < record["bytes"] <= MAX_MODEL_BYTES,
            "Semantic file contract is malformed",
        )
        path = (directory / record["local_path"]).resolve()
        _require(
            path.is_relative_to(directory.resolve())
            and path.is_file()
            and path.stat().st_size == record["bytes"],
            "Pinned semantic file identity differs",
        )
        raw = path.read_bytes()
        if key == "model_file":
            _require(
                type(record.get("sha256")) is str
                and HASH.fullmatch(record["sha256"]) is not None
                and hashlib.sha256(raw).hexdigest() == record["sha256"],
                "Semantic weight checksum differs",
            )
        else:
            expected = record.get("git_blob_sha1")
            _require(
                type(expected) is str
                and REVISION.fullmatch(expected) is not None
                and hashlib.sha1(
                    b"blob " + str(len(raw)).encode() + b"\0" + raw
                ).hexdigest()
                == expected,
                "Semantic tokenizer checksum differs",
            )
        paths[key] = path
    return paths


def mean_pool(token_embeddings, attention_mask):
    """Mean only visible tokens, then normalize the fixed 384-dimensional vector."""
    values = np.asarray(token_embeddings, dtype=np.float32)
    mask = np.asarray(attention_mask, dtype=np.float32)
    _require(
        values.ndim == 3
        and values.shape[0] == 1
        and values.shape[2] == 384
        and mask.shape == values.shape[:2]
        and np.isfinite(values).all()
        and np.isfinite(mask).all()
        and np.isin(mask, [0, 1]).all()
        and float(mask.sum()) >= 1,
        "Semantic output or attention mask differs",
    )
    pooled = (values * mask[:, :, None]).sum(axis=1)[0] / mask.sum()
    norm = float(np.linalg.norm(pooled))
    _require(math.isfinite(norm) and norm > 1e-12, "Semantic output is degenerate")
    return (pooled / norm).astype("<f4")


class FrozenSemanticEncoder:
    """One local, CPU-only ONNX model with no remote fallback or implicit retry.

    ``max_calls`` bounds this process instance, not a durable allowance. A host
    must reserve each job before calling ``observe`` and retain ambiguous work
    across process restarts. A runtime failure stops this instance.
    """

    def __init__(self, model_directory, manifest_path, max_calls=12):
        _require(
            type(max_calls) is int and 1 <= max_calls <= 4096,
            "Semantic call ceiling is invalid",
        )
        self.directory = Path(model_directory).resolve()
        self.manifest_path = Path(manifest_path).resolve()
        self._manifest = _manifest(self.manifest_path)
        paths = _verify_files(self.directory, self._manifest)
        self._manifest_sha = _sha(self.manifest_path)
        self._implementation_sha = _sha(Path(__file__))
        ort, tokenizer_class, tokenizer_version = _load_runtime()
        runtime = {
            "onnxruntime": ort.__version__,
            "tokenizers": tokenizer_version,
            "numpy": np.__version__,
        }
        _require(
            runtime == self._manifest["runtime_versions"],
            "Semantic runtime versions differ from their manifest",
        )
        self.tokenizer = tokenizer_class.from_file(str(paths["tokenizer_file"]))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(
            str(paths["model_file"]),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        _require(
            self.session.get_providers() == ["CPUExecutionProvider"],
            "Semantic runtime is not CPU-only",
        )
        self.inputs = {item.name: item.type for item in self.session.get_inputs()}
        _require(
            set(self.inputs) <= {"input_ids", "attention_mask", "token_type_ids"}
            and {"input_ids", "attention_mask"} <= set(self.inputs)
            and all(value == "tensor(int64)" for value in self.inputs.values()),
            "Semantic ONNX input contract differs",
        )
        outputs = self.session.get_outputs()
        _require(len(outputs) == 1, "Semantic ONNX output contract differs")
        self.output = outputs[0].name
        self.max_calls, self.calls, self.failed = max_calls, 0, False
        self._lock = threading.Lock()
        binding = binding_from_manifest(self.manifest_path, max_calls)
        _require(
            binding["manifest_sha256"] == self._manifest_sha
            and binding["implementation_sha256"] == self._implementation_sha
            and binding["runtime"] == runtime,
            "Semantic binding changed during runtime loading",
        )
        self._binding = canonical(binding)
        self._binding_sha256 = digest(self.binding)

    @property
    def binding(self):
        return json.loads(self._binding)

    @property
    def binding_sha256(self):
        return self._binding_sha256

    def _verify(self):
        _require(
            type(self.max_calls) is int
            and self.max_calls == self.binding["max_calls"]
            and type(self.calls) is int
            and 0 <= self.calls <= self.max_calls
            and digest(self.binding) == self.binding_sha256
            and _sha(self.manifest_path) == self._manifest_sha
            and _sha(Path(__file__)) == self._implementation_sha,
            "Semantic implementation or manifest changed",
        )
        _verify_files(self.directory, self._manifest)

    def observe(self, question):
        """Encode exactly the supplied question, refusing invisible truncation."""
        _require(
            type(question) is str and 0 < len(question.encode()) <= 5000,
            "Question exceeds the semantic text bound",
        )
        with self._lock:
            _require(
                not self.failed and self.calls < self.max_calls,
                "Semantic runtime is stopped or its call ceiling is exhausted",
            )
            self._verify()
            encoded = self.tokenizer.encode(question, add_special_tokens=True)
            count = len(encoded.ids)
            _require(
                0 < count <= 256,
                "Question exceeds the semantic wordpiece bound; truncation refused",
            )
            supplied = {
                "input_ids": np.asarray([encoded.ids], dtype=np.int64),
                "attention_mask": np.asarray([encoded.attention_mask], dtype=np.int64),
                "token_type_ids": np.asarray([encoded.type_ids], dtype=np.int64),
            }
            _require(
                all(value.shape == (1, count) for value in supplied.values()),
                "Semantic tokenizer arrays differ",
            )
            self.calls += 1
            started = time.monotonic()
            try:
                raw = self.session.run(
                    [self.output], {key: supplied[key] for key in self.inputs}
                )[0]
                vector = mean_pool(raw, supplied["attention_mask"])
                self._verify()
            except BaseException:
                self.failed = True
                raise
            receipt = {
                "schema": OBSERVATION,
                "call": self.calls,
                "binding_sha256": self.binding_sha256,
                "text_sha256": hashlib.sha256(question.encode()).hexdigest(),
                "input_bytes": len(question.encode()),
                "wordpieces_before_truncation": count,
                "wordpieces_used": count,
                "truncated": False,
                "dimensions": 384,
                "vector_float32_sha256": hashlib.sha256(vector.tobytes()).hexdigest(),
                "wall_seconds": time.monotonic() - started,
                "scope": "Frozen pretrained semantic observation; not language newly learned by the spiking model",
            }
            receipt["receipt_sha256"] = digest(receipt)
            return {"vector": vector.tolist(), "receipt": receipt}


def semantic_projection():
    """A fixed projection independent of tasks, identities, sources and labels."""
    rng = np.random.Generator(np.random.PCG64(1729))
    return (
        (rng.integers(0, 2, size=(64, 384), dtype=np.int8) * 2 - 1) / math.sqrt(64)
    ).astype("<f4")


def semantic_currents(vector, matrix):
    """Preserve projected signs through separate positive/negative input channels."""
    vector, matrix = (
        np.asarray(vector, dtype=np.float32),
        np.asarray(matrix, dtype=np.float32),
    )
    _require(
        vector.shape == (384,)
        and matrix.shape == (64, 384)
        and np.isfinite(vector).all()
        and np.isfinite(matrix).all(),
        "Semantic dimensions or finite values differ",
    )
    norm = float(np.linalg.norm(vector))
    _require(math.isfinite(norm) and norm > 1e-12, "Semantic vector is degenerate")
    projected = matrix @ (vector / norm)
    norm = float(np.linalg.norm(projected))
    _require(math.isfinite(norm) and norm > 1e-12, "Semantic projection is degenerate")
    signed = np.tanh(4 * projected / norm)
    drive = np.zeros(1024, dtype=np.float64)
    drive[:64] = 0.18 * np.maximum(signed, 0)
    drive[64:128] = 0.18 * np.maximum(-signed, 0)
    memory = (0.5 + 0.5 * signed).astype(np.float64)
    _require(
        np.isfinite(drive).all()
        and abs(drive).max() <= 0.2
        and np.isfinite(memory).all()
        and memory.min() >= 0
        and memory.max() <= 1,
        "Semantic currents exceed circuit bounds",
    )
    return {
        "regional_drive": drive.tolist(),
        "context": {"memory_features": memory.tolist(), "salience": 1.0, "energy": 1.0},
    }
