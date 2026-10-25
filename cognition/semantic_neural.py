"""Optional semantic observations and delayed outcomes around a separate Brian2 lab.

The inherited capsule store owns locking, immutable checkpoints and exact RNG
recovery. This subclass replaces the identity-texture input path completely.
Source and encoder identities bind evidence; none become a sensory feature.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import numpy as np

from cognition import semantic_encoder
from cognition.hybrid_neural import (
    STRATEGIES,
    HybridNeuralBridge,
    _file_hash,
    _hash,
    _sync,
    _text,
    _write,
    select_strategy,
    tool_features,
)
from cognition.procedural_memory import canonical, digest

VERSION = "nuria.semantic-neural.v1"
MAX_JOBS = 128
MAX_PENDING = 64


def _bounded_binding(encoder):
    value = copy.deepcopy(encoder.binding)
    if (
        type(value) is not dict
        or value.get("dimensions") != 384
        or len(canonical(value).encode()) > 16384
        or encoder.binding_sha256 != digest(value)
    ):
        raise ValueError("Semantic encoder requires a bounded immutable binding")
    return value


def _vector_observation(packet, question, binding_sha):
    if type(packet) is not dict or set(packet) != {"vector", "receipt"}:
        raise ValueError("Semantic observation has an unknown envelope")
    if type(packet["vector"]) is not list or not all(
        type(item) in (int, float) for item in packet["vector"]
    ):
        raise ValueError("Semantic observation requires explicit numeric values")
    vector = np.asarray(packet["vector"], dtype="<f4")
    if (
        vector.shape != (384,)
        or not np.isfinite(vector).all()
        or not np.isclose(np.linalg.norm(vector), 1, rtol=0, atol=1e-4)
    ):
        raise ValueError("Semantic observation must be a finite normalized 384-vector")
    receipt = copy.deepcopy(packet["receipt"])
    if (
        type(receipt) is not dict
        or receipt.get("schema") != "nuria.semantic-observation.v1"
        or receipt.get("binding_sha256") != binding_sha
        or receipt.get("text_sha256") != hashlib.sha256(question.encode()).hexdigest()
        or receipt.get("dimensions") != 384
        or receipt.get("vector_float32_sha256")
        != hashlib.sha256(vector.tobytes()).hexdigest()
        or receipt.get("receipt_sha256")
        != digest(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
    ):
        raise ValueError("Semantic receipt does not bind this question and vector")
    return vector, receipt


class SemanticNeuralBridge(HybridNeuralBridge):
    """A finite optional semantic-strategy lab, separate from existing histories.

    ``encoder`` is explicitly supplied by the host. Its observation method sees
    only the question. An interrupted observation leaves a durable reservation;
    reopening the lab does not authorize an implicit retry or a new request.
    The public app receives no model, wallet or source credential through this
    interface. Positive credit still requires a host-verified terminal outcome.
    """

    def __init__(
        self,
        directory,
        *,
        source_id,
        model_id,
        tool_version,
        encoder,
        mode="neural",
        window_ms=100,
    ):
        self.encoder = encoder
        self.encoder_binding = _bounded_binding(encoder)
        self.encoder_binding_sha256 = digest(self.encoder_binding)
        self.matrix = semantic_encoder.semantic_projection()
        if self.matrix.shape != (64, 384) or not np.isfinite(self.matrix).all():
            raise ValueError("Semantic projection dimensions differ")
        self.semantic_terms = {
            "schema": VERSION,
            "source_id": _text(source_id, 256),
            "model_id": _text(model_id, 512),
            "tool_version": _text(tool_version, 256),
            "encoder_binding": self.encoder_binding,
            "encoder_binding_sha256": self.encoder_binding_sha256,
            "projection_sha256": hashlib.sha256(
                np.asarray(self.matrix, dtype="<f4").tobytes()
            ).hexdigest(),
            "mapping": "fixed64projection,L2,tanh(4*z),signed128sensory,64memory",
            "implementation": {
                "semantic_neural.py": _file_hash(Path(__file__)),
                "semantic_encoder.py": _file_hash(Path(semantic_encoder.__file__)),
            },
            "maximum_jobs": MAX_JOBS,
            "scope": "Question and actual observed tool statistics only; provenance remains metadata",
        }
        self.semantic_terms_sha256 = digest(self.semantic_terms)
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("Semantic state requires a private real directory")
        marker = directory / "SEMANTIC.json"
        existing = (directory / "LAB.json").exists()
        if existing:
            for private in (directory, directory / "capsules", directory / "neural"):
                if (
                    private.is_symlink()
                    or not private.is_dir()
                    or private.stat().st_mode & 0o077
                ):
                    raise ValueError("Semantic state requires mode-0700 directories")
            if not marker.is_file() or marker.stat().st_size > 32768:
                raise ValueError("Semantic history has no bounded encoder identity")
            recorded = json.loads(marker.read_bytes())
            if recorded != {
                "terms": self.semantic_terms,
                "terms_sha256": self.semantic_terms_sha256,
            }:
                raise ValueError("Semantic source, encoder or implementation changed")
        # The base's internal source binding commits the full semantic contract.
        # This value never reaches semantic_projection, observe or neural input.
        super().__init__(
            directory,
            source_id="semantic:" + self.semantic_terms_sha256,
            model_id=model_id,
            tool_version=tool_version,
            mode=mode,
            window_ms=window_ms,
        )
        try:
            if not existing:
                # Fresh nested parents are created by the base store under the
                # private lab root. Tighten those exact new directories before
                # the first encoding; do not change an existing history's mode.
                for private in (directory / "capsules", directory / "neural"):
                    if private.is_symlink() or not private.is_dir():
                        raise ValueError("Semantic state requires real directories")
                    os.chmod(private, 0o700)
                _write(
                    marker,
                    canonical(
                        {
                            "terms": self.semantic_terms,
                            "terms_sha256": self.semantic_terms_sha256,
                        }
                    ).encode(),
                )
                _sync(directory)
        except BaseException:
            self.close()
            raise

    def _check_semantics(self):
        if self.failed or self.lock is None:
            raise RuntimeError("Reopen the interrupted semantic lab before continuing")
        if (
            digest(self.terms) != self.terms_sha256
            or digest(self.semantic_terms) != self.semantic_terms_sha256
            or self.matrix.shape != (64, 384)
            or not np.isfinite(self.matrix).all()
            or hashlib.sha256(
                np.asarray(self.matrix, dtype="<f4").tobytes()
            ).hexdigest()
            != self.semantic_terms["projection_sha256"]
            or _bounded_binding(self.encoder) != self.encoder_binding
            or _file_hash(Path(__file__))
            != self.semantic_terms["implementation"]["semantic_neural.py"]
            or _file_hash(Path(semantic_encoder.__file__))
            != self.semantic_terms["implementation"]["semantic_encoder.py"]
        ):
            raise ValueError("Semantic lab identity changed after initialization")

    def choose(self, job_id, context_sha256, question, features):
        self._check_semantics()
        job_id, question = _text(job_id, 256), _text(question, 5000)
        statistics = tool_features(features)
        origin = {
            "job_id": job_id,
            "context_sha256": _hash(context_sha256),
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "tool_statistics": statistics,
            "terms_sha256": self.terms_sha256,
            "semantic_terms_sha256": self.semantic_terms_sha256,
            "encoder_binding_sha256": self.encoder_binding_sha256,
            "lab_id": self.lab_id,
        }
        if any(
            row.get("status") == "embedding_reserved"
            for row in self.state["jobs"].values()
        ):
            raise RuntimeError(
                "Unresolved semantic observation requires explicit reconciliation; retry refused"
            )
        existing = self.state["jobs"].get(job_id)
        if existing is not None:
            if existing["origin"] != origin:
                raise ValueError("Changed duplicate semantic job input")
            return copy.deepcopy(existing["decision"])
        if (
            len(self.state["jobs"]) >= MAX_JOBS
            or sum(row["settlement"] is None for row in self.state["jobs"].values())
            >= MAX_PENDING
        ):
            raise ValueError("Semantic lab history or pending-job bound reached")
        # Commit disclosure before entering the encoder. A crash cannot turn an
        # absent completed vector into permission for an unaccounted retry.
        self.state["jobs"][job_id] = {
            "origin": origin,
            "status": "embedding_reserved",
            "decision": None,
            "eligibility": None,
            "settlement": None,
        }
        try:
            self._commit()
            packet = self.encoder.observe(question)
            self._check_semantics()
            vector, receipt = _vector_observation(
                packet, question, self.encoder_binding_sha256
            )
            inputs = semantic_encoder.semantic_currents(vector, self.matrix)
            utilities = {}
            for tool in STRATEGIES:
                row = statistics[tool]
                cost = 100000 if row["cost_unknown"] else row["mean_cost_units"]
                utilities[tool] = float(
                    np.clip(
                        0.5
                        + 0.25 * (row["success_probability_mean"] - 0.5)
                        + 0.15 * row["uncertainty"]
                        - 0.1 * min(1, cost / 100000),
                        0,
                        1,
                    )
                )
            frame = self.brain.advance(
                event=None,
                utilities={
                    STRATEGIES[tool]: value for tool, value in utilities.items()
                },
                duration_ms=self.terms["window_ms"],
                context=inputs["context"],
                regional_drive=np.asarray(inputs["regional_drive"]),
            )
            spikes = {
                tool: frame["action_spike_scores"][action]
                for tool, action in STRATEGIES.items()
            }
            decision = {
                **select_strategy(utilities, spikes, self.terms["mode"]),
                "job_id": job_id,
                "origin_sha256": digest(origin),
                "context_sha256": context_sha256,
                "utilities": utilities,
                "action_spikes": spikes,
                "window_ms": frame["window_ms"],
                "spikes": frame["spikes"],
                "spikes_sha256": frame["spikes_sha256"],
                "before_state_sha256": frame["before_state_sha256"],
                "after_state_sha256": frame["after_state_sha256"],
                "semantic_observation": receipt,
                "semantic_vector": vector.tolist(),
                "semantic_input_sha256": digest(inputs),
                "sensory_drive": inputs["regional_drive"][:128],
                "memory_features": inputs["context"]["memory_features"],
                "scope": "New isolated semantic-input lab; actual spikes and source-bound outcomes require a separate useful-decision comparison",
            }
            decision["decision_sha256"] = digest(decision)
            self.state["jobs"][job_id] = {
                "origin": origin,
                "status": "chosen",
                "decision": decision,
                "eligibility": self.brain.task_trace(
                    STRATEGIES[decision["first_tool"]]
                ),
                "settlement": None,
            }
            self._commit()
            return copy.deepcopy(decision)
        except BaseException:
            self.failed = True
            raise

    def settle(self, job_id, accepted, evidence_sha256):
        self._check_semantics()
        row = self.state["jobs"].get(job_id)
        if row is None or row.get("status") != "chosen":
            raise ValueError("Semantic outcome has no completed originating decision")
        return super().settle(job_id, accepted, evidence_sha256)
