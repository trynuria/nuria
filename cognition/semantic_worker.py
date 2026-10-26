"""Bounded one-shot CPU encoding across explicitly configured Python runtimes.

Only question JSON crosses stdin. The interpreter and worker paths are host
configuration, never model actions. Isolated Python mode ignores user-site and
PYTHONPATH settings; the worker loads only this repository's explicit source.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

# Executed as a fixed file under ``python -I``. Restore only its own reviewed
# repository package path, not an inherited environment or user-site directory.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognition.semantic_encoder import (
    FrozenSemanticEncoder,
    _manifest,
    _verify_files,
    binding_from_manifest,
    digest,
)

VERSION = "nuria.semantic-worker.v1"
MAX_INPUT = 8192
MAX_OUTPUT = 65536
MAX_ERROR = 16384


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stop_owned(child):
    # A leader can exit while a descendant keeps its pipes open. Kill the
    # session's known process group even if poll() already reaped its leader.
    # Every worker is spawned with start_new_session=True.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=1)


def _exchange(command, payload, timeout, cwd):
    """Bound pipe input, stdout, stderr and wall time without buffering a flood."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in ("PATH", "TMPDIR", "LANG", "LC_ALL")
    }
    environment.update(
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        DO_NOT_TRACK="1",
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="2",
    )
    child = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    pending = memoryview(payload)
    started = time.monotonic()
    try:
        for stream, name, mode in (
            (child.stdin, "stdin", selectors.EVENT_WRITE),
            (child.stdout, "stdout", selectors.EVENT_READ),
            (child.stderr, "stderr", selectors.EVENT_READ),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, mode, name)
        while selector.get_map() or child.poll() is None:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Semantic worker wall allowance exhausted")
            if not selector.get_map():
                child.wait(timeout=remaining)
                break
            for key, _ in selector.select(min(remaining, 0.25)):
                stream, name = key.fileobj, key.data
                if name == "stdin":
                    try:
                        written = os.write(stream.fileno(), pending[:4096])
                    except BlockingIOError:
                        continue
                    pending = pending[written:]
                    if not pending:
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffers[name].extend(chunk)
                if len(buffers[name]) > (MAX_OUTPUT if name == "stdout" else MAX_ERROR):
                    raise ValueError(
                        "Semantic worker response exceeded its byte allowance"
                    )
        if child.returncode != 0:
            raise RuntimeError("Semantic worker did not complete successfully")
        return bytes(buffers["stdout"]), hashlib.sha256(buffers["stderr"]).hexdigest()
    finally:
        selector.close()
        _stop_owned(child)
        for stream in (child.stdin, child.stdout, child.stderr):
            if not stream.closed:
                stream.close()


class OneShotSemanticEncoder:
    """Question-only encoder using a pinned, isolated CPU worker interpreter.

    No worker is launched by construction. A host must durably reserve the job
    before ``observe``. Any failed or interrupted disclosure stops this instance;
    the host must retain the reservation instead of implicitly retrying it.
    """

    def __init__(
        self,
        python_executable,
        model_directory,
        manifest_path,
        *,
        max_calls=12,
        timeout_seconds=30,
        worker_file=None,
        worker_sha256=None,
    ):
        _require(
            type(timeout_seconds) is int and 1 <= timeout_seconds <= 30,
            "Semantic worker time bound is invalid",
        )
        # Keep the configured venv entry path for Python's prefix detection.
        # Hashing follows its target, but executing the resolved base binary
        # would silently discard the separately provisioned CPU environment.
        self.python = Path(python_executable).absolute()
        self.worker = Path(worker_file or __file__).resolve()
        _require(
            self.python.is_file() and self.worker.is_file(),
            "Explicit semantic runtime files are required",
        )
        self.manifest_path = Path(manifest_path).resolve()
        self.directory = Path(model_directory).resolve()
        self.encoder_binding = binding_from_manifest(self.manifest_path, max_calls)
        self.encoder_binding_sha256 = digest(self.encoder_binding)
        self._manifest = _manifest(self.manifest_path)
        _verify_files(self.directory, self._manifest)
        self.worker_sha256, self.python_sha256 = _sha(self.worker), _sha(self.python)
        _require(
            worker_sha256 is None or worker_sha256 == self.worker_sha256,
            "Pinned semantic worker identity differs",
        )
        self.timeout, self.max_calls, self.calls = timeout_seconds, max_calls, 0
        self.failed = False
        self._lock = threading.Lock()
        self._binding = {
            "schema": VERSION,
            "dimensions": 384,
            "encoder_binding": self.encoder_binding,
            "encoder_binding_sha256": self.encoder_binding_sha256,
            "worker_sha256": self.worker_sha256,
            "interpreter_sha256": self.python_sha256,
            "max_calls": max_calls,
            "timeout_seconds": timeout_seconds,
            "maximum_input_bytes": MAX_INPUT,
            "maximum_output_bytes": MAX_OUTPUT,
            "python_isolated_mode": True,
            "question_transport": "JSON stdin only",
        }
        self._binding_sha256 = digest(self._binding)

    @property
    def binding(self):
        return copy.deepcopy(self._binding)

    @property
    def binding_sha256(self):
        return self._binding_sha256

    def _verify(self):
        _require(
            type(self.timeout) is int
            and self.timeout == self._binding["timeout_seconds"]
            and self.max_calls == self._binding["max_calls"]
            and digest(self._binding) == self.binding_sha256
            and _sha(self.worker) == self.worker_sha256
            and _sha(self.python) == self.python_sha256
            and binding_from_manifest(self.manifest_path, self.max_calls)
            == self.encoder_binding,
            "Semantic worker, interpreter or model manifest changed",
        )
        _verify_files(self.directory, self._manifest)

    def observe(self, question):
        _require(
            type(question) is str and 0 < len(question.encode()) <= 5000,
            "Semantic question exceeds its byte bound",
        )
        _require(
            self._lock.acquire(blocking=False),
            "Semantic encoder already owns an in-flight request",
        )
        try:
            _require(
                not self.failed and self.calls < self.max_calls,
                "Semantic worker requires explicit reconciliation or a new reviewed allowance",
            )
            self._verify()
            payload = json.dumps({"question": question}, ensure_ascii=False).encode()
            _require(
                len(payload) <= MAX_INPUT,
                "Semantic worker input envelope exceeded its bound",
            )
            self.calls += 1
            started = time.monotonic()
            command = [
                str(self.python),
                "-I",
                "-B",
                "-u",
                str(self.worker),
                "--model-directory",
                str(self.directory),
                "--manifest",
                str(self.manifest_path),
                "--max-calls",
                str(self.max_calls),
            ]
            raw, error_sha = _exchange(
                command, payload, self.timeout, self.worker.parent.parent
            )
            packet = json.loads(raw)
            _require(
                type(packet) is dict and set(packet) == {"vector", "receipt"},
                "Semantic worker output envelope differs",
            )
            _require(
                type(packet["vector"]) is list
                and all(type(item) in (int, float) for item in packet["vector"]),
                "Semantic worker vector must contain explicit JSON numbers",
            )
            vector = np.asarray(packet["vector"], dtype="<f4")
            _require(
                vector.shape == (384,)
                and np.isfinite(vector).all()
                and np.isclose(np.linalg.norm(vector), 1, rtol=0, atol=1e-4),
                "Semantic worker returned an invalid normalized vector",
            )
            original = packet["receipt"]
            text_sha, vector_sha = (
                hashlib.sha256(question.encode()).hexdigest(),
                hashlib.sha256(vector.tobytes()).hexdigest(),
            )
            _require(
                type(original) is dict
                and original.get("schema") == "nuria.semantic-observation.v1"
                and original.get("binding_sha256") == self.encoder_binding_sha256
                and original.get("text_sha256") == text_sha
                and original.get("vector_float32_sha256") == vector_sha
                and original.get("dimensions") == 384
                and original.get("truncated") is False
                and type(original.get("call")) is int
                and original["call"] == 1
                and type(original.get("input_bytes")) is int
                and original["input_bytes"] == len(question.encode())
                and type(original.get("wordpieces_used")) is int
                and 1 <= original["wordpieces_used"] <= 256
                and type(original.get("wordpieces_before_truncation")) is int
                and original["wordpieces_before_truncation"]
                == original["wordpieces_used"]
                and type(original.get("wall_seconds")) in (int, float)
                and math.isfinite(original["wall_seconds"])
                and original["wall_seconds"] >= 0
                and original.get("receipt_sha256")
                == digest({k: v for k, v in original.items() if k != "receipt_sha256"}),
                "Semantic worker observation is not bound to this text and model",
            )
            self._verify()
            receipt = {
                "schema": "nuria.semantic-observation.v1",
                "binding_sha256": self.binding_sha256,
                "text_sha256": text_sha,
                "vector_float32_sha256": vector_sha,
                "dimensions": 384,
                "wordpieces_used": original["wordpieces_used"],
                "truncated": False,
                "underlying_observation": original,
                "worker_stdout_sha256": hashlib.sha256(raw).hexdigest(),
                "worker_stderr_sha256": error_sha,
                "worker_sha256": self.worker_sha256,
                "interpreter_sha256": self.python_sha256,
                "call": self.calls,
                "wall_seconds": time.monotonic() - started,
                "scope": "One completed pinned CPU worker observation; local provenance is not remote weight attestation",
            }
            receipt["receipt_sha256"] = digest(receipt)
            return {"vector": vector.tolist(), "receipt": receipt}
        except BaseException:
            self.failed = True
            raise
        finally:
            self._lock.release()


def worker_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-directory", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--max-calls", type=int, default=12)
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    _require(len(raw) <= MAX_INPUT, "Semantic worker input byte bound exceeded")
    payload = json.loads(raw)
    _require(
        type(payload) is dict
        and set(payload) == {"question"}
        and type(payload["question"]) is str
        and 0 < len(payload["question"].encode()) <= 5000,
        "Semantic worker accepts one bounded question only",
    )
    os.nice(10)
    encoder = FrozenSemanticEncoder(
        args.model_directory, args.manifest, max_calls=args.max_calls
    )
    packet = encoder.observe(payload["question"])
    output = json.dumps(packet, allow_nan=False).encode()
    _require(len(output) <= MAX_OUTPUT, "Semantic worker output byte bound exceeded")
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(worker_main())
    except Exception as error:
        print(
            json.dumps(
                {"error_type": type(error).__name__, "status": "semantic_worker_failed"}
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
