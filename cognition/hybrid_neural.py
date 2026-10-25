"""An optional, separately persistent Brian2 first-tool strategy experiment.

Inputs describe software jobs and observed tool costs, never fabricated trades.
The host enforces the returned registered first tool and alone supplies verified
terminal outcomes. Wiring and recovery do not establish a useful neural gain.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import sys
from pathlib import Path

import brian2
import numpy as np

from cognition import brain, procedural_memory, task_brain
from cognition.brain import MODEL_VERSION
from cognition.procedural_memory import canonical, digest
from cognition.task_brain import TaskLearningBrain

VERSION = "nuria.hybrid-neural.v1"
STRATEGIES = {"list_tables": "explore", "read_knowledge": "experiment"}
CAPSULE_NAME = re.compile(r"[0-9]{6}-[0-9a-f]{16}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILE = 32 * 1024 * 1024
MAX_JOBS, MAX_PENDING = 256, 64


def _text(value, maximum):
    if type(value) is not str or not value or len(value.encode()) > maximum:
        raise ValueError("Neural bridge text exceeds its bound")
    return value


def _hash(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise ValueError("Neural bridge requires a full evidence hash")
    return value


def _file_hash(path):
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError("Neural capsule file is missing or exceeds its bound")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _write(path, raw):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def tool_features(features):
    """Normalize host-recorded observations; missing costs retain uncertainty."""
    if type(features) is not dict or set(features) - set(STRATEGIES):
        raise ValueError("Only registered past tool statistics can drive this bridge")
    normalized = {}
    for tool in STRATEGIES:
        record = features.get(tool)
        if record is None:
            record = {
                "observations": 0,
                "tool_successes": 0,
                "tool_errors": 0,
                "cost_known_count": 0,
                "cost_unknown_count": 0,
                "known_cost_units": 0,
                "mean_cost_units": None,
            }
        allowed = {
            "tool_name",
            "observations",
            "tool_successes",
            "tool_errors",
            "total_cost_units",
            "known_cost_units",
            "cost_known_count",
            "cost_unknown_count",
            "mean_cost_units",
            "success_probability_mean",
            "posterior_standard_deviation",
            "scope",
        }
        if type(record) is not dict or set(record) - allowed:
            raise ValueError("Unexpected tool statistic fields")
        n, success, errors = (
            record.get(key) for key in ("observations", "tool_successes", "tool_errors")
        )
        if (
            any(type(v) is not int or not 0 <= v <= 10**9 for v in (n, success, errors))
            or n != success + errors
            or record.get("tool_name", tool) != tool
        ):
            raise ValueError("Past tool observation counts disagree")
        known = record.get(
            "cost_known_count", n if record.get("total_cost_units") is not None else 0
        )
        unknown = record.get("cost_unknown_count", n - known)
        cost = record.get("known_cost_units", record.get("total_cost_units"))
        if (
            any(type(v) is not int or not 0 <= v <= n for v in (known, unknown))
            or known + unknown != n
            or type(cost) is not int
            or not 0 <= cost <= 10**15
            or known == 0
            and cost != 0
        ):
            raise ValueError("Past tool cost evidence is inconsistent")
        mean_cost = cost / known if known else None
        if "mean_cost_units" in record and record["mean_cost_units"] != mean_cost:
            raise ValueError("Past mean cost differs from its observations")
        a, b = 1 + success, 1 + errors
        mean, uncertainty = a / (a + b), math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
        for key, value in (
            ("success_probability_mean", mean),
            ("posterior_standard_deviation", uncertainty),
        ):
            if key in record and (
                type(record[key]) not in (int, float)
                or not math.isfinite(record[key])
                or not math.isclose(record[key], value, abs_tol=1e-12)
            ):
                raise ValueError("Tool probability differs from its observed counts")
        normalized[tool] = {
            "observations": n,
            "successes": success,
            "errors": errors,
            "known_cost_count": known,
            "unknown_cost_count": unknown,
            "known_cost_units": cost,
            "mean_cost_units": mean_cost,
            "success_probability_mean": mean,
            "uncertainty": uncertainty,
            "cost_unknown": mean_cost is None or unknown > 0,
        }
    return normalized


def select_strategy(utilities, spikes, mode):
    """Declared utility/spike blend; fixed mode is the exact first-tool ablation."""
    if (
        mode not in ("neural", "fixed")
        or set(utilities) != set(STRATEGIES)
        or set(spikes) != set(STRATEGIES)
    ):
        raise ValueError("Strategy selection needs the two registered arms")
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
        for v in utilities.values()
    ) or any(type(v) is not int or v < 0 for v in spikes.values()):
        raise ValueError("Strategy utilities or measured spikes are invalid")
    total = max(1, sum(spikes.values()))
    scores = {
        tool: 0.65 * utilities[tool] + 0.35 * spikes[tool] / total
        for tool in STRATEGIES
    }
    chosen = (
        max(STRATEGIES, key=lambda tool: scores[tool])
        if mode == "neural"
        else "list_tables"
    )
    return {
        "first_tool": chosen,
        "scores": scores
        if mode == "neural"
        else {tool: float(tool == "list_tables") for tool in STRATEGIES},
        "recorded_blend_scores": scores,
        "neural_selection_weight": 0.35 if mode == "neural" else 0.0,
    }


class HybridNeuralBridge:
    def __init__(
        self,
        directory,
        *,
        source_id,
        model_id,
        tool_version,
        mode="neural",
        window_ms=100,
    ):
        if (
            mode not in ("neural", "fixed")
            or type(window_ms) is not int
            or not 20 <= window_ms <= 100
        ):
            raise ValueError("Unknown neural mode or sensory window")
        self.directory = Path(directory)
        self.terms = {
            "schema": VERSION,
            "source_id": _text(source_id, 256),
            "model_id": _text(model_id, 512),
            "tool_version": _text(tool_version, 256),
            "mode": mode,
            "window_ms": window_ms,
            "brain_model": MODEL_VERSION,
            "brian2": brian2.__version__,
            "numpy": np.__version__,
            "python": list(sys.version_info[:3]),
            "strategy_actions": STRATEGIES,
            "utility": "clip(0.5+0.25*(validity_mean-0.5)+0.15*posterior_std-0.1*min(1,mean_cost/100000),0,1); unavailable mean cost uses conservative 100000 prior",
            "credit": "accepted:+1,rejected:-1 on retained action-region eligibility; not proof of correct causal attribution",
            "implementation": {
                Path(module.__file__).name: _file_hash(Path(module.__file__))
                for module in (brain, task_brain, procedural_memory)
            }
            | {Path(__file__).name: _file_hash(Path(__file__))},
        }
        self.terms_sha256 = digest(self.terms)
        self.failed = False
        self.lock = None
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker = self.directory / "LAB.json"
        if not marker.exists() and any(self.directory.iterdir()):
            raise ValueError("An unrelated history cannot become a neural lab")
        if self.directory.stat().st_mode & 0o077:
            raise ValueError("Neural history must be in a private directory")
        try:
            self.lock = (self.directory / "writer.lock").open("a+b")
            os.chmod(self.directory / "writer.lock", 0o600)
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fresh = not marker.exists()
            if fresh:
                _write(
                    marker,
                    canonical(
                        {"terms": self.terms, "lab_id": secrets.token_hex(16)}
                    ).encode(),
                )
            identity = json.loads(marker.read_bytes())
            if identity.get("terms") != self.terms:
                raise ValueError(
                    "Neural lab source, model, tools or implementation changed"
                )
            self.lab_id = _text(identity.get("lab_id"), 32)
            self.brain = TaskLearningBrain(self.directory / "neural")
            if fresh:
                self.state = {
                    "schema": VERSION,
                    "terms_sha256": self.terms_sha256,
                    "lab_id": self.lab_id,
                    "jobs": {},
                }
                self.head = None
                self._commit()
            else:
                self.head = self._verify_chain()
                capsule = self.directory / "capsules" / self.head["capsule"]
                metadata = json.loads((capsule / "metadata.json").read_bytes())
                self.state = metadata["state"]
                self.brain.net.restore(
                    "cognition",
                    filename=str(capsule / "network.pkl"),
                    restore_random_state=True,
                )
                for key in ("offset", "frames", "total_spikes", "rate_history"):
                    setattr(self.brain, key, metadata["brain"][key])
                if self.brain.state_hash() != metadata["brain"]["state_sha256"]:
                    raise ValueError(
                        "Restored neural state differs from its checkpoint"
                    )
        except BaseException:
            self.close()
            raise

    def _verify_chain(self):
        head_path = self.directory / "HEAD.json"
        if not head_path.is_file() or head_path.stat().st_size > 4096:
            raise ValueError(
                "Neural history exists without a bounded HEAD; reset refused"
            )
        head = json.loads(head_path.read_bytes())
        current, expected, sequence = (
            head.get("capsule"),
            head.get("manifest_sha256"),
            head.get("sequence"),
        )
        if type(sequence) is not int or not 0 <= sequence <= 2 * MAX_JOBS:
            raise ValueError("Neural capsule sequence is invalid")
        while current is not None:
            if type(current) is not str or not CAPSULE_NAME.fullmatch(current):
                raise ValueError("Neural capsule path is invalid")
            capsule = self.directory / "capsules" / current
            if _file_hash(capsule / "manifest.json") != _hash(expected):
                raise ValueError("Neural capsule manifest checksum differs")
            manifest = json.loads((capsule / "manifest.json").read_bytes())
            if (
                manifest.get("sequence") != sequence
                or manifest.get("terms_sha256") != self.terms_sha256
                or manifest.get("lab_id") != self.lab_id
            ):
                raise ValueError("Neural capsule lineage differs")
            for name in ("network.pkl", "metadata.json"):
                if _file_hash(capsule / name) != manifest.get(name):
                    raise ValueError("Neural capsule content checksum differs")
            metadata = json.loads((capsule / "metadata.json").read_bytes())
            if (
                metadata.get("state", {}).get("terms_sha256") != self.terms_sha256
                or metadata["state"].get("lab_id") != self.lab_id
                or metadata.get("brain", {}).get("topology_sha256")
                != self.brain.topology["sha256"]
            ):
                raise ValueError("Neural capsule state source or topology differs")
            current, expected = (
                manifest.get("previous_capsule"),
                manifest.get("previous_manifest_sha256"),
            )
            if (sequence == 0) != (current is None and expected is None):
                raise ValueError("Neural capsule genesis or parent is invalid")
            sequence -= 1
        if sequence != -1:
            raise ValueError("Neural capsule lineage is incomplete")
        return head

    def _commit(self):
        sequence = 0 if self.head is None else self.head["sequence"] + 1
        name = f"{sequence:06d}-{secrets.token_hex(8)}"
        capsule = self.directory / "capsules" / name
        capsule.mkdir(mode=0o700, parents=True, exist_ok=False)
        metadata = {
            "state": self.state,
            "brain": {
                "state_sha256": self.brain.state_hash(),
                "topology_sha256": self.brain.topology["sha256"],
                **{
                    key: getattr(self.brain, key)
                    for key in ("offset", "frames", "total_spikes", "rate_history")
                },
            },
        }
        encoded = canonical(metadata).encode()
        if len(encoded) > MAX_FILE:
            raise ValueError("Neural checkpoint metadata exceeds its bound")
        self.brain.net.store("cognition", filename=str(capsule / "network.pkl"))
        os.chmod(capsule / "network.pkl", 0o600)
        with (capsule / "network.pkl").open("rb") as stream:
            os.fsync(stream.fileno())
        _write(capsule / "metadata.json", encoded)
        manifest = {
            "schema": VERSION,
            "sequence": sequence,
            "terms_sha256": self.terms_sha256,
            "lab_id": self.lab_id,
            "previous_capsule": self.head["capsule"] if self.head else None,
            "previous_manifest_sha256": self.head["manifest_sha256"]
            if self.head
            else None,
            **{
                key: _file_hash(capsule / key)
                for key in ("network.pkl", "metadata.json")
            },
        }
        _write(capsule / "manifest.json", canonical(manifest).encode())
        _sync(capsule)
        _sync(capsule.parent)
        head = {
            "capsule": name,
            "manifest_sha256": _file_hash(capsule / "manifest.json"),
            "sequence": sequence,
        }
        temporary = self.directory / ("HEAD-" + secrets.token_hex(8) + ".tmp")
        _write(temporary, canonical(head).encode())
        os.replace(temporary, self.directory / "HEAD.json")
        _sync(self.directory)
        self.head = head

    def choose(self, job_id, context_sha256, question, features):
        if self.failed or self.lock is None:
            raise RuntimeError("Reopen the interrupted neural lab before continuing")
        if digest(self.terms) != self.terms_sha256:
            raise ValueError("Neural lab terms changed after initialization")
        job_id, question = _text(job_id, 256), _text(question, 16384)
        statistics = tool_features(features)
        origin = {
            "job_id": job_id,
            "context_sha256": _hash(context_sha256),
            "question_sha256": digest(question),
            "tool_statistics": statistics,
            "terms_sha256": self.terms_sha256,
            "lab_id": self.lab_id,
        }
        existing = self.state["jobs"].get(job_id)
        if existing:
            if existing["origin"] != origin:
                raise ValueError("Changed duplicate neural job input")
            return copy.deepcopy(existing["decision"])
        if (
            len(self.state["jobs"]) >= MAX_JOBS
            or sum(row["settlement"] is None for row in self.state["jobs"].values())
            >= MAX_PENDING
        ):
            raise ValueError("Neural lab history or pending-job bound reached")
        texture = (
            np.frombuffer(
                hashlib.shake_256(
                    canonical(
                        {
                            key: origin[key]
                            for key in ("job_id", "context_sha256", "question_sha256")
                        }
                    ).encode()
                ).digest(64),
                dtype=np.uint8,
            ).astype(float)
            / 255
        )
        memory = texture.copy()
        utilities = {}
        for index, tool in enumerate(STRATEGIES):
            row = statistics[tool]
            cost = 100000 if row["cost_unknown"] else row["mean_cost_units"]
            scaled_cost = min(1, cost / 100000)
            utilities[tool] = float(
                np.clip(
                    0.5
                    + 0.25 * (row["success_probability_mean"] - 0.5)
                    + 0.15 * row["uncertainty"]
                    - 0.1 * scaled_cost,
                    0,
                    1,
                )
            )
            memory[index * 4 : index * 4 + 4] = [
                row["success_probability_mean"],
                row["uncertainty"],
                scaled_cost,
                float(row["cost_unknown"]),
            ]
        drive = np.zeros(1024)
        drive[:128] = np.tile(texture, 2) * 0.18
        try:
            frame = self.brain.advance(
                event=None,
                utilities={
                    STRATEGIES[tool]: value for tool, value in utilities.items()
                },
                duration_ms=self.terms["window_ms"],
                context={
                    "memory_features": memory,
                    "salience": min(1, len(question.encode()) / 4096),
                    "energy": 1,
                },
                regional_drive=drive,
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
                "memory_features": memory.tolist(),
                "sensory_drive": drive[:128].tolist(),
                "scope": "New isolated software-job circuit; actual action spikes and declared utility, no live-trade history or superiority result",
            }
            decision["decision_sha256"] = digest(decision)
            self.state["jobs"][job_id] = {
                "origin": origin,
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
        """Host-only: pass a verified exact-job terminal result, never a model grade."""
        if self.failed or self.lock is None:
            raise RuntimeError("Reopen the interrupted neural lab before continuing")
        if digest(self.terms) != self.terms_sha256:
            raise ValueError("Neural lab terms changed after initialization")
        _text(job_id, 256)
        if type(accepted) is not bool:
            raise ValueError("Neural credit needs a verified Boolean outcome")
        _hash(evidence_sha256)
        row = self.state["jobs"].get(job_id)
        if row is None:
            raise ValueError("Neural credit has no originating decision")
        previous = row["settlement"]
        if previous is not None:
            if (
                previous["accepted"] != accepted
                or previous["evidence_sha256"] != evidence_sha256
            ):
                raise ValueError("Changed duplicate neural outcome")
            return copy.deepcopy(previous)
        try:
            signal = 1.0 if accepted else -1.0
            credit = self.brain.task_reward(row["eligibility"], signal)
            receipt = {
                "job_id": job_id,
                "origin_sha256": digest(row["origin"]),
                "decision_sha256": row["decision"]["decision_sha256"],
                "accepted": accepted,
                "evidence_sha256": evidence_sha256,
                "neural_reward_signal": signal,
                "credit": credit,
            }
            receipt["receipt_sha256"] = digest(receipt)
            row["settlement"] = receipt
            self._commit()
            return copy.deepcopy(receipt)
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self.lock is not None:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None
